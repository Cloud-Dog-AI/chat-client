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

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from ..session.transcript import TranscriptEvent


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TestFlowState:
    flow_id: str
    session_id: str
    script: list[dict[str, Any]]
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    status: str = "running"
    current_step: int = 0
    waiting_for: str = ""
    pending_prompt: str = ""
    results: list[dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "session_id": self.session_id,
            "status": self.status,
            "current_step": int(self.current_step),
            "total_steps": len(self.script),
            "waiting_for": self.waiting_for or None,
            "pending_prompt": self.pending_prompt or None,
            "results": list(self.results),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "cancelled": bool(self.cancelled),
        }


class TestFlowRuntime:
    """In-process scripted test-flow engine backed by real session events."""

    def __init__(
        self,
        *,
        create_session: Callable[[dict[str, Any]], str],
        get_session: Callable[[str], dict[str, Any]],
        update_session_metadata: Callable[[str, dict[str, Any]], dict[str, Any]],
        append_event: Callable[[str, TranscriptEvent], None],
        inject_message: Callable[..., dict[str, Any]],
        audit: Callable[..., None],
    ) -> None:
        self._create_session = create_session
        self._get_session = get_session
        self._update_session_metadata = update_session_metadata
        self._append_event = append_event
        self._inject_message = inject_message
        self._audit = audit
        self._flows: dict[str, TestFlowState] = {}
        self._lock = RLock()

    def create_flow(
        self,
        *,
        script: list[dict[str, Any]],
        session_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        actor: str,
        request_id: str,
    ) -> dict[str, Any]:
        if not isinstance(script, list) or not script:
            raise ValueError("script must contain at least one step")
        flow_id = str(uuid4())
        if session_id:
            self._get_session(session_id)
        else:
            session_meta = dict(metadata or {})
            session_meta.setdefault("title", "Test Flow Session")
            session_meta["active_test_flow_id"] = flow_id
            session_meta["test_harness"] = True
            session_id = self._create_session(session_meta)

        with self._lock:
            flow = TestFlowState(
                flow_id=flow_id,
                session_id=session_id,
                script=[dict(item or {}) for item in script],
            )
            self._flows[flow_id] = flow
            self._update_session_metadata(
                session_id,
                {"active_test_flow_id": flow_id, "test_harness": True},
            )
            self._append_event(
                session_id,
                TranscriptEvent(
                    event_type="test_flow_started",
                    data={"flow_id": flow_id, "steps": len(flow.script)},
                ),
            )
            self._audit(
                action="test_flow_created",
                session_id=session_id,
                request_id=request_id,
                actor=actor,
                detail={"flow_id": flow_id, "steps": len(flow.script)},
            )
            self._advance_locked(flow, actor=actor, request_id=request_id)
            return flow.snapshot()

    def get_flow(self, flow_id: str) -> dict[str, Any]:
        with self._lock:
            flow = self._require_flow_locked(flow_id)
            return flow.snapshot()

    def continue_flow(self, flow_id: str, *, actor: str, request_id: str) -> dict[str, Any]:
        with self._lock:
            flow = self._require_flow_locked(flow_id)
            if flow.status != "paused":
                raise ValueError("flow is not paused")
            flow.current_step += 1
            flow.status = "running"
            flow.waiting_for = ""
            flow.pending_prompt = ""
            flow.updated_at = _utcnow()
            self._audit(
                action="test_flow_continued",
                session_id=flow.session_id,
                request_id=request_id,
                actor=actor,
                detail={"flow_id": flow_id, "current_step": flow.current_step},
            )
            self._advance_locked(flow, actor=actor, request_id=request_id)
            return flow.snapshot()

    def respond_flow(
        self,
        flow_id: str,
        *,
        content: str,
        actor: str,
        request_id: str,
    ) -> dict[str, Any]:
        response = str(content or "").strip()
        if not response:
            raise ValueError("response content must be non-empty")
        with self._lock:
            flow = self._require_flow_locked(flow_id)
            if flow.status != "awaiting_response":
                raise ValueError("flow is not awaiting a response")
            step = flow.script[flow.current_step]
            expected = str(step.get("expected_response") or "").strip()
            matched = (not expected) or response == expected
            self._inject_message(
                session_id=flow.session_id,
                role="user",
                content=response,
                actor=actor,
                request_id=request_id,
                source="test_flow_response",
                flow_id=flow_id,
            )
            assistant_response = str(step.get("assistant_response") or "").strip()
            if assistant_response:
                self._inject_message(
                    session_id=flow.session_id,
                    role="assistant",
                    content=assistant_response,
                    actor=actor,
                    request_id=request_id,
                    source="test_flow_follow_up",
                    flow_id=flow_id,
                )

            flow.results.append(
                {
                    "step_index": flow.current_step,
                    "type": "prompt",
                    "prompt": str(step.get("content") or step.get("prompt") or ""),
                    "response": response,
                    "expected_response": expected or None,
                    "matched": matched,
                }
            )
            flow.current_step += 1
            flow.waiting_for = ""
            flow.pending_prompt = ""
            flow.updated_at = _utcnow()
            self._audit(
                action="test_flow_responded",
                session_id=flow.session_id,
                request_id=request_id,
                actor=actor,
                detail={"flow_id": flow_id, "matched": matched},
            )
            if expected and not matched:
                self._fail_locked(
                    flow,
                    actor=actor,
                    request_id=request_id,
                    reason="response_mismatch",
                )
                return flow.snapshot()
            flow.status = "running"
            self._advance_locked(flow, actor=actor, request_id=request_id)
            return flow.snapshot()

    def cancel_flow(self, flow_id: str, *, actor: str, request_id: str) -> dict[str, Any]:
        with self._lock:
            flow = self._require_flow_locked(flow_id)
            flow.cancelled = True
            flow.status = "cancelled"
            flow.waiting_for = ""
            flow.pending_prompt = ""
            flow.updated_at = _utcnow()
            self._update_session_metadata(
                flow.session_id,
                {"active_test_flow_id": "", "last_cancelled_test_flow_id": flow_id},
            )
            self._append_event(
                flow.session_id,
                TranscriptEvent(
                    event_type="test_flow_cancelled",
                    data={"flow_id": flow_id},
                ),
            )
            self._audit(
                action="test_flow_cancelled",
                session_id=flow.session_id,
                request_id=request_id,
                actor=actor,
                detail={"flow_id": flow_id},
            )
            return flow.snapshot()

    def _require_flow_locked(self, flow_id: str) -> TestFlowState:
        flow = self._flows.get(str(flow_id or "").strip())
        if flow is None:
            raise KeyError(f"Unknown test flow: {flow_id}")
        return flow

    def _advance_locked(self, flow: TestFlowState, *, actor: str, request_id: str) -> None:
        while flow.current_step < len(flow.script):
            step = flow.script[flow.current_step]
            step_type = str(step.get("type") or "inject").strip().lower()

            if step_type in {"inject", "message"}:
                role = str(step.get("role") or "").strip().lower()
                content = str(step.get("content") or "").strip()
                self._inject_message(
                    session_id=flow.session_id,
                    role=role,
                    content=content,
                    actor=actor,
                    request_id=request_id,
                    source="test_flow_inject",
                    flow_id=flow.flow_id,
                )
                flow.results.append(
                    {
                        "step_index": flow.current_step,
                        "type": "inject",
                        "role": role,
                        "content": content,
                    }
                )
                flow.current_step += 1
                flow.updated_at = _utcnow()
                continue

            if step_type == "pause":
                flow.status = "paused"
                flow.waiting_for = "continue"
                flow.pending_prompt = ""
                flow.updated_at = _utcnow()
                self._append_event(
                    flow.session_id,
                    TranscriptEvent(
                        event_type="test_flow_paused",
                        data={
                            "flow_id": flow.flow_id,
                            "step_index": flow.current_step,
                            "label": str(step.get("label") or step.get("content") or ""),
                        },
                    ),
                )
                self._audit(
                    action="test_flow_paused",
                    session_id=flow.session_id,
                    request_id=request_id,
                    actor=actor,
                    detail={"flow_id": flow.flow_id, "step_index": flow.current_step},
                )
                return

            if step_type == "prompt":
                prompt = str(step.get("content") or step.get("prompt") or "").strip()
                if not prompt:
                    self._fail_locked(
                        flow,
                        actor=actor,
                        request_id=request_id,
                        reason="prompt_missing_content",
                    )
                    return
                self._inject_message(
                    session_id=flow.session_id,
                    role="assistant",
                    content=prompt,
                    actor=actor,
                    request_id=request_id,
                    source="test_flow_prompt",
                    flow_id=flow.flow_id,
                )
                flow.status = "awaiting_response"
                flow.waiting_for = "respond"
                flow.pending_prompt = prompt
                flow.updated_at = _utcnow()
                self._append_event(
                    flow.session_id,
                    TranscriptEvent(
                        event_type="test_flow_prompt",
                        data={
                            "flow_id": flow.flow_id,
                            "step_index": flow.current_step,
                            "prompt": prompt,
                        },
                    ),
                )
                self._audit(
                    action="test_flow_prompt",
                    session_id=flow.session_id,
                    request_id=request_id,
                    actor=actor,
                    detail={"flow_id": flow.flow_id, "step_index": flow.current_step},
                )
                return

            self._fail_locked(
                flow,
                actor=actor,
                request_id=request_id,
                reason=f"unsupported_step_type:{step_type}",
            )
            return

        flow.status = "completed"
        flow.waiting_for = ""
        flow.pending_prompt = ""
        flow.updated_at = _utcnow()
        self._update_session_metadata(
            flow.session_id,
            {"active_test_flow_id": "", "last_completed_test_flow_id": flow.flow_id},
        )
        self._append_event(
            flow.session_id,
            TranscriptEvent(
                event_type="test_flow_completed",
                data={"flow_id": flow.flow_id, "results": list(flow.results)},
            ),
        )
        self._audit(
            action="test_flow_completed",
            session_id=flow.session_id,
            request_id=request_id,
            actor=actor,
            detail={"flow_id": flow.flow_id, "results_count": len(flow.results)},
        )

    def _fail_locked(
        self,
        flow: TestFlowState,
        *,
        actor: str,
        request_id: str,
        reason: str,
    ) -> None:
        flow.status = "failed"
        flow.waiting_for = ""
        flow.pending_prompt = ""
        flow.updated_at = _utcnow()
        self._update_session_metadata(
            flow.session_id,
            {"active_test_flow_id": "", "last_failed_test_flow_id": flow.flow_id},
        )
        self._append_event(
            flow.session_id,
            TranscriptEvent(
                event_type="test_flow_failed",
                data={"flow_id": flow.flow_id, "reason": reason},
            ),
        )
        self._audit(
            action="test_flow_failed",
            session_id=flow.session_id,
            request_id=request_id,
            actor=actor,
            detail={"flow_id": flow.flow_id, "reason": reason},
            status="error",
        )
