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
# Covers: R15, CFG-12, CFG-13
import pytest

import json
import os
import re
from pathlib import Path

from fastapi.testclient import TestClient

from cloud_dog_chat_client.api.server import create_app
from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.utils import setup_logging

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _build_env_file_for_st1_13(base_env_file: str, tmp_path: Path) -> str:
    base_text = Path(base_env_file).read_text(encoding="utf-8")
    filtered_lines = [
        line
        for line in base_text.splitlines()
        if not line.startswith("CLOUD_DOG__MCP__SERVERS__")
    ]
    log_folder = tmp_path / "logs"
    merged = "\n".join(
        [
            "\n".join(filtered_lines).rstrip(),
            "",
            "CLOUD_DOG__CLIENT_API__HOST=127.0.0.1",
            "CLOUD_DOG__API_SERVER__PORT=8103",
            "CLOUD_DOG__CLIENT_API__ENABLED=true",
            "CLOUD_DOG__CLIENT_API__START_TIMEOUT_SECONDS=30",
            "CLOUD_DOG__CLIENT_API__STOP_TIMEOUT_SECONDS=30",
            "CLOUD_DOG__CLIENT_API__READY_TIMEOUT_SECONDS=30",
            "CLOUD_DOG__CLIENT_API__READY_POLL_SECONDS=0.5",
            "CLOUD_DOG__CLIENT_API__REQUEST_TIMEOUT_SECONDS=20",
            "CLOUD_DOG__CLIENT_API__API_KEY_HEADER=X-API-Key",
            "CLOUD_DOG__CLIENT_API__API_KEY=user-key",
            "CLOUD_DOG__CLIENT_API__ADMIN_API_KEY_HEADER=X-Admin-Key",
            "CLOUD_DOG__CLIENT_API__ADMIN_API_KEY=admin-key",
            "CLOUD_DOG__CLIENT_API__USER_HEADER=X-User",
            f"CLOUD_DOG__APP__LOGFOLDER={log_folder}",
            "CLOUD_DOG__MCP__SERVERS__0__NAME=search-seed",
            "CLOUD_DOG__MCP__SERVERS__0__TRANSPORT=streamable_http",
            "CLOUD_DOG__MCP__SERVERS__0__BASE_URL=https://search-seed.example",
            "",
        ]
    )
    out_path = tmp_path / "env-st1-13"
    out_path.write_text(merged, encoding="utf-8")
    return str(out_path)
@pytest.mark.ST
@pytest.mark.mcp
@pytest.mark.req("FR-009")


