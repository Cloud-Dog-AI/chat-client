import pytest
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

from cloud_dog_chat_client.cli import app as cli_app
from cloud_dog_chat_client.cli.app import _build_cfg, _resolve_env_files
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_1_cli_build_cfg_without_env_files(env_file):
    cfg = _build_cfg([])
    assert cfg is not None
    assert cfg.env_file is None
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_1_cli_resolve_env_files_allows_empty_without_defaults(monkeypatch, env_file):
    monkeypatch.delenv("CLOUD_DOG__APP__ENV_FILE", raising=False)
    monkeypatch.setattr(cli_app.Path, "exists", lambda self: False)
    assert _resolve_env_files([]) == []

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.pure, pytest.mark.fast]

