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
# Covers: R13, NFR4, CFG-12

import json
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_headers, start_api, stop_api, wait_for_api


def _find_route_event(
    events: list[dict[str, Any]],
    *,
    method: str,
    route: str,
) -> dict[str, Any] | None:
    method_expected = method.upper()
    for event in reversed(events):
        details = event.get("details") or {}
        if not isinstance(details, dict):
            continue
        http_info = details.get("http") or {}
        if not isinstance(http_info, dict):
            continue
        if str(http_info.get("method") or "").upper() != method_expected:
            continue
        if str(http_info.get("route") or "") != route:
            continue
        return event
    return None


def _wait_for_route_event(
    read_new: Callable[[], list[dict[str, Any]]],
    *,
    method: str,
    route: str,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    seen: list[dict[str, Any]] = []
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        seen.extend(read_new())
        event = _find_route_event(seen, method=method, route=route)
        if event is not None:
            return event
        time.sleep(0.05)
    raise AssertionError(
        f"Missing audit event for {method.upper()} {route}. Seen={json.dumps(seen[-5:], default=str)}"
    )


def _runtime_mode(cfg: ConfigManager) -> str:
    raw = cfg.get("chat_tests.runtime_mode") or cfg.get("tests.runtime_mode") or "local-server"
    return str(raw).strip().lower()
@pytest.mark.ST
@pytest.mark.api
@pytest.mark.req("FR-009")


def test_audit_positive_route_au3_identity_chain(
    api_client,
    running_server: ConfigManager,
    audit_log_reader,
    assert_au3_complete,
    assert_identity_chain,
    assert_no_secrets,
) -> None:
    # Covers: R13, NFR4
    headers = api_headers(running_server)
    headers.update(
        {
            "X-User": "audit-user-01",
            "X-Forwarded-For": "203.0.113.10, 198.51.100.15",
            "X-Cloud-Dog-Intermediary": "chat-gateway",
            "X-Cloud-Dog-Intermediary-Ip": "198.51.100.15",
            "X-Cloud-Dog-Transport": "mcp",
        }
    )
    resp = api_client.post("/sessions", json={"metadata": {"suite": "st-audit"}}, headers=headers)
    assert resp.status_code == 200
    assert str(resp.json().get("session_id") or "").strip()

    event = _wait_for_route_event(audit_log_reader, method="POST", route="/sessions")
    assert str(event.get("outcome") or "") == "success"
    assert str(event.get("severity") or "") == "INFO"
    assert_au3_complete(event)
    assert_identity_chain(
        event,
        user_id="audit-user-01",
        user_ip="203.0.113.10",
        intermediary="chat-gateway",
    )
    assert_no_secrets(event)
@pytest.mark.ST
@pytest.mark.api
@pytest.mark.req("FR-009")


def test_audit_negative_auth_denied(
    api_client,
    audit_log_reader,
    assert_au3_complete,
    assert_identity_chain,
) -> None:
    resp = api_client.post(
        "/sessions",
        json={"metadata": {}},
        headers={
            "X-User": "unauth-user",
            "X-Forwarded-For": "198.51.100.77",
        },
    )
    assert resp.status_code in {401, 403}

    event = _wait_for_route_event(audit_log_reader, method="POST", route="/sessions")
    assert str(event.get("outcome") or "") == "denied"
    assert str(event.get("severity") or "") == "WARNING"
    assert_au3_complete(event)
    assert_identity_chain(event, user_id="unauth-user", user_ip="198.51.100.77")
@pytest.mark.ST
@pytest.mark.api
@pytest.mark.req("FR-009")


def test_audit_negative_params_failure(
    api_client,
    running_server: ConfigManager,
    audit_log_reader,
    assert_au3_complete,
    assert_identity_chain,
) -> None:
    headers = api_headers(running_server)
    headers.update({"X-User": "audit-user-02", "X-Forwarded-For": "203.0.113.11"})
    create_resp = api_client.post("/sessions", json={"metadata": {"suite": "st-audit"}}, headers=headers)
    assert create_resp.status_code == 200
    session_id = str(create_resp.json().get("session_id") or "").strip()
    assert session_id

    resp = api_client.post(
        f"/sessions/{session_id}/messages",
        headers=headers,
        json={"content": ""},
    )
    assert resp.status_code == 400

    event = _wait_for_route_event(
        audit_log_reader,
        method="POST",
        route="/sessions/{session_id}/messages",
    )
    assert str(event.get("outcome") or "") == "failure"
    assert str(event.get("severity") or "") == "ERROR"
    assert_au3_complete(event)
    assert_identity_chain(event, user_id="audit-user-02", user_ip="203.0.113.11")
@pytest.mark.ST
@pytest.mark.api
@pytest.mark.req("FR-009")


def test_audit_log_level_mapping(
    api_client,
    running_server: ConfigManager,
    audit_log_reader,
) -> None:
    # Success -> INFO
    ok = api_client.get("/health")
    assert ok.status_code == 200
    success_event = _wait_for_route_event(audit_log_reader, method="GET", route="/health")
    assert str(success_event.get("severity") or "") == "INFO"

    # Denied -> WARNING
    denied = api_client.post("/sessions", json={"metadata": {}})
    assert denied.status_code in {401, 403}
    denied_event = _wait_for_route_event(audit_log_reader, method="POST", route="/sessions")
    assert str(denied_event.get("severity") or "") == "WARNING"

    # Failure -> ERROR
    headers = api_headers(running_server)
    headers.update({"X-User": "audit-user-03", "X-Forwarded-For": "203.0.113.12"})
    create_resp = api_client.post("/sessions", json={"metadata": {"suite": "st-audit"}}, headers=headers)
    assert create_resp.status_code == 200
    session_id = str(create_resp.json().get("session_id") or "").strip()
    bad = api_client.post(
        f"/sessions/{session_id}/messages",
        json={"content": ""},
        headers=headers,
    )
    assert bad.status_code == 400
    failure_event = _wait_for_route_event(
        audit_log_reader,
        method="POST",
        route="/sessions/{session_id}/messages",
    )
    assert str(failure_event.get("severity") or "") == "ERROR"
@pytest.mark.ST
@pytest.mark.api
@pytest.mark.req("FR-009")


def test_audit_no_secrets_on_query_values(
    api_client,
    audit_log_reader,
    assert_no_secrets,
) -> None:
    resp = api_client.get("/ui/config/tree?password=test123&api_key=raw-value")
    assert resp.status_code == 200

    event = _wait_for_route_event(audit_log_reader, method="GET", route="/ui/config/tree")
    details = event.get("details") or {}
    http_info = details.get("http") if isinstance(details, dict) else {}
    query_keys = http_info.get("query_keys") if isinstance(http_info, dict) else []
    if isinstance(query_keys, str):
        query_values = [query_keys]
    else:
        query_values = [str(item) for item in (query_keys or [])]
    assert any(item in {"__redacted__", "***REDACTED***"} for item in query_values)
    assert_no_secrets(event)
@pytest.mark.ST
@pytest.mark.api
@pytest.mark.req("FR-009")


def test_server_restart_log_consistency(
    api_client,
    running_server: ConfigManager,
    running_env_file: str,
    audit_log_path: Path,
    app_log_path: Path,
    audit_log_reader,
) -> None:
    mode = _runtime_mode(running_server)
    if mode != "local-server":
        pytest.skip(f"Restart semantics validated only for local-server mode; current={mode}")

    # Ensure there is at least one baseline entry before restart.
    first_health = api_client.get("/health")
    assert first_health.status_code == 200
    _wait_for_route_event(audit_log_reader, method="GET", route="/health")

    stop_api(running_server, env_file=running_env_file)
    existing_lines = [line for line in audit_log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert existing_lines, "Audit log must not be empty before restart"
    for line in existing_lines:
        json.loads(line)

    start_api(running_server, env_file=running_env_file)
    wait_for_api(running_server)

    health_after = api_client.get("/health")
    assert health_after.status_code == 200
    restarted_event = _wait_for_route_event(audit_log_reader, method="GET", route="/health")
    assert str(restarted_event.get("outcome") or "") == "success"

    # Post-restart log file is still valid JSONL and has no obvious stale debug markers.
    for line in [ln for ln in audit_log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]:
        json.loads(line)
    if app_log_path.exists():
        app_log_text = app_log_path.read_text(encoding="utf-8")
        assert "TODO: remove" not in app_log_text
        assert "debug test data" not in app_log_text

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.mcp, pytest.mark.slow]
