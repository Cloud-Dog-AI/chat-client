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
import re
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, start_api, stop_api, wait_for_api


_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_SECRET_VALUE_MARKERS = ("test123", "secret-value", "token-value", "api-key-value")


def _select_runtime_log(log_folder: Path, *candidates: str) -> Path:
    for name in candidates:
        candidate = log_folder / name
        if candidate.exists():
            return candidate
    return log_folder / candidates[0]


def parse_audit_log(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("message"), str):
            msg = str(parsed.get("message") or "").strip()
            if msg.startswith("{"):
                try:
                    nested = json.loads(msg)
                except json.JSONDecodeError:
                    nested = None
                if isinstance(nested, dict):
                    parsed = nested
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def _iter_string_values(value: Any):
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for inner in value.values():
            yield from _iter_string_values(inner)
        return
    if isinstance(value, list):
        for inner in value:
            yield from _iter_string_values(inner)


def assert_au3_complete(event: dict[str, Any]) -> None:
    assert str(event.get("event_type") or "").strip()
    assert str(event.get("action") or "").strip()
    ts = str(event.get("timestamp") or "").strip()
    assert _TS_RE.match(ts), f"timestamp is not ISO8601 UTC ms: {ts}"
    assert str(event.get("service") or "").strip()
    assert str(event.get("service_instance") or "").strip()
    assert str(event.get("environment") or "").strip()
    actor = event.get("actor") or {}
    assert str(actor.get("id") or "").strip()
    assert str(actor.get("ip") or "").strip()
    assert str(event.get("outcome") or "").strip()


def assert_identity_chain(
    event: dict[str, Any],
    *,
    user_id: str,
    user_ip: str,
    intermediary: str | None = None,
) -> None:
    actor = event.get("actor") or {}
    assert str(actor.get("id") or "") == str(user_id)
    assert str(actor.get("ip") or "") == str(user_ip)
    details = event.get("details") or {}
    source = details.get("source") if isinstance(details, dict) else {}
    source = source if isinstance(source, dict) else {}
    if intermediary is None:
        assert not str(source.get("intermediary") or "").strip()
    else:
        assert str(source.get("intermediary") or "").strip() == intermediary


def assert_no_secrets(event: dict[str, Any]) -> None:
    for raw in _iter_string_values(event):
        lowered = str(raw).lower()
        for marker in _SECRET_VALUE_MARKERS:
            assert marker not in lowered


@pytest.fixture(name="assert_au3_complete")
def fixture_assert_au3_complete():
    return assert_au3_complete


@pytest.fixture(name="assert_identity_chain")
def fixture_assert_identity_chain():
    return assert_identity_chain


@pytest.fixture(name="assert_no_secrets")
def fixture_assert_no_secrets():
    return assert_no_secrets


@pytest.fixture(scope="module")
def running_env_file(env_file: str) -> str:
    return env_file


@pytest.fixture(scope="module")
def running_server(running_env_file: str):
    cfg = ConfigManager(env_file=running_env_file)
    start_api(cfg, env_file=running_env_file)
    try:
        wait_for_api(cfg)
        yield cfg
    finally:
        stop_api(cfg, env_file=running_env_file)


@pytest.fixture(scope="module")
def api_base(running_server: ConfigManager) -> str:
    return api_base_url(running_server)


@pytest.fixture(scope="module")
def request_timeout(running_server: ConfigManager) -> float:
    value = running_server.get("client_api.request_timeout_seconds")
    return float(value if value is not None else 20.0)


@pytest.fixture
def api_client(api_base: str, request_timeout: float):
    with httpx.Client(base_url=api_base, timeout=request_timeout) as client:
        yield client


@pytest.fixture(scope="module")
def audit_log_path(running_server: ConfigManager) -> Path:
    log_folder = Path(str(running_server.get("app.logfolder") or "logs"))
    return _select_runtime_log(
        log_folder,
        "audit.log.jsonl",
        "api_server.audit.jsonl",
        "client_api.audit.jsonl",
    )


@pytest.fixture(scope="module")
def app_log_path(running_server: ConfigManager) -> Path:
    log_folder = Path(str(running_server.get("app.logfolder") or "logs"))
    return _select_runtime_log(
        log_folder,
        "api_server.log",
        "client_api.log",
    )


@pytest.fixture
def audit_log_reader(
    audit_log_path: Path,
) -> Callable[[], list[dict[str, Any]]]:
    cursor = {"index": len(parse_audit_log(audit_log_path))}

    def _read_new() -> list[dict[str, Any]]:
        events = parse_audit_log(audit_log_path)
        start = cursor["index"]
        cursor["index"] = len(events)
        return events[start:]

    return _read_new
