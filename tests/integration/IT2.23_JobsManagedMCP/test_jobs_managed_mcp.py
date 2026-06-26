# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
# Covers: R7.1, R14

from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud_dog_chat_client.api.server import create_app
from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.jobs import JobsRuntime


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _fake_http_jsonrpc_mcp_server():
    app = FastAPI()
    port = _free_port()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/messages")
    async def messages(payload: dict):
        method = str(payload.get("method") or "")
        req_id = payload.get("id", 1)
        if method == "tools/call":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "isError": False,
                    "content": [{"type": "text", "text": "ok"}],
                    "structuredContent": {"ok": True, "source": "fake-mcp"},
                },
            }
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": [{"name": "search"}]},
            }
        return {"jsonrpc": "2.0", "id": req_id, "result": {"ok": True}}

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/health", timeout=1.0)
            if response.status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        server.should_exit = True
        thread.join(timeout=5.0)
        raise RuntimeError("fake MCP server failed to start")

    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
@pytest.mark.IT
@pytest.mark.mcp
@pytest.mark.req("FR-002")


@pytest.mark.integration
def test_it2_23_mcp_proxy_tool_call_is_tracked_as_managed_job(tmp_path: Path):
    with _fake_http_jsonrpc_mcp_server() as base_url:
        db_path = tmp_path / "it_jobs_managed_mcp.sqlite3"
        cfg = ConfigManager(
            env_file="tests/env-UT",
            overrides={
                "app.server_id": "it-jobs-server",
                "client_api.api_key_header": "X-API-Key",
                "client_api.api_key": "dev-key",
                "client_api.admin_api_key_header": "X-API-Key",
                "client_api.admin_api_key": "dev-key",
                "db.database": str(db_path),
                "cloud_dog_db.database": str(db_path),
                "mcp.servers.0.name": "fake-jobs-mcp",
                "mcp.servers.0.transport": "http_jsonrpc",
                "mcp.servers.0.base_url": base_url,
                "mcp.servers.0.messages_path": "/messages",
                "mcp.servers.0.health_path": "/health",
                "mcp.servers.0.timeout_seconds": 30,
                "jobs.enabled": True,
            },
        )

        headers = {"X-API-Key": "dev-key"}
        with TestClient(create_app(cfg)) as client:
            create = client.post("/sessions", json={"metadata": {}}, headers=headers)
            assert create.status_code == 200
            session_id = str(create.json()["session_id"])

            call = client.post(
                f"/sessions/{session_id}/mcp/tools/call",
                json={"server_index": 0, "name": "search", "arguments": {"q": "hello"}},
                headers=headers,
            )
            assert call.status_code == 200
            assert call.json()["structuredContent"]["ok"] is True

            job_id = str(call.headers.get("X-Job-Id") or "").strip()
            assert job_id

            job = client.get(f"/v1/jobs/{job_id}", headers=headers)
            assert job.status_code == 200
            payload = job.json()
            assert payload["job_id"] == job_id
            assert payload["status"] == "succeeded"
            assert payload["session_id"] == session_id
            assert payload["server_id"] == "it-jobs-server"

            listed = client.get(f"/v1/jobs?session_id={session_id}", headers=headers)
            assert listed.status_code == 200
            listed_payload = listed.json()
            assert int(listed_payload["count"]) >= 1
            assert any(item["job_id"] == job_id for item in listed_payload["jobs"])


@pytest.mark.IT
@pytest.mark.api
@pytest.mark.negative
@pytest.mark.req("FR-002")
@pytest.mark.integration
def test_it2_23_jobs_api_denies_unauth_and_cancels_authorised_queued_job(tmp_path: Path):
    db_path = tmp_path / "it_jobs_cancel.sqlite3"
    cfg = ConfigManager(
        env_file="tests/env-UT",
        overrides={
            "app.server_id": "it-jobs-cancel-server",
            "client_api.api_key_header": "X-API-Key",
            "client_api.api_key": "dev-key",
            "client_api.admin_api_key_header": "X-API-Key",
            "client_api.admin_api_key": "dev-key",
            "db.database": str(db_path),
            "cloud_dog_db.database": str(db_path),
            "jobs.enabled": True,
        },
    )
    seeded_runtime = JobsRuntime.from_config(cfg)
    queued_job_id = seeded_runtime.create_job(
        job_type="mcp_proxy_tools_call",
        payload={"server_index": 0, "method": "tools/call"},
        session_id="session-cancel",
        correlation_id="corr-cancel",
        user_id="user-cancel",
    )

    headers = {"X-API-Key": "dev-key"}
    with TestClient(create_app(cfg)) as client:
        unauth_list = client.get("/v1/jobs")
        assert unauth_list.status_code == 401

        unauth_cancel = client.post(f"/v1/jobs/{queued_job_id}/cancel")
        assert unauth_cancel.status_code == 401

        cancelled = client.post(
            f"/v1/jobs/{queued_job_id}/cancel",
            params={"reason": "integration negative/control proof"},
            headers=headers,
        )
        assert cancelled.status_code == 200
        assert cancelled.json() == {"job_id": queued_job_id, "status": "cancelled"}

        job = client.get(f"/v1/jobs/{queued_job_id}", headers=headers)
        assert job.status_code == 200
        payload = job.json()
        assert payload["status"] == "cancelled"
        assert payload["payload"]["cancel_reason"] == "integration negative/control proof"
