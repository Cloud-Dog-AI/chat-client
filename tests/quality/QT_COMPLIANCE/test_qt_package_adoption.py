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

"""QT checks for platform package adoption and bespoke bypasses."""

from __future__ import annotations
import pytest

from pathlib import Path


def _all_src_text(src_python_files: list[Path]) -> str:
    return "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in src_python_files)
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-002")


def test_config_uses_cloud_dog_config(src_python_files):
    text = _all_src_text(src_python_files)
    assert "cloud_dog_config" in text, "cloud_dog_config import not found in src/"
    assert "yaml.safe_load(" not in text, "Found yaml.safe_load usage in src/"
    assert "yaml.load(" not in text, "Found yaml.load usage in src/"
    assert "dotenv.load_dotenv" not in text, "Found dotenv.load_dotenv usage in src/"
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-002")


def test_logging_uses_cloud_dog_logging(project_root: Path, src_python_files, allowlist, helper_api):
    text = _all_src_text(src_python_files)
    assert "cloud_dog_logging" in text, "cloud_dog_logging import not found in src/"
    assert "logging.basicConfig(" not in text, "Found logging.basicConfig usage in src/"
    violations = []
    for path in src_python_files:
        for line_no, line in helper_api["line_iter"](path):
            if "logging.getLogger(" not in line:
                continue
            item = f"{helper_api['rel'](path, project_root)}:{line_no}:{line.strip()}"
            if not helper_api["matches_allowlist"](item, allowlist["rules"]["stdlib_logging_allowed"]):
                violations.append(item)
    assert not violations, "Found disallowed logging.getLogger usage:\n" + "\n".join(violations)
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-002")


def test_api_uses_cloud_dog_api_kit(src_python_files):
    text = _all_src_text(src_python_files)
    assert "cloud_dog_api_kit" in text, "cloud_dog_api_kit import not found in src/"
    assert "FastAPI(" not in text, "Found raw FastAPI() instantiation in src/"
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-002")


def test_auth_uses_cloud_dog_idam(src_python_files):
    text = _all_src_text(src_python_files)
    assert "cloud_dog_idam" in text, "cloud_dog_idam import not found in src/"
    assert "APIKeyHeader(" not in text, "Found direct APIKeyHeader usage in src/"
    assert "def verify_token(" not in text, "Found bespoke verify_token in src/"
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-002")


def test_no_bespoke_db_access(src_python_files):
    text = _all_src_text(src_python_files)
    assert "create_engine(" not in text, "Found direct SQLAlchemy create_engine() usage in src/"
    assert "sqlite3.connect(" not in text, "Found direct sqlite3.connect() usage in src/"
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-002")


def test_no_bespoke_llm_calls(src_python_files):
    text = _all_src_text(src_python_files)
    assert "openai.OpenAI(" not in text, "Found direct openai.OpenAI() usage in src/"
    assert "ollama.chat(" not in text, "Found direct ollama.chat() usage in src/"
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-002")


def test_no_bespoke_vdb_calls(src_python_files):
    text = _all_src_text(src_python_files)
    assert "chromadb.Client(" not in text, "Found direct chromadb.Client() usage in src/"
    assert "qdrant_client.QdrantClient(" not in text, "Found direct qdrant client usage in src/"
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-002")


def test_pyproject_declares_platform_packages(project_root: Path):
    pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
    required = [
        "cloud_dog_config",
        "cloud_dog_logging",
        "cloud_dog_api_kit",
        "cloud_dog_idam",
        "cloud_dog_llm",
        "cloud_dog_db",
        "cloud_dog_jobs",
        "cloud-dog-storage",
    ]
    missing = [pkg for pkg in required if pkg not in pyproject]
    assert not missing, f"Missing required platform packages in pyproject.toml: {missing}"
@pytest.mark.QT
@pytest.mark.cli
@pytest.mark.req("NF-002")


def test_platform_package_declaration_versions_are_aligned(project_root: Path):
    """W28A-118D: requirements must not drift below pyproject platform package floors."""
    pyproject_text = (project_root / "pyproject.toml").read_text(encoding="utf-8")
    requirements = (project_root / "requirements.txt").read_text(encoding="utf-8")

    expected = {
        "cloud_dog_llm": ">=0.3.1",
        "cloud_dog_jobs": ">=0.3.0",
        "cloud-dog-storage": ">=0.1.1",
    }
    for package_name, version_floor in expected.items():
        pyproject_token = f"{package_name}{version_floor}"
        requirements_token = f"{package_name}{version_floor}"
        assert pyproject_token in pyproject_text, f"{package_name} drift in pyproject.toml"
        assert requirements_token in requirements, f"{package_name} drift in requirements.txt"

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.quality, pytest.mark.llm, pytest.mark.vdb, pytest.mark.db, pytest.mark.fast]
