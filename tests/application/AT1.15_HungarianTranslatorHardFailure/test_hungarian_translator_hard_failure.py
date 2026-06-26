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

from typing import Any, Dict, List

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


@pytest.fixture(scope="module", autouse=True)
def _api_server(env_file):
    cfg = ConfigManager(env_file=env_file)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        yield None
    finally:
        stop_api(cfg, env_file=env_file)
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_15_translator_unavailable_returns_hard_failure(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))

    search_server_index = int(
        cfg.get("chat_tests.at1_15.search_server_index")
        or _require_cfg(cfg, "chat_tests.at1_14.search_server_index")
    )
    translator_server_index = int(
        cfg.get("chat_tests.at1_15.translator_server_index")
        or _require_cfg(cfg, "chat_tests.at1_14.translator_server_index")
    )
    prompt = str(
        cfg.get("chat_tests.at1_15.prompt")
        or _require_cfg(cfg, "chat_tests.at1_14.prompt")
    ).strip()

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds, headers=api_headers(cfg)) as client:
        created = await client.post("/sessions", json={"metadata": {"suite": "at1.15"}})
        if created.status_code != 200:
            raise RuntimeError(f"CRITICAL ERROR: create session failed: {created.status_code} {created.text}")
        session_id = str(created.json().get("session_id") or "")
        if not session_id:
            raise RuntimeError("CRITICAL ERROR: create session did not return session_id")

        prefs = await client.put(
            f"/sessions/{session_id}/preferences",
            json={"selected_mcp_server_indices": [search_server_index, translator_server_index]},
        )
        if prefs.status_code != 200:
            raise RuntimeError(f"CRITICAL ERROR: preferences update failed: {prefs.status_code} {prefs.text}")

        reply = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": prompt, "stream": False},
        )
        if reply.status_code != 502:
            raise RuntimeError(
                f"CRITICAL ERROR: strict fail expected HTTP 502, got {reply.status_code}: {reply.text}"
            )

        body = reply.json() if reply.headers.get("content-type", "").startswith("application/json") else {}
        detail = str(body.get("detail") or "").lower()
        errors = body.get("errors") if isinstance(body, dict) else None
        if isinstance(errors, list) and errors:
            first_error = errors[0] if isinstance(errors[0], dict) else {}
            detail = str(first_error.get("message") or detail).lower()
        translator_terms = ("translator", "hungarian", "translation")
        unavailable_terms = ("unavailable", "unreachable", "connection", "refused", "failed")
        if not any(term in detail for term in translator_terms) or not any(term in detail for term in unavailable_terms):
            raise RuntimeError(
                f"CRITICAL ERROR: 502 payload must indicate translator availability failure, got: {body}"
            )

        transcript = await client.get(f"/sessions/{session_id}/transcript")
        if transcript.status_code != 200:
            raise RuntimeError(f"CRITICAL ERROR: transcript fetch failed: {transcript.status_code} {transcript.text}")
        events: List[Dict[str, Any]] = transcript.json().get("events") or []

        strict_fail_events: List[Dict[str, Any]] = []
        for event in events:
            if str((event or {}).get("event_type") or "") != "mcp_context_error":
                continue
            data = (event or {}).get("data") or {}
            if bool(data.get("strict_fail")):
                strict_fail_events.append(data)

        if not strict_fail_events:
            raise RuntimeError("CRITICAL ERROR: strict_fail mcp_context_error event missing")

        translator_recorded = False
        for data in strict_fail_events:
            indices = data.get("server_indices")
            if isinstance(indices, list) and translator_server_index in [int(x) for x in indices]:
                translator_recorded = True
                break
        if not translator_recorded:
            raise RuntimeError(
                "CRITICAL ERROR: strict_fail event missing selected translator server index"
            )

        has_direct_response = any(
            str((event or {}).get("event_type") or "") == "mcp_direct_response"
            for event in events
        )
        if has_direct_response:
            raise RuntimeError("CRITICAL ERROR: mcp_direct_response should not exist in hard-failure path")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.mcp, pytest.mark.heavy]

