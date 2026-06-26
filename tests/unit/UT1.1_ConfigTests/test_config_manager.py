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

from pathlib import Path

import pytest

from cloud_dog_chat_client.config import ConfigManager
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_1_requires_env_file_fixture(env_file):
    assert env_file
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_1_loads_default_yaml_and_env_override(tmp_path: Path, env_file, monkeypatch):
    project_root = tmp_path
    (project_root / "defaults.yaml").write_text(
        "utcfg:\n  provider: default_provider\n  base_url: http://example\n  model: default_model\n"
    )
    (project_root / "config.yaml").write_text("utcfg:\n  model: from_config\n")

    monkeypatch.setenv("CLOUD_DOG__UTCFG__MODEL", "from_env")

    cfg = ConfigManager(project_root=project_root)

    assert cfg.get("utcfg.provider") == "default_provider"
    assert cfg.get("utcfg.base_url") == "http://example"
    assert cfg.get("utcfg.model") == "from_env"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_1_precedence_os_env_over_env_file_config_and_default(tmp_path: Path, env_file, monkeypatch):
    # Covers: R2, R8, NFR5
    project_root = tmp_path
    (project_root / "defaults.yaml").write_text(
        "utcfg:\n  provider: default_provider\n  model: default_model\n"
    )
    (project_root / "config.yaml").write_text(
        "utcfg:\n  provider: config_provider\n  model: config_model\n"
    )
    custom_env = project_root / "custom.env"
    custom_env.write_text(
        "CLOUD_DOG__UTCFG__PROVIDER=env_provider\n"
        "CLOUD_DOG__UTCFG__MODEL=env_model\n"
    )

    monkeypatch.setenv("CLOUD_DOG__UTCFG__MODEL", "os_model")
    cfg = ConfigManager(project_root=project_root, env_file=str(custom_env))

    assert cfg.get("utcfg.provider") == "env_provider"
    assert cfg.get("utcfg.model") == "os_model"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_1_allows_no_env_file_and_reads_from_os_environ_only(tmp_path: Path, env_file, monkeypatch):
    project_root = tmp_path
    (project_root / "defaults.yaml").write_text("app:\n  name: from_default\n")
    (project_root / "config.yaml").write_text("app:\n  name: from_config\n")

    monkeypatch.setenv("CLOUD_DOG__APP__NAME", "from_os")
    cfg = ConfigManager(project_root=project_root, env_file=None)

    assert cfg.get("app.name") == "from_os"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_1_detects_project_root_from_cwd_default_yaml(tmp_path: Path, env_file, monkeypatch):
    (tmp_path / "defaults.yaml").write_text("log:\n  level: INFO\napp:\n  name: from_cwd\n")
    monkeypatch.chdir(tmp_path)
    cfg = ConfigManager()
    assert str(cfg.project_root) == str(tmp_path.resolve())
    assert cfg.get("app.name") == "from_cwd"

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.pure, pytest.mark.fast]
