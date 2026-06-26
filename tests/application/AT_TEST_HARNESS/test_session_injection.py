# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited

from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest
import websockets

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import (
    a2a_base_url,
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
        wait_for_base_url(cfg, a2a_base_url(cfg))
        yield cfg
    finally:
        stop_all(cfg, env_file=env_file)


async def _recv_until(ws, predicate, *, limit: int = 12, timeout: float = 20.0):
    seen = []
    for _ in range(limit):
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        payload = json.loads(raw)
        seen.append(payload)
        if predicate(seen):
            return seen
    return seen
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")
@pytest.mark.req("FR-012")
@pytest.mark.req("FR-013")


@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_at_test_harness_injection_and_a2a_live_flow(_runtime):
    cfg = _runtime
    headers = api_headers(cfg)
    timeout = float(cfg.get("client_api.request_timeout_seconds") or 120)

    async with httpx.AsyncClient(base_url=api_base_url(cfg), timeout=timeout) as api_client:
        async with httpx.AsyncClient(base_url=a2a_base_url(cfg), timeout=timeout) as a2a_client:
            create = await api_client.post(
                "/sessions",
                json={"metadata": {"suite": "at-test-harness", "title": "Harness AT"}},
                headers=headers,
            )
            assert create.status_code == 200
            session_id = create.json()["session_id"]

            events_before = await a2a_client.get("/a2a/events", headers=headers)
            assert events_before.status_code == 200
            after_session_id = max(
                (
                    int(item.get("id") or 0)
                    for item in (events_before.json().get("events") or [])
                    if str(item.get("topic") or "") in {"sessions", "messages"}
                ),
                default=0,
            )

            ws_url = (
                a2a_base_url(cfg).replace("http://", "ws://", 1).replace("https://", "wss://", 1)
                + (
                    f"/a2a/ws?topics=sessions,messages"
                    f"&after_session_id={after_session_id}"
                    f"&api_key={cfg.get('client_api.api_key') or 'dev-key'}"
                )
            )

            async with websockets.connect(ws_url) as ws:
                injected = await api_client.post(
                    f"/v1/sessions/{session_id}/inject-sequence",
                    json={
                        "events": [
                            {"role": "user", "content": "Injected operator turn"},
                            {"role": "assistant", "content": "Injected harness reply"},
                        ]
                    },
                    headers=headers,
                )
                assert injected.status_code == 200
                assert injected.json()["injected_count"] == 2

                create_flow = await api_client.post(
                    "/v1/test-flows",
                    json={
                        "session_id": session_id,
                        "script": [
                            {"type": "pause", "label": "manual checkpoint"},
                            {
                                "type": "prompt",
                                "prompt": "Reply READY",
                                "expected_response": "READY",
                                "assistant_response": "Harness finished.",
                            },
                        ],
                    },
                    headers=headers,
                )
                assert create_flow.status_code == 200
                flow_id = create_flow.json()["flow"]["flow_id"]
                assert create_flow.json()["flow"]["status"] == "paused"

                flow_continue = await api_client.post(
                    f"/v1/test-flows/{flow_id}/continue",
                    headers=headers,
                )
                assert flow_continue.status_code == 200
                assert flow_continue.json()["flow"]["status"] == "awaiting_response"

                flow_respond = await api_client.post(
                    f"/v1/test-flows/{flow_id}/respond",
                    json={"content": "READY"},
                    headers=headers,
                )
                assert flow_respond.status_code == 200
                assert flow_respond.json()["flow"]["status"] == "completed"

                received = await _recv_until(
                    ws,
                    lambda items: (
                        any(
                            str(item.get("session_id") or "") == session_id
                            and str(item.get("topic") or "") == "messages"
                            and str((item.get("data") or {}).get("content") or "") == "Injected harness reply"
                            for item in items
                        )
                        and any(
                            str(item.get("session_id") or "") == session_id
                            and str(item.get("topic") or "") == "sessions"
                            and str(item.get("event_type") or "") == "test_flow_completed"
                            for item in items
                        )
                        and any(
                            str(item.get("session_id") or "") == session_id
                            and str(item.get("topic") or "") == "messages"
                            and str((item.get("data") or {}).get("content") or "") == "Reply READY"
                            for item in items
                        )
                    ),
                    limit=20,
                )

            assert any(
                str(item.get("session_id") or "") == session_id
                and str(item.get("topic") or "") == "messages"
                and str((item.get("data") or {}).get("content") or "") == "Reply READY"
                for item in received
            )
            assert any(
                str(item.get("session_id") or "") == session_id
                and str(item.get("topic") or "") == "sessions"
                and str(item.get("event_type") or "") == "test_flow_paused"
                for item in received
            )

            transcript = await api_client.get(
                f"/sessions/{session_id}/transcript",
                headers=headers,
            )
            assert transcript.status_code == 200
            contents = [
                str((item.get("data") or {}).get("content") or "")
                for item in (transcript.json().get("events") or [])
                if item.get("event_type") in {"user_message", "assistant_message"}
            ]
            assert "Injected operator turn" in contents
            assert "Injected harness reply" in contents
            assert "Reply READY" in contents
            assert "READY" in contents
            assert "Harness finished." in contents


pytestmark = [pytest.mark.application, pytest.mark.pure, pytest.mark.slow]
