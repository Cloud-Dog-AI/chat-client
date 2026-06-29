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

from datetime import datetime, timezone
from typing import Any

from cloud_dog_db.session.session_manager import SyncSessionManager
from sqlalchemy import delete, func, select

from ..session.transcript import TranscriptEvent
from .models import ChatAuditLog, ChatSession, ChatSessionEvent, ChatSessionPreference


def _parse_iso_datetime(value: str | None) -> datetime:
    """Internal helper to iso datetime for this module."""
    raw = str(value or "").strip()
    if not raw:
        return datetime.now(timezone.utc)
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_dict(value: Any) -> dict[str, Any]:
    """Internal helper to safe dict for this module."""
    if isinstance(value, dict):
        return dict(value)
    return {}


class ChatSessionStore:
    """Persistent chat session store backed by cloud_dog_db session manager."""

    def __init__(
        self,
        session_manager: SyncSessionManager,
        *,
        tenant_id: str = "default",
        actor: str = "chat-client",
    ):
        """Initialise ChatSessionStore state and dependencies."""
        self._sessions = session_manager
        self._tenant_id = str(tenant_id or "default").strip() or "default"
        self._actor = str(actor or "chat-client").strip() or "chat-client"

    def _base_session_query(self):
        """Internal helper to base session query for this module."""
        return (
            select(ChatSession)
            .where(ChatSession.tenant_id == self._tenant_id)
            .where(ChatSession.is_deleted.is_(False))
        )

    def _upsert_preferences(
        self, session_id: str, selected_indices: list[int], *, db
    ) -> None:
        """Internal helper to upsert preferences for this module."""
        stmt = (
            select(ChatSessionPreference)
            .where(ChatSessionPreference.tenant_id == self._tenant_id)
            .where(ChatSessionPreference.session_id == session_id)
            .where(ChatSessionPreference.is_deleted.is_(False))
        )
        row = db.execute(stmt).scalar_one_or_none()
        if row is None:
            row = ChatSessionPreference(
                session_id=session_id,
                selected_mcp_server_indices_json=list(selected_indices),
                tenant_id=self._tenant_id,
                created_by=self._actor,
                updated_by=self._actor,
            )
            db.add(row)
            return
        row.selected_mcp_server_indices_json = list(selected_indices)
        row.updated_by = self._actor
        row.is_deleted = False
        row.deleted_at = None

    def create_session(
        self,
        *,
        session_id: str,
        created_at: str,
        metadata: dict[str, Any],
        log_path: str,
    ) -> None:
        """Create session for the current runtime context."""
        with self._sessions.session() as db:
            row = db.get(ChatSession, session_id)
            if row is None:
                row = ChatSession(
                    id=session_id,
                    metadata_json=_safe_dict(metadata),
                    log_path=str(log_path or ""),
                    tenant_id=self._tenant_id,
                    created_by=self._actor,
                    updated_by=self._actor,
                )
                row.created_at = _parse_iso_datetime(created_at)
                db.add(row)
            else:
                row.metadata_json = _safe_dict(metadata)
                row.log_path = str(log_path or "")
                row.updated_by = self._actor
                row.is_deleted = False
                row.deleted_at = None

            selected = metadata.get("selected_mcp_server_indices")
            if isinstance(selected, list):
                filtered = [int(v) for v in selected if isinstance(v, int) and v >= 0]
                self._upsert_preferences(session_id, filtered, db=db)

            # Ensure parent session row is persisted before audit FK insert.
            db.flush()
            self.write_audit(
                action="session_created",
                session_id=session_id,
                detail={"metadata_json": _safe_dict(metadata)},
                db=db,
            )

    def append_event(self, session_id: str, event: TranscriptEvent) -> None:
        """Handle append event for the current runtime context."""
        with self._sessions.session() as db:
            session_row = db.get(ChatSession, session_id)
            if session_row is None:
                raise KeyError(f"Unknown session: {session_id}")
            session_row.updated_by = self._actor
            current_max = db.execute(
                select(func.max(ChatSessionEvent.sequence))
                .where(ChatSessionEvent.tenant_id == self._tenant_id)
                .where(ChatSessionEvent.session_id == session_id)
                .where(ChatSessionEvent.is_deleted.is_(False))
            ).scalar_one()
            next_sequence = int(current_max if current_max is not None else -1) + 1
            requested_sequence = event.sequence
            if requested_sequence is None:
                sequence = next_sequence
            else:
                sequence = max(int(requested_sequence), next_sequence)
            event.sequence = sequence
            db.add(
                ChatSessionEvent(
                    session_id=session_id,
                    sequence=sequence,
                    event_type=str(event.event_type or ""),
                    event_timestamp=_parse_iso_datetime(event.timestamp),
                    event_json=_safe_dict(event.data),
                    tenant_id=self._tenant_id,
                    created_by=self._actor,
                    updated_by=self._actor,
                )
            )

    def list_sessions(self) -> list[dict[str, Any]]:
        """List sessions for the current runtime context."""
        with self._sessions.session() as db:
            rows = db.execute(self._base_session_query()).scalars().all()
            out: list[dict[str, Any]] = []
            for row in rows:
                out.append(
                    {
                        "id": row.id,
                        "created_at": row.created_at.isoformat(),
                        "metadata": _safe_dict(row.metadata_json),
                        "log_path": str(row.log_path or ""),
                    }
                )
            return out

    def list_events(self, *, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        """List persisted session events for cross-process A2A consumers."""
        bounded_limit = max(1, min(int(limit or 100), 500))
        with self._sessions.session() as db:
            query = (
                select(ChatSessionEvent)
                .where(ChatSessionEvent.tenant_id == self._tenant_id)
                .where(ChatSessionEvent.is_deleted.is_(False))
                .where(ChatSessionEvent.id > int(after_id or 0))
            )
            if int(after_id or 0) <= 0:
                # Default zero-offset checkpoints should start from the newest
                # persisted window, not replay the oldest rows in the table.
                rows = (
                    db.execute(
                        query
                        .order_by(ChatSessionEvent.id.desc())
                        .limit(bounded_limit)
                    )
                    .scalars()
                    .all()
                )
                rows.reverse()
            else:
                rows = (
                    db.execute(
                        query
                        .order_by(ChatSessionEvent.id.asc())
                        .limit(bounded_limit)
                    )
                    .scalars()
                    .all()
                )
            out: list[dict[str, Any]] = []
            for row in rows:
                event_type = str(row.event_type or "")
                out.append(
                    {
                        "id": int(row.id),
                        "session_id": str(row.session_id or ""),
                        "sequence": int(row.sequence or 0),
                        "event_type": event_type,
                        "topic": (
                            "messages"
                            if event_type in {"user_message", "assistant_message"}
                            else "sessions"
                        ),
                        "timestamp": row.event_timestamp.isoformat(),
                        "data": _safe_dict(row.event_json),
                    }
                )
            return out

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return session for the current runtime context."""
        with self._sessions.session() as db:
            row = db.execute(
                self._base_session_query().where(ChatSession.id == session_id)
            ).scalar_one_or_none()
            if row is None:
                return None

            event_rows = (
                db.execute(
                    select(ChatSessionEvent)
                    .where(ChatSessionEvent.tenant_id == self._tenant_id)
                    .where(ChatSessionEvent.session_id == session_id)
                    .where(ChatSessionEvent.is_deleted.is_(False))
                    .order_by(ChatSessionEvent.sequence.asc(), ChatSessionEvent.id.asc())
                )
                .scalars()
                .all()
            )
            events: list[TranscriptEvent] = []
            for item in event_rows:
                events.append(
                    TranscriptEvent(
                        event_type=str(item.event_type or ""),
                        timestamp=item.event_timestamp.isoformat(),
                        data=_safe_dict(item.event_json),
                        sequence=int(item.sequence),
                    )
                )

            metadata = _safe_dict(row.metadata_json)
            pref_row = (
                db.execute(
                    select(ChatSessionPreference)
                    .where(ChatSessionPreference.tenant_id == self._tenant_id)
                    .where(ChatSessionPreference.session_id == session_id)
                    .where(ChatSessionPreference.is_deleted.is_(False))
                )
                .scalars()
                .first()
            )
            if pref_row is not None:
                selected = pref_row.selected_mcp_server_indices_json or []
                if isinstance(selected, list):
                    metadata["selected_mcp_server_indices"] = [
                        int(v) for v in selected if isinstance(v, int)
                    ]

            next_sequence = (
                max((int(item.sequence) for item in event_rows), default=-1) + 1
            )
            return {
                "id": row.id,
                "created_at": row.created_at.isoformat(),
                "metadata": metadata,
                "events": events,
                "log_path": str(row.log_path or ""),
                "sequence": next_sequence,
            }

    def update_session_metadata(
        self, session_id: str, metadata_updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Update session metadata for the current runtime context."""
        with self._sessions.session() as db:
            row = db.execute(
                self._base_session_query().where(ChatSession.id == session_id)
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(f"Unknown session: {session_id}")

            metadata = _safe_dict(row.metadata_json)
            metadata.update(_safe_dict(metadata_updates))
            row.metadata_json = metadata
            row.updated_by = self._actor

            selected = metadata_updates.get("selected_mcp_server_indices")
            if isinstance(selected, list):
                filtered = sorted(
                    {
                        int(v)
                        for v in selected
                        if isinstance(v, int) and int(v) >= 0
                    }
                )
                metadata["selected_mcp_server_indices"] = filtered
                row.metadata_json = metadata
                self._upsert_preferences(session_id, filtered, db=db)

            self.write_audit(
                action="session_metadata_updated",
                session_id=session_id,
                detail={"metadata_json": metadata_updates},
                db=db,
            )
            return metadata

    def delete_session(self, session_id: str) -> bool:
        """Delete session for the current runtime context."""
        with self._sessions.session() as db:
            row = db.execute(
                self._base_session_query().where(ChatSession.id == session_id)
            ).scalar_one_or_none()
            if row is None:
                return False

            db.execute(
                delete(ChatSessionEvent)
                .where(ChatSessionEvent.session_id == session_id)
                .where(ChatSessionEvent.tenant_id == self._tenant_id)
            )
            db.execute(
                delete(ChatSessionPreference)
                .where(ChatSessionPreference.session_id == session_id)
                .where(ChatSessionPreference.tenant_id == self._tenant_id)
            )
            db.execute(
                delete(ChatAuditLog)
                .where(ChatAuditLog.session_id == session_id)
                .where(ChatAuditLog.tenant_id == self._tenant_id)
            )
            db.delete(row)
            return True

    def write_audit(
        self,
        *,
        action: str,
        session_id: str | None,
        detail: dict[str, Any] | None = None,
        status: str = "ok",
        request_id: str | None = None,
        db=None,
    ) -> None:
        """Write audit for the current runtime context."""
        payload = ChatAuditLog(
            session_id=session_id,
            action=str(action or "").strip() or "unknown",
            status=str(status or "").strip() or "ok",
            request_id=str(request_id or "").strip() or None,
            detail_json=_safe_dict(detail),
            tenant_id=self._tenant_id,
            created_by=self._actor,
            updated_by=self._actor,
        )
        if db is not None:
            db.add(payload)
            return
        with self._sessions.session() as new_db:
            new_db.add(payload)
