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

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pathlib import Path

import cloud_dog_chat_client.api.routes as routes_module
from cloud_dog_chat_client.api.routes import (
    CreateSessionRequest,
    SendMessageRequest,
    SessionPreferencesRequest,
    _build_tool_arguments_from_schema,
    _build_prompt_assist_tool_call,
    _derive_direct_prompt_assist_output,
    _is_file_workspace_prompt,
    _is_table_listing_request,
    _authoritative_expert_service_digest,
    _expert_denied_authoritative_service_results,
    build_router,
)
from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.llm.protocols import ChatCompletionResult
from cloud_dog_chat_client.llm.response_policy import ResponsePolicy
from cloud_dog_chat_client.session import SessionManager
from cloud_dog_chat_client.servers import web_server as web_server_module


def _route_endpoint(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"endpoint not found: {method} {path}")
@pytest.mark.UT
@pytest.mark.webui
@pytest.mark.req("FR-001")


def test_ut1_6_api_key_login_session_creates_cookie_auth_session(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "viewer-api-key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY", "admin-api-key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    monkeypatch.setenv("CLOUD_DOG__WEB_SERVER__SECURE_COOKIES", "false")

    app = web_server_module.create_app()
    with TestClient(app) as client:
        invalid = client.post("/login/session", json={"api_key": "wrong-api-key"})
        assert invalid.status_code == 401

        login = client.post("/login/session", json={"api_key": "admin-api-key"})
        assert login.status_code == 200
        assert login.cookies.get("chat_web_session")
        assert login.cookies.get("chat_client_api_key") == "admin-api-key"
        body = login.json()
        assert body["user"]["roles"] == ["admin"]
        assert body["user"]["permissions"] == ["*"]

        me = client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["roles"] == ["admin"]
@pytest.mark.UT
@pytest.mark.webui
@pytest.mark.req("FR-001")


def test_ut1_6_web_server_serves_spa_client_routes(env_file):
    app = web_server_module.create_app()
    with TestClient(app) as client:
        for route in ("/profiles", "/api-docs", "/admin/rbac"):
            response = client.get(route)
            assert response.status_code == 200
            assert "<div id=\"root\"></div>" in response.text
            assert "/runtime-config.js" in response.text


# Covers: R16.1, R16.2, R16.3, R16.4, R16.5, R16.6, R16.7
@pytest.mark.UT
@pytest.mark.webui
@pytest.mark.req("FR-001")
@pytest.mark.asyncio
async def test_ut1_6_web_ui_endpoints_and_session_mcp_preferences(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__NAME", "sql-agent-mcp")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__TRANSPORT", "http_jsonrpc")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__BASE_URL", "http://sql-agent-mcp:8081")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__1__NAME", "search-mcp")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__1__TRANSPORT", "streamable_http")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__1__BASE_URL", "http://search-mcp:8000")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__1__TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__2__NAME", "file-mcp")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__2__TRANSPORT", "streamable_http")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__2__BASE_URL", "http://file-mcp:8000")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__2__TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "super-secret-dev-key")
    monkeypatch.setenv("CLOUD_DOG__LLM__API_KEY", "llm-secret-key")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__AUTHORIZATION_TOKEN", "token-secret")

    cfg = ConfigManager(env_file=env_file)
    sessions = SessionManager("./logs")
    router = build_router(config=cfg, sessions=sessions)

    root_redirect = _route_endpoint(router, "/", "GET")
    access_redirect = _route_endpoint(router, "/access", "GET")
    web_ui = _route_endpoint(router, "/ui", "GET")
    files_ui = _route_endpoint(router, "/files", "GET")
    ui_config = _route_endpoint(router, "/ui/config", "GET")
    ui_config_tree = _route_endpoint(router, "/ui/config/tree", "GET")
    ui_logs = _route_endpoint(router, "/ui/logs", "GET")
    mcp_servers_health = _route_endpoint(router, "/mcp/servers/health", "GET")
    create_session = _route_endpoint(router, "/sessions", "POST")
    delete_session = _route_endpoint(router, "/sessions/{session_id}", "DELETE")
    get_preferences = _route_endpoint(router, "/sessions/{session_id}/preferences", "GET")
    put_preferences = _route_endpoint(router, "/sessions/{session_id}/preferences", "PUT")

    root_resp = await root_redirect()
    assert root_resp.status_code == 307
    assert root_resp.headers.get("location") == "/ui"

    access_resp = await access_redirect()
    assert access_resp.status_code == 307
    assert access_resp.headers.get("location") == "/ui"

    html_resp = await web_ui()
    html = html_resp.body.decode("utf-8")
    assert "<div id=\"root\"></div>" in html
    assert "<title>cloud-dog" in html
    assert "/runtime-config.js" in html
    assert "/assets/index-" in html
    # W28A-727-R5 (corruption reopen): the login/SPA shell must be the clean built
    # index with NO server-injected 'Cloud Dog demo inventory' panel and NO extra
    # same-origin /v1/* probes before the SPA root.
    assert "cloud-dog-demo" not in html
    assert "Cloud Dog demo inventory" not in html
    assert "/v1/profiles" not in html

    files_resp = await files_ui()
    assert files_resp.status_code == 200
    files_html = files_resp.body.decode("utf-8")
    assert "<div id=\"root\"></div>" in files_html
    assert "cloud-dog-demo" not in files_html

    runtime_config = _route_endpoint(router, "/runtime-config.js", "GET")
    request = type(
        "_Req",
        (),
        {"base_url": "http://testserver/", "url": type("_Url", (), {"path": "/runtime-config.js"})()},
    )()
    runtime_resp = await runtime_config(request)
    runtime_js = runtime_resp.body.decode("utf-8")
    assert "window.__RUNTIME_CONFIG__" in runtime_js
    assert '"API_BASE_URL": __origin,' in runtime_js
    assert '"AUTH_MODE": "cookie"' in runtime_js
    # W28A-727-R5 (corruption reopen): cookie auth mode must NOT advertise an
    # API-key header — that contradicted AUTH_MODE "cookie" and was login-surface
    # noise. The SPA defaults the header internally for the optional admin path.
    assert "API_KEY_HEADER" not in runtime_js

    cfg_data = await ui_config()
    assert cfg_data["application"]["name"]
    assert cfg_data["application"]["release"]
    assert isinstance(cfg_data["llm"], dict)
    assert cfg_data["llm"]["provider"]
    assert "temperature" in cfg_data["llm"]
    assert "top_k" in cfg_data["llm"]
    assert "num_ctx" in cfg_data["llm"]
    assert "max_tokens" in cfg_data["llm"]
    assert int(cfg_data["client_api"]["ui_wait_timeout_seconds"]) >= 30
    assert int(cfg_data["a2a"]["port"]) >= 1
    assert cfg_data["a2a"]["ws_path"] == "/a2a/ws"
    assert cfg_data["test_harness"]["enabled"] is True
    assert len(cfg_data["mcp_servers"]) >= 3
    assert cfg_data["mcp_servers"][0]["name"] == "sql-agent-mcp"

    cfg_tree = await ui_config_tree()
    assert cfg_tree["application"]["name"]
    full_cfg = cfg_tree["config"]
    assert full_cfg["client_api"]["api_key"] == "***REDACTED***"
    assert full_cfg["llm"]["api_key"] == "***REDACTED***"
    assert full_cfg["mcp"]["servers"][0]["authorization_token"] == "***REDACTED***"
    assert full_cfg["client_api"]["api_key_header"]

    log_dir = Path(cfg.get("app.logfolder") or "./logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "audit.log.jsonl").write_text(
        '{"timestamp":"2026-04-05T17:00:00.000Z","event_type":"identity.user.create","action":"create","outcome":"success","severity":"INFO","trace_id":"trace-audit","request_id":"request-audit","service":"chat-client-api","service_instance":"chat-client-local","environment":"test","actor":{"type":"user","id":"admin","ip":"127.0.0.1","roles":["admin"],"user_agent":"pytest"},"target":{"type":"user","id":"user-1","name":"User One"},"details":{"source":"unit"}}\n',
        encoding="utf-8",
    )
    (log_dir / "api_server.log").write_text(
        '{"timestamp":"2026-04-05T17:01:00.000Z","level":"INFO","message":"user list viewed","correlation_id":"trace-api","service":"chat-client-api","service_instance":"chat-client-local","environment":"test","extra":{"method":"GET","path":"/v1/users","request_id":"request-api","status_code":200,"user":"admin"}}\n',
        encoding="utf-8",
    )

    audit_rows = await ui_logs(surface="audit", limit=10)
    assert audit_rows["count"] == 1
    assert audit_rows["entries"][0]["actor"]["id"] == "admin"
    assert audit_rows["entries"][0]["target"]["id"] == "user-1"
    assert audit_rows["entries"][0]["trace_id"] == "trace-audit"

    api_rows = await ui_logs(surface="api", limit=10)
    assert api_rows["count"] == 1
    assert api_rows["entries"][0]["action"] == "GET"
    assert api_rows["entries"][0]["target"]["id"] == "/v1/users"
    assert api_rows["entries"][0]["outcome"] == "success"
    assert any(item["id"] == "audit" for item in api_rows["available_surfaces"])

    health = await mcp_servers_health()
    assert isinstance(health.get("servers"), list)
    assert len(health.get("servers") or []) >= 3
    for entry in health.get("servers") or []:
        assert "index" in entry
        assert "ok" in entry

    create_resp = await create_session(CreateSessionRequest(metadata={}))
    session_id = create_resp.session_id
    session = sessions.get_session(session_id)
    assert (session.get("metadata") or {}).get("title") == "New Session"

    pref_get = await get_preferences(session_id)
    assert pref_get.selected_mcp_server_indices == []

    pref_put = await put_preferences(
        session_id,
        SessionPreferencesRequest(selected_mcp_server_indices=[2, 1, 99, -1, 1]),
    )
    assert pref_put.selected_mcp_server_indices == [2, 1]

    pref_get_2 = await get_preferences(session_id)
    assert pref_get_2.selected_mcp_server_indices == [2, 1]

    send_message = _route_endpoint(router, "/sessions/{session_id}/messages", "POST")

    class _FakeLLMService:
        def __init__(self, _cfg, **_kwargs):
            self.response_policy = ResponsePolicy(
                enforce=False,
                envelope_tag="",
                format="",
                marker_key="",
                marker_value="",
                answer_key="",
                strip_for_user=False,
                show_thinking=False,
                display_answer_tag="",
                allow_header_only=False,
                retry_attempts=0,
                retry_backoff_seconds=0.0,
            )

        async def complete(self, _messages):
            return ChatCompletionResult(content="unit test assistant response", raw={})

    monkeypatch.setattr(routes_module, "LLMService", _FakeLLMService)
    send_resp = await send_message(
        session_id,
        SendMessageRequest(content="Compare France and Germany for wellbeing in one sentence.", stream=False),
    )
    assert send_resp.session_id == session_id
    session_after = sessions.get_session(session_id)
    meta_after = session_after.get("metadata") or {}
    assert bool(meta_after.get("title_generated")) is True
    assert "Compare France and Germany" in str(meta_after.get("title") or "")
    assert "file-mcp+search-mcp" in str(meta_after.get("title") or "")

    deleted = await delete_session(session_id)
    assert deleted["deleted"] is True
    assert deleted["session_id"] == session_id

    with pytest.raises(HTTPException) as err:
        await delete_session(session_id)
    assert err.value.status_code == 404
@pytest.mark.UT
@pytest.mark.webui
@pytest.mark.req("FR-001")


@pytest.mark.asyncio
async def test_ut1_6_send_message_defaults_to_non_stream_endpoint(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "dev-key")
    monkeypatch.setenv("CLOUD_DOG__LLM__STREAM", "true")

    cfg = ConfigManager(env_file=env_file)
    sessions = SessionManager("./logs")
    router = build_router(config=cfg, sessions=sessions)
    send_message = _route_endpoint(router, "/sessions/{session_id}/messages", "POST")

    class _FakeLLMService:
        def __init__(self, _cfg, **_kwargs):
            self.response_policy = ResponsePolicy(
                enforce=False,
                envelope_tag="",
                format="",
                marker_key="",
                marker_value="",
                answer_key="",
                strip_for_user=False,
                show_thinking=False,
                display_answer_tag="",
                allow_header_only=False,
                retry_attempts=0,
                retry_backoff_seconds=0.0,
            )

        async def complete(self, _messages):
            return ChatCompletionResult(content="plain route response", raw={})

    monkeypatch.setattr(routes_module, "LLMService", _FakeLLMService)

    session_id = sessions.create_session(metadata={})
    response = await send_message(session_id, SendMessageRequest(content="hello"))

    assert response.session_id == session_id
    assert response.content == "plain route response"


# Covers: R16.6
@pytest.mark.UT
@pytest.mark.webui
@pytest.mark.req("FR-001")
@pytest.mark.asyncio
async def test_ut1_6_mcp_health_streamable_notify_session_warning(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__NAME", "file-mcp")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__TRANSPORT", "streamable_http")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__BASE_URL", "http://file-mcp:8000")
    monkeypatch.setenv("CLOUD_DOG__MCP__API__REQUIRE_INITIALIZE", "true")
    monkeypatch.setenv("CLOUD_DOG__MCP__DEFAULTS__PROTOCOL_VERSION", "2024-11-05")

    cfg = ConfigManager(env_file=env_file)
    sessions = SessionManager("./logs")
    router = build_router(config=cfg, sessions=sessions)
    mcp_servers_health = _route_endpoint(router, "/mcp/servers/health", "GET")

    class _FakeTransport:
        async def initialize(self, protocol_version=None):
            raise RuntimeError("Streamable HTTP notifications require an established session")

        async def tools_list(self):
            return {"tools": [{"name": "search"}]}

    class _FakeConn:
        def __init__(self):
            self.transport = _FakeTransport()

        async def connect(self):
            return None

        async def close(self):
            return None

    from cloud_dog_chat_client import mcp as mcp_module

    monkeypatch.setattr(mcp_module.MCPConnection, "from_config", lambda *args, **kwargs: _FakeConn())

    health = await mcp_servers_health()
    servers = health.get("servers") or []
    assert len(servers) >= 1
    first = servers[0]
    assert first.get("ok") is True
    assert int(first.get("tool_count") or 0) == 1
    assert "Streamable HTTP notifications require an established session" in str(first.get("warning") or "")
@pytest.mark.UT
@pytest.mark.webui
@pytest.mark.req("FR-001")


@pytest.mark.asyncio
async def test_ut1_6_streaming_route_includes_mcp_context(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__MCP__CHAT_ASSIST__ENABLED", "true")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__NAME", "sqlagent-mcp")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__TRANSPORT", "http_jsonrpc")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__BASE_URL", "https://sqlagentmcp.example.com")
    cfg = ConfigManager(env_file=env_file)
    sessions = SessionManager("./logs")
    router = build_router(config=cfg, sessions=sessions)
    create_session = _route_endpoint(router, "/sessions", "POST")
    send_message_stream = _route_endpoint(
        router, "/sessions/{session_id}/messages/stream", "POST"
    )

    class _FakeChunk:
        def __init__(self, content_delta: str):
            self.content_delta = content_delta

    captured_messages = []

    class _FakeLLMService:
        def __init__(self, _cfg, **_kwargs):
            self.response_policy = ResponsePolicy(
                enforce=False,
                envelope_tag="",
                format="",
                marker_key="",
                marker_value="",
                answer_key="",
                strip_for_user=False,
                show_thinking=False,
                display_answer_tag="",
                allow_header_only=False,
                retry_attempts=0,
                retry_backoff_seconds=0.0,
            )

        async def stream(self, messages):
            captured_messages.extend(messages)
            yield _FakeChunk("tables: access_logs, api_keys")

    monkeypatch.setattr(routes_module, "LLMService", _FakeLLMService)

    class _FakeTransport:
        async def tools_list(self):
            return {
                "tools": [
                    {
                        "name": "list_tables",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }

        async def tools_call(self, _name, _arguments):
            return {
                "isError": False,
                "content": [
                    {
                        "type": "text",
                        "text": "access_logs\napi_keys\nusers\ngroups",
                    }
                ],
            }

    class _FakeConn:
        def __init__(self):
            self.transport = _FakeTransport()

        async def connect(self):
            return None

        async def close(self):
            return None

    from cloud_dog_chat_client import mcp as mcp_module

    monkeypatch.setattr(
        mcp_module.MCPConnection,
        "from_config",
        lambda *args, **kwargs: _FakeConn(),
    )

    create_resp = await create_session(CreateSessionRequest(metadata={"suite": "ut1.6.stream"}))
    session_id = create_resp.session_id
    sessions.update_session_metadata(
        session_id, {"selected_mcp_server_indices": [0]}
    )

    resp = await send_message_stream(
        session_id,
        SendMessageRequest(
            content="What tables are available in the database?",
            stream=True,
        ),
    )
    chunks = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))

    body = "".join(chunks)
    assert '"type": "delta"' in body
    assert "access_logs" in body
    if captured_messages:
        assert any(getattr(msg, "role", None) == "system" for msg in captured_messages)
        assert any(
            "access_logs"
            in str(getattr(msg, "content", ""))
            for msg in captured_messages
        )
    else:
        assert "Available tables:" in body
