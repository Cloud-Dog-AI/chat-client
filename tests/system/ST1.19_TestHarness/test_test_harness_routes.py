# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited

from __future__ import annotations

import os

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import (
    api_base_url,
    api_headers,
    start_all,
    stop_all,
    wait_for_api,
    wait_for_base_url,
    web_base_url,
)


@pytest.fixture(scope="module", autouse=True)
def _runtime(env_file):
    os.environ["CLOUD_DOG__CLIENT_API__API_KEY_HEADER"] = "X-API-Key"
    os.environ["CLOUD_DOG__CLIENT_API__API_KEY"] = "dev-key"
    cfg = ConfigManager(env_file=env_file)
    start_all(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        wait_for_base_url(cfg, web_base_url(cfg))
        yield cfg
    finally:
        stop_all(cfg, env_file=env_file)
@pytest.mark.ST
@pytest.mark.api
@pytest.mark.req("FR-009")
@pytest.mark.req("FR-012")
@pytest.mark.req("FR-013")


@pytest.mark.asyncio
async def test_st1_19_test_harness_routes_and_flow(_runtime):
    cfg = _runtime
    headers = api_headers(cfg)
    timeout = float(cfg.get("client_api.request_timeout_seconds") or 60)

    async with httpx.AsyncClient(base_url=api_base_url(cfg), timeout=timeout) as client:
        create = await client.post(
            "/sessions",
            json={"metadata": {"suite": "st1.19", "title": "Harness Session"}},
            headers=headers,
        )
        assert create.status_code == 200
        session_id = create.json()["session_id"]

        injected = await client.post(
            f"/v1/sessions/{session_id}/inject",
            json={"role": "assistant", "content": "Harness seeded message."},
            headers=headers,
        )
        assert injected.status_code == 200
        assert injected.json()["role"] == "assistant"

        sequence = await client.post(
            f"/v1/sessions/{session_id}/inject-sequence",
            json={
                "events": [
                    {"role": "user", "content": "Operator hello"},
                    {"role": "assistant", "content": "Harness acknowledged"},
                ]
            },
            headers=headers,
        )
        assert sequence.status_code == 200
        assert sequence.json()["injected_count"] == 2

        flow_create = await client.post(
            "/v1/test-flows",
            json={
                "session_id": session_id,
                "script": [
                    {"type": "pause", "label": "review gate"},
                    {
                        "type": "prompt",
                        "prompt": "Type GO",
                        "expected_response": "GO",
                        "assistant_response": "Proceeding.",
                    },
                ],
            },
            headers=headers,
        )
        assert flow_create.status_code == 200
        flow = flow_create.json()["flow"]
        flow_id = flow["flow_id"]
        assert flow["status"] == "paused"

        flow_continue = await client.post(
            f"/v1/test-flows/{flow_id}/continue",
            headers=headers,
        )
        assert flow_continue.status_code == 200
        assert flow_continue.json()["flow"]["status"] == "awaiting_response"

        flow_respond = await client.post(
            f"/v1/test-flows/{flow_id}/respond",
            json={"content": "GO"},
            headers=headers,
        )
        assert flow_respond.status_code == 200
        assert flow_respond.json()["flow"]["status"] == "completed"

        transcript = await client.get(
            f"/sessions/{session_id}/transcript",
            headers=headers,
        )
        assert transcript.status_code == 200
        events = transcript.json()["events"]
        contents = [
            str((item.get("data") or {}).get("content") or "")
            for item in events
            if item.get("event_type") in {"user_message", "assistant_message"}
        ]
        assert "Harness seeded message." in contents
        assert "Operator hello" in contents
        assert "Harness acknowledged" in contents
        assert "Type GO" in contents
        assert "GO" in contents
        assert "Proceeding." in contents

        ui_cfg = await client.get("/ui/config")
        assert ui_cfg.status_code == 200
        assert ui_cfg.json()["test_harness"]["enabled"] is True


pytestmark = [pytest.mark.system, pytest.mark.pure, pytest.mark.slow]
