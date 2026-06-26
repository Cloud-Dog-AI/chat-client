#!/usr/bin/env python3
"""
Root pytest configuration.

WHY THIS FILE EXISTS:
- Pytest parses CLI args before collecting tests.
- Options like `--env` must be registered early, so they must live in a root-level conftest.

RULES.md:
- Tests MUST require --env (handled in tests fixtures).
"""

import sys
from pathlib import Path


# Ensure repo-local src/ is importable for tests without requiring editable install.
_PROJECT_ROOT = Path(__file__).parent
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def pytest_addoption(parser):
    """Register global CLI options used across test suites."""
    project_root = Path(__file__).parent

    try:
        parser.addoption(
            "--env",
            action="store",
            default=None,
            help="Path to environment file (REQUIRED). Example: --env private/env-local",
        )
    except ValueError:
        # cloud_dog_config pytest plugin may already register --env.
        pass

    try:
        parser.addoption(
            "--output-dir",
            action="store",
            default=str(project_root / "working" / "test_output"),
            help="Output directory for test results (defaults under working/)",
        )
    except ValueError:
        # Safe for mixed plugin environments where this option already exists.
        pass
