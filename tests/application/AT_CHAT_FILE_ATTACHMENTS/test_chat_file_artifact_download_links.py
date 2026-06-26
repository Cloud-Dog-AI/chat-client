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

"""AT: Chat file artifact download links — agent/MCP returns Markdown or
profile-linked artifact and chat transcript renders authenticated download
link using FileArtifactCard.

Covers R7.3 (MCP Chat Artifact Download Links) from REQUIREMENTS.md.

Non-LLM: verifies that file artifacts created via the MCP file transfer
proxy produce correct download links through authenticated chat-client
routes, including Markdown and report file types.  No LLM interaction is
required.
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
# 1. Upload Markdown artifact then download through authenticated route
# ---------------------------------------------------------------------------
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-005")


@pytest.mark.asyncio
async def test_markdown_artifact_download_link(env_file):
    """Upload a Markdown file as an artifact, then download it through the
    authenticated GET /sessions/{id}/mcp/files/download/content endpoint.
    Verify:
    - Content-Disposition is set with the correct filename
    - Content-Type is text or markdown
    - The body matches the original content
    - The transcript shows the download event
    """
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    file_server_index = int(_require_cfg(cfg, "mcp.at1_10.file_server_index"))
    require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)
    file_root = str(_require_cfg(cfg, "mcp.at1_10.file_root")).rstrip("/")
    headers = api_headers(cfg)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    md_content = f"# Report Artifact\n\nGenerated at {ts}\n\n- Item 1\n- Item 2\n"
    md_path = f"{file_root}/at_artifact_{ts}.md"

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_seconds) as client:
        session_resp = await client.post("/sessions", json={"metadata": {"suite": "AT_CHAT_FILE_ARTIFACT_DL"}})
        assert session_resp.status_code == 200
        session_id = session_resp.json()["session_id"]

        # Upload the markdown artifact
        upload_payload = {
            "path": md_path,
            "content_base64": base64.b64encode(md_content.encode("utf-8")).decode("ascii"),
            "server_index": file_server_index,
            "overwrite": True,
        }
        if require_initialize:
            upload_payload["require_initialize"] = True

        upload_resp = await client.post(f"/sessions/{session_id}/mcp/files/upload", json=upload_payload)
        assert upload_resp.status_code == 200, f"upload: {upload_resp.text}"
        assert upload_resp.json()["bytes_written"] > 0

        # Download through the authenticated content endpoint
        download_params = {
            "path": md_path,
            "server_index": str(file_server_index),
            "download_name": f"at_artifact_{ts}.md",
        }
        if require_initialize:
            download_params["require_initialize"] = "true"

        download_resp = await client.get(
            f"/sessions/{session_id}/mcp/files/download/content",
            params=download_params,
        )
        assert download_resp.status_code == 200, f"download: {download_resp.text}"

        # Verify Content-Disposition
        content_disp = download_resp.headers.get("content-disposition", "")
        assert "attachment" in content_disp.lower() or "inline" in content_disp.lower(), \
            f"Expected Content-Disposition header, got: {content_disp}"
        assert f"at_artifact_{ts}.md" in content_disp, \
            f"Expected filename in Content-Disposition, got: {content_disp}"

        # Verify content type
        content_type = download_resp.headers.get("content-type", "")
        assert "text" in content_type or "markdown" in content_type, \
            f"Expected text or markdown content type, got: {content_type}"

        # Verify body matches
        body = download_resp.text
        assert "# Report Artifact" in body
        assert ts in body

        # Verify transcript records both upload and download events
        transcript_resp = await client.get(f"/sessions/{session_id}/transcript")
        events = transcript_resp.json().get("events", [])
        upload_events = [e for e in events if e["event_type"] == "mcp_file_upload_result"]
        download_events = [e for e in events if e["event_type"] == "mcp_file_download_result"]
        assert len(upload_events) >= 1, "Missing upload event in transcript"
        assert len(download_events) >= 1, "Missing download event in transcript"
        assert download_events[-1]["data"]["path"]

        await client.delete(f"/sessions/{session_id}")


# ---------------------------------------------------------------------------
# 2. HTML report artifact download
# ---------------------------------------------------------------------------
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-005")


@pytest.mark.asyncio
async def test_html_report_artifact_download(env_file):
    """Upload an HTML report artifact and verify authenticated download
    returns correct content and headers."""
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    file_server_index = int(_require_cfg(cfg, "mcp.at1_10.file_server_index"))
    require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)
    file_root = str(_require_cfg(cfg, "mcp.at1_10.file_root")).rstrip("/")
    headers = api_headers(cfg)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    html_content = f"<!doctype html><html><body><h1>Report {ts}</h1><p>Content</p></body></html>"
    html_path = f"{file_root}/at_report_{ts}.html"

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_seconds) as client:
        session_resp = await client.post("/sessions", json={"metadata": {"suite": "AT_CHAT_FILE_ARTIFACT_DL"}})
        assert session_resp.status_code == 200
        session_id = session_resp.json()["session_id"]

        upload_payload = {
            "path": html_path,
            "content_base64": base64.b64encode(html_content.encode("utf-8")).decode("ascii"),
            "server_index": file_server_index,
            "overwrite": True,
        }
        if require_initialize:
            upload_payload["require_initialize"] = True

        upload_resp = await client.post(f"/sessions/{session_id}/mcp/files/upload", json=upload_payload)
        assert upload_resp.status_code == 200

        download_params = {
            "path": html_path,
            "server_index": str(file_server_index),
            "download_name": f"at_report_{ts}.html",
        }
        if require_initialize:
            download_params["require_initialize"] = "true"

        download_resp = await client.get(
            f"/sessions/{session_id}/mcp/files/download/content",
            params=download_params,
        )
        assert download_resp.status_code == 200
        assert f"Report {ts}" in download_resp.text
        assert "html" in download_resp.headers.get("content-type", "").lower()

        await client.delete(f"/sessions/{session_id}")


# ---------------------------------------------------------------------------
# 3. JSON download (base64 response)
# ---------------------------------------------------------------------------
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-005")


@pytest.mark.asyncio
async def test_json_download_returns_base64(env_file):
    """Upload a file then use the JSON download endpoint (POST) and verify
    the response contains valid base64 content."""
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    file_server_index = int(_require_cfg(cfg, "mcp.at1_10.file_server_index"))
    require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)
    file_root = str(_require_cfg(cfg, "mcp.at1_10.file_root")).rstrip("/")
    headers = api_headers(cfg)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    test_content = f"JSON download test: {ts}\n"
    test_path = f"{file_root}/at_json_dl_{ts}.txt"

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_seconds) as client:
        session_resp = await client.post("/sessions", json={"metadata": {"suite": "AT_CHAT_FILE_ARTIFACT_DL"}})
        assert session_resp.status_code == 200
        session_id = session_resp.json()["session_id"]

        upload_payload = {
            "path": test_path,
            "content_base64": base64.b64encode(test_content.encode("utf-8")).decode("ascii"),
            "server_index": file_server_index,
            "overwrite": True,
        }
        if require_initialize:
            upload_payload["require_initialize"] = True

        upload_resp = await client.post(f"/sessions/{session_id}/mcp/files/upload", json=upload_payload)
        assert upload_resp.status_code == 200

        download_payload = {
            "path": test_path,
            "server_index": file_server_index,
        }
        if require_initialize:
            download_payload["require_initialize"] = True

        download_resp = await client.post(
            f"/sessions/{session_id}/mcp/files/download",
            json=download_payload,
        )
        assert download_resp.status_code == 200, f"json download: {download_resp.text}"
        result = download_resp.json()
        assert result["path"]
        assert result["content_base64"]
        assert result["byte_size"] > 0

        # Decode and verify content
        decoded = base64.b64decode(result["content_base64"]).decode("utf-8")
        assert ts in decoded

        await client.delete(f"/sessions/{session_id}")


# ---------------------------------------------------------------------------
# 4. Download requires authentication
# ---------------------------------------------------------------------------
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-005")


@pytest.mark.asyncio
async def test_download_requires_authentication(env_file):
    """Attempt download without valid auth headers and verify 401/403."""
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))

    # Check if auth is required
    api_key_required = bool(cfg.get("client_api.api_key_required"))
    if not api_key_required:
        raise AssertionError("client_api.api_key_required must be true for auth enforcement AT")

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
        # No auth headers
        resp = await client.get(
            "/sessions/nonexistent/mcp/files/download/content",
            params={"path": "/test.txt"},
        )
        assert resp.status_code in (401, 403), \
            f"Expected 401/403 without auth, got {resp.status_code}"


# ---------------------------------------------------------------------------
# 5. Upload roundtrip integrity
# ---------------------------------------------------------------------------
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-005")


@pytest.mark.asyncio
async def test_upload_download_roundtrip_content_integrity(env_file):
    """Upload binary content by value, download it, and verify byte-exact
    roundtrip integrity through the authenticated proxy."""
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    file_server_index = int(_require_cfg(cfg, "mcp.at1_10.file_server_index"))
    require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)
    file_root = str(_require_cfg(cfg, "mcp.at1_10.file_root")).rstrip("/")
    headers = api_headers(cfg)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    # Create content with mixed binary/text to verify integrity
    test_bytes = (f"Roundtrip integrity {ts}\n" + "x" * 1024).encode("utf-8")
    test_path = f"{file_root}/at_roundtrip_{ts}.bin"

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout_seconds) as client:
        session_resp = await client.post("/sessions", json={"metadata": {"suite": "AT_CHAT_FILE_ARTIFACT_DL"}})
        assert session_resp.status_code == 200
        session_id = session_resp.json()["session_id"]

        # Upload
        upload_payload = {
            "path": test_path,
            "content_base64": base64.b64encode(test_bytes).decode("ascii"),
            "server_index": file_server_index,
            "overwrite": True,
        }
        if require_initialize:
            upload_payload["require_initialize"] = True

        upload_resp = await client.post(f"/sessions/{session_id}/mcp/files/upload", json=upload_payload)
        assert upload_resp.status_code == 200
        assert upload_resp.json()["bytes_written"] == len(test_bytes)

        # Download via streaming endpoint
        download_params = {
            "path": test_path,
            "server_index": str(file_server_index),
            "download_name": f"at_roundtrip_{ts}.bin",
        }
        if require_initialize:
            download_params["require_initialize"] = "true"

        download_resp = await client.get(
            f"/sessions/{session_id}/mcp/files/download/content",
            params=download_params,
        )
        assert download_resp.status_code == 200
        downloaded_bytes = download_resp.content
        assert downloaded_bytes == test_bytes, \
            f"Roundtrip mismatch: uploaded {len(test_bytes)} bytes, downloaded {len(downloaded_bytes)} bytes"

        await client.delete(f"/sessions/{session_id}")
