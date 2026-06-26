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

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

from cloud_dog_logging import get_logger  # type: ignore[import-untyped]

from ..storage_fs import (
    append_text,
    delete_file,
    ensure_directory,
    join_path,
    list_matching_paths,
    parent_dir,
    path_exists,
    read_text,
    write_text,
)
from .transcript import TranscriptEvent

if TYPE_CHECKING:
    from ..database.store import ChatSessionStore


class SessionManager:
    def __init__(self, log_folder: str, session_store: Optional["ChatSessionStore"] = None):
        """Initialise in-memory session tracking and optional persistence backend."""
        self.log_folder = ensure_directory(log_folder)
        ensure_directory(join_path(self.log_folder, "sessions"))
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._store = session_store
        self._logger = get_logger("cloud_dog_chat_session")

    def create_session(self, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Create a new session and append an initial `session_started` event."""
        session_id = str(uuid4())
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_path = join_path(self.log_folder, "sessions", f"{ts}_{session_id}.jsonl")

        self._sessions[session_id] = {
            "id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
            "events": [],
            "log_path": str(log_path),
            "sequence": 0,
        }

        if self._store is not None:
            self._store.create_session(
                session_id=session_id,
                created_at=str(self._sessions[session_id]["created_at"]),
                metadata=dict(metadata or {}),
                log_path=str(log_path),
            )

        self.append_event(
            session_id,
            TranscriptEvent(
                event_type="session_started", data={"metadata": metadata or {}}
            ),
        )
        return session_id

    def load_session(self, session_id: str) -> str:
        """Load a session from memory, store, or JSONL transcript file."""
        if session_id in self._sessions:
            return session_id

        if self._store is not None:
            stored = self._store.get_session(session_id)
            if stored is not None:
                self._sessions[session_id] = {
                    "id": stored["id"],
                    "created_at": stored["created_at"],
                    "metadata": stored["metadata"],
                    "events": stored["events"],
                    "log_path": stored["log_path"],
                    "sequence": int(stored.get("sequence", 0)),
                }
                self.append_event(
                    session_id, TranscriptEvent(event_type="session_resumed", data={})
                )
                return session_id

        log_dir = join_path(self.log_folder, "sessions")
        if not path_exists(log_dir):
            raise FileNotFoundError(f"Session log folder does not exist: {log_dir}")

        candidates = [
            join_path(self.log_folder, str(item).lstrip("/"))
            for item in list_matching_paths(self.log_folder, "/sessions", suffix=f"_{session_id}.jsonl")
        ]
        if not candidates:
            raise FileNotFoundError(
                f"Session log file not found for session_id={session_id}"
            )

        log_path = candidates[-1]
        events: List[TranscriptEvent] = []
        created_at = datetime.now(timezone.utc).isoformat()
        metadata: Dict[str, Any] = {}

        for line in read_text(log_path, encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            event = TranscriptEvent(
                event_type=str(payload.get("event_type") or ""),
                timestamp=str(
                    payload.get("timestamp")
                    or datetime.now(timezone.utc).isoformat()
                ),
                data=payload.get("data") or {},
                sequence=payload.get("sequence"),
            )
            events.append(event)
            if event.event_type == "session_started":
                meta = event.data.get("metadata")
                if isinstance(meta, dict):
                    metadata = meta
            if len(events) == 1:
                created_at = event.timestamp

        sequence = max(
            [e.sequence for e in events if e.sequence is not None] or [len(events)]
        )

        self._sessions[session_id] = {
            "id": session_id,
            "created_at": created_at,
            "metadata": metadata,
            "events": events,
            "log_path": str(log_path),
            "sequence": sequence + 1,
        }

        self.append_event(
            session_id, TranscriptEvent(event_type="session_resumed", data={})
        )
        return session_id

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Return lightweight metadata for all known sessions."""
        if self._store is not None:
            persisted = self._store.list_sessions()
            for item in persisted:
                sid = str(item.get("id") or "")
                if not sid:
                    continue
                if sid not in self._sessions:
                    self._sessions[sid] = {
                        "id": sid,
                        "created_at": str(item.get("created_at") or ""),
                        "metadata": dict(item.get("metadata") or {}),
                        "events": [],
                        "log_path": str(item.get("log_path") or ""),
                        "sequence": 0,
                    }
            return persisted

        return [
            {
                "id": s["id"],
                "created_at": s["created_at"],
                "metadata": s["metadata"],
                "log_path": s["log_path"],
            }
            for s in self._sessions.values()
        ]

    def get_session(self, session_id: str) -> Dict[str, Any]:
        """Return a full session record by identifier."""
        if session_id not in self._sessions and self._store is not None:
            stored = self._store.get_session(session_id)
            if stored is not None:
                self._sessions[session_id] = {
                    "id": stored["id"],
                    "created_at": stored["created_at"],
                    "metadata": stored["metadata"],
                    "events": stored["events"],
                    "log_path": stored["log_path"],
                    "sequence": int(stored.get("sequence", 0)),
                }
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session: {session_id}")
        return self._sessions[session_id]

    def delete_session(self, session_id: str) -> bool:
        """Delete session runtime state, transcript files, and persisted records."""
        removed = False
        session = self._sessions.pop(session_id, None)
        if session is not None:
            log_path = str(session.get("log_path") or "")
            if log_path and path_exists(log_path):
                delete_file(log_path, missing_ok=True)
                removed = True
            else:
                removed = True

        for candidate in list_matching_paths(self.log_folder, "/sessions", suffix=f"_{session_id}.jsonl"):
            delete_file(join_path(self.log_folder, str(candidate).lstrip("/")), missing_ok=True)
            removed = True

        if self._store is not None:
            removed = self._store.delete_session(session_id) or removed

        return removed

    def update_session_metadata(
        self, session_id: str, metadata_updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Merge metadata updates and record the change in the transcript."""
        session = self.get_session(session_id)
        if not isinstance(metadata_updates, dict):
            raise ValueError("metadata_updates must be a dictionary")
        metadata = session.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.update(metadata_updates)
        session["metadata"] = metadata
        self.append_event(
            session_id,
            TranscriptEvent(
                event_type="session_metadata_updated",
                data={"metadata_updates": metadata_updates},
            ),
        )
        if self._store is not None:
            self._store.update_session_metadata(session_id, dict(metadata_updates))
        return metadata

    def append_event(self, session_id: str, event: TranscriptEvent) -> None:
        """Append an event to runtime state and best-effort persistent storage."""
        session = self.get_session(session_id)
        seq = int(session.get("sequence", 0))
        event.sequence = seq
        session["sequence"] = seq + 1

        session["events"].append(event)

        log_path = str(session["log_path"])
        try:
            append_text(log_path, event.to_json_line() + "\n", encoding="utf-8")
        except OSError as exc:
            # Keep API behavior available even when the filesystem log path is
            # temporarily unavailable (for example permission drift on mounted
            # docker volumes). Session state remains in memory.
            self._logger.warning(
                "session_event_log_write_failed",
                extra={
                    "session_id": session_id,
                    "event_type": event.event_type,
                    "sequence": event.sequence,
                    "log_path": str(log_path),
                    "error": str(exc),
                },
            )

        self._logger.info(
            "session_event",
            extra={
                "session_id": session_id,
                "event_type": event.event_type,
                "sequence": event.sequence,
            },
        )

        if self._store is not None:
            try:
                self._store.append_event(session_id, event)
            except KeyError:
                # Recover from unexpected store drift where a runtime-live
                # session row is missing in the DB layer.
                session_snapshot = self.get_session(session_id)
                self._logger.warning(
                    "session_store_row_missing_recovering",
                    extra={
                        "session_id": session_id,
                        "event_type": event.event_type,
                        "sequence": event.sequence,
                    },
                )
                self._store.create_session(
                    session_id=session_id,
                    created_at=str(
                        session_snapshot.get("created_at")
                        or datetime.now(timezone.utc).isoformat()
                    ),
                    metadata=dict(session_snapshot.get("metadata") or {}),
                    log_path=str(session_snapshot.get("log_path") or ""),
                )
                self._store.append_event(session_id, event)

    def load_context_file(self, session_id: str, context_file: str) -> None:
        """Load a text context file and append it as a session event."""
        if not path_exists(context_file):
            raise FileNotFoundError(str(context_file))

        content = read_text(context_file, encoding="utf-8")

        self.append_event(
            session_id,
            TranscriptEvent(
                event_type="context_loaded",
                data={"path": str(context_file), "size": len(content), "content": content},
            ),
        )

    def write_context_snapshot(self, session_id: str, output_file: str) -> None:
        """Write the full session transcript into a snapshot JSON document."""
        session = self.get_session(session_id)
        ensure_directory(parent_dir(output_file))

        snapshot = {
            "session_id": session_id,
            "created_at": session["created_at"],
            "metadata": session["metadata"],
            "events": [json.loads(e.to_json_line()) for e in session["events"]],
        }

        rendered_snapshot = json.dumps(snapshot, ensure_ascii=False, indent=2)
        write_text(output_file, rendered_snapshot, encoding="utf-8", overwrite=True)

        self.append_event(
            session_id,
            TranscriptEvent(event_type="context_written", data={"path": str(output_file)}),
        )
