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

"""W28A-144 QT2.6 secrets separation checks."""

from __future__ import annotations
import pytest

from pathlib import Path
import re

PROJECT_ROOT = Path(__file__).resolve().parents[3]

SECRET_KEY_RE = re.compile(
    r"(?:^|__)(PASSWORD|SECRET|API[_-]?KEY|AUTH_BEARER_TOKEN|ACCESS[_-]?KEY|PRIVATE[_-]?KEY)(?:$|__)",
    re.I,
)
ASSIGNMENT_RE = re.compile(
    r"\b(?:password|token|secret|api[_-]?key|access[_-]?key|private[_-]?key)\b\s*=\s*['\"][^'\"]{3,}['\"]",
    re.I,
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _iter_py(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        p for p in root.rglob("*.py") if "__pycache__" not in p.parts and ".venv" not in p.parts
    )


def _iter_env_files() -> list[Path]:
    paths: list[Path] = []
    for pattern in ("env*", "*.env", "env-*", "*.env-*", "tests/env-*", "private/env*"):
        paths.extend(PROJECT_ROOT.glob(pattern))
    unique = sorted({path.resolve() for path in paths if path.is_file()})
    return [Path(path) for path in unique]


def _is_placeholder(value: str) -> bool:
    cleaned = value.strip().strip('"').strip("'")
    if not cleaned:
        return True
    placeholders = {
        "changeme",
        "change-me",
        "example",
        "placeholder",
        "your-key",
        "your_token_here",
        "set-me",
        "dummy",
        "none",
        "null",
        "~",
    }
    return cleaned.lower() in placeholders


def _is_sensitive_key(key: str) -> bool:
    upper = key.strip().upper()
    if not upper:
        return False
    if upper.endswith("_HEADER"):
        return False
    if upper.endswith("_TOKENS"):
        return False
    if upper.endswith("MAX_TOKENS"):
        return False
    return bool(SECRET_KEY_RE.search(upper))
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-004")


def test_qt2_6_no_hardcoded_secrets_in_source() -> None:
    """Ensure sensitive literals are not hardcoded in src/ Python modules."""
    violations: list[str] = []
    for path in _iter_py(PROJECT_ROOT / "src"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        for line_no, line in enumerate(_read(path).splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "os.getenv(" in line or "os.environ" in line or "${vault." in line:
                continue
            if ASSIGNMENT_RE.search(line):
                violations.append(f"{rel}:{line_no}: {stripped}")
    assert not violations, "QT2.6 hardcoded source secret assignments:\n" + "\n".join(violations)
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-004")


def test_qt2_6_sensitive_env_values_use_vault_or_scoped_files() -> None:
    """Check sensitive env values are in Vault expressions or scoped test/private env files."""
    violations: list[str] = []
    for env_path in _iter_env_files():
        rel = env_path.relative_to(PROJECT_ROOT).as_posix()
        for line_no, line in enumerate(_read(env_path).splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if not _is_sensitive_key(key):
                continue
            clean_value = value.strip().strip('"').strip("'")
            if not clean_value or clean_value.startswith("${vault.") or _is_placeholder(clean_value):
                continue
            in_test_env = rel.startswith("tests/env-")
            in_private_env = rel.startswith("private/")
            if in_test_env or in_private_env:
                continue
            violations.append(f"{rel}:{line_no}: {key} has non-vault sensitive value")

    assert not violations, "QT2.6 env-value separation violations:\n" + "\n".join(violations)
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-004")


def test_qt2_6_defaults_config_do_not_embed_plain_secrets() -> None:
    """Check defaults/config YAML for plaintext sensitive values."""
    violations: list[str] = []
    for rel in ("defaults.yaml", "default.yaml", "config.yaml"):
        path = PROJECT_ROOT / rel
        if not path.exists():
            continue
        for line_no, line in enumerate(_read(path).splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            if not _is_sensitive_key(key):
                continue
            val = value.strip().strip('"').strip("'")
            if not val or val.startswith("${vault.") or val in {"null", "~"}:
                continue
            violations.append(f"{rel}:{line_no}: {key.strip()} has plaintext value")

    assert not violations, "QT2.6 defaults/config plaintext secret violations:\n" + "\n".join(violations)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.quality, pytest.mark.pure, pytest.mark.fast]

