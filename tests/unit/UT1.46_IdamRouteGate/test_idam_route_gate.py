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
UT1.46 — /idam/* SPA route gate (W28C-1703 / CC6 / 1601-C).

W28A-876 mounted the /api/v1 IDAM routes and the SPA wires /idam/* to the shared
@cloud-dog/idam pages, but the web shell never served the SPA shell for the
/idam/* prefix, so direct navigation fell through to the API proxy (anon -> 401,
authed -> 404). The OPT-B fix adds explicit web routes:

  - anonymous GET /idam/<page> -> 302 -> /auth/login?next=/idam/<page>
  - authenticated GET /idam/<page> -> 200 SPA shell (the SPA's isAdmin guard then
    renders the shared @cloud-dog/idam page or redirects a non-admin home)
  - GET /auth/login (POST-only credential endpoint otherwise) forwards a browser
    to the SPA /login page so the redirect lands somewhere usable.

Related Tasks: W28C-1703 (CC6)

Recent Changes:
- 2026-06-10: W28C-1703 — initial /idam route-gate regression guard.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.fast]

IDAM_PAGES = ["/idam/users", "/idam/groups", "/idam/roles", "/idam/api-keys", "/idam/rbac"]


@pytest.fixture()
def web_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "ut146-key")
    monkeypatch.setenv("CLOUD_DOG__WEB_LOGIN__USERNAME", "admin")
    monkeypatch.setenv("CLOUD_DOG__WEB_LOGIN__PASSWORD", "ut146-pass")
    from cloud_dog_chat_client.servers import web_server

    return TestClient(
        web_server.create_app(), raise_server_exceptions=False, follow_redirects=False
    )
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")


@pytest.mark.parametrize("page", IDAM_PAGES)
def test_anonymous_idam_redirects_to_login(web_client, page) -> None:
    with web_client as client:
        resp = client.get(page)
        assert resp.status_code == 302, f"anon {page} must 302: {resp.status_code}"
        assert resp.headers.get("location") == f"/auth/login?next={page}", (
            f"redirect must carry next={page}: {resp.headers.get('location')}"
        )
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")


def test_auth_login_get_forwards_to_spa_login(web_client) -> None:
    with web_client as client:
        resp = client.get("/auth/login?next=/idam/users")
        assert resp.status_code in (302, 307)
        assert "/login" in (resp.headers.get("location") or "")
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")


@pytest.mark.parametrize("page", IDAM_PAGES)
def test_authenticated_idam_serves_spa_shell(web_client, page) -> None:
    with web_client as client:
        login = client.post("/auth/login", json={"username": "admin", "password": "<password>"})
        assert login.status_code == 200, login.text[:200]
        resp = client.get(page)
        assert resp.status_code == 200, f"authed {page}: {resp.status_code} {resp.text[:160]}"
        assert "text/html" in resp.headers.get("content-type", "")
