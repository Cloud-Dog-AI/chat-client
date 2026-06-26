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

import httpx
import pytest

from cloud_dog_api_kit.mcp.client_transport import (
    StreamableHTTPConfig,
    StreamableHTTPTransport,
)
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_4_streamable_tools_list_falls_back_to_tool_router():
    calls: list[tuple[str, str]] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "POST" and req.url.path == "/mcp":
            return httpx.Response(404, json={"detail": "Not Found"})
        if req.method == "GET" and req.url.path == "/mcp/tools":
            return httpx.Response(200, json={"ok": True, "data": [{"name": "mail_search"}]})
        return httpx.Response(500, json={"detail": "unexpected"})

    t = StreamableHTTPTransport(
        StreamableHTTPConfig(
            base_url="http://mcp.local",
            mcp_path="/mcp",
            enable_sse=False,
            timeout_seconds=1.0,
            verify_tls=False,
        )
    )
    t._client = httpx.AsyncClient(
        base_url="http://mcp.local", transport=httpx.MockTransport(_handler)
    )

    try:
        result = await t.tools_list()
    finally:
        await t._client.aclose()
        t._client = None

    assert result == {"tools": [{"name": "mail_search"}]}
    assert calls == [("POST", "/mcp"), ("GET", "/mcp/tools")]
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_4_streamable_tools_call_falls_back_to_tool_router():
    calls: list[tuple[str, str]] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "POST" and req.url.path == "/mcp":
            return httpx.Response(404, json={"detail": "Not Found"})
        if req.method == "POST" and req.url.path == "/mcp/tools/mail_search":
            body = json.loads(req.content.decode("utf-8"))
            assert body == {"query": "ALL"}
            return httpx.Response(200, json={"ok": True, "data": {"messages": [{"uid": "101"}]}})
        return httpx.Response(500, json={"detail": "unexpected"})

    t = StreamableHTTPTransport(
        StreamableHTTPConfig(
            base_url="http://mcp.local",
            mcp_path="/mcp",
            enable_sse=False,
            timeout_seconds=1.0,
            verify_tls=False,
        )
    )
    t._client = httpx.AsyncClient(
        base_url="http://mcp.local", transport=httpx.MockTransport(_handler)
    )

    try:
        result = await t.tools_call("mail_search", {"query": "ALL"})
    finally:
        await t._client.aclose()
        t._client = None

    assert result.get("isError") is False
    content = result.get("content") or []
    assert isinstance(content, list) and content
    payload = json.loads(content[0].get("text") or "{}")
    assert payload.get("messages") == [{"uid": "101"}]
    assert calls == [("POST", "/mcp"), ("POST", "/mcp/tools/mail_search")]
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_4_streamable_tools_call_falls_back_to_api_v1_route():
    calls: list[tuple[str, str]] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "POST" and req.url.path == "/mcp":
            return httpx.Response(404, json={"detail": "Not Found"})
        if req.method == "POST" and req.url.path == "/mcp/tools/admin_collection_create":
            return httpx.Response(404, json={"detail": "Not Found"})
        if req.method == "POST" and req.url.path == "/api/v1/tools/admin_collection_create":
            return httpx.Response(200, json={"ok": True, "result": {"status": "ok"}})
        return httpx.Response(500, json={"detail": "unexpected"})

    t = StreamableHTTPTransport(
        StreamableHTTPConfig(
            base_url="http://mcp.local",
            mcp_path="/mcp",
            enable_sse=False,
            timeout_seconds=1.0,
            verify_tls=False,
        )
    )
    t._client = httpx.AsyncClient(
        base_url="http://mcp.local", transport=httpx.MockTransport(_handler)
    )

    try:
        result = await t.tools_call(
            "admin_collection_create", {"profile": "default", "collection": "c1"}
        )
    finally:
        await t._client.aclose()
        t._client = None

    assert result.get("isError") is False
    payload = json.loads((result.get("content") or [{}])[0].get("text") or "{}")
    assert payload.get("status") == "ok"
    assert calls == [
        ("POST", "/mcp"),
        ("POST", "/mcp/tools/admin_collection_create"),
        ("POST", "/api/v1/tools/admin_collection_create"),
    ]
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_4_streamable_tools_call_falls_back_on_jsonrpc_method_not_found():
    calls: list[tuple[str, str]] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "POST" and req.url.path == "/mcp":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "Method not found: tools/call"},
                },
            )
        if req.method == "POST" and req.url.path == "/mcp/tools/git_status":
            return httpx.Response(404, json={"detail": "Not Found"})
        if req.method == "POST" and req.url.path == "/api/v1/tools/git_status":
            return httpx.Response(200, json={"ok": True, "result": {"state": "clean"}})
        return httpx.Response(500, json={"detail": "unexpected"})

    t = StreamableHTTPTransport(
        StreamableHTTPConfig(
            base_url="http://mcp.local",
            mcp_path="/mcp",
            enable_sse=False,
            timeout_seconds=1.0,
            verify_tls=False,
        )
    )
    t._client = httpx.AsyncClient(
        base_url="http://mcp.local", transport=httpx.MockTransport(_handler)
    )

    try:
        result = await t.tools_call("git_status", {"workspace_id": "w1"})
    finally:
        await t._client.aclose()
        t._client = None

    assert result.get("isError") is False
    payload = json.loads((result.get("content") or [{}])[0].get("text") or "{}")
    assert payload.get("state") == "clean"
    assert calls == [
        ("POST", "/mcp"),
        ("POST", "/mcp/tools/git_status"),
        ("POST", "/api/v1/tools/git_status"),
    ]
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_4_streamable_tools_call_continues_after_mcp_tool_router_500():
    calls: list[tuple[str, str]] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "POST" and req.url.path == "/mcp":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "Method not found: tools/call"},
                },
            )
        if req.method == "POST" and req.url.path == "/mcp/tools/git_status":
            return httpx.Response(500, json={"detail": "wrapper failed"})
        if req.method == "POST" and req.url.path == "/api/v1/tools/git_status":
            return httpx.Response(200, json={"ok": True, "result": {"state": "clean"}})
        return httpx.Response(500, json={"detail": "unexpected"})

    t = StreamableHTTPTransport(
        StreamableHTTPConfig(
            base_url="http://mcp.local",
            mcp_path="/mcp",
            enable_sse=False,
            timeout_seconds=1.0,
            verify_tls=False,
        )
    )
    t._client = httpx.AsyncClient(
        base_url="http://mcp.local", transport=httpx.MockTransport(_handler)
    )

    try:
        result = await t.tools_call("git_status", {"workspace_id": "w1"})
    finally:
        await t._client.aclose()
        t._client = None

    assert result.get("isError") is False
    payload = json.loads((result.get("content") or [{}])[0].get("text") or "{}")
    assert payload.get("state") == "clean"
    assert calls == [
        ("POST", "/mcp"),
        ("POST", "/mcp/tools/git_status"),
        ("POST", "/api/v1/tools/git_status"),
    ]
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_4_streamable_tools_call_normalises_direct_json_payload():
    calls: list[tuple[str, str]] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "POST" and req.url.path == "/mcp":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"bytes_written": 7, "path": "/tmp/demo.txt"}},
            )
        return httpx.Response(500, json={"detail": "unexpected"})

    t = StreamableHTTPTransport(
        StreamableHTTPConfig(
            base_url="http://mcp.local",
            mcp_path="/mcp",
            enable_sse=False,
            timeout_seconds=1.0,
            verify_tls=False,
        )
    )
    t._client = httpx.AsyncClient(
        base_url="http://mcp.local", transport=httpx.MockTransport(_handler)
    )

    try:
        result = await t.tools_call("b64_decode_to_file", {"path": "/tmp/demo.txt", "data": "ZGF0YQ=="})
    finally:
        await t._client.aclose()
        t._client = None

    assert result.get("isError") is False
    payload = json.loads((result.get("content") or [{}])[0].get("text") or "{}")
    assert payload == {"bytes_written": 7, "path": "/tmp/demo.txt"}
    assert calls == [("POST", "/mcp")]
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_ut1_4_streamable_parse_inline_sse_ignores_non_text_payload():
    t = StreamableHTTPTransport(
        StreamableHTTPConfig(
            base_url="http://mcp.local",
            mcp_path="/mcp",
            enable_sse=False,
            timeout_seconds=1.0,
            verify_tls=False,
        )
    )

    assert t._parse_inline_sse({"jsonrpc": "2.0"}) == []
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_4_streamable_tools_list_falls_back_to_api_v1_route():
    calls: list[tuple[str, str]] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "POST" and req.url.path == "/mcp":
            return httpx.Response(404, json={"detail": "Not Found"})
        if req.method == "GET" and req.url.path == "/mcp/tools":
            return httpx.Response(404, json={"detail": "Not Found"})
        if req.method == "GET" and req.url.path == "/api/v1/tools":
            return httpx.Response(200, json=[{"name": "search"}])
        return httpx.Response(500, json={"detail": "unexpected"})

    t = StreamableHTTPTransport(
        StreamableHTTPConfig(
            base_url="http://mcp.local",
            mcp_path="/mcp",
            enable_sse=False,
            timeout_seconds=1.0,
            verify_tls=False,
        )
    )
    t._client = httpx.AsyncClient(
        base_url="http://mcp.local", transport=httpx.MockTransport(_handler)
    )

    try:
        result = await t.tools_list()
    finally:
        await t._client.aclose()
        t._client = None

    assert result == {"tools": [{"name": "search"}]}
    assert calls == [
        ("POST", "/mcp"),
        ("GET", "/mcp/tools"),
        ("GET", "/api/v1/tools"),
    ]
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_4_streamable_initialize_ignores_unsupported_endpoint():
    calls: list[tuple[str, str]] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "POST" and req.url.path == "/mcp":
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(500, json={"detail": "unexpected"})

    t = StreamableHTTPTransport(
        StreamableHTTPConfig(
            base_url="http://mcp.local",
            mcp_path="/mcp",
            enable_sse=False,
            timeout_seconds=1.0,
            verify_tls=False,
        )
    )
    t._client = httpx.AsyncClient(
        base_url="http://mcp.local", transport=httpx.MockTransport(_handler)
    )

    try:
        await t.initialize(protocol_version="2024-11-05")
    finally:
        await t._client.aclose()
        t._client = None

    assert calls == [("POST", "/mcp")]
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_4_streamable_base_url_with_mcp_path_does_not_double_prefix():
    calls: list[tuple[str, str]] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "POST" and req.url.path == "/mcp":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
            )
        return httpx.Response(500, json={"detail": "unexpected"})

    t = StreamableHTTPTransport(
        StreamableHTTPConfig(
            base_url="https://mcp.local/mcp",
            mcp_path="/mcp",
            enable_sse=False,
            timeout_seconds=1.0,
            verify_tls=False,
        )
    )
    t._client = httpx.AsyncClient(
        base_url=t.cfg.base_url, transport=httpx.MockTransport(_handler)
    )

    try:
        result = await t.tools_list()
    finally:
        await t._client.aclose()
        t._client = None

    assert result == {"tools": []}
    assert calls == [("POST", "/mcp")]

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.smtp, pytest.mark.mcp, pytest.mark.fast]
