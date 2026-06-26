# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
# Covers: R7.1, R14

from __future__ import annotations

from pathlib import Path

from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.jobs import JobsRuntime
import pytest
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-002")


def test_ut1_9_jobs_runtime_lifecycle(tmp_path: Path):
    cfg = ConfigManager(
        env_file="tests/env-UT",
        overrides={
            "app.server_id": "ut-jobs-server",
            "db.database": str(tmp_path / "ut_jobs.sqlite3"),
            "cloud_dog_db.database": str(tmp_path / "ut_jobs.sqlite3"),
        },
    )

    runtime = JobsRuntime.from_config(cfg)
    job_id = runtime.create_job(
        job_type="mcp_proxy_tools_call",
        payload={"name": "search", "arguments": {"q": "test"}},
        session_id="session-ut-1",
        correlation_id="corr-ut-1",
        user_id="user-ut-1",
    )

    created = runtime.get_job(job_id)
    assert created is not None
    assert created["status"] == "queued"
    assert created["server_id"] == "ut-jobs-server"
    assert created["session_id"] == "session-ut-1"

    runtime.mark_running(job_id, worker_id="worker-ut")
    running = runtime.get_job(job_id)
    assert running is not None
    assert running["status"] == "running"

    runtime.complete(job_id, result={"ok": True, "items": 1})
    completed = runtime.get_job(job_id)
    assert completed is not None
    assert completed["status"] == "succeeded"
    assert completed["payload"]["result"]["ok"] is True
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-002")


def test_ut1_9_jobs_runtime_failure_and_filtering(tmp_path: Path):
    cfg = ConfigManager(
        env_file="tests/env-UT",
        overrides={
            "app.server_id": "ut-jobs-server",
            "db.database": str(tmp_path / "ut_jobs_fail.sqlite3"),
            "cloud_dog_db.database": str(tmp_path / "ut_jobs_fail.sqlite3"),
        },
    )
    runtime = JobsRuntime.from_config(cfg)

    ok_job_id = runtime.create_job(
        job_type="mcp_proxy_tools_list",
        payload={"server_index": 0},
        session_id="session-a",
    )
    failed_job_id = runtime.create_job(
        job_type="mcp_proxy_execute",
        payload={"steps": 2},
        session_id="session-b",
    )

    runtime.mark_running(ok_job_id, worker_id="worker-ut")
    runtime.complete(ok_job_id, result={"tool_count": 3})

    runtime.mark_running(failed_job_id, worker_id="worker-ut")
    runtime.fail(failed_job_id, error="upstream timeout")

    failed_only = runtime.list_jobs(session_id="session-b", status="failed")
    assert len(failed_only) == 1
    assert failed_only[0]["job_id"] == failed_job_id
    assert failed_only[0]["payload"]["error"] == "upstream timeout"

    all_jobs = runtime.list_jobs(limit=10)
    returned_ids = {item["job_id"] for item in all_jobs}
    assert {ok_job_id, failed_job_id} <= returned_ids
