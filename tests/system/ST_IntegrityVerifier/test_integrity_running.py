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
import pytest

import json
from pathlib import Path

from cloud_dog_logging.integrity import AuditIntegrityVerifier
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


def test_integrity_verifier_starts_with_server(tmp_path: Path) -> None:
    audit_log = tmp_path / "audit.log.jsonl"
    audit_log.write_text('{"timestamp":"2026-03-13T00:00:00.000Z"}\n', encoding="utf-8")
    integrity_log = tmp_path / "audit-integrity.log"

    verifier = AuditIntegrityVerifier(str(audit_log), str(integrity_log), interval_seconds=1)
    verifier.start()
    verifier.stop()

    assert integrity_log.exists()
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


def test_integrity_log_file_populated(tmp_path: Path) -> None:
    audit_log = tmp_path / "audit.log.jsonl"
    audit_log.write_text('{"timestamp":"2026-03-13T00:00:00.000Z"}\n', encoding="utf-8")
    integrity_log = tmp_path / "audit-integrity.log"

    verifier = AuditIntegrityVerifier(str(audit_log), str(integrity_log), interval_seconds=1)
    verifier.start()
    verifier.stop()

    rows = [json.loads(line) for line in integrity_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


def test_integrity_record_fields(tmp_path: Path) -> None:
    audit_log = tmp_path / "audit.log.jsonl"
    audit_log.write_text('{"timestamp":"2026-03-13T00:00:00.000Z"}\n', encoding="utf-8")
    integrity_log = tmp_path / "audit-integrity.log"

    verifier = AuditIntegrityVerifier(str(audit_log), str(integrity_log), interval_seconds=60)
    record = verifier.compute_now(trigger="manual")

    for key in (
        "timestamp",
        "service",
        "service_instance",
        "audit_log_path",
        "hash_algorithm",
        "hash_value",
        "file_size_bytes",
        "line_count",
        "verification_status",
        "trigger",
    ):
        assert key in record

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.pure, pytest.mark.slow]

