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

import base64
import json

from fastapi.testclient import TestClient

import cloud_dog_chat_client.mcp.connection as mcp_connection_module
from cloud_dog_chat_client.servers import a2a_server as a2a_server_module
import pytest


class _FakeConfig:
    def __init__(self) -> None:
        self.env_file = "tests/env-UT"
        self._values = {
            "client_api.api_key": "",
            "client_api.api_key_header": "X-API-Key",
            "mcp.defaults.protocol_version": "2025-03-26",
            "mcp.api.require_initialize": False,
            "mcp.servers": [
                {
                    "name": "file-mcp",
                    "transport": "streamable_http",
                    "base_url": "http://file-mcp.local",
                    "mcp_path": "/mcp",
                }
            ],
        }

    def get(self, key: str, default=None):
        return self._values.get(key, default)


class _FakeConfigStore:
    def list_events(self, after_id: int = 0, limit: int = 100):
        return []


class _FakeStore:
    def __init__(self) -> None:
        self._sessions = {
            "session-a2a": {
                "id": "session-a2a",
                "metadata": {
                    "profile_mcp_servers": [
                        {
                            "name": "file-mcp",
                            "transport": "streamable_http",
                            "base_url": "http://file-mcp.profile",
                            "mcp_path": "/mcp",
                        }
                    ]
                },
            }
        }
        self.events: list[tuple[str, str]] = []

    def list_events(self, after_id: int = 0, limit: int = 100):
        return []

    def get_session(self, session_id: str):
        return self._sessions.get(session_id)

    def append_event(self, session_id: str, event) -> None:
        self.events.append((session_id, str(getattr(event, "event_type", ""))))


class _FakeRuntime:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.config_store = _FakeConfigStore()

    def dispose(self) -> None:
        return


class _FakeTransport:
    def __init__(self, storage: dict[str, bytes]) -> None:
        self._storage = storage

    async def initialize(self, *, protocol_version: str, client_name: str = "", client_version: str = "") -> None:
        return

    async def tools_call(self, name: str, arguments: dict[str, object]):
        path = str(arguments.get("path") or "")
        if name == "b64_decode_to_file":
            raw = str(arguments.get("data") or "")
            raw += "=" * ((4 - len(raw) % 4) % 4)
            content = base64.b64decode(raw, validate=False)
            self._storage[path] = content
            return {
                "structuredContent": {
                    "path": path,
                    "bytes_written": len(content),
                    "dry_run": bool(arguments.get("dry_run", False)),
                }
            }
        if name == "write_file":
            content = str(arguments.get("content") or "").encode("utf-8")
            self._storage[path] = content
            return {
                "structuredContent": {
                    "path": path,
                    "bytes_written": len(content),
                    "dry_run": bool(arguments.get("dry_run", False)),
                }
            }
        if name == "b64_encode_file":
            if path not in self._storage:
                return {"isError": True, "content": [{"type": "text", "text": "not found"}]}
            return {
                "structuredContent": {
                    "path": path,
                    "data": base64.b64encode(self._storage[path]).decode("ascii"),
                }
            }
        if name == "read_file":
            if path not in self._storage:
                return {"isError": True, "content": [{"type": "text", "text": "not found"}]}
            return {
                "structuredContent": {
                    "path": path,
                    "content": self._storage[path].decode("utf-8"),
                }
            }
        return {"isError": True, "content": [{"type": "text", "text": f"unsupported tool: {name}"}]}


class _FakeConnection:
    def __init__(self, storage: dict[str, bytes]) -> None:
        self.transport = _FakeTransport(storage)

    async def connect(self) -> None:
        return

    async def close(self) -> None:
        return
@pytest.mark.UT
@pytest.mark.a2a
@pytest.mark.req("FR-006")


def test_ut1_6_a2a_file_skills_roundtrip(monkeypatch):
    cfg = _FakeConfig()
    runtime = _FakeRuntime()
    storage: dict[str, bytes] = {}

    def _fake_runtime_factory(_cfg):
        return runtime

    def _fake_from_config(cls, cfg, server_index=0, servers_override=None):
        return _FakeConnection(storage)

    monkeypatch.setattr(a2a_server_module, "load_config", lambda: cfg)
    monkeypatch.setattr(a2a_server_module, "ChatDatabaseRuntime", _fake_runtime_factory)
    monkeypatch.setattr(
        mcp_connection_module.MCPConnection,
        "from_config",
        classmethod(_fake_from_config),
    )

    upload_bytes = b"a2a roundtrip payload\nline two\n"
    upload_path = "/root/test-a2a-roundtrip.txt"
    normalized_path = "root/test-a2a-roundtrip.txt"

    app = a2a_server_module.create_app()
    with TestClient(app) as client:
        card = client.get("/.well-known/agent.json")
        assert card.status_code == 200
        skill_ids = {item["id"] for item in card.json().get("skills") or []}
        assert {"upload_file", "download_file"}.issubset(skill_ids)

        upload_response = client.post(
            "/tasks",
            json={
                "id": "upload-task",
                "skill_id": "upload_file",
                "input": {
                    "text": json.dumps(
                        {
                            "session_id": "session-a2a",
                            "server_index": 0,
                            "path": upload_path,
                            "profile": "default",
                            "content_base64": base64.b64encode(upload_bytes).decode("ascii"),
                        }
                    )
                },
            },
        )
        assert upload_response.status_code == 200
        upload_payload = upload_response.json()
        assert upload_payload["status"] == "completed"
        upload_result = json.loads(upload_payload["output"]["text"])
        assert upload_result["ok"] is True
        assert upload_result["path"] == normalized_path
        assert upload_result["bytes_written"] == len(upload_bytes)
        assert storage[normalized_path] == upload_bytes

        download_response = client.post(
            "/tasks",
            json={
                "id": "download-task",
                "skill_id": "download_file",
                "input": {
                    "text": json.dumps(
                        {
                            "session_id": "session-a2a",
                            "server_index": 0,
                            "path": upload_path,
                            "profile": "default",
                        }
                    )
                },
            },
        )
        assert download_response.status_code == 200
        download_payload = download_response.json()
        assert download_payload["status"] == "completed"
        download_result = json.loads(download_payload["output"]["text"])
        assert download_result["ok"] is True
        assert download_result["path"] == normalized_path
        assert base64.b64decode(download_result["content_base64"]) == upload_bytes
        assert download_result["content_text"] == upload_bytes.decode("utf-8")

    recorded_event_types = [event_type for _, event_type in runtime.store.events]
    assert "a2a_file_upload" in recorded_event_types
    assert "a2a_file_upload_result" in recorded_event_types
    assert "a2a_file_download" in recorded_event_types
    assert "a2a_file_download_result" in recorded_event_types
