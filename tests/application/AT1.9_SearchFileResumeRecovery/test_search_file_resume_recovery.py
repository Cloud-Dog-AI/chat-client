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

import base64
import json
from datetime import datetime, timezone
from typing import Any, Dict

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.file_mcp_runtime import maybe_start_file_mcp, maybe_stop_file_mcp
from tests.helpers.ollama_preflight import curl_ollama_tags


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _parse_json_obj(value: Any, key: str) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as e:
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON object") from e
        if not isinstance(parsed, dict):
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON object")
        return parsed
    raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON object")
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_9_search_file_resume_recovery(env_file):
    cfg = ConfigManager(env_file=env_file)
    curl_ollama_tags(cfg)
    started_file_mcp = maybe_start_file_mcp(cfg)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        base_url = api_base_url(cfg)
        timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
        protocol_version = str(_require_cfg(cfg, "mcp.defaults.protocol_version"))
        search_server_index = int(_require_cfg(cfg, "mcp.at1_9.search_server_index"))
        file_server_index = int(_require_cfg(cfg, "mcp.at1_9.file_server_index"))
        search_require_initialize = bool(cfg.get("mcp.at1_9.require_initialize_search") or False)
        file_require_initialize = bool(cfg.get("mcp.at1_9.require_initialize_file") or False)
        search_tool_name = str(_require_cfg(cfg, "mcp.at1_9.search_tool_name"))
        search_args = _parse_json_obj(_require_cfg(cfg, "mcp.at1_9.search_args"), "mcp.at1_9.search_args")
        file_root = str(_require_cfg(cfg, "mcp.at1_9.file_root")).rstrip("/")
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        stage_file = f"{file_root}/at1_9_stage_{ts}.md"
        prompt1 = str(_require_cfg(cfg, "chat_tests.at1_9.prompt1"))
        prompt2 = str(_require_cfg(cfg, "chat_tests.at1_9.prompt2"))

        async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
            session_resp = await client.post("/sessions", json={"metadata": {"suite": "at1.9"}})
            assert session_resp.status_code == 200
            session_id = str(session_resp.json().get("session_id") or "")
            assert session_id

            exec_resp = await client.post(
                f"/sessions/{session_id}/mcp/execute",
                json={
                    "server_index": search_server_index,
                    "require_initialize": search_require_initialize,
                    "protocol_version": protocol_version,
                    "steps": [
                        {"method": "tools/list"},
                        {"method": "tools/call", "params": {"name": search_tool_name, "arguments": search_args}},
                    ],
                },
            )
            assert exec_resp.status_code == 200
            results = exec_resp.json().get("results") or []
            if len(results) < 2 or not results[1].get("ok"):
                raise RuntimeError("CRITICAL ERROR: Search MCP pre-resume stage failed")
            search_text = json.dumps(results[1].get("result") or {})

            msg1 = await client.post(
                f"/sessions/{session_id}/messages",
                json={"content": f"{prompt1}\n\nSearch results:\n{search_text}", "stream": False},
            )
            assert msg1.status_code == 200
            msg1_text = str(msg1.json().get("content") or "")
            if not msg1_text.strip():
                raise RuntimeError("CRITICAL ERROR: first response was empty")

            upload = await client.post(
                f"/sessions/{session_id}/mcp/files/upload",
                json={
                    "server_index": file_server_index,
                    "path": stage_file,
                    "content_base64": base64.b64encode(msg1_text.encode("utf-8")).decode("ascii"),
                    "overwrite": True,
                    "require_initialize": file_require_initialize,
                },
            )
            assert upload.status_code == 200

            stop_api(cfg, env_file=env_file)
            start_api(cfg, env_file=env_file)
            wait_for_api(cfg)

            load_resp = await client.post(f"/sessions/{session_id}/load")
            assert load_resp.status_code == 200

            download = await client.post(
                f"/sessions/{session_id}/mcp/files/download",
                json={"server_index": file_server_index, "path": stage_file, "require_initialize": file_require_initialize},
            )
            assert download.status_code == 200
            down_text = base64.b64decode(str((download.json() or {}).get("content_base64") or "")).decode("utf-8")
            if not down_text.strip():
                raise RuntimeError("CRITICAL ERROR: resumed download content empty")

            msg2 = await client.post(
                f"/sessions/{session_id}/messages",
                json={"content": f"{prompt2}\n\nPrior summary:\n{down_text}", "stream": False},
            )
            assert msg2.status_code == 200
            msg2_text = str(msg2.json().get("content") or "")
            if not msg2_text.strip():
                raise RuntimeError("CRITICAL ERROR: second response after resume was empty")

            transcript = await client.get(f"/sessions/{session_id}/transcript")
            assert transcript.status_code == 200
            events = transcript.json().get("events") or []
            resumed = [e for e in events if e.get("event_type") == "session_resumed"]
            if not resumed:
                raise RuntimeError("CRITICAL ERROR: transcript missing session_resumed event")
    finally:
        stop_api(cfg, env_file=env_file)
        if started_file_mcp:
            maybe_stop_file_mcp(cfg)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.llm, pytest.mark.mcp, pytest.mark.heavy]

