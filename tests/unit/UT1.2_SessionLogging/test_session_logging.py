import pytest
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

from pathlib import Path
from unittest.mock import patch

from cloud_dog_chat_client.session import SessionManager
from cloud_dog_chat_client.session.transcript import TranscriptEvent
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_2_session_writes_jsonl(tmp_path: Path, env_file):
    mgr = SessionManager(str(tmp_path / "logs"))
    session_id = mgr.create_session(metadata={"k": "v"})

    mgr.append_event(session_id, mgr.get_session(session_id)["events"][0])

    log_path = Path(mgr.get_session(session_id)["log_path"])
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_2_session_delete_removes_runtime_and_log(tmp_path: Path):
    mgr = SessionManager(str(tmp_path / "logs"))
    session_id = mgr.create_session(metadata={"k": "v"})
    log_path = Path(mgr.get_session(session_id)["log_path"])
    assert log_path.exists()

    deleted = mgr.delete_session(session_id)
    assert deleted is True
    assert not log_path.exists()
    assert session_id not in {s["id"] for s in mgr.list_sessions()}
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_2_session_create_tolerates_log_write_permission_error(tmp_path: Path):
    mgr = SessionManager(str(tmp_path / "logs"))

    with patch(
        "cloud_dog_chat_client.session.session_manager.open",
        side_effect=PermissionError("permission denied"),
    ):
        session_id = mgr.create_session(metadata={"k": "v"})

    session = mgr.get_session(session_id)
    assert session["id"] == session_id
    assert len(session["events"]) == 1
    assert session["events"][0].event_type == "session_started"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_2_session_recovers_when_store_row_missing(tmp_path: Path):
    class _FlakyStore:
        def __init__(self):
            self.created: list[dict[str, str]] = []
            self.appended: list[str] = []
            self.fail_next_append = False

        def create_session(self, *, session_id: str, created_at: str, metadata: dict, log_path: str):
            self.created.append(
                {
                    "session_id": session_id,
                    "created_at": created_at,
                    "log_path": log_path,
                }
            )

        def append_event(self, session_id: str, event: TranscriptEvent):
            if self.fail_next_append:
                self.fail_next_append = False
                raise KeyError(f"Unknown session: {session_id}")
            self.appended.append(event.event_type)

    store = _FlakyStore()
    mgr = SessionManager(str(tmp_path / "logs"), session_store=store)
    session_id = mgr.create_session(metadata={"suite": "ut1.2"})
    assert store.created and store.created[0]["session_id"] == session_id

    store.fail_next_append = True
    mgr.append_event(
        session_id,
        TranscriptEvent(event_type="assistant_message", data={"content": "ok"}),
    )

    # One create call from initial session setup + one recovery create after KeyError.
    assert len(store.created) == 2
    assert store.appended.count("assistant_message") == 1

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.pure, pytest.mark.fast]