def test_st1_13_mcp_server_admin_rbac_and_audit_logging(env_file, tmp_path):
    # env-vault can set client_api key values in os.environ; pin this test's auth keys explicitly.
    overrides = {
        "CLOUD_DOG__CLIENT_API__API_KEY": "user-key",
        "CLOUD_DOG__CLIENT_API__ADMIN_API_KEY": "admin-key",
        "CLOUD_DOG__CLIENT_API__API_KEY_HEADER": "X-API-Key",
        "CLOUD_DOG__CLIENT_API__ADMIN_API_KEY_HEADER": "X-Admin-Key",
        "CLOUD_DOG__CLIENT_API__USER_HEADER": "X-User",
    }
    inherited_mcp_env = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("CLOUD_DOG__MCP__SERVERS__")
    }
    previous = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    for key in inherited_mcp_env:
        os.environ.pop(key, None)

    try:
        run_env = _build_env_file_for_st1_13(env_file, tmp_path)
        cfg = ConfigManager(env_file=run_env)

        log_folder = Path(str(_require_cfg(cfg, "app.logfolder")))
        setup_logging(
            log_level=str(_require_cfg(cfg, "log.level")),
            log_file=str(log_folder / "client_api.log"),
            log_console=False,
            app_name="cloud_dog_chat_api",
        )

        app = create_app(cfg)
        user_headers = {"X-API-Key": "user-key", "X-User": "viewer"}
        admin_headers = {"X-Admin-Key": "admin-key", "X-User": "platform-admin"}

        with TestClient(app) as client:
            list_before = client.get("/mcp/servers", headers=user_headers)
            assert list_before.status_code == 200
            assert len(list_before.json().get("servers") or []) == 1

            denied = client.post(
                "/mcp/servers",
                headers=user_headers,
                json={
                    "server": {
                        "name": "sql-agent-rbac",
                        "transport": "http_jsonrpc",
                        "base_url": "https://sql.example",
                    }
                },
            )
            assert denied.status_code in (401, 403)

            added = client.post(
                "/mcp/servers",
                headers=admin_headers,
                json={
                    "server": {
                        "name": "sql-agent-rbac",
                        "transport": "http_jsonrpc",
                        "base_url": "https://sql.example",
                        "messages_path": "/messages",
                    }
                },
            )
            assert added.status_code == 200
            added_payload = added.json()
            assert added_payload["index"] == 1
            assert added_payload["server"]["name"] == "sql-agent-rbac"

            updated = client.put(
                "/mcp/servers/1",
                headers=admin_headers,
                json={
                    "server": {
                        "name": "sql-agent-rbac-v2",
                        "transport": "http_jsonrpc",
                        "base_url": "https://sql-v2.example",
                    }
                },
            )
            assert updated.status_code == 200
            assert updated.json()["server"]["name"] == "sql-agent-rbac-v2"

            removed = client.delete("/mcp/servers/1", headers=admin_headers)
            assert removed.status_code == 200
            assert removed.json()["removed"]["name"] == "sql-agent-rbac-v2"

            list_after = client.get("/mcp/servers", headers=user_headers)
            assert list_after.status_code == 200
            assert len(list_after.json().get("servers") or []) == 1

        entries = []
        audit_paths = sorted(
            set(log_folder.glob("*.audit.jsonl")) | set(log_folder.glob("audit.log.jsonl"))
        )
        assert audit_paths
        for audit_path in audit_paths:
            for line in audit_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                parsed = json.loads(line)
                if isinstance(parsed, dict) and isinstance(parsed.get("message"), str):
                    raw_message = str(parsed.get("message") or "").strip()
                    if raw_message.startswith("{"):
                        try:
                            parsed_message = json.loads(raw_message)
                            if isinstance(parsed_message, dict):
                                parsed = parsed_message
                        except Exception:
                            pass
                if isinstance(parsed, dict):
                    entries.append(parsed)

        expected_server_names = {"sql-agent-rbac", "sql-agent-rbac-v2"}
        admin_entries_latest: dict[str, dict] = {}
        for entry in reversed(entries):
            if not str(entry.get("event_type") or "").startswith("security.mcp_server_"):
                continue
            details = entry.get("details") or {}
            if not isinstance(details, dict):
                continue
            server_name = str(details.get("server_name") or "")
            if server_name not in expected_server_names:
                continue
            action = str(entry.get("action") or "").strip()
            if not action or action in admin_entries_latest:
                continue
            admin_entries_latest[action] = entry
        admin_entries = list(admin_entries_latest.values())
        assert len(admin_entries) >= 3
        actions = {str(entry.get("action") or "") for entry in admin_entries}
        assert "mcp_server_add" in actions
        assert "mcp_server_update" in actions
        assert "mcp_server_delete" in actions

        for entry in admin_entries:
            assert _TS_RE.match(str(entry.get("timestamp") or ""))
            assert str(entry.get("service") or "").strip()
            assert str(entry.get("service_instance") or "").strip()
            assert str(entry.get("service_instance") or "").strip() != "unknown"
            assert str(entry.get("environment") or "").strip()
            assert str(entry.get("correlation_id") or "").strip()
            assert str(entry.get("outcome") or "") == "success"
            actor = entry.get("actor") or {}
            assert str(actor.get("id") or "") == "platform-admin"
            assert str(actor.get("ip") or "").strip()
            details = entry.get("details") or {}
            assert isinstance(details, dict)
            source = details.get("source") or {}
            assert isinstance(source, dict)
    finally:
        for key, value in inherited_mcp_env.items():
            os.environ[key] = value
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.mcp, pytest.mark.slow]