@pytest.mark.UT
@pytest.mark.webui
@pytest.mark.req("FR-001")


def test_ut1_6_async_mcp_tool_args_prefer_wait_false():
    tool = {
        "name": "query_database",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "use_history": {"type": "boolean"},
                "wait": {"type": "boolean"},
            },
        },
    }

    args = _build_tool_arguments_from_schema(
        tool,
        "What tables are available in the database?",
        prefer_async_jobs=True,
    )
    assert args == {
        "question": "What tables are available in the database?",
        "use_history": False,
        "wait": False,
    }

    sync_args = _build_tool_arguments_from_schema(
        tool,
        "What tables are available in the database?",
        prefer_async_jobs=False,
    )
    assert sync_args["wait"] is True
@pytest.mark.UT
@pytest.mark.webui
@pytest.mark.req("FR-001")


def test_ut1_6_table_prompt_prefers_list_tables():
    tool_name, tool_args = _build_prompt_assist_tool_call(
        {"query_database": "query_database", "list_tables": "list_tables"},
        "What tables are available in the database?",
    )
    assert tool_name == "list_tables"
    assert tool_args == {}
@pytest.mark.UT
@pytest.mark.webui
@pytest.mark.req("FR-001")


def test_ut1_6_file_workspace_prompt_prefers_list_dir():
    tool_name, tool_args = _build_prompt_assist_tool_call(
        {"list_dir": "list_dir", "search_paths": "search_paths"},
        "Search for files in the workspace",
    )
    assert tool_name == "list_dir"
    assert tool_args == {"path": "working"}
    assert _is_file_workspace_prompt("Search for files in the workspace") is True

    tool_name, tool_args = _build_prompt_assist_tool_call(
        {"list_dir": "list_dir", "search_paths": "search_paths"},
        "Search for files in the workspace",
        browse_path="/workspace",
    )
    assert tool_name == "list_dir"
    assert tool_args == {"path": "/workspace"}
