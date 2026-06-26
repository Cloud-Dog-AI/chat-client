# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.cross_project import _ensure_local_server_runtime, _stop_local_server_runtime


def _prepare_local_translator_expert(
    *,
    db_path: str = "/tmp/expert_at.db",
    expert_config_id: int = 10,
) -> None:
    """Pin the local translator expert to a fast real model for AT1.14.

    This helper only applies when a LOCAL expert-agent runtime has been
    started alongside the chat-client test (local-docker / local-server
    modes that bring up a local expert-agent with its own ``/tmp/expert_at.db``
    seeded via the service's migrations).

    When the test runs against a REMOTE / PREPROD expertagent0 (env-AT-local-server
    points translator at https://expertagent.example.com:8000), there is no
    local expert-agent runtime: ``/tmp/expert_at.db`` does not exist, or is an
    empty file from a prior aborted run. The preprod expertagent0 owns its own
    ``expert_configs`` row id=10 seeded from migrations; it must not be mutated
    from chat-client tests. F-3m (W28A-F3m-APPLY).
    """
    # Guard: only operate on a local expert-agent DB that actually contains the
    # expected schema. Skip silently against preprod / absent local runtime.
    if not os.path.isfile(db_path) or os.path.getsize(db_path) == 0:
        return
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error:
        return
    try:
        cur = conn.execute(
            "select name from sqlite_master where type='table' and name='expert_configs'"
        )
        if cur.fetchone() is None:
            return
        cur = conn.execute(
            """
            update expert_configs
               set llm_provider = ?,
                   llm_model = ?,
                   llm_params_json = ?
             where id = ?
            """,
            (
                "ollama",
                "ibm/granite4:tiny-h",
                json.dumps({"base_url": "https://llm.example.com"}),
                int(expert_config_id),
            ),
        )
        if cur.rowcount:
            conn.commit()
    finally:
        conn.close()


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _event_calls(events: List[Dict[str, Any]], *, server_index: int) -> List[str]:
    out: List[str] = []
    for e in events:
        if not isinstance(e, dict):
            continue
        if str(e.get("event_type") or "") != "mcp_tool_call":
            continue
        data = e.get("data") or {}
        if not isinstance(data, dict):
            continue
        if int(data.get("server_index", -1)) != int(server_index):
            continue
        name = str(data.get("name") or "").strip()
        if name:
            out.append(name)
    return out


@pytest.fixture(scope="module", autouse=True)
def _api_server(env_file):
    cfg = ConfigManager(env_file=env_file)
    local_expert_started = _ensure_local_server_runtime(
        cfg,
        "chat_tests.at1_14.expert_mcp",
        label="AT1.14 expert-agent-mcp",
    )
    # Only mutate the local expert-agent SQLite when a local runtime was
    # actually started; preprod/remote flows must leave the remote DB alone.
    # F-3m (W28A-F3m-APPLY).
    if local_expert_started:
        _prepare_local_translator_expert()
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        yield None
    finally:
        try:
            stop_api(cfg, env_file=env_file)
        finally:
            _stop_local_server_runtime(
                cfg,
                "chat_tests.at1_14.expert_mcp",
                label="AT1.14 expert-agent-mcp",
            )
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_14_bbc_news_summary_then_hungarian_translation_via_messages(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))

    search_server_index = int(_require_cfg(cfg, "chat_tests.at1_14.search_server_index"))
    translator_server_index = int(_require_cfg(cfg, "chat_tests.at1_14.translator_server_index"))
    prompt = str(_require_cfg(cfg, "chat_tests.at1_14.prompt")).strip()

    blocked_markers = [
        "no current access to the outside world",
        "no access to the outside world",
        "i don't have access to the outside world",
        "i do not have access to the outside world",
    ]

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds, headers=api_headers(cfg)) as client:
        created = await client.post("/sessions", json={"metadata": {"suite": "at1.14"}})
        assert created.status_code == 200
        session_id = str(created.json().get("session_id") or "")
        assert session_id

        prefs = await client.put(
            f"/sessions/{session_id}/preferences",
            json={"selected_mcp_server_indices": [search_server_index, translator_server_index]},
        )
        assert prefs.status_code == 200

        reply = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": prompt, "stream": False},
        )
        assert reply.status_code == 200
        content = str(reply.json().get("content") or "")
        if not content.strip():
            raise RuntimeError("CRITICAL ERROR: assistant response is empty")

        lowered = content.lower()
        if any(marker in lowered for marker in blocked_markers):
            raise RuntimeError("CRITICAL ERROR: assistant reported no outside access")

        transcript = await client.get(f"/sessions/{session_id}/transcript")
        assert transcript.status_code == 200
        events = transcript.json().get("events") or []

        search_calls = _event_calls(events, server_index=search_server_index)
        translator_calls = _event_calls(events, server_index=translator_server_index)

        if not search_calls:
            raise RuntimeError("CRITICAL ERROR: search MCP was not called by /messages orchestration")

        if "start_session" not in translator_calls:
            raise RuntimeError("CRITICAL ERROR: translator MCP start_session was not called")

        if "chat" not in translator_calls:
            raise RuntimeError("CRITICAL ERROR: translator MCP chat was not called")

        has_direct_response = any(
            str((e or {}).get("event_type") or "") == "mcp_direct_response"
            for e in events
        )
        if not has_direct_response:
            raise RuntimeError(
                "CRITICAL ERROR: translator direct response event missing; strict-fail policy forbids fallback masking"
            )

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.mcp, pytest.mark.heavy]
