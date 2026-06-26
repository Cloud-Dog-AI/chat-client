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

"""
UT1.45 — X-Admin-Key dual-key contract documentation guard.

W28C-1703 / CC9. Admin-scope operations are defence-in-depth: they require the
``X-API-Key`` user credential AND the ``X-Admin-Key`` admin-scope header. A caller
presenting only one header previously got the misleading ``401 Missing
X-API-Key``; it now returns a 401 whose message names BOTH headers. The OpenAPI
schema documents the two security schemes and marks admin mutations as requiring
both, while user-scope reads require only the user key.

Related Tasks: W28C-1703 (CC9)

Recent Changes:
- 2026-06-10: W28C-1703 — initial dual-key contract documentation guard.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cloud_dog_chat_client.api.server import create_app
from cloud_dog_chat_client.config import ConfigManager

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.fast]


def _client(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "ut145-key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY", "ut145-admin")
    cfg = ConfigManager(env_file=env_file)
    base = str(cfg.get("api_server.base_path") or "/v1")
    return base, TestClient(create_app(cfg), raise_server_exceptions=False)
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_cc9_admin_single_header_error_names_both(env_file, monkeypatch):
    base, client = _client(env_file, monkeypatch)
    with client:
        resp = client.post(
            f"{base}/users", headers={"X-Admin-Key": "ut145-admin"}, json={"username": "x"}
        )
        assert resp.status_code == 401, resp.text[:200]
        body = resp.json()
        assert body.get("ok") is False
        error = (body.get("errors") or [{}])[0]
        assert error.get("code") == "UNAUTHENTICATED"
        message = error.get("message", "")
        assert "X-API-Key" in message and "X-Admin-Key" in message, (
            f"the admin error must name BOTH headers, not just one: {message!r}"
        )
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_cc9_openapi_documents_dual_key(env_file, monkeypatch):
    base, client = _client(env_file, monkeypatch)
    with client:
        schema = client.get("/openapi.json").json()
        schemes = (schema.get("components") or {}).get("securitySchemes") or {}
        assert "ApiKeyAuth" in schemes and "AdminKeyAuth" in schemes
        assert schemes["ApiKeyAuth"]["name"] == "X-API-Key"
        assert schemes["AdminKeyAuth"]["name"] == "X-Admin-Key"

        users = (schema.get("paths") or {}).get(f"{base}/users") or {}
        post_security = (users.get("post") or {}).get("security") or []
        get_security = (users.get("get") or {}).get("security") or []
        assert {"ApiKeyAuth": [], "AdminKeyAuth": []} in post_security, (
            f"admin mutation must require both keys: {post_security}"
        )
        assert {"ApiKeyAuth": []} in get_security, (
            f"user-scope read requires only the user key: {get_security}"
        )
