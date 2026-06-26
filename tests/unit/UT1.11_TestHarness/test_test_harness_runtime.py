# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited

from __future__ import annotations

import pytest
from cloud_dog_chat_client.session.transcript import TranscriptEvent
from cloud_dog_chat_client.test_harness.runtime import TestFlowRuntime as _TestFlowRuntime


def _runtime_fixture():
    sessions: dict[str, dict] = {}
    audits: list[dict] = []
    injected: list[dict] = []
    appended_events: list[tuple[str, TranscriptEvent]] = []
    counter = {"value": 0}

    def create_session(metadata: dict) -> str:
        counter["value"] += 1
        session_id = f"session-{counter['value']}"
        sessions[session_id] = {"id": session_id, "metadata": dict(metadata)}
        return session_id

    def get_session(session_id: str) -> dict:
        if session_id not in sessions:
            raise KeyError(session_id)
        return sessions[session_id]

    def update_session_metadata(session_id: str, metadata: dict) -> dict:
        session = get_session(session_id)
        merged = dict(session.get("metadata") or {})
        merged.update(dict(metadata or {}))
        session["metadata"] = merged
        return merged

    def append_event(session_id: str, event: TranscriptEvent) -> None:
        appended_events.append((session_id, event))

    def inject_message(**kwargs) -> dict:
        injected.append(dict(kwargs))
        return dict(kwargs)

    def audit(**kwargs) -> None:
        audits.append(dict(kwargs))

    runtime = _TestFlowRuntime(
        create_session=create_session,
        get_session=get_session,
        update_session_metadata=update_session_metadata,
        append_event=append_event,
        inject_message=inject_message,
        audit=audit,
    )
    return runtime, sessions, audits, injected, appended_events
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")
@pytest.mark.req("FR-012")


def test_ut1_11_test_flow_runtime_pause_continue_and_complete() -> None:
    runtime, sessions, audits, injected, appended_events = _runtime_fixture()

    created = runtime.create_flow(
        script=[
            {"type": "inject", "role": "assistant", "content": "Harness ready."},
            {"type": "pause", "label": "operator review"},
            {
                "type": "prompt",
                "prompt": "Respond with ACK",
                "expected_response": "ACK",
                "assistant_response": "Acknowledged.",
            },
        ],
        metadata={"suite": "ut1.11"},
        actor="tester",
        request_id="req-1",
    )

    session_id = created["session_id"]
    assert created["status"] == "paused"
    assert sessions[session_id]["metadata"]["active_test_flow_id"] == created["flow_id"]
    assert injected[0]["content"] == "Harness ready."
    assert appended_events[0][1].event_type == "test_flow_started"
    assert appended_events[1][1].event_type == "test_flow_paused"

    continued = runtime.continue_flow(created["flow_id"], actor="tester", request_id="req-2")
    assert continued["status"] == "awaiting_response"
    assert continued["pending_prompt"] == "Respond with ACK"
    assert injected[1]["role"] == "assistant"
    assert injected[1]["content"] == "Respond with ACK"

    completed = runtime.respond_flow(
        created["flow_id"],
        content="ACK",
        actor="tester",
        request_id="req-3",
    )
    assert completed["status"] == "completed"
    assert completed["waiting_for"] is None
    assert sessions[session_id]["metadata"]["active_test_flow_id"] == ""
    assert sessions[session_id]["metadata"]["last_completed_test_flow_id"] == created["flow_id"]
    assert injected[2]["role"] == "user"
    assert injected[2]["content"] == "ACK"
    assert injected[3]["role"] == "assistant"
    assert injected[3]["content"] == "Acknowledged."
    assert appended_events[-1][1].event_type == "test_flow_completed"
    assert any(item.get("action") == "test_flow_completed" for item in audits)
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")
@pytest.mark.req("FR-012")


def test_ut1_11_test_flow_runtime_marks_mismatch_as_failed() -> None:
    runtime, sessions, _audits, injected, appended_events = _runtime_fixture()
    sessions["session-existing"] = {"id": "session-existing", "metadata": {}}

    created = runtime.create_flow(
        script=[
            {
                "type": "prompt",
                "content": "Type YES",
                "expected_response": "YES",
            }
        ],
        session_id="session-existing",
        actor="tester",
        request_id="req-10",
    )
    assert created["status"] == "awaiting_response"

    failed = runtime.respond_flow(
        created["flow_id"],
        content="NO",
        actor="tester",
        request_id="req-11",
    )
    assert failed["status"] == "failed"
    assert sessions["session-existing"]["metadata"]["last_failed_test_flow_id"] == created["flow_id"]
    assert injected[-1]["content"] == "NO"
    assert appended_events[-1][1].event_type == "test_flow_failed"


pytestmark = [pytest.mark.unit, pytest.mark.pure, pytest.mark.fast]
