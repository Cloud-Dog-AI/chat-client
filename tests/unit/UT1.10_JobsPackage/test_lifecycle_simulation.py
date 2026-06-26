# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
# W28A-679 — PS-75 lifecycle simulation tests.
# Covers: R7.1, R14, PS-75 JQ4, JQ7, JQ8

from __future__ import annotations

from pathlib import Path

from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.jobs import JobsRuntime
import pytest


def _make_runtime(tmp_path: Path, db_name: str = "lifecycle.sqlite3") -> JobsRuntime:
    cfg = ConfigManager(
        env_file="tests/env-UT",
        overrides={
            "app.server_id": "lifecycle-test-server",
            "db.database": str(tmp_path / db_name),
            "cloud_dog_db.database": str(tmp_path / db_name),
            "jobs.retry.max_attempts": "3",
            "jobs.dead_letter.enabled": "true",
            "jobs.dead_letter.queue_name": "dead_letter",
        },
    )
    return JobsRuntime.from_config(cfg)
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-002")


def test_lifecycle_create_queue_run_succeed(tmp_path: Path):
    """create -> queued -> running -> succeeded"""
    rt = _make_runtime(tmp_path, "lifecycle_succeed.sqlite3")

    job_id = rt.create_job(
        job_type="mcp_proxy_tools_call",
        payload={"name": "search", "arguments": {"q": "hello"}},
        session_id="s1",
        correlation_id="c1",
        user_id="u1",
    )
    job = rt.get_job(job_id)
    assert job is not None
    assert job["status"] == "queued"

    rt.mark_running(job_id, worker_id="w1")
    job = rt.get_job(job_id)
    assert job["status"] == "running"

    rt.complete(job_id, result={"ok": True})
    job = rt.get_job(job_id)
    assert job["status"] == "succeeded"
    assert job["payload"]["result"]["ok"] is True
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-002")


def test_lifecycle_create_queue_run_fail_retry_wait(tmp_path: Path):
    """create -> queued -> running -> fail (retryable) -> retry_wait"""
    rt = _make_runtime(tmp_path, "lifecycle_retry.sqlite3")

    job_id = rt.create_job(
        job_type="mcp_proxy_execute",
        payload={"steps": 1},
        session_id="s2",
    )
    assert rt.get_job(job_id)["status"] == "queued"

    rt.mark_running(job_id, worker_id="w1")
    assert rt.get_job(job_id)["status"] == "running"

    # First attempt fails with retryable=True — transitions to retry_wait
    rt.fail(job_id, error="upstream timeout", retryable=True)
    job = rt.get_job(job_id)
    assert job["status"] == "retry_wait"
    assert "upstream timeout" in job["payload"].get("retry_reason", "")
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-002")


def test_lifecycle_create_queue_cancel(tmp_path: Path):
    """create -> queued -> cancelled"""
    rt = _make_runtime(tmp_path, "lifecycle_cancel.sqlite3")

    job_id = rt.create_job(
        job_type="mcp_proxy_tools_list",
        payload={"server_index": 0},
        session_id="s3",
    )
    assert rt.get_job(job_id)["status"] == "queued"

    ok = rt.cancel(job_id, reason="user requested cancellation")
    assert ok is True
    job = rt.get_job(job_id)
    assert job["status"] == "cancelled"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-002")


def test_lifecycle_create_queue_run_fail_terminal(tmp_path: Path):
    """create -> queued -> running -> failed (non-retryable terminal)"""
    rt = _make_runtime(tmp_path, "lifecycle_fail.sqlite3")

    job_id = rt.create_job(
        job_type="mcp_proxy_execute",
        payload={"steps": 1},
        session_id="s4",
    )
    rt.mark_running(job_id, worker_id="w1")

    rt.fail(job_id, error="fatal error", retryable=False)
    job = rt.get_job(job_id)
    assert job["status"] == "failed"
    assert "fatal error" in job["payload"]["error"]
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-002")


def test_lifecycle_non_retryable_fail_is_terminal(tmp_path: Path):
    """create -> queued -> running -> fail (non-retryable) stays terminal even with dead_letter enabled"""
    rt = _make_runtime(tmp_path, "lifecycle_deadletter.sqlite3")

    job_id = rt.create_job(
        job_type="mcp_proxy_execute",
        payload={"steps": 1},
        session_id="s5",
    )
    rt.mark_running(job_id, worker_id="w1")

    # Non-retryable failure goes directly to terminal failed state
    rt.fail(job_id, error="permanent error", retryable=False)
    job = rt.get_job(job_id)
    assert job["status"] in ("failed", "dead_lettered")
