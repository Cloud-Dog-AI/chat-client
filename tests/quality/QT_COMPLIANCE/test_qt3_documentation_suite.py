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

"""W28A-144 QT3 documentation compliance checks (QT3.1-QT3.3)."""

from __future__ import annotations
import pytest

from collections import Counter
from pathlib import Path
import re

PROJECT_ROOT = Path(__file__).resolve().parents[3]

REQUIREMENT_ID_RE = re.compile(r"\b(?:FR|NFR)-?\d+(?:\.\d+)?\b|\bR-DB-\d+\b", re.IGNORECASE)
TEST_ID_RE = re.compile(r"\b(?:UT|ST|IT|AT|QT|PT|CT)\d+(?:\.\d+)?[a-z]?\b")


def _doc_path(name: str) -> Path | None:
    root = PROJECT_ROOT / name
    docs = PROJECT_ROOT / "docs" / name
    if root.exists():
        return root
    if docs.exists():
        return docs
    return None
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-005")


def test_qt3_1_required_files_exist() -> None:
    """QT3.1: Verify required project and documentation files exist."""
    required_direct = ["RULES.md", "README.md", "pyproject.toml", "defaults.yaml"]
    missing: list[str] = [name for name in required_direct if not (PROJECT_ROOT / name).exists()]

    for logical in ("REQUIREMENTS.md", "ARCHITECTURE.md", "TESTS.md"):
        if _doc_path(logical) is None:
            missing.append(logical)

    assert not missing, "QT3.1 missing required files: " + ", ".join(missing)
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-005")


def test_qt3_2_requirement_id_format() -> None:
    """QT3.2: Ensure REQUIREMENTS doc contains formal FR/NFR/R-DB IDs."""
    req_path = _doc_path("REQUIREMENTS.md")
    assert req_path is not None, "QT3.2 prerequisite failed: REQUIREMENTS.md missing"
    text = req_path.read_text(encoding="utf-8", errors="ignore")
    matches = REQUIREMENT_ID_RE.findall(text)
    assert matches, "QT3.2 failed: REQUIREMENTS.md has no formal FR/NFR/R-DB IDs"
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-005")


def test_qt3_3_test_id_uniqueness() -> None:
    """QT3.3: Ensure TESTS doc has no duplicate test IDs."""
    tests_path = _doc_path("TESTS.md")
    assert tests_path is not None, "QT3.3 prerequisite failed: TESTS.md missing"
    text = tests_path.read_text(encoding="utf-8", errors="ignore")
    ids: list[str] = []

    # Parse the markdown table Test ID column first to avoid false positives
    # from suite-folder names like `AT1.1_SuiteName`, which can produce
    # truncated prefix matches under generic regex scans.
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cols = [col.strip() for col in line.split("|")]
        if len(cols) < 4:
            continue
        candidate = cols[2]
        if TEST_ID_RE.fullmatch(candidate):
            ids.append(candidate)

    if not ids:
        ids = TEST_ID_RE.findall(text)
    duplicates = sorted(test_id for test_id, count in Counter(ids).items() if count > 1)
    assert not duplicates, "QT3.3 duplicate test IDs in TESTS.md: " + ", ".join(duplicates)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.quality, pytest.mark.pure, pytest.mark.fast]

