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

"""QT rules-compliance static checks (RC-01..RC-10 subset)."""

from __future__ import annotations
import pytest

import re
from pathlib import Path


def _assert_no_violations(violations: list[str], title: str) -> None:
    if violations:
        joined = "\n".join(sorted(violations))
        raise AssertionError(f"{title}\n{joined}")
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-006")


def test_no_hardcoded_urls(project_root: Path, src_python_files, allowlist, helper_api):
    rel = helper_api["rel"]
    line_iter = helper_api["line_iter"]
    matches_allowlist = helper_api["matches_allowlist"]
    patterns = allowlist["rules"]["hardcoded_urls"]

    url_pattern = re.compile(r"https?://|localhost|127\.0\.0\.1")
    violations: list[str] = []
    for path in src_python_files:
        for line_no, line in line_iter(path):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if url_pattern.search(stripped):
                item = f"{rel(path, project_root)}:{line_no}:{stripped}"
                if not matches_allowlist(item, patterns):
                    violations.append(item)
    _assert_no_violations(violations, "RC-01 hardcoded URL violations")
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-006")


def test_no_hardcoded_credentials(project_root: Path, src_python_files, helper_api):
    rel = helper_api["rel"]
    line_iter = helper_api["line_iter"]
    cred_pattern = re.compile(
        r"""(?ix)
        \b(password|token|api_key|secret)\b
        \s*=\s*
        ["'](?!\s*$)[^"']+["']
        """
    )
    violations: list[str] = []
    for path in src_python_files:
        for line_no, line in line_iter(path):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if cred_pattern.search(stripped):
                violations.append(f"{rel(path, project_root)}:{line_no}:{stripped}")
    _assert_no_violations(violations, "RC-02 hardcoded credential assignment violations")
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-006")


def test_no_direct_external_imports(project_root: Path, src_python_files, allowlist, helper_api):
    rel = helper_api["rel"]
    libs = ["requests", "httpx", "smtplib", "chromadb", "openai", "qdrant_client", "ollama"]
    lib_hits: dict[str, set[str]] = {lib: set() for lib in libs}

    import_pattern = re.compile(r"^\s*(?:from|import)\s+([a-zA-Z0-9_.]+)")
    for path in src_python_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            m = import_pattern.match(line)
            if not m:
                continue
            root_mod = m.group(1).split(".")[0]
            if root_mod in lib_hits:
                lib_hits[root_mod].add(rel(path, project_root))

    violations: list[str] = []
    allow_multi = set(allowlist["rules"]["multi_import_allowed_modules"])
    for lib, files in sorted(lib_hits.items()):
        if len(files) > 1 and lib not in allow_multi:
            violations.append(f"{lib}: imported in {len(files)} modules -> {sorted(files)}")
    _assert_no_violations(violations, "RC-03 external library single-interface violations")
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-006")


def test_no_pytest_skip_in_it_at(project_root: Path, helper_api):
    # Covers: NFR1, NFR3
    rel = helper_api["rel"]
    violations: list[str] = []
    for test_root in ["tests/integration", "tests/application"]:
        for path in sorted((project_root / test_root).rglob("*.py")):
            for line_no, line in helper_api["line_iter"](path):
                if "pytest.skip(" in line:
                    violations.append(f"{rel(path, project_root)}:{line_no}:{line.strip()}")
    _assert_no_violations(violations, "RC-06 pytest.skip in IT/AT violations")
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-006")


def test_no_mock_in_it_at(project_root: Path, helper_api):
    # Covers: NFR1
    rel = helper_api["rel"]
    patterns = ["MagicMock", "MockTransport", "local_mode=True"]
    violations: list[str] = []
    for test_root in ["tests/integration", "tests/application"]:
        for path in sorted((project_root / test_root).rglob("*.py")):
            for line_no, line in helper_api["line_iter"](path):
                if any(pattern in line for pattern in patterns):
                    violations.append(f"{rel(path, project_root)}:{line_no}:{line.strip()}")
    _assert_no_violations(violations, "RC-05 mock/local_mode usage in IT/AT violations")
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-006")


def test_file_headers_present(project_root: Path, src_python_files, allowlist, helper_api):
    rel = helper_api["rel"]
    matches_allowlist = helper_api["matches_allowlist"]
    exempt_patterns = allowlist["rules"]["missing_headers"]

    violations: list[str] = []
    for path in src_python_files:
        first_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[:10]
        has_header = any(
            line.strip().startswith("#")
            and re.search(r"license|owner|description|copyright|spdx", line, flags=re.IGNORECASE)
            for line in first_lines
        )
        if not has_header:
            item = rel(path, project_root)
            if not matches_allowlist(item, exempt_patterns):
                violations.append(item)
    _assert_no_violations(violations, "RC-04 missing source header violations")
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-006")


def test_functions_have_docstrings(src_python_files, allowlist, helper_api):
    total, with_doc = helper_api["count_function_docstrings"](src_python_files)
    coverage_pct = (with_doc * 100.0 / total) if total else 100.0
    min_required = float(allowlist["rules"]["min_docstring_coverage_pct"])
    if coverage_pct < min_required:
        raise AssertionError(
            f"RC-04 function docstring coverage too low: {coverage_pct:.2f}% < {min_required:.2f}% "
            f"(with_doc={with_doc}, total={total})"
        )

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.quality, pytest.mark.llm, pytest.mark.vdb, pytest.mark.smtp, pytest.mark.fast]
