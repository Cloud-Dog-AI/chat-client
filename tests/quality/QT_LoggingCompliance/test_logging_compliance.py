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

from pathlib import Path


def _defaults_path() -> Path:
    root = Path(__file__).resolve().parents[3]
    for name in ("defaults.yaml", "default.yaml"):
        path = root / name
        if path.exists():
            return path
    raise AssertionError("Missing defaults.yaml/default.yaml")
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-003")


def test_defaults_yaml_has_integrity_config() -> None:
    text = _defaults_path().read_text(encoding="utf-8")
    assert "integrity:" in text
    assert "interval_seconds" in text
    assert "hash_algorithm" in text
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-003")


def test_defaults_yaml_has_rotation_config() -> None:
    text = _defaults_path().read_text(encoding="utf-8")
    assert "rotation:" in text
    assert "max_bytes" in text
    assert "backup_count" in text
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-003")


def test_defaults_yaml_has_retention_config() -> None:
    text = _defaults_path().read_text(encoding="utf-8")
    assert "retention:" in text
    assert "hot_days" in text
    assert "cold_days" in text
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-003")


def test_audit_events_doc_exists() -> None:
    root = Path(__file__).resolve().parents[3]
    doc = root / "docs" / "AUDIT-EVENTS.md"
    assert doc.exists()
    assert doc.read_text(encoding="utf-8").strip()

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.quality, pytest.mark.pure, pytest.mark.fast]

