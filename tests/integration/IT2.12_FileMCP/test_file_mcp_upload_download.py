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


@pytest.fixture(scope="module", autouse=True)
def _api_server(env_file):
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
@pytest.mark.IT
@pytest.mark.mcp
@pytest.mark.req("FR-011")


@pytest.mark.asyncio
async def test_it2_12_file_mcp_upload_download_roundtrip(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)
    server_cfg = cfg.get("mcp.it2_12.server")
    if server_cfg is not None and not isinstance(server_cfg, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_12.server must be an object")
    server_index_raw = cfg.get("mcp.it2_12.server_index")
    try:
        server_index = int(server_index_raw) if server_index_raw is not None else 0
    except (TypeError, ValueError) as e:
        raise RuntimeError("CRITICAL ERROR: mcp.it2_12.server_index must be an integer") from e

    target_path = str(_require_cfg(cfg, "mcp.it2_12.file_path")).strip()
    if not target_path:
        raise RuntimeError("CRITICAL ERROR: mcp.it2_12.file_path must be non-empty")
    file_content = str(_require_cfg(cfg, "mcp.it2_12.file_content"))
    search_query = str(_require_cfg(cfg, "mcp.it2_12.search_query")).strip()
    if not search_query:
        raise RuntimeError("CRITICAL ERROR: mcp.it2_12.search_query must be non-empty")

    encoded_content = base64.b64encode(file_content.encode("utf-8")).decode("ascii")

    async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
        create_resp = await client.post("/sessions", json={"metadata": {"suite": "it2.12"}})
        assert create_resp.status_code == 200
        session_id = create_resp.json().get("session_id")
        assert session_id

        upload_resp = await client.post(
            f"/sessions/{session_id}/mcp/files/upload",
            json={
                "server_index": None if isinstance(server_cfg, dict) else server_index,
                "server": server_cfg if isinstance(server_cfg, dict) else None,
                "path": target_path,
                "content_base64": encoded_content,
                "overwrite": True,
                "require_initialize": require_initialize,
            },
        )
        assert upload_resp.status_code == 200
        upload_payload = upload_resp.json()
        assert upload_payload.get("path")
        assert int(upload_payload.get("bytes_written") or 0) > 0
        if isinstance(server_cfg, dict):
            assert upload_payload.get("mcp_server_index") is None
        else:
            assert upload_payload.get("mcp_server_index") == server_index

        download_resp = await client.post(
            f"/sessions/{session_id}/mcp/files/download",
            json={
                "server_index": None if isinstance(server_cfg, dict) else server_index,
                "server": server_cfg if isinstance(server_cfg, dict) else None,
                "path": target_path,
                "require_initialize": require_initialize,
            },
        )
        assert download_resp.status_code == 200
        download_payload = download_resp.json()
        encoded = download_payload.get("content_base64")
        assert isinstance(encoded, str) and encoded
        decoded = base64.b64decode(encoded).decode("utf-8")
        assert decoded == file_content

        exec_resp = await client.post(
            f"/sessions/{session_id}/mcp/execute",
            json={
                "server_index": None if isinstance(server_cfg, dict) else server_index,
                "server": server_cfg if isinstance(server_cfg, dict) else None,
                "require_initialize": require_initialize,
                "protocol_version": str(_require_cfg(cfg, "mcp.defaults.protocol_version")),
                "steps": [
                    {
                        "method": "tools/call",
                        "params": {
                            "name": "search_content",
                            "arguments": {"query": search_query, "max_results": 5},
                        },
                    }
                ],
            },
        )
        assert exec_resp.status_code == 200
        items = exec_resp.json().get("results") or []
        if not items or not items[0].get("ok"):
            raise RuntimeError("CRITICAL ERROR: search_content failed via API")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.mcp, pytest.mark.heavy]
