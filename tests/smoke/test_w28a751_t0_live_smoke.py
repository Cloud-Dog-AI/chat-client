from __future__ import annotations

from tests.helpers.w28a751_live import request
import pytest
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("FR-010")
@pytest.mark.req("FR-015")
@pytest.mark.req("NF-001")


def test_t0_live_health_login_runtime_and_public_routes():
    health = request("GET", "/health")
    assert health.status == 200
    assert "ok" in health.body.lower()

    login = request("GET", "/login")
    assert login.status == 200
    assert "/runtime-config.js" in login.body

    runtime = request("GET", "/runtime-config.js")
    assert runtime.status == 200
    assert '"AUTH_MODE": "cookie"' in runtime.body
    assert "API_KEY_HEADER" not in runtime.body

    for path in ("/", "/chat", "/sessions", "/profiles", "/mcp-servers", "/api-docs", "/mcp-console", "/a2a-console"):
        resp = request("GET", path)
        assert resp.status in {200, 302, 401}, f"{path} returned {resp.status}: {resp.body[:200]}"

    assert request("GET", "/webmcp/health").status == 200
    assert request("GET", "/weba2a/health").status == 200
