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

"""QT checks for migration completeness and bespoke bypass remnants."""

from __future__ import annotations
import pytest

import re
from pathlib import Path


def _assert_no_violations(violations: list[str], title: str) -> None:
    if violations:
        raise AssertionError(f"{title}\n" + "\n".join(sorted(violations)))
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-007")


def test_no_yaml_safe_load_for_config(project_root: Path, src_python_files, helper_api):
    rel = helper_api["rel"]
    violations = []
    for path in src_python_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "yaml.safe_load(" in text or "yaml.load(" in text:
            for line_no, line in helper_api["line_iter"](path):
                if "yaml.safe_load(" in line or "yaml.load(" in line:
                    violations.append(f"{rel(path, project_root)}:{line_no}:{line.strip()}")
    _assert_no_violations(violations, "Migration gap: yaml load used for config")
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-007")


def test_no_raw_fastapi(project_root: Path, src_python_files, helper_api):
    rel = helper_api["rel"]
    violations = []
    for path in src_python_files:
        for line_no, line in helper_api["line_iter"](path):
            if "FastAPI(" in line:
                violations.append(f"{rel(path, project_root)}:{line_no}:{line.strip()}")
    _assert_no_violations(violations, "Migration gap: raw FastAPI instantiation found")
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-007")


def test_no_bespoke_auth(project_root: Path, src_python_files, helper_api):
    rel = helper_api["rel"]
    auth_patterns = [
        re.compile(r"\bAPIKeyHeader\s*\("),
        re.compile(r"\bdef\s+verify_token\s*\("),
        re.compile(r"\bjwt\.decode\s*\("),
    ]
    violations = []
    for path in src_python_files:
        for line_no, line in helper_api["line_iter"](path):
            if any(p.search(line) for p in auth_patterns):
                violations.append(f"{rel(path, project_root)}:{line_no}:{line.strip()}")
    _assert_no_violations(violations, "Migration gap: bespoke auth implementation found")
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-007")


def test_no_os_environ_for_config(project_root: Path, src_python_files, helper_api, allowlist):
    rel = helper_api["rel"]
    matches_allowlist = helper_api["matches_allowlist"]
    allowed = allowlist["migration"]["os_env_for_config_allowed"]
    violations = []
    pattern = re.compile(r"\bos\.environ\b|\bos\.getenv\s*\(")
    for path in src_python_files:
        for line_no, line in helper_api["line_iter"](path):
            if not pattern.search(line):
                continue
            item = f"{rel(path, project_root)}:{line_no}:{line.strip()}"
            if not matches_allowlist(item, allowed):
                violations.append(item)
    _assert_no_violations(violations, "Migration gap: direct os.environ/os.getenv config access")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.quality, pytest.mark.pure, pytest.mark.fast]

