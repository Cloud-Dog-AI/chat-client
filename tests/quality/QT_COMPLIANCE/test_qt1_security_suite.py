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

"""W28A-144 QT1 security compliance checks (QT1.1-QT1.4)."""

from __future__ import annotations
import pytest

from pathlib import Path
import re

PROJECT_ROOT = Path(__file__).resolve().parents[3]


SECRET_LOG_RE = re.compile(
    r"(?:logger\.|logging\.|print\().*(?:password|secret|token|api[_-]?key)\s*[:=]\s*['\"][^'\"]+['\"]",
    re.IGNORECASE,
)

US_SPELLINGS = {
    "color": "colour",
    "behavior": "behaviour",
    "initialize": "initialise",
    "optimization": "optimisation",
    "optimize": "optimise",
    "authorization": "authorisation",
}

PROTOCOL_SPELLING_ALLOWLIST = {
    ("docs/MCP-REFERENCE.md", "authorization"),
    ("docs/MCP-REFERENCE.md", "initialize"),
    ("docs/TESTS.md", "initialize"),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _iter_py(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        p for p in root.rglob("*.py") if "__pycache__" not in p.parts and ".venv" not in p.parts
    )


def _all_src_text() -> str:
    src_root = PROJECT_ROOT / "src"
    return "\n".join(_read(path) for path in _iter_py(src_root))
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-008")


def test_qt1_1_secrets_never_logged() -> None:
    """QT1.1: Ensure plaintext credentials are not logged/printed from source."""
    violations: list[str] = []
    for path in _iter_py(PROJECT_ROOT / "src"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        for line_no, line in enumerate(_read(path).splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if SECRET_LOG_RE.search(line):
                violations.append(f"{rel}:{line_no}: {line.strip()}")
    assert not violations, "QT1.1 violations found:\n" + "\n".join(violations)
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-008")


def test_qt1_2_path_traversal_prevention() -> None:
    """QT1.2: Ensure path-capable code has traversal guard primitives."""
    src_text = _all_src_text()
    file_ops_tokens = ("open(", "Path(", "read_text(", "write_text(", "rglob(")
    guard_tokens = (
        "resolve(",
        "relative_to(",
        "normpath(",
        "realpath(",
        "commonpath(",
        "is_relative_to(",
        "PathScopeError",
        "sandbox",
    )
    has_file_ops = any(token in src_text for token in file_ops_tokens)
    has_guards = any(token in src_text for token in guard_tokens)
    assert (not has_file_ops) or has_guards, (
        "QT1.2 failed: file/path operations detected but no traversal guard patterns "
        "(resolve/relative_to/normpath/commonpath/sandbox) found in src/."
    )
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-008")


def test_qt1_3_domain_specific_safety() -> None:
    """QT1.3: Domain-specific safety invariant per project."""
    project_name = PROJECT_ROOT.name
    src_text = _all_src_text()

    if project_name == "chat-client":
        assert "verify=False" not in src_text, "QT1.3 failed: TLS verification bypass (verify=False) detected"
        return

    if project_name == "expert-agent-mcp-server":
        audit_files = [
            path for path in _iter_py(PROJECT_ROOT / "src") if "audit" in path.as_posix().lower()
        ]
        audit_text = "\n".join(_read(path).lower() for path in audit_files)
        assert "audit" in audit_text and (
            "sign" in audit_text or "signature" in audit_text or "hmac" in audit_text
        ), "QT1.3 failed: no audit-signing implementation signal found"
        return

    if project_name == "sql-agent-mcp-server":
        violations: list[str] = []
        regexes = [
            re.compile(r"execute\s*\(\s*f[\"']", re.IGNORECASE),
            re.compile(r"execute\s*\(\s*[\"'][^\"']*\{", re.IGNORECASE),
            re.compile(r"execute\s*\(\s*[^,]+\+", re.IGNORECASE),
        ]
        for path in _iter_py(PROJECT_ROOT / "src"):
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            for line_no, line in enumerate(_read(path).splitlines(), 1):
                if any(rx.search(line) for rx in regexes):
                    violations.append(f"{rel}:{line_no}: {line.strip()}")
        assert not violations, "QT1.3 SQL injection-risk patterns found:\n" + "\n".join(violations)
        return

    if project_name == "notification-agent-mcp-server":
        pii_module = PROJECT_ROOT / "src/core/compliance/pii_redaction.py"
        assert pii_module.exists(), "QT1.3 failed: PII redaction module missing"
        assert "pii_redaction" in src_text, "QT1.3 failed: pii_redaction is not referenced from src/"
        return

    if project_name == "file-mcp-server":
        policy = PROJECT_ROOT / "src/file_tools/scope/policy.py"
        assert policy.exists(), "QT1.3 failed: file scope policy module missing"
        policy_text = _read(policy)
        assert "scope" in policy_text.lower() and (
            "resolve(" in policy_text or "sandbox" in policy_text.lower()
        ), "QT1.3 failed: path scope/sandbox guards not evident in file policy"
        return

    if project_name == "index-retriever-mcp-server":
        scope_file = PROJECT_ROOT / "src/index_tools/security/scope.py"
        assert scope_file.exists(), "QT1.3 failed: URI scope enforcement module missing"
        scope_text = _read(scope_file).lower()
        assert "allow" in scope_text and (
            "scope" in scope_text or "trusted" in scope_text
        ), "QT1.3 failed: URI allowlist/scope enforcement signal missing"
        return

    if project_name == "imap-mcp-server":
        rbac_file = PROJECT_ROOT / "src/imap_hub_core/security/rbac.py"
        assert rbac_file.exists(), "QT1.3 failed: RBAC module missing"
        rbac_text = _read(rbac_file)
        assert "default_deny" in rbac_text and "True" in rbac_text, (
            "QT1.3 failed: default-deny RBAC posture not detected"
        )
        return

    if project_name == "git-mcp-server":
        workspace_file = PROJECT_ROOT / "src/git_tools/workspaces/manager.py"
        assert workspace_file.exists(), "QT1.3 failed: workspace manager missing"
        workspace_text = _read(workspace_file)
        assert "core.hooksPath" in workspace_text and "/dev/null" in workspace_text, (
            "QT1.3 failed: git hooks disablement not detected"
        )
        return

    if project_name == "search-mcp-server":
        assert "urlparse(" in src_text or "urllib.parse" in src_text, (
            "QT1.3 failed: URL parsing/validation signal missing"
        )
        assert (
            "skip_internal_links" in src_text
            or "localhost" in src_text
            or "127.0.0.1" in src_text
            or "is_private" in src_text
            or "private" in src_text
        ), "QT1.3 failed: SSRF/internal URL filtering signal missing"
        return

    raise AssertionError(f"QT1.3 has no domain safety rule for project: {project_name}")
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-008")


def test_qt1_4_uk_english_compliance() -> None:
    """QT1.4: Check public docs for disallowed US spellings."""
    docs_paths: list[Path] = []
    for rel in ("README.md", "REQUIREMENTS.md", "ARCHITECTURE.md", "TESTS.md"):
        candidate = PROJECT_ROOT / rel
        if candidate.exists():
            docs_paths.append(candidate)
    docs_root = PROJECT_ROOT / "docs"
    if docs_root.exists():
        docs_paths.extend(sorted(docs_root.rglob("*.md")))

    violations: list[str] = []
    for path in docs_paths:
        text = _read(path).lower()
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        for us_spelling, uk_spelling in US_SPELLINGS.items():
            if (rel, us_spelling) in PROTOCOL_SPELLING_ALLOWLIST:
                continue
            if re.search(rf"\b{re.escape(us_spelling)}\b", text):
                violations.append(f"{rel}: found '{us_spelling}' (expected '{uk_spelling}')")

    assert not violations, "QT1.4 US spelling violations found:\n" + "\n".join(violations)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.quality, pytest.mark.smtp, pytest.mark.mcp, pytest.mark.fast]
