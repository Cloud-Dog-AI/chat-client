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

"""W28A-144 QT2.7 bespoke implementation scan."""

from __future__ import annotations
import pytest

from pathlib import Path
import re

PROJECT_ROOT = Path(__file__).resolve().parents[3]

PATTERNS = {
    "direct_env_access": re.compile(r"\bos\.(?:getenv|environ)\b"),
    "stdlib_logging": re.compile(r"\blogging\.(?:getLogger|basicConfig)\s*\("),
    "manual_fastapi": re.compile(r"\bFastAPI\s*\("),
    "manual_auth": re.compile(r"\b(?:APIKeyHeader\s*\(|def\s+verify_token\s*\()"),
    "manual_http_server": re.compile(r"\b(?:aiohttp\.|uvicorn\.run\s*\()"),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _iter_py(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        p for p in root.rglob("*.py") if "__pycache__" not in p.parts and ".venv" not in p.parts
    )
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-002")


def test_qt2_7_no_bespoke_platform_replacements() -> None:
    """Scan src/ for bespoke patterns that should be platform package integrations."""
    violations: list[str] = []
    for path in _iter_py(PROJECT_ROOT / "src"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        for line_no, line in enumerate(_read(path).splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for name, regex in PATTERNS.items():
                if regex.search(line):
                    violations.append(f"{name}: {rel}:{line_no}: {stripped}")

    assert not violations, (
        "QT2.7 bespoke-code findings (use cloud_dog packages instead):\n"
        + "\n".join(violations)
    )

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.quality, pytest.mark.pure, pytest.mark.fast]

