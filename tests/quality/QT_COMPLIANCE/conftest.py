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

"""Shared fixtures and helpers for QT compliance static analysis."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterable

import pytest


REQ_ID_PATTERN = re.compile(
    r"\b(?:FR|UC|NF|CS|BR|SV|BO)-?\d+(?:\.\d+)*\b|\bR(?:-DB)?(?:-\d+|\d+(?:\.\d+)*)\b"
)
TEST_ID_PATTERN = re.compile(r"\b(?:UT|ST|IT|AT|QT)\d+\.\d+\b")


def _iter_py_files(base: Path) -> list[Path]:
    return sorted(
        p
        for p in base.rglob("*.py")
        if "__pycache__" not in p.parts and ".venv" not in p.parts
    )


def _rel(path: Path, project_root: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def _line_iter(path: Path) -> Iterable[tuple[int, str]]:
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        yield line_no, line


def _matches_allowlist(item: str, patterns: Iterable[str]) -> bool:
    for pat in patterns:
        if re.search(pat, item):
            return True
    return False


def _parse_requirement_ids(requirements_text: str) -> list[str]:
    ids: set[str] = set()
    for line in requirements_text.splitlines():
        m = re.match(r"^\s*###\s+([A-Za-z0-9_.-]+)\b", line)
        if m:
            candidate = m.group(1).strip()
            if REQ_ID_PATTERN.fullmatch(candidate):
                ids.add(candidate)
        for found in REQ_ID_PATTERN.findall(line):
            ids.add(found)
    return sorted(ids)


def _parse_docs_test_ids(tests_text: str) -> list[str]:
    return sorted(set(TEST_ID_PATTERN.findall(tests_text)))


def _count_function_docstrings(paths: list[Path]) -> tuple[int, int]:
    total = 0
    with_doc = 0
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total += 1
                if ast.get_docstring(node):
                    with_doc += 1
    return total, with_doc


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def src_dir(project_root: Path) -> Path:
    return project_root / "src"


@pytest.fixture(scope="session")
def src_python_files(src_dir: Path) -> list[Path]:
    return _iter_py_files(src_dir)


@pytest.fixture(scope="session")
def test_python_files(project_root: Path) -> list[Path]:
    return _iter_py_files(project_root / "tests")


@pytest.fixture(scope="session")
def allowlist() -> dict:
    return {
        "rules": {
            "hardcoded_urls": [],
            "missing_headers": [],
            "min_docstring_coverage_pct": 80.0,
            "multi_import_allowed_modules": ["httpx"],
            "stdlib_logging_allowed": [],
        },
        "migration": {
            "os_env_for_config_allowed": [
                # Pytest env-isolation fixture deliberately mutates process env state.
                r"^tests/conftest.py$",
                # Runtime helper forwards selected environment variables into docker subprocesses.
                r"^tests/helpers/api_server.py$",
                # Quality rule checks string literals for environment access patterns.
                r"^tests/quality/QT_COMPLIANCE/test_qt_migration_completeness.py$",
                # ST suites below validate environment override precedence and reset semantics.
                r"^tests/system/ST1.10_ResponseFormattingDefault/test_response_formatting_default.py$",
                r"^tests/system/ST1.13_MCPServerAdminRBAC/test_mcp_server_admin_rbac.py$",
                r"^tests/system/ST1.14_WebUIFlow/test_web_ui_flow.py$",
                r"^tests/system/ST1.3_ClientAPILLM/test_client_api_llm.py$",
                r"^tests/system/ST1.5_CLIChat/test_cli_chat.py$",
                r"^tests/system/ST1.6_SessionPersistence/test_session_persistence.py$",
                r"^tests/system/ST1.7_ServerOnlyCLI/test_server_only_cli.py$",
                r"^tests/system/ST1.8_ResponseFormatting/test_response_formatting.py$",
                r"^tests/system/ST1.9_ResponseFormattingRaw/test_response_formatting_raw.py$",
                # UT parser contract test must set raw env keys directly for precedence verification.
                r"^tests/unit/UT1.1_ConfigTests/test_config_list_env_parsing.py$",
            ],
        },
        "traceability": {
            "untested_requirements_allowed": [],
            "min_functional_delivery_ratio": 1.0,
            "orphan_tests_allowed": [],
            "requirements_without_code_allowed": ["CS-001", "CS-002", "CS-003", "CS-004", "R7.3"],
            "orphan_test_files_allowed": [
                "tests/application/AT_AGENT_PROFILES/test_agent_profile_strategies.py",
                "tests/unit/UT1.11_CodeRunnerClient/test_code_runner_client.py",
                "tests/unit/UT1.40_UnauthAuthGate/test_unauth_auth_gate.py",
                "tests/unit/UT1.41_DemoInventoryGate/test_demo_inventory_gate.py",
                "tests/unit/UT1.42_SessionApiSchema/test_session_api_schema.py",
                "tests/unit/UT1.43_McpAnonGate/test_mcp_anon_gate.py",
                "tests/unit/UT1.44_A2aEventsAnonGate/test_a2a_events_anon_gate.py",
                "tests/unit/UT1.45_AdminDualKey/test_admin_dual_key.py",
                "tests/unit/UT1.46_IdamRouteGate/test_idam_route_gate.py",
                "tests/unit/UT_AGENT_ADAPTERS/test_agent_adapters.py",
                "tests/unit/UT_AGENT_PROFILE_API/test_profile_api_strategy.py",
                "tests/unit/UT_AGENT_RUNTIME_STRATEGIES/test_agent_runtime.py",
                "tests/unit/UT_AGENT_SIMPLE_DISPATCH_COMPAT/test_simple_dispatch_compat.py",
                "tests/unit/UT_AGENT_STRATEGY_NORMALIZATION/test_agent_strategy_normalization.py",
                "tests/unit/UT_PROMPT_TEMPLATE_OPTIN/test_prompt_template_optin.py",
            ],
        },
    }


@pytest.fixture(scope="session")
def helper_api():
    return {
        "rel": _rel,
        "line_iter": _line_iter,
        "matches_allowlist": _matches_allowlist,
        "parse_requirement_ids": _parse_requirement_ids,
        "parse_docs_test_ids": _parse_docs_test_ids,
        "count_function_docstrings": _count_function_docstrings,
        "req_id_pattern": REQ_ID_PATTERN,
        "test_id_pattern": TEST_ID_PATTERN,
    }
