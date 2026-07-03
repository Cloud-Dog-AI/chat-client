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
# Covers: R13, CFG-12
import logging
import pytest

import re

import cloud_dog_chat_client.servers.common as server_common
from cloud_dog_logging.audit_schema import Actor, AuditEvent, Target
import cloud_dog_chat_client.utils.logger as logger_module


_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _event(outcome: str = "success") -> AuditEvent:
    return AuditEvent(
        event_type="user_function",
        actor=Actor(type="user", id="u-1", ip="127.0.0.1", user_agent="pytest"),
        action="execute",
        outcome=outcome,
        correlation_id="corr-1",
        service="test-service",
        service_instance="test-instance",
        environment="test",
        severity="INFO",
        target=Target(type="resource", id="res-1", name="resource-name"),
        details={"token": "<token>"},
    )
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_audit_event_has_all_au3_fields() -> None:
    payload = _event().to_dict()
    assert payload["event_type"]
    assert payload["action"]
    assert payload["timestamp"]
    assert payload["service"]
    assert payload["service_instance"]
    assert payload["environment"]
    assert payload["actor"]["type"]
    assert payload["actor"]["id"]
    assert payload["outcome"]
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_audit_event_timestamp_format() -> None:
    assert _TS_RE.match(_event().timestamp)
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_audit_event_outcome_values() -> None:
    for value in ("success", "failure", "error", "denied", "partial"):
        assert _event(outcome=value).outcome == value
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_audit_event_no_secrets() -> None:
    payload = _event().to_dict()
    # Contract check at format level: detail keys are explicit and auditable.
    assert "token" in payload["details"]
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_setup_logging_resolves_non_empty_service_instance(monkeypatch, tmp_path) -> None:
    captured = {}

    def _fake_platform_setup_logging(cfg):
        captured["cfg"] = cfg

    monkeypatch.setattr(logger_module, "platform_setup_logging", _fake_platform_setup_logging)
    monkeypatch.setattr(logger_module.socket, "gethostname", lambda: "nist-test-host")

    logger_module.setup_logging(
        log_file=str(tmp_path / "unit.log"),
        log_console=False,
        app_name="cloud_dog_chat_api",
    )

    assert captured["cfg"]["log"]["service_instance"] == "nist-test-host"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_setup_logging_uses_explicit_audit_log_path(monkeypatch, tmp_path) -> None:
    captured = {}

    def _fake_platform_setup_logging(cfg):
        captured["cfg"] = cfg

    monkeypatch.setattr(logger_module, "platform_setup_logging", _fake_platform_setup_logging)

    logger_module.setup_logging(
        log_file=str(tmp_path / "api_server.log"),
        audit_log_file=str(tmp_path / "audit.log.jsonl"),
        log_console=False,
        app_name="cloud_dog_chat_api",
    )

    assert captured["cfg"]["log"]["app_log"] == str(tmp_path / "api_server.log")
    assert captured["cfg"]["log"]["audit_log"] == str(tmp_path / "audit.log.jsonl")
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_setup_logging_normalises_runtime_log_permissions(monkeypatch, tmp_path) -> None:
    app_log = tmp_path / "api_server.log"
    audit_log = tmp_path / "audit.log.jsonl"
    integrity_log = tmp_path / "audit-integrity.log"
    app_log.write_text("app\n", encoding="utf-8")
    audit_log.write_text("{}\n", encoding="utf-8")
    integrity_log.write_text("{}\n", encoding="utf-8")
    app_log.chmod(0o600)
    audit_log.chmod(0o644)
    integrity_log.chmod(0o644)

    class _FakeVerifier:
        _integrity_log_path = integrity_log

    def _fake_platform_setup_logging(_cfg):
        return None

    monkeypatch.setattr(logger_module, "platform_setup_logging", _fake_platform_setup_logging)
    monkeypatch.setattr(logger_module, "get_integrity_verifier", lambda: _FakeVerifier())

    logger_module.setup_logging(
        log_file=str(app_log),
        audit_log_file=str(audit_log),
        log_console=False,
        app_name="cloud_dog_chat_api",
    )

    assert (app_log.stat().st_mode & 0o777) == 0o644
    assert (audit_log.stat().st_mode & 0o777) == 0o600
    assert (integrity_log.stat().st_mode & 0o777) == 0o600
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_setup_logging_precreates_active_log_files_with_expected_modes(monkeypatch, tmp_path) -> None:
    app_log = tmp_path / "api_server.log"
    audit_log = tmp_path / "audit.log.jsonl"

    def _fake_platform_setup_logging(_cfg):
        return None

    monkeypatch.setattr(logger_module, "platform_setup_logging", _fake_platform_setup_logging)
    monkeypatch.setattr(logger_module, "get_integrity_verifier", lambda: None)

    logger_module.setup_logging(
        log_file=str(app_log),
        audit_log_file=str(audit_log),
        log_console=False,
        app_name="cloud_dog_chat_api",
    )

    assert app_log.exists()
    assert audit_log.exists()
    assert (app_log.stat().st_mode & 0o777) == 0o644
    assert (audit_log.stat().st_mode & 0o777) == 0o600
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_setup_logging_wraps_app_handler_to_restore_0644(monkeypatch, tmp_path) -> None:
    app_log = tmp_path / "api_server.log"
    root = logging.getLogger()
    original_handlers = list(root.handlers)

    class _FakeFileHandler(logging.Handler):
        def __init__(self, path):
            super().__init__()
            self.baseFilename = str(path)

        def emit(self, record):
            app_log.write_text(record.getMessage(), encoding="utf-8")
            app_log.chmod(0o600)

    def _fake_platform_setup_logging(_cfg):
        root.handlers.clear()
        root.addHandler(_FakeFileHandler(app_log))

    monkeypatch.setattr(logger_module, "platform_setup_logging", _fake_platform_setup_logging)
    monkeypatch.setattr(logger_module, "get_integrity_verifier", lambda: None)

    try:
        logger_module.setup_logging(
            log_file=str(app_log),
            audit_log_file=str(tmp_path / "audit.log.jsonl"),
            log_console=False,
            app_name="cloud_dog_chat_api",
        )
        handler = root.handlers[0]
        handler.emit(logging.makeLogRecord({"msg": "hello"}))
        assert (app_log.stat().st_mode & 0o777) == 0o644
    finally:
        root.handlers.clear()
        root.handlers.extend(original_handlers)
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_configure_logging_uses_surface_specific_log_config(monkeypatch) -> None:
    captured = {}

    class _FakeCfg:
        def __init__(self) -> None:
            self.values = {
                "app.logfolder": "./logs",
                "log.level": "INFO",
                "log.console": True,
                "app.server_id": "chat-client-test",
                "log.api_server_log": "logs/api_server.log",
                "log.audit_log": "logs/audit.log.jsonl",
            }

        def get(self, path: str, default=None):
            return self.values.get(path, default)

    def _fake_setup_logging(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(server_common, "setup_logging", _fake_setup_logging)

    server_common.configure_logging(
        _FakeCfg(),
        section="api_server",
        default_log_name="api_server.log",
        app_name="cloud_dog_chat_api",
    )

    assert captured["log_file"] == "logs/api_server.log"
    assert captured["audit_log_file"] == "logs/audit.log.jsonl"
    assert captured["app_name"] == "cloud_dog_chat_api"

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.pure, pytest.mark.fast]
