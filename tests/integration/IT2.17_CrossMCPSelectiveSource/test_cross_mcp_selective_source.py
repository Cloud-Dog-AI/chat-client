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
from typing import Any, Dict, List, Optional

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


def _parse_json_list(value: Any, key: str) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as e:
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list") from e
        if not isinstance(parsed, list):
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list")
        return [str(item) for item in parsed]
    raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list")


def _resolve_server(
    cfg: ConfigManager,
    *,
    server_key: str,
    index_key: str,
) -> tuple[Optional[int], Optional[Dict[str, Any]]]:
    server = cfg.get(server_key)
    if server is not None and not isinstance(server, dict):
        raise RuntimeError(f"CRITICAL ERROR: {server_key} must be an object")
    if isinstance(server, dict):
        return None, dict(server)
    return int(_require_cfg(cfg, index_key)), None
@pytest.mark.IT
@pytest.mark.mcp
@pytest.mark.req("FR-011")


@pytest.mark.asyncio
async def test_it2_17_cross_mcp_selective_source(env_file):
    cfg = ConfigManager(env_file=env_file)
    curl_ollama_tags(cfg)
    started_file_mcp = maybe_start_file_mcp(cfg)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        base_url = api_base_url(cfg)
        timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
        protocol_version = str(_require_cfg(cfg, "mcp.defaults.protocol_version"))
        search_server_index, search_server = _resolve_server(
            cfg,
            server_key="mcp.it2_17.search_server",
            index_key="mcp.it2_17.search_server_index",
        )
        file_server_index, file_server = _resolve_server(
            cfg,
            server_key="mcp.it2_17.file_server",
            index_key="mcp.it2_17.file_server_index",
        )
        search_require_initialize = bool(cfg.get("mcp.it2_17.require_initialize_search") or False)
        file_require_initialize = bool(cfg.get("mcp.it2_17.require_initialize_file") or False)
        search_tool_name = str(_require_cfg(cfg, "mcp.it2_17.search_tool_name"))
        search_args = _parse_json_obj(_require_cfg(cfg, "mcp.it2_17.search_args"), "mcp.it2_17.search_args")
        site_list = _parse_json_list(_require_cfg(cfg, "mcp.it2_17.site_list"), "mcp.it2_17.site_list")
        blocked_domains = _parse_json_list(_require_cfg(cfg, "mcp.it2_17.blocked_domains"), "mcp.it2_17.blocked_domains")
        file_root = str(_require_cfg(cfg, "mcp.it2_17.file_root")).rstrip("/")
        prompt = str(_require_cfg(cfg, "chat_tests.it2_17.summary_prompt"))
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        sites_path = f"{file_root}/it2_17_sites_{ts}.txt"
        answer_path = f"{file_root}/it2_17_answer_{ts}.md"

        async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
            session_resp = await client.post("/sessions", json={"metadata": {"suite": "it2.17"}})
            assert session_resp.status_code == 200
            session_id = str(session_resp.json().get("session_id") or "")
            assert session_id

            sites_text = "\n".join(site_list) + "\n"
            upload_payload: Dict[str, Any] = {
                "path": sites_path,
                "content_base64": base64.b64encode(sites_text.encode("utf-8")).decode("ascii"),
                "overwrite": True,
                "require_initialize": file_require_initialize,
            }
            if file_server is not None:
                upload_payload["server"] = file_server
            else:
                upload_payload["server_index"] = file_server_index
            upload_sites = await client.post(f"/sessions/{session_id}/mcp/files/upload", json=upload_payload)
            assert upload_sites.status_code == 200

            exec_payload: Dict[str, Any] = {
                "require_initialize": search_require_initialize,
                "protocol_version": protocol_version,
                "steps": [
                    {"method": "tools/list"},
                    {"method": "tools/call", "params": {"name": search_tool_name, "arguments": search_args}},
                ],
            }
            if search_server is not None:
                exec_payload["server"] = search_server
            else:
                exec_payload["server_index"] = search_server_index
            exec_resp = await client.post(f"/sessions/{session_id}/mcp/execute", json=exec_payload)
            assert exec_resp.status_code == 200
            results = exec_resp.json().get("results") or []
            if len(results) < 2 or not results[1].get("ok"):
                raise RuntimeError("CRITICAL ERROR: Search MCP selective-source query failed")
            search_text = json.dumps(results[1].get("result") or {})

            msg = await client.post(
                f"/sessions/{session_id}/messages",
                json={
                    "content": (
                        f"{prompt}\n\nAllowed sites list:\n{sites_text}\n\n"
                        f"Search output:\n{search_text}\n\n"
                        "Only reference domains from the allowed list."
                    ),
                    "stream": False,
                },
            )
            assert msg.status_code == 200
            answer = str(msg.json().get("content") or "")
            if not answer.strip():
                raise RuntimeError("CRITICAL ERROR: selective source summary was empty")

            if not any(domain in answer for domain in site_list):
                raise RuntimeError("CRITICAL ERROR: summary did not include any allowed domain")
            for blocked in blocked_domains:
                if blocked and blocked in answer:
                    raise RuntimeError(f"CRITICAL ERROR: summary included blocked domain {blocked}")

            save_payload: Dict[str, Any] = {
                "path": answer_path,
                "content_base64": base64.b64encode(answer.encode("utf-8")).decode("ascii"),
                "overwrite": True,
                "require_initialize": file_require_initialize,
            }
            if file_server is not None:
                save_payload["server"] = file_server
            else:
                save_payload["server_index"] = file_server_index
            save_resp = await client.post(f"/sessions/{session_id}/mcp/files/upload", json=save_payload)
            assert save_resp.status_code == 200
    finally:
        stop_api(cfg, env_file=env_file)
        if started_file_mcp:
            maybe_stop_file_mcp(cfg)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.llm, pytest.mark.mcp, pytest.mark.heavy]