@pytest.mark.UT
@pytest.mark.webui
@pytest.mark.req("FR-001")


def test_ut1_6_direct_prompt_assist_output_prefers_factual_context():
    assert _is_table_listing_request("What tables are available in the database?") is True
    assert _is_table_listing_request("Show tables") is True
    assert _is_table_listing_request("Translate this to Hungarian") is False

    table_output = _derive_direct_prompt_assist_output(
        "What tables are available in the database?",
        "access_logs\nusers\ngroups",
    )
    assert table_output.startswith("Available tables:\n")
    assert "access_logs" in table_output
    assert "users" in table_output

    file_output = _derive_direct_prompt_assist_output(
        "Search for files in the workspace",
        "/path/to/cloud-dog-ai/file-mcp-server/working",
    )
    assert "/path/to/cloud-dog-ai/file-mcp-server/working" in file_output

    assert _derive_direct_prompt_assist_output("Translate this", "ignored") == ""


# Covers: R16.6
@pytest.mark.UT
@pytest.mark.webui
@pytest.mark.req("FR-001")
@pytest.mark.asyncio
async def test_ut1_6_mcp_health_streamable_missing_sse_session_warning(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__NAME", "file-mcp")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__TRANSPORT", "streamable_http")
    monkeypatch.setenv("CLOUD_DOG__MCP__SERVERS__0__BASE_URL", "http://file-mcp:8000")
    monkeypatch.setenv("CLOUD_DOG__MCP__API__REQUIRE_INITIALIZE", "true")
    monkeypatch.setenv("CLOUD_DOG__MCP__DEFAULTS__PROTOCOL_VERSION", "2024-11-05")

    cfg = ConfigManager(env_file=env_file)
    sessions = SessionManager("./logs")
    router = build_router(config=cfg, sessions=sessions)
    mcp_servers_health = _route_endpoint(router, "/mcp/servers/health", "GET")

    class _FakeTransport:
        async def initialize(self, protocol_version=None):
            return None

        async def ensure_sse_stream(self):
            raise RuntimeError("Cannot open SSE stream without session id")

        async def tools_list(self):
            return {"tools": [{"name": "search"}]}

    class _FakeConn:
        def __init__(self):
            self.transport = _FakeTransport()

        async def connect(self):
            return None

        async def close(self):
            return None

    from cloud_dog_chat_client import mcp as mcp_module

    monkeypatch.setattr(mcp_module.MCPConnection, "from_config", lambda *args, **kwargs: _FakeConn())

    health = await mcp_servers_health()
    servers = health.get("servers") or []
    assert len(servers) >= 1
    first = servers[0]
    assert first.get("ok") is True
    assert int(first.get("tool_count") or 0) == 1
    assert "Cannot open SSE stream without session id" in str(first.get("warning") or "")
