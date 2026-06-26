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

"""QT checks for Vault/config/secret contract."""

from __future__ import annotations
import pytest

import re
from pathlib import Path


SECRET_KEY_PATTERN = re.compile(r"(?i)\b(password|token|secret|api[_-]?key)\b")
VAULT_EXPR_PATTERN = re.compile(r"\$\{vault\.dev\.[^}]+\}")


def _env_files(project_root: Path) -> list[Path]:
    return sorted((project_root / "tests").glob("env-*"))
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-004")


def test_defaults_yaml_exists(project_root: Path):
    assert (project_root / "defaults.yaml").exists(), "defaults.yaml missing"
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-004")


def test_defaults_yaml_no_secrets(project_root: Path):
    defaults = (project_root / "defaults.yaml").read_text(encoding="utf-8", errors="ignore")
    violations = []
    for line_no, line in enumerate(defaults.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if SECRET_KEY_PATTERN.search(stripped) and ":" in stripped:
            key = stripped.split(":", 1)[0].strip().lower()
            if key.endswith("_header"):
                continue
            rhs = stripped.split(":", 1)[1].strip().strip("'\"")
            if rhs and rhs.lower() not in {"null", "none", "{}", "[]"} and not VAULT_EXPR_PATTERN.search(rhs):
                violations.append(f"defaults.yaml:{line_no}:{stripped}")
    assert not violations, "Hardcoded secret-like values in defaults.yaml:\n" + "\n".join(violations)
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-004")


def test_config_yaml_no_secrets(project_root: Path):
    config_path = project_root / "config.yaml"
    if not config_path.exists():
        return
    text = config_path.read_text(encoding="utf-8", errors="ignore")
    violations = []
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if SECRET_KEY_PATTERN.search(stripped) and ":" in stripped:
            rhs = stripped.split(":", 1)[1].strip().strip("'\"")
            if rhs and not VAULT_EXPR_PATTERN.search(rhs):
                violations.append(f"config.yaml:{line_no}:{stripped}")
    assert not violations, "Hardcoded secret-like values in config.yaml:\n" + "\n".join(violations)
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-004")


def test_env_files_use_vault_expressions(project_root: Path):
    # IT/AT env files should use vault expressions for real credentials where possible.
    envs = [p for p in _env_files(project_root) if "-IT" in p.name or "-AT" in p.name or p.name in {"env-IT", "env-AT"}]
    violations = []
    allowed_test_values = {"", "test-api-key", "secret", "12345678", "ExpertAgent5678"}

    for path in envs:
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not SECRET_KEY_PATTERN.search(key):
                continue
            if VAULT_EXPR_PATTERN.search(value):
                continue
            if value in allowed_test_values:
                continue
            if value.startswith("${") and value.endswith("}"):
                continue
            violations.append(f"{path.as_posix()}:{line_no}:{key}={value}")
    assert not violations, "IT/AT env secret keys without vault expressions:\n" + "\n".join(violations)
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-004")


def test_no_secrets_in_source(project_root: Path, src_python_files, helper_api):
    rel = helper_api["rel"]
    secret_literal = re.compile(
        r"(?i)\b(?:password|token|secret|api[_-]?key)\b\s*[:=]\s*[\"'](?!\s*$)[^\"']+[\"']"
    )
    violations = []
    for path in src_python_files:
        for line_no, line in helper_api["line_iter"](path):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if secret_literal.search(stripped):
                violations.append(f"{rel(path, project_root)}:{line_no}:{stripped}")
    assert not violations, "Secret-like literals detected in src:\n" + "\n".join(violations)
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-004")


def test_env_files_exist_per_tier(project_root: Path):
    required = ["tests/env-UT", "tests/env-ST", "tests/env-IT", "tests/env-AT", "tests/env-QT"]
    missing = [p for p in required if not (project_root / p).exists()]
    assert not missing, f"Missing required env files: {missing}"

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.quality, pytest.mark.pure, pytest.mark.fast]

