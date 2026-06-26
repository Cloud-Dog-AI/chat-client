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

"""W28A-727-R5 — chat-client flat WebUI login: admin / read-write / read-only.

Covers the three flat roles resolved via the ONE shared cloud_dog_idam guard
(no fork), the constant-time credential check, the role-reflecting /auth/me, and
the read-only write-gate (403-inline on POST/PUT/PATCH/DELETE to data paths).
"""


from __future__ import annotations
import pytest

from fastapi.testclient import TestClient

from cloud_dog_chat_client.servers import web_server as web_server_module
from cloud_dog_chat_client.servers import web_flat_roles as flat


# --------------------------------------------------------------------------- #
# Pure flat-role catalog (shared-guard derived, no fork)
# --------------------------------------------------------------------------- #
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")
def test_flat_roles_are_exactly_three():
    assert flat.FLAT_ROLES == ("admin", "read-write", "read-only")
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_admin_is_wildcard():
    assert flat.permissions_for_role("admin") == ["*"]
    assert flat.role_is_admin("admin") is True
    assert flat.role_can_write("admin") is True
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_read_write_has_baseline_plus_chat_use_perms():
    perms = set(flat.permissions_for_role("read-write"))
    # shared user baseline + chat use-permissions; never the wildcard.
    assert "*" not in perms
    assert "chat:message:send" in perms
    assert flat.role_can_write("read-write") is True
    assert flat.role_is_admin("read-write") is False
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_read_only_is_view_only_baseline():
    perms = set(flat.permissions_for_role("read-only"))
    assert "*" not in perms
    assert "chat:message:send" not in perms
    assert flat.role_can_write("read-only") is False
    assert flat.role_is_admin("read-only") is False
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_normalise_is_fail_closed():
    assert flat.normalise_flat_role("viewer") == "read-only"
    assert flat.normalise_flat_role("user") == "read-write"
    assert flat.normalise_flat_role("owner") == "admin"
    assert flat.normalise_flat_role("totally-unknown") == "read-only"
    assert flat.normalise_flat_role(None) == "read-only"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_write_gate_path_classification():
    # Data/mutation surfaces are gated.
    assert flat.is_write_gated_data_path("/api/v1/users") is True
    assert flat.is_write_gated_data_path("/sessions/abc/messages") is True
    assert flat.is_write_gated_data_path("/webmcp") is True
    # Auth, health, and login bootstrap are never gated.
    assert flat.is_write_gated_data_path("/auth/login") is False
    assert flat.is_write_gated_data_path("/auth/logout") is False
    assert flat.is_write_gated_data_path("/login/session") is False
    assert flat.is_write_gated_data_path("/health") is False


