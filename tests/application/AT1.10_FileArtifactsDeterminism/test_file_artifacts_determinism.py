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
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_10_file_artifacts_determinism(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    file_server_index = int(_require_cfg(cfg, "mcp.at1_10.file_server_index"))
    require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)
    file_root = str(_require_cfg(cfg, "mcp.at1_10.file_root")).rstrip("/")
    pdf_base64 = str(_require_cfg(cfg, "mcp.at1_10.pdf_base64"))
    min_md_bytes = int(_require_cfg(cfg, "mcp.at1_10.min_md_bytes"))
    min_html_bytes = int(_require_cfg(cfg, "mcp.at1_10.min_html_bytes"))
    min_pdf_bytes = int(_require_cfg(cfg, "mcp.at1_10.min_pdf_bytes"))

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    md_path = f"{file_root}/at1_10_artifact_{ts}.md"
    html_path = f"{file_root}/at1_10_artifact_{ts}.html"
    pdf_path = f"{file_root}/at1_10_artifact_{ts}.pdf"
    expected_names = [
        f"at1_10_artifact_{ts}.md",
        f"at1_10_artifact_{ts}.html",
        f"at1_10_artifact_{ts}.pdf",
    ]

    md_text = f"# Deterministic Artifact\n\nTimestamp: {ts}\n"
    html_text = f"<!doctype html><html><body><h1>{ts}</h1></body></html>"
    pdf_bytes = base64.b64decode(pdf_base64)

    async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
        session_resp = await client.post("/sessions", json={"metadata": {"suite": "at1.10"}})
        assert session_resp.status_code == 200
        session_id = str(session_resp.json().get("session_id") or "")
        assert session_id

        for path, raw in ((md_path, md_text.encode("utf-8")), (html_path, html_text.encode("utf-8")), (pdf_path, pdf_bytes)):
            upload_resp = await client.post(
                f"/sessions/{session_id}/mcp/files/upload",
                json={
                    "server_index": file_server_index,
                    "path": path,
                    "content_base64": base64.b64encode(raw).decode("ascii"),
                    "overwrite": True,
                    "require_initialize": require_initialize,
                },
            )
            assert upload_resp.status_code == 200

        paths_resp = await client.post(
            f"/sessions/{session_id}/mcp/tools/call",
            json={
                "server_index": file_server_index,
                "name": "search_paths",
                "arguments": {"query": f"at1_10_artifact_{ts}", "max_depth": 8},
                "require_initialize": require_initialize,
            },
        )
        assert paths_resp.status_code == 200
        text = str(paths_resp.json())
        for expected in expected_names:
            if expected not in text:
                raise RuntimeError(f"CRITICAL ERROR: expected timestamped artifact missing: {expected}")

        md_download = await client.post(
            f"/sessions/{session_id}/mcp/files/download",
            json={"server_index": file_server_index, "path": md_path, "require_initialize": require_initialize},
        )
        html_download = await client.post(
            f"/sessions/{session_id}/mcp/files/download",
            json={"server_index": file_server_index, "path": html_path, "require_initialize": require_initialize},
        )
        pdf_download = await client.post(
            f"/sessions/{session_id}/mcp/files/download",
            json={"server_index": file_server_index, "path": pdf_path, "require_initialize": require_initialize},
        )
        assert md_download.status_code == 200
        assert html_download.status_code == 200
        assert pdf_download.status_code == 200

        md_bytes = base64.b64decode(str((md_download.json() or {}).get("content_base64") or ""))
        html_bytes = base64.b64decode(str((html_download.json() or {}).get("content_base64") or ""))
        pdf_bytes_out = base64.b64decode(str((pdf_download.json() or {}).get("content_base64") or ""))

        if len(md_bytes) < min_md_bytes:
            raise RuntimeError("CRITICAL ERROR: markdown artifact below minimum byte threshold")
        if len(html_bytes) < min_html_bytes:
            raise RuntimeError("CRITICAL ERROR: html artifact below minimum byte threshold")
        if len(pdf_bytes_out) < min_pdf_bytes:
            raise RuntimeError("CRITICAL ERROR: pdf artifact below minimum byte threshold")

        if b"# Deterministic Artifact" not in md_bytes:
            raise RuntimeError("CRITICAL ERROR: markdown artifact content mismatch")
        if b"<html" not in html_bytes.lower():
            raise RuntimeError("CRITICAL ERROR: html artifact content mismatch")
        if not pdf_bytes_out.startswith(b"%PDF"):
            raise RuntimeError("CRITICAL ERROR: pdf artifact missing PDF signature")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.mcp, pytest.mark.heavy]