@pytest.mark.UT
@pytest.mark.webui
@pytest.mark.req("FR-001")


def test_ut1_6_detects_expert_denial_of_authoritative_service_results():
    assert _expert_denied_authoritative_service_results(
        "I don't have access to your local file system.",
        explicit_service_calls=[{"tool_name": "list_dir"}],
        post_service_calls=[],
    ) is True
    assert _expert_denied_authoritative_service_results(
        "alpha.md covers deployment controls; beta.md covers coordination; gamma.md covers cleanup.",
        explicit_service_calls=[{"tool_name": "list_dir"}],
        post_service_calls=[],
    ) is False
@pytest.mark.UT
@pytest.mark.webui
@pytest.mark.req("FR-001")


def test_ut1_6_builds_authoritative_service_digest_for_file_results():
    payload = {
        "services_invoked": [
            {
                "tool_name": "list_dir",
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "alpha.md\nbeta.md\ngamma.md",
                        }
                    ]
                },
            },
            {
                "tool_name": "read_file",
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "# Alpha\nAlpha covers safe deployment controls.",
                        }
                    ]
                },
            },
        ]
    }
    digest = _authoritative_expert_service_digest(
        payload,
        explicit_service_calls=[
            {"tool_name": "list_dir", "arguments": {"path": "/tmp/test-dir"}},
            {"tool_name": "read_file", "arguments": {"path": "/tmp/test-dir/alpha.md"}},
        ],
    )
    lowered = digest.lower()
    assert "directory listing for /tmp/test-dir" in lowered
    assert "alpha.md" in lowered
    assert "file alpha.md contents" in lowered
    assert "safe deployment controls" in lowered
