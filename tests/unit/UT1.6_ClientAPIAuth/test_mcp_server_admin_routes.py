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

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from cloud_dog_chat_client.api.routes import MCPServerAdminRequest, build_router
from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.session import SessionManager


def _route_endpoint(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"endpoint not found: {method} {path}")


def _request() -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp/servers",
        "headers": [(b"x-user", b"unit-admin")],
    }
    return Request(scope)
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_6_mcp_server_admin_add_update_delete_and_validation(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__NAME", "search-a")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__TRANSPORT", "streamable_http")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__BASE_URL", "https://search-a.example")

    cfg = ConfigManager(env_file=env_file)
    sessions = SessionManager("./logs")
    router = build_router(config=cfg, sessions=sessions)

    add_endpoint = _route_endpoint(router, "/mcp/servers", "POST")
    update_endpoint = _route_endpoint(router, "/mcp/servers/{server_index}", "PUT")
    delete_endpoint = _route_endpoint(router, "/mcp/servers/{server_index}", "DELETE")
    list_endpoint = _route_endpoint(router, "/mcp/servers", "GET")

    before = await list_endpoint()
    assert len(before["servers"]) == 1

    add_resp = await add_endpoint(
        MCPServerAdminRequest(
            server={
                "name": "sql-admin",
                "transport": "http_jsonrpc",
                "base_url": "https://sql.example",
                "messages_path": "/messages",
            }
        ),
        request=_request(),  # direct endpoint invocation bypasses dependency/auth
    )
    assert add_resp["index"] == 1
    assert add_resp["server"]["name"] == "sql-admin"

    update_resp = await update_endpoint(
        1,
        MCPServerAdminRequest(
            server={
                "name": "sql-admin-updated",
                "transport": "http_jsonrpc",
                "base_url": "https://sql2.example",
            }
        ),
        request=_request(),  # direct endpoint invocation bypasses dependency/auth
    )
    assert update_resp["server"]["name"] == "sql-admin-updated"

    with pytest.raises(HTTPException) as exc:
        await update_endpoint(
            1,
            MCPServerAdminRequest(
                server={
                    "name": "bad",
                    "transport": "streamable_http",
                    # base_url required for this transport
                }
            ),
            request=_request(),  # direct endpoint invocation bypasses dependency/auth
        )
    assert exc.value.status_code == 400

    delete_resp = await delete_endpoint(1, request=_request())  # direct endpoint invocation bypasses dependency/auth
    assert delete_resp["removed"]["name"] == "sql-admin-updated"

    after = await list_endpoint()
    assert len(after["servers"]) == 1

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.mcp, pytest.mark.fast]

