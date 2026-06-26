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

import json
import base64
from typing import Any, Dict

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.file_mcp_runtime import maybe_start_file_mcp, maybe_stop_file_mcp


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _parse_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    structured = result.get("structuredContent")
    if structured not in (None, {}):
        if isinstance(structured, dict):
            return structured
        return {"value": structured}
    content = result.get("content") or []
    text = ""
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text += str(item.get("text") or "")
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}
    if not isinstance(parsed, dict):
        return {"value": parsed}
    return parsed


async def _call_tool(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    server_index: int,
    require_initialize: bool,
    name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    resp = await client.post(
        f"/sessions/{session_id}/mcp/tools/call",
        json={
            "server_index": server_index,
            "name": name,
            "arguments": arguments,
            "require_initialize": require_initialize,
        },
    )
    assert resp.status_code == 200
    payload = resp.json() or {}
    if payload.get("isError") is True:
        raise RuntimeError(f"CRITICAL ERROR: tool '{name}' failed")
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        return {"ok": True, "value": result}
    parsed = _parse_payload(result)
    if not parsed:
        return {"ok": True}
    return parsed


@pytest.fixture(scope="module", autouse=True)
def _servers(env_file):
    cfg = ConfigManager(env_file=env_file)
    started_file_mcp = maybe_start_file_mcp(cfg)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        yield None
    finally:
        stop_api(cfg, env_file=env_file)
        if started_file_mcp:
            maybe_stop_file_mcp(cfg)
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_11_file_mcp_path_ops_multistep(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    file_server_index = int(_require_cfg(cfg, "mcp.at1_11.file_server_index"))
    require_initialize = bool(cfg.get("mcp.at1_11.require_initialize") or False)
    file_root = str(_require_cfg(cfg, "mcp.at1_11.file_root")).rstrip("/")

    dir_initial = f"{file_root}/at1_11_源-Δ"
    dir_renamed = f"{file_root}/at1_11_renamed-ß"
    dir_moved = f"{file_root}/at1_11/final-δοκιμή"
    file_initial = f"{dir_renamed}/draft-🙂.txt"
    file_renamed = f"{dir_moved}/final-данные.txt"

    async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
        session_resp = await client.post("/sessions", json={"metadata": {"suite": "at1.11"}})
        assert session_resp.status_code == 200
        session_id = str(session_resp.json().get("session_id") or "")
        assert session_id

        tools_resp = await client.post(
            f"/sessions/{session_id}/mcp/tools/list",
            json={"server_index": file_server_index, "require_initialize": require_initialize},
        )
        assert tools_resp.status_code == 200
        tool_names = {str(item.get("name") or "") for item in (tools_resp.json().get("tools") or [])}
        for required in {"create_dir", "chmod_path", "rename_path", "move_path", "write_file", "delete_file"}:
            assert required in tool_names

        create_result = await _call_tool(
            client,
            session_id,
            server_index=file_server_index,
            require_initialize=require_initialize,
            name="create_dir",
            arguments={"path": dir_initial},
        )
        assert create_result.get("ok") is True

        rename_dir_result = await _call_tool(
            client,
            session_id,
            server_index=file_server_index,
            require_initialize=require_initialize,
            name="rename_path",
            arguments={"src": dir_initial, "dst": dir_renamed, "overwrite": True},
        )
        assert rename_dir_result.get("ok") is True

        write_result = await _call_tool(
            client,
            session_id,
            server_index=file_server_index,
            require_initialize=require_initialize,
            name="write_file",
            arguments={"path": file_initial, "content": "version-1", "overwrite": True},
        )
        assert write_result.get("ok") is True

        update_result = await _call_tool(
            client,
            session_id,
            server_index=file_server_index,
            require_initialize=require_initialize,
            name="write_file",
            arguments={"path": file_initial, "content": "version-2", "overwrite": True},
        )
        assert update_result.get("ok") is True

        chmod_result = await _call_tool(
            client,
            session_id,
            server_index=file_server_index,
            require_initialize=require_initialize,
            name="chmod_path",
            arguments={"path": file_initial, "mode": "640"},
        )
        assert chmod_result.get("ok") is True

        move_dir_result = await _call_tool(
            client,
            session_id,
            server_index=file_server_index,
            require_initialize=require_initialize,
            name="move_path",
            arguments={"src": dir_renamed, "dst": dir_moved, "overwrite": True},
        )
        assert move_dir_result.get("ok") is True

        rename_file_result = await _call_tool(
            client,
            session_id,
            server_index=file_server_index,
            require_initialize=require_initialize,
            name="rename_path",
            arguments={"src": f"{dir_moved}/draft-🙂.txt", "dst": file_renamed, "overwrite": True},
        )
        assert rename_file_result.get("ok") is True

        download_resp = await client.post(
            f"/sessions/{session_id}/mcp/files/download",
            json={
                "server_index": file_server_index,
                "path": file_renamed,
                "require_initialize": require_initialize,
            },
        )
        assert download_resp.status_code == 200
        download_payload = download_resp.json() or {}
        content_b64 = str(download_payload.get("content_base64") or "")
        if not content_b64:
            raise RuntimeError("CRITICAL ERROR: download payload missing content_base64")
        downloaded = base64.b64decode(content_b64).decode("utf-8", errors="replace")
        if "version-2" not in downloaded:
            raise RuntimeError(f"CRITICAL ERROR: expected updated content not found; downloaded={downloaded}")

        delete_result = await _call_tool(
            client,
            session_id,
            server_index=file_server_index,
            require_initialize=require_initialize,
            name="delete_file",
            arguments={"path": file_renamed},
        )
        assert delete_result.get("ok") is True

        list_result = await _call_tool(
            client,
            session_id,
            server_index=file_server_index,
            require_initialize=require_initialize,
            name="list_dir",
            arguments={"path": dir_moved, "recursive": True},
        )
        entries = list_result.get("entries") or []
        assert all("final-данные.txt" not in str(item) for item in entries)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.mcp, pytest.mark.heavy]