@pytest.mark.UT
@pytest.mark.webui
@pytest.mark.req("FR-001")


@pytest.mark.asyncio
async def test_ut1_6_expert_execute_retries_without_stale_remote_session(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "dev-key")

    cfg = ConfigManager(env_file=env_file)
    sessions = SessionManager("./logs")
    router = build_router(config=cfg, sessions=sessions)
    send_message = _route_endpoint(router, "/sessions/{session_id}/messages", "POST")

    session_id = sessions.create_session(
        metadata={
            "selected_mcp_server_indices": [0],
            "profile_mcp_servers": [
                {
                    "name": "expert-orchestrator",
                    "transport": "http_jsonrpc",
                    "base_url": "http://127.0.0.1:8032",
                    "mcp_path": "/mcp",
                    "health_path": "/mcp/health",
                    "api_key_header": "X-API-Key",
                    "api_key": "local-test-key",
                    "assist_role": "expert_execute",
                    "assist_api_base_url": "http://127.0.0.1:8030",
                    "assist_expert_config_id": 291,
                    "assist_execute_parameters": {
                        "persist_session": True,
                        "max_tokens": 384,
                    },
                    "assist_max_tokens": 384,
                    "assist_history_messages": 8,
                }
            ],
        }
    )

    class _FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code
            self.text = "" if payload is None else str(payload)

        def json(self):
            return self._payload

    attempts = []

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            attempts.append({"url": url, "payload": json})
            input_text = json["input_text"]
            context = json["context"]
            if input_text.startswith("List the markdown files"):
                assert "remote_session_id" not in context
                return _FakeResponse(
                    {
                        "output_text": "alpha beta gamma",
                        "session_id": "remote-1",
                    }
                )
            if context.get("remote_session_id") == "remote-1":
                raise httpx.RemoteProtocolError(
                    "Server disconnected without sending a response."
                )
            assert "remote_session_id" not in context
            return _FakeResponse(
                {
                    "output_text": "combined summary",
                    "session_id": "remote-2",
                }
            )

    monkeypatch.setattr(routes_module.httpx, "AsyncClient", _FakeAsyncClient)

    first = await send_message(
        session_id,
        SendMessageRequest(
            content="List the markdown files in test-dir and tell me what each one is about",
            stream=False,
        ),
    )
    assert "alpha" in first.content
    assert "beta" in first.content
    assert "gamma" in first.content

    second = await send_message(
        session_id,
        SendMessageRequest(
            content="Create a summary document combining all of them",
            stream=False,
        ),
    )
    assert "combined summary" in second.content

    session = sessions.get_session(session_id)
    metadata = session.get("metadata") or {}
    assert metadata.get("assist_remote_sessions") == {"0": "remote-2"}
    assert [a["payload"]["context"].get("remote_session_id") for a in attempts] == [
        None,
        "remote-1",
        None,
    ]
    assert not any(
        event.event_type == "mcp_context_error"
        for event in session.get("events") or []
    )

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.mcp, pytest.mark.fast]
