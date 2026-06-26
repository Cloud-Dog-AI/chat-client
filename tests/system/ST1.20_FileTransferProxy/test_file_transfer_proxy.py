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
import contextlib
import os
import socket
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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


def _unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _isolated_sqlite_path() -> str:
    fd, path = tempfile.mkstemp(prefix="st120-chat-client-", suffix=".db")
    Path(path).unlink(missing_ok=True)
    return path


def _isolated_env_file(source_env: str, *, port: int, db_path: str) -> str:
    source = Path(source_env)
    fd, path = tempfile.mkstemp(prefix="st120-chat-client-", suffix=".env")
    os.close(fd)
    Path(path).write_text(
        source.read_text(encoding="utf-8").rstrip()
        + "\n"
        + "\n".join(
            [
                "CLOUD_DOG__API_SERVER__HOST=127.0.0.1",
                f"CLOUD_DOG__API_SERVER__PORT={port}",
                "CLOUD_DOG__CLIENT_API__HOST=127.0.0.1",
                f"CLOUD_DOG__CLIENT_API__PORT={port}",
                f"CLOUD_DOG__CLIENT_API__BASE_URL=http://127.0.0.1:{port}",
                f"CLOUD_DOG_DB__DATABASE={db_path}",
                f"CLOUD_DOG__DB__DATABASE={db_path}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class _StaticFileHandler(BaseHTTPRequestHandler):
    payload = b""
    content_type = "text/plain; charset=utf-8"

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/fixture.txt":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


@contextlib.contextmanager
def _serve_fixture(payload: bytes):
    class Handler(_StaticFileHandler):
        pass

    Handler.payload = payload
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/fixture.txt"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module", autouse=True)
def _servers(env_file):
    port = _unused_tcp_port()
    db_path = _isolated_sqlite_path()
    isolated_env = _isolated_env_file(env_file, port=port, db_path=db_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("CLOUD_DOG__API_SERVER__PORT", str(port))
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("CLOUD_DOG_DB__DATABASE", db_path)
    monkeypatch.setenv("CLOUD_DOG__DB__DATABASE", db_path)
    cfg = ConfigManager(env_file=isolated_env)
    started_file_mcp = maybe_start_file_mcp(cfg)
    start_api(cfg, env_file=isolated_env)
    try:
        wait_for_api(cfg)
        yield cfg
    finally:
        stop_api(cfg, env_file=isolated_env)
        if started_file_mcp:
            maybe_stop_file_mcp(cfg)
        monkeypatch.undo()
        Path(isolated_env).unlink(missing_ok=True)
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_20_file_transfer_proxy_roundtrip_and_errors(_servers):
    cfg = _servers
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    file_server_index = int(_require_cfg(cfg, "mcp.st1_20.file_server_index"))
    file_root = str(_require_cfg(cfg, "mcp.st1_20.file_root")).rstrip("/")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    multipart_path = f"{file_root}/st1_20_upload_{ts}.txt"
    url_path = f"{file_root}/st1_20_url_{ts}.txt"
    missing_path = f"{file_root}/st1_20_missing_{ts}.txt"
    multipart_bytes = b"st1.20 multipart upload payload\n"
    url_bytes = b"st1.20 fetched via source_url\n"

    async with httpx.AsyncClient(
        base_url=base_url,
        headers=api_headers(cfg),
        timeout=timeout_seconds,
    ) as client:
        create_resp = await client.post("/sessions", json={"metadata": {"suite": "st1.20"}})
        assert create_resp.status_code == 200
        session_id = str(create_resp.json().get("session_id") or "")
        assert session_id

        async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as unauth_client:
            unauth_resp = await unauth_client.post(
                f"/sessions/{session_id}/mcp/files/upload-multipart",
                data={"path": multipart_path, "server_index": str(file_server_index)},
                files={"file": ("unauth.txt", b"blocked", "text/plain")},
            )
        assert unauth_resp.status_code in {401, 403}

        upload_multipart = await client.post(
            f"/sessions/{session_id}/mcp/files/upload-multipart",
            data={"path": multipart_path, "server_index": str(file_server_index)},
            files={"file": ("roundtrip.txt", multipart_bytes, "text/plain")},
        )
        assert upload_multipart.status_code == 200
        upload_payload = upload_multipart.json()
        assert upload_payload["path"] == multipart_path
        assert int(upload_payload["bytes_written"]) == len(multipart_bytes)
        assert upload_payload["mcp_server_index"] == file_server_index

        download_json = await client.post(
            f"/sessions/{session_id}/mcp/files/download",
            json={"server_index": file_server_index, "path": multipart_path},
        )
        assert download_json.status_code == 200
        multipart_decoded = base64.b64decode(download_json.json()["content_base64"])
        assert multipart_decoded == multipart_bytes

        with _serve_fixture(url_bytes) as source_url:
            upload_url = await client.post(
                f"/sessions/{session_id}/mcp/files/upload",
                json={
                    "server_index": file_server_index,
                    "path": url_path,
                    "source_url": source_url,
                    "overwrite": True,
                },
            )
            assert upload_url.status_code == 200
            upload_url_payload = upload_url.json()
            assert upload_url_payload["path"] == url_path
            assert int(upload_url_payload["bytes_written"]) == len(url_bytes)
            assert upload_url_payload["mcp_server_index"] == file_server_index

        download_stream = await client.get(
            f"/sessions/{session_id}/mcp/files/download/content",
            params={
                "path": url_path,
                "server_index": str(file_server_index),
                "download_name": "downloaded-url.txt",
            },
        )
        assert download_stream.status_code == 200
        assert download_stream.content == url_bytes
        assert download_stream.headers["content-type"].startswith("text/plain")
        assert "downloaded-url.txt" in download_stream.headers.get("content-disposition", "")
        assert (
            download_stream.headers.get("x-mcp-server-index") == str(file_server_index)
        )

        missing_download = await client.get(
            f"/sessions/{session_id}/mcp/files/download/content",
            params={"path": missing_path, "server_index": str(file_server_index)},
        )
        assert missing_download.status_code == 404
