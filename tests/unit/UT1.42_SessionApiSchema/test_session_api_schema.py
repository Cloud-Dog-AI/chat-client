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
UT1.42 — Session API schema + single-version-source regression guards.

W28C-1703 forensic fixes:
  - CC4: ``GET /sessions/{session_id}`` returns 200 + metadata + last-N events
    for an existing session, 404 for an unknown id, and 401 for an anonymous
    caller. Previously only DELETE was registered, so a GET returned 405.
  - CC5: ``GET /sessions`` list rows carry ``session_id`` (canonical, matching
    the create response) plus ``id`` as a deprecated alias (one release cycle).
  - CC8: ``/version``, ``/status``, ``/api/status``, ``/health`` and
    ``/api/health`` all report the SAME value and it equals
    ``cloud_dog_chat_client.__version__`` (single source of truth). The previous
    ``/api/status`` + ``/health`` builder fell back to a hardcoded ``"0.1.0"``.

Related Tasks: W28C-1703 (CC4, CC5, CC8)

Recent Changes:
- 2026-06-10: W28C-1703 — initial schema + version regression guards.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cloud_dog_chat_client import __version__
from cloud_dog_chat_client.api.server import create_app
from cloud_dog_chat_client.config import ConfigManager

pytestmark = [pytest.mark.unit, pytest.mark.pure, pytest.mark.fast]

_KEY = "ut142-key"
_HEADERS = {"X-API-Key": _KEY}


def _client(env_file, monkeypatch) -> TestClient:
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", _KEY)
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    cfg = ConfigManager(env_file=env_file)
    return TestClient(create_app(cfg), raise_server_exceptions=False)
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")


def test_cc8_single_version_source(env_file, monkeypatch) -> None:
    """All version-bearing endpoints agree and equal the package __version__."""
    with _client(env_file, monkeypatch) as client:
        observed = {}
        for path in ("/version", "/status", "/api/status", "/health", "/api/health"):
            resp = client.get(path)
            assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text[:160]}"
            observed[path] = (resp.json() or {}).get("version")
        assert len(set(observed.values())) == 1, f"version drift across endpoints: {observed}"
        assert set(observed.values()) == {__version__}, (
            f"endpoints must equal __version__={__version__!r}: {observed}"
        )
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")


def test_cc4_get_single_session_authed(env_file, monkeypatch) -> None:
    """CC4: GET a known session returns 200 with metadata + serialised events."""
    with _client(env_file, monkeypatch) as client:
        created = client.post("/sessions", headers=_HEADERS, json={"metadata": {"title": "ut142"}})
        assert created.status_code == 200, created.text[:200]
        sid = (created.json() or {}).get("session_id")
        assert sid

        resp = client.get(f"/sessions/{sid}", headers=_HEADERS)
        assert resp.status_code == 200, resp.text[:200]
        body = resp.json()
        assert body.get("session_id") == sid
        assert body.get("id") == sid  # CC5 deprecated alias on the detail response
        assert isinstance(body.get("metadata"), dict)
        assert isinstance(body.get("events"), list)
        assert body.get("events_count", 0) >= 1
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")


def test_cc4_get_single_unknown_is_404(env_file, monkeypatch) -> None:
    with _client(env_file, monkeypatch) as client:
        resp = client.get("/sessions/does-not-exist-xyz", headers=_HEADERS)
        assert resp.status_code == 404, resp.text[:200]
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")


def test_cc4_get_single_anon_is_401(env_file, monkeypatch) -> None:
    with _client(env_file, monkeypatch) as client:
        resp = client.get("/sessions/anything")
        assert resp.status_code == 401, resp.text[:200]
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")


def test_cc5_list_rows_carry_session_id_and_id_alias(env_file, monkeypatch) -> None:
    with _client(env_file, monkeypatch) as client:
        client.post("/sessions", headers=_HEADERS, json={"metadata": {"title": "ut142-list"}})
        rows = (client.get("/sessions", headers=_HEADERS).json() or {}).get("sessions") or []
        assert rows, "expected at least one session row"
        for row in rows:
            assert row.get("session_id"), f"row missing canonical session_id: {row}"
            assert row.get("id") == row.get("session_id"), (
                f"deprecated id alias must equal session_id: {row}"
            )
