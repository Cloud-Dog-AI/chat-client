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
UT1.44 — chat-client A2A event-stream anonymous gate (negative-auth guard).

W28C-1703 / CC2 / 1601-B (S-SECURITY P0). The legacy ``GET /a2a/events`` poll
(merged session + config events) had NO auth dependency — an anonymous caller
could read live user messages (the ``/weba2a/events`` leak). The handshake is
now gated by the same credential check as the WebSocket stream; the canonical
``/a2a/events/sse`` router is gated too. Anonymous callers get HTTP 401.

Related Tasks: W28C-1703 (CC2)

Recent Changes:
- 2026-06-10: W28C-1703 — initial A2A events anon-gate regression guard.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.fast]

_KEY = "ut144-key"


@pytest.fixture()
def a2a_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", _KEY)
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    from cloud_dog_chat_client.servers import a2a_server

    return TestClient(a2a_server.create_app(), raise_server_exceptions=False)
@pytest.mark.UT
@pytest.mark.a2a
@pytest.mark.req("FR-006")


def test_anonymous_events_poll_denied_401(a2a_client) -> None:
    with a2a_client as client:
        resp = client.get("/a2a/events")
        assert resp.status_code == 401, (
            f"anon /a2a/events must be HTTP 401 (handshake refused): "
            f"{resp.status_code} {resp.text[:200]}"
        )
        body = resp.json()
        assert body.get("ok") is False
        assert (body.get("errors") or [{}])[0].get("code") == "UNAUTHENTICATED"
@pytest.mark.UT
@pytest.mark.a2a
@pytest.mark.req("FR-006")


def test_authenticated_events_poll_ok(a2a_client) -> None:
    with a2a_client as client:
        resp = client.get("/a2a/events", headers={"X-API-Key": _KEY})
        assert resp.status_code == 200, resp.text[:200]
        assert "events" in (resp.json() or {})
@pytest.mark.UT
@pytest.mark.a2a
@pytest.mark.req("FR-006")


def test_anonymous_canonical_sse_denied(a2a_client) -> None:
    """The platform canonical SSE router is gated too (no anon config-event replay)."""
    with a2a_client as client:
        resp = client.get("/a2a/events/sse", headers={"accept": "text/event-stream"})
        assert resp.status_code == 401, (
            f"anon canonical SSE must be denied: {resp.status_code} {resp.text[:160]}"
        )
