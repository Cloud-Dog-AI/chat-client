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

"""QT traceability checks: requirements <-> tests <-> code matrix."""

from __future__ import annotations
import pytest

from pathlib import Path


def _render_matrix(rows: list[dict]) -> str:
    header = "| Req ID | Code | Test | Status |"
    sep = "|---|---|---|---|"
    lines = [header, sep]
    for row in rows:
        lines.append(
            f"| {row['req_id']} | {row['code']} | {row['test']} | {row['status']} |"
        )
    return "\n".join(lines)


def _collect_requirement_artifacts(project_root: Path, helper_api):
    requirements_text = (project_root / "docs/REQUIREMENTS.md").read_text(
        encoding="utf-8", errors="ignore"
    )
    tests_doc_text = (project_root / "docs/TESTS.md").read_text(
        encoding="utf-8", errors="ignore"
    )
    req_ids = helper_api["parse_requirement_ids"](requirements_text)

    src_files = sorted((project_root / "src").rglob("*.py"))
    test_files = sorted((project_root / "tests").rglob("test_*.py"))

    src_blobs = {
        path: path.read_text(encoding="utf-8", errors="ignore")
        for path in src_files
    }
    test_blobs = {
        path: path.read_text(encoding="utf-8", errors="ignore")
        for path in test_files
    }
    return req_ids, tests_doc_text, src_blobs, test_blobs
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-005")


def test_all_requirements_have_tests(project_root: Path, helper_api, allowlist):
    req_ids, tests_doc_text, _, test_blobs = _collect_requirement_artifacts(
        project_root, helper_api
    )
    missing = []
    joined_tests = tests_doc_text + "\n" + "\n".join(test_blobs.values())
    for req_id in req_ids:
        if req_id not in joined_tests:
            missing.append(req_id)

    allowed = set(allowlist["traceability"]["untested_requirements_allowed"])
    unexpected = sorted([rid for rid in missing if rid not in allowed])
    if unexpected:
        raise AssertionError(
            "Requirements with zero test references:\n" + "\n".join(unexpected)
        )
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-005")


def test_all_tests_have_requirements(project_root: Path, helper_api, allowlist):
    tests_doc_text = (project_root / "docs/TESTS.md").read_text(
        encoding="utf-8", errors="ignore"
    )
    test_ids = helper_api["parse_docs_test_ids"](tests_doc_text)
    req_pattern = helper_api["req_id_pattern"]

    orphans = []
    lines = tests_doc_text.splitlines()
    for idx, line in enumerate(lines):
        for test_id in test_ids:
            if test_id not in line:
                continue
            window = "\n".join(lines[idx : idx + 4])
            if not req_pattern.search(window):
                orphans.append(test_id)
    # Deduplicate and sort for deterministic output.
    orphans = sorted(set(orphans))
    allowed = set(allowlist["traceability"]["orphan_tests_allowed"])
    unexpected = [tid for tid in orphans if tid not in allowed]
    assert not unexpected, "Tests in docs without requirement references:\n" + "\n".join(unexpected)
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-005")


def test_all_requirements_have_code(project_root: Path, helper_api, allowlist):
    req_ids, _, src_blobs, _ = _collect_requirement_artifacts(project_root, helper_api)
    no_code = []
    for req_id in req_ids:
        if not any(req_id in blob for blob in src_blobs.values()):
            no_code.append(req_id)
    allowed = set(allowlist["traceability"]["requirements_without_code_allowed"])
    unexpected = [rid for rid in no_code if rid not in allowed]
    assert not unexpected, "Requirements with no source-code references:\n" + "\n".join(unexpected)
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-005")
@pytest.mark.req("FR-018")


def test_delivery_matrix_complete(project_root: Path, helper_api, allowlist):
    req_ids, tests_doc_text, src_blobs, test_blobs = _collect_requirement_artifacts(
        project_root, helper_api
    )
    matrix_rows = []
    joined_tests = tests_doc_text + "\n" + "\n".join(test_blobs.values())
    functional_ids = [
        rid for rid in req_ids if rid.startswith("FR") or rid.startswith("R")
    ]
    delivered = 0

    for req_id in req_ids:
        code_refs = [
            helper_api["rel"](path, project_root)
            for path, blob in src_blobs.items()
            if req_id in blob
        ]
        has_test_ref = req_id in joined_tests

        if code_refs and has_test_ref:
            status = "DELIVERED"
            if req_id in functional_ids:
                delivered += 1
        elif code_refs and not has_test_ref:
            status = "UNTESTABLE"
        elif has_test_ref and not code_refs:
            status = "PARTIAL"
        else:
            status = "NOT STARTED"

        matrix_rows.append(
            {
                "req_id": req_id,
                "code": ", ".join(code_refs[:3]) if code_refs else "—",
                "test": "referenced" if has_test_ref else "—",
                "status": status,
            }
        )

    matrix_text = _render_matrix(matrix_rows)
    print("\nDELIVERY MATRIX\n" + matrix_text)

    if not functional_ids:
        return
    ratio = delivered / len(functional_ids)
    threshold = float(allowlist["traceability"]["min_functional_delivery_ratio"])
    assert ratio >= threshold, (
        f"Functional delivery ratio below threshold: {ratio:.2%} < {threshold:.2%}\n"
        + matrix_text
    )
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-005")


def test_no_orphan_test_files(project_root: Path, allowlist):
    tests_doc_text = (project_root / "docs/TESTS.md").read_text(
        encoding="utf-8", errors="ignore"
    )
    allowed = set(allowlist["traceability"]["orphan_test_files_allowed"])

    orphan_files = []
    for path in sorted((project_root / "tests").rglob("test_*.py")):
        rel_path = path.resolve().relative_to(project_root.resolve()).as_posix()
        parent_suite = path.parent.name
        if rel_path in tests_doc_text or parent_suite in tests_doc_text:
            continue
        if rel_path in allowed:
            continue
        orphan_files.append(rel_path)

    assert not orphan_files, "Test files not catalogued in docs/TESTS.md:\n" + "\n".join(orphan_files)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.quality, pytest.mark.pure, pytest.mark.fast]
