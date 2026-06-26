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

"""AT: Chat file attachments — attach by value and by reference through
chat composer/profile and verify MCP receives the artifact.

Covers R7.3 (MCP Chat File Attachments) from REQUIREMENTS.md.

Non-LLM: tests profile file intake settings, upload-by-value through the
authenticated proxy, and attachment metadata preservation in the session
transcript.  No LLM interaction is required.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.file_mcp_runtime import maybe_start_file_mcp, maybe_stop_file_mcp


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


@pytest.fixture(scope="module", autouse=True)
def _servers(env_file):
    cfg = ConfigManager(env_file=env_file)
    started_file_mcp = maybe_start_file_mcp(cfg)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        yield None
    finally:
        stop_api(cfg, env_file=env_file)
        if started_file_mcp:
            maybe_stop_file_mcp(cfg)


# ---------------------------------------------------------------------------
# 1. Profile file-intake settings CRUD
# ---------------------------------------------------------------------------
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-005")


@pytest.mark.asyncio
async def test_profile_file_intake_settings_roundtrip(env_file):
    """Create a profile with file_intake in session_defaults, then verify
    the returned profile preserves the settings accurately."""
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    headers = api_headers(cfg)

    file_intake = {
        "uploads_enabled": True,
        "allowed_modes": ["by-value", "by-reference"],
        "file_server_index": None,
        "max_size_bytes": 52_428_800,
        "allowed_source_schemes": ["https", "http"],
        "artifact_rendering_enabled": True,
    }

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    profile_payload = {
        "name": f"file-intake-test-{ts}",
        "description": "Profile for AT_CHAT_FILE_ATTACHMENTS test",
        "session_defaults": {"file_intake": file_intake},
    }

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_seconds) as client:
        create_resp = await client.post("/v1/profiles", json=profile_payload)
        assert create_resp.status_code == 200, f"create profile: {create_resp.text}"
        profile = create_resp.json()["profile"]
        profile_id = profile["profile_id"]

        assert profile["session_defaults"]["file_intake"]["uploads_enabled"] is True
        assert "by-value" in profile["session_defaults"]["file_intake"]["allowed_modes"]
        assert "by-reference" in profile["session_defaults"]["file_intake"]["allowed_modes"]
        assert profile["session_defaults"]["file_intake"]["max_size_bytes"] == 52_428_800
        assert "https" in profile["session_defaults"]["file_intake"]["allowed_source_schemes"]
        assert profile["session_defaults"]["file_intake"]["artifact_rendering_enabled"] is True

        # Read back via GET
        get_resp = await client.get(f"/v1/profiles/{profile_id}")
        assert get_resp.status_code == 200
        read_profile = get_resp.json()["profile"]
        assert read_profile["session_defaults"]["file_intake"] == file_intake

        # Update: disable uploads
        update_payload = {
            "name": profile["name"],
            "session_defaults": {
                "file_intake": {**file_intake, "uploads_enabled": False},
            },
        }
        update_resp = await client.put(f"/v1/profiles/{profile_id}", json=update_payload)
        assert update_resp.status_code == 200
        updated = update_resp.json()["profile"]
        assert updated["session_defaults"]["file_intake"]["uploads_enabled"] is False

        # Cleanup
        delete_resp = await client.delete(f"/v1/profiles/{profile_id}")
        assert delete_resp.status_code == 200
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-005")


@pytest.mark.asyncio
async def test_profile_file_intake_defaults_when_absent(env_file):
    """A profile with no file_intake key should still be creatable and
    returned with no file_intake key — the UI defaults apply client-side."""
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    headers = api_headers(cfg)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    profile_payload = {
        "name": f"no-file-intake-{ts}",
        "session_defaults": {"llm_model": "test"},
    }

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_seconds) as client:
        create_resp = await client.post("/v1/profiles", json=profile_payload)
        assert create_resp.status_code == 200
        profile = create_resp.json()["profile"]
        profile_id = profile["profile_id"]

        defaults = profile["session_defaults"]
        assert defaults.get("llm_model") == "test"
        # file_intake should be absent (not injected server-side)
        assert "file_intake" not in defaults or defaults["file_intake"] is None or isinstance(defaults["file_intake"], dict)

        await client.delete(f"/v1/profiles/{profile_id}")


# ---------------------------------------------------------------------------
# 2. Upload by value through chat session MCP proxy
# ---------------------------------------------------------------------------
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-005")


@pytest.mark.asyncio
async def test_upload_by_value_attachment_metadata(env_file):
    """Upload a file by value (multipart) to a chat session and verify the
    attachment metadata appears in the transcript with correct fields."""
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    file_server_index = int(_require_cfg(cfg, "mcp.at1_10.file_server_index"))
    require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)
    file_root = str(_require_cfg(cfg, "mcp.at1_10.file_root")).rstrip("/")
    headers = api_headers(cfg)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    test_content = f"Chat file attachment test content: {ts}\n"
    test_path = f"{file_root}/at_chat_attach_{ts}.txt"

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_seconds) as client:
        # Create session
        session_resp = await client.post("/sessions", json={"metadata": {"suite": "AT_CHAT_FILE_ATTACHMENTS"}})
        assert session_resp.status_code == 200
        session_id = session_resp.json()["session_id"]

        # Upload by value (multipart)
        files = {"file": ("test_attach.txt", test_content.encode("utf-8"), "text/plain")}
        data = {
            "path": test_path,
            "server_index": str(file_server_index),
            "overwrite": "true",
            "dry_run": "false",
        }
        if require_initialize:
            data["require_initialize"] = "true"

        upload_resp = await client.post(
            f"/sessions/{session_id}/mcp/files/upload-multipart",
            files=files,
            data=data,
        )
        assert upload_resp.status_code == 200, f"upload: {upload_resp.text}"
        upload_result = upload_resp.json()
        assert upload_result["path"]
        assert upload_result["bytes_written"] > 0

        # Verify transcript contains the upload event
        transcript_resp = await client.get(f"/sessions/{session_id}/transcript")
        assert transcript_resp.status_code == 200
        events = transcript_resp.json().get("events", [])
        upload_events = [e for e in events if e["event_type"] == "mcp_file_upload_result"]
        assert len(upload_events) >= 1, f"Expected upload event in transcript, got {[e['event_type'] for e in events]}"
        event_data = upload_events[-1]["data"]
        assert event_data["path"]
        assert event_data["bytes_written"] > 0
        assert event_data.get("source_kind") == "multipart"

        # Cleanup session
        await client.delete(f"/sessions/{session_id}")


# ---------------------------------------------------------------------------
# 3. Upload by value (JSON base64)
# ---------------------------------------------------------------------------
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-005")


@pytest.mark.asyncio
async def test_upload_by_value_base64(env_file):
    """Upload a file by value (JSON base64) and verify transcript metadata."""
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    file_server_index = int(_require_cfg(cfg, "mcp.at1_10.file_server_index"))
    require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)
    file_root = str(_require_cfg(cfg, "mcp.at1_10.file_root")).rstrip("/")
    headers = api_headers(cfg)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    test_content = f"Base64 attachment test: {ts}\n"
    test_path = f"{file_root}/at_chat_b64_{ts}.txt"

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_seconds) as client:
        session_resp = await client.post("/sessions", json={"metadata": {"suite": "AT_CHAT_FILE_ATTACHMENTS"}})
        assert session_resp.status_code == 200
        session_id = session_resp.json()["session_id"]

        upload_payload = {
            "path": test_path,
            "content_base64": base64.b64encode(test_content.encode("utf-8")).decode("ascii"),
            "server_index": file_server_index,
            "overwrite": True,
            "dry_run": False,
        }
        if require_initialize:
            upload_payload["require_initialize"] = True

        upload_resp = await client.post(
            f"/sessions/{session_id}/mcp/files/upload",
            json=upload_payload,
        )
        assert upload_resp.status_code == 200, f"upload: {upload_resp.text}"
        result = upload_resp.json()
        assert result["bytes_written"] > 0
        assert result.get("dry_run") is False

        transcript_resp = await client.get(f"/sessions/{session_id}/transcript")
        events = transcript_resp.json().get("events", [])
        upload_events = [e for e in events if e["event_type"] == "mcp_file_upload_result"]
        assert len(upload_events) >= 1
        assert upload_events[-1]["data"]["source_kind"] == "base64"

        await client.delete(f"/sessions/{session_id}")


# ---------------------------------------------------------------------------
# 4. Upload by reference (source_url)
# ---------------------------------------------------------------------------
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-005")


@pytest.mark.asyncio
async def test_upload_by_reference_source_url(env_file):
    """Upload a file by reference (source_url) and verify transcript metadata
    records source_kind as 'url'.

    NOTE: This test requires a reachable URL. It uses the chat-client's own
    /version endpoint as a lightweight GET-able source.  If URL fetch is
    disabled or the scheme is not allowed, the test skips gracefully.
    """
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    file_server_index = int(_require_cfg(cfg, "mcp.at1_10.file_server_index"))
    require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)
    file_root = str(_require_cfg(cfg, "mcp.at1_10.file_root")).rstrip("/")
    headers = api_headers(cfg)

    source_url = f"{base_url.rstrip('/')}/version"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    test_path = f"{file_root}/at_chat_ref_{ts}.json"

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_seconds) as client:
        session_resp = await client.post("/sessions", json={"metadata": {"suite": "AT_CHAT_FILE_ATTACHMENTS"}})
        assert session_resp.status_code == 200
        session_id = session_resp.json()["session_id"]

        upload_payload = {
            "path": test_path,
            "source_url": source_url,
            "server_index": file_server_index,
            "overwrite": True,
            "dry_run": False,
        }
        if require_initialize:
            upload_payload["require_initialize"] = True

        upload_resp = await client.post(
            f"/sessions/{session_id}/mcp/files/upload",
            json=upload_payload,
        )

        if upload_resp.status_code == 400:
            detail = upload_resp.json().get("detail", "")
            if "scheme" in detail.lower():
                raise AssertionError(f"URL scheme must be allowed for AT source-url coverage: {detail}")
            raise AssertionError(f"upload by reference failed: {upload_resp.text}")

        assert upload_resp.status_code == 200, f"upload by ref: {upload_resp.text}"
        result = upload_resp.json()
        assert result["bytes_written"] > 0

        transcript_resp = await client.get(f"/sessions/{session_id}/transcript")
        events = transcript_resp.json().get("events", [])
        upload_events = [e for e in events if e["event_type"] == "mcp_file_upload_result"]
        assert len(upload_events) >= 1
        assert upload_events[-1]["data"]["source_kind"] == "url"

        await client.delete(f"/sessions/{session_id}")
