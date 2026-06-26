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
UT1.40 — Unauthenticated auth-gate (negative-auth) regression guard.

W28A-889-B estate unauth auth-gate hardening. The missing NEGATIVE-auth test for
the unauthenticated front door (the index-retriever WebApiProxy admin-key
injection class, W28A-734-R2).

chat-client's web tier:
  - handles /auth/me LOCALLY (never proxied) and returns 401 for an anonymous
    caller (no chat_web_session cookie), so the service key is never injected
    onto the principal endpoint;
  - gates the /api/* proxy with the W28A-F-E2E-05 credential check, returning
    401 ("Missing required header: X-API-Key") when the caller presents no
    cookie/api-key — the WebApiProxy service key is injected ONLY for an
    already-authenticated caller.

This test fails if either gate is removed. The AUTHENTICATED inventory path
(/api/mcp/servers returning data when logged in) is proven separately by the
W28A-889-B live preprod evidence.

Related Tasks: W28A-889-B

Recent Changes:
- 2026-06-09: W28A-889-B — initial negative-auth regression guard.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cloud_dog_chat_client.servers.web_server import create_app

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.fast]

PRINCIPAL_PATH = "/auth/me"
PROTECTED_DATA_PATHS = ("/api/mcp/servers", "/api/sessions")
_COOKIE_NAME = "chat_web_session"


@pytest.fixture()
def web_client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-003")


def test_unauth_principal_denied(web_client: TestClient) -> None:
    resp = web_client.get(PRINCIPAL_PATH)
    assert resp.status_code == 401, f"anon {PRINCIPAL_PATH} must be 401: {resp.status_code} {resp.text[:200]}"
    assert '"roles"' not in resp.text and '"permissions"' not in resp.text, resp.text
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-003")


def test_unauth_protected_data_denied(web_client: TestClient) -> None:
    for path in PROTECTED_DATA_PATHS:
        resp = web_client.get(path)
        assert resp.status_code == 401, (
            f"anon {path} must be 401 (proxy gate, no key injection); "
            f"got {resp.status_code}: {resp.text[:200]}"
        )
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-003")


def test_forged_session_cookie_does_not_bypass(web_client: TestClient) -> None:
    web_client.cookies.set(_COOKIE_NAME, "forged-not-a-real-session-id")
    resp = web_client.get(PRINCIPAL_PATH)
    assert resp.status_code == 401, f"forged cookie must not authenticate: {resp.status_code} {resp.text[:200]}"
    data_resp = web_client.get(PROTECTED_DATA_PATHS[0])
    assert data_resp.status_code == 401, data_resp.text