# --------------------------------------------------------------------------- #
# Web login flow — three accounts, constant-time, role-reflecting /auth/me
# --------------------------------------------------------------------------- #
def _client(monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "user-api-key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY", "admin-api-key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    monkeypatch.setenv("CLOUD_DOG__WEB_SERVER__SECURE_COOKIES", "false")
    return TestClient(web_server_module.create_app())


def _login(client, username: str, password: str):
    return client.post("/auth/login", json={"username": username, "password": password})
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_admin_login_reflects_admin_role(env_file, monkeypatch):
    with _client(monkeypatch) as client:
        resp = _login(client, "admin", "OrangeRiverTable")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user"]["roles"] == ["admin"]
        assert body["user"]["permissions"] == ["*"]
        # admin forwards the admin API key for full API access.
        assert resp.cookies.get("chat_client_api_key") == "admin-api-key"
        me = client.get("/auth/me")
        assert me.json()["user"]["roles"] == ["admin"]
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_read_write_login_reflects_read_write_role(env_file, monkeypatch):
    with _client(monkeypatch) as client:
        resp = _login(client, "read-write", "BlueRiverChair")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user"]["roles"] == ["read-write"]
        assert "*" not in body["user"]["permissions"]
        assert "chat:message:send" in body["user"]["permissions"]
        # read-write forwards the user API key (so API admin-config gate 403s it).
        assert resp.cookies.get("chat_client_api_key") == "user-api-key"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_read_only_login_reflects_read_only_role(env_file, monkeypatch):
    with _client(monkeypatch) as client:
        resp = _login(client, "read-only", "GreenRiverDesk")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user"]["roles"] == ["read-only"]
        assert "chat:message:send" not in body["user"]["permissions"]
        assert resp.cookies.get("chat_client_api_key") == "user-api-key"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_invalid_credentials_rejected(env_file, monkeypatch):
    with _client(monkeypatch) as client:
        assert _login(client, "admin", "wrong").status_code == 401
        assert _login(client, "nobody", "nope").status_code == 401
        assert _login(client, "read-only", "BlueRiverChair").status_code == 401


# --------------------------------------------------------------------------- #
# Read-only write-gate — 403-inline on data writes; reads + auth never gated
# --------------------------------------------------------------------------- #
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")
def test_read_only_write_is_403_inline(env_file, monkeypatch):
    with _client(monkeypatch) as client:
        assert _login(client, "read-only", "GreenRiverDesk").status_code == 200
        resp = client.post("/api/v1/users", json={"id": "x"})
        assert resp.status_code == 403
        body = resp.json()
        assert body["role"] == "read-only"
        assert "read-only" in body["detail"]
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_read_only_get_is_not_gated(env_file, monkeypatch):
    # A GET is a read — it must NOT hit the read-only write-gate (it may fail for
    # other upstream reasons, but never with the read-only 403 message).
    with _client(monkeypatch) as client:
        assert _login(client, "read-only", "GreenRiverDesk").status_code == 200
        resp = client.get("/api/v1/health")
        if resp.status_code == 403:
            assert resp.json().get("role") != "read-only"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_read_write_write_is_not_gated_by_readonly_rule(env_file, monkeypatch):
    # read-write may write — the read-only 403-inline must NOT fire for it.
    with _client(monkeypatch) as client:
        assert _login(client, "read-write", "BlueRiverChair").status_code == 200
        resp = client.post("/api/v1/users", json={"id": "x"})
        if resp.status_code == 403:
            assert resp.json().get("role") != "read-only"


# --------------------------------------------------------------------------- #
# W28A-727-R4 — the single write-seam guard must cover EVERY write prefix, not
# just /webapi/*. These assert the exact live-deployed regression: read-only
# POST to BOTH /api/* AND /webapi/* (and the /webapi/conversations data path)
# resolves to 403-inline, while an anonymous write is 401. The /api/sessions
# row is the one that previously slipped through (gate keyed off /webapi only).
# --------------------------------------------------------------------------- #
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")
def test_read_only_write_gate_covers_api_and_webapi(env_file, monkeypatch):
    with _client(monkeypatch) as client:
        assert _login(client, "read-only", "GreenRiverDesk").status_code == 200
        # Every write path a read-only session can reach must be 403-inline.
        for endpoint in ("/webapi/sessions", "/api/sessions", "/webapi/conversations"):
            resp = client.post(endpoint, json={})
            assert resp.status_code == 403, f"{endpoint} -> {resp.status_code} (expected 403): {resp.text}"
            body = resp.json()
            assert body["role"] == "read-only", f"{endpoint}: {body}"
            assert "read-only" in body["detail"], f"{endpoint}: {body}"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_read_only_write_gate_all_methods_on_api_sessions(env_file, monkeypatch):
    # The guard is method-keyed (POST/PUT/PATCH/DELETE), not verb-specific to one
    # route — prove every mutation method on the /api/* seam is gated.
    with _client(monkeypatch) as client:
        assert _login(client, "read-only", "GreenRiverDesk").status_code == 200
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            resp = client.request(method, "/api/sessions", json={})
            assert resp.status_code == 403, f"{method} /api/sessions -> {resp.status_code}: {resp.text}"
            assert resp.json().get("role") == "read-only"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_anonymous_write_is_401_not_403(env_file, monkeypatch):
    # No session, no API key: a write must be rejected as unauthenticated (401),
    # never silently proxied and never the read-only 403 (which implies a session).
    with _client(monkeypatch) as client:
        resp = client.post("/webapi/sessions", json={})
        assert resp.status_code == 401, resp.text
