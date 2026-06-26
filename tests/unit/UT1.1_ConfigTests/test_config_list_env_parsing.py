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

import os
from pathlib import Path

from cloud_dog_chat_client.config import ConfigManager
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_1_parses_list_style_env_vars(tmp_path: Path, env_file):
    project_root = tmp_path
    (project_root / "defaults.yaml").write_text(
        "mcp:\n  defaults:\n    messages_path: /messages\n    health_path: /health\n    api_key_header: X-API-Key\n  servers: []\n"
    )
    (project_root / "config.yaml").write_text("mcp:\n  servers: []\n")

    os.environ["CLOUD_DOG__MCP__SERVERS__0__NAME"] = "sql-agent"
    os.environ["CLOUD_DOG__MCP__SERVERS__0__BASE_URL"] = "http://localhost:8081"
    os.environ["CLOUD_DOG__MCP__SERVERS__0__MESSAGES_PATH"] = "/messages"

    cfg = ConfigManager(project_root=project_root)

    assert cfg.get("mcp.servers.0.name") == "sql-agent"
    assert cfg.get("mcp.servers.0.base_url") == "http://localhost:8081"
    assert cfg.get("mcp.servers.0.messages_path") == "/messages"

    del os.environ["CLOUD_DOG__MCP__SERVERS__0__NAME"]
    del os.environ["CLOUD_DOG__MCP__SERVERS__0__BASE_URL"]
    del os.environ["CLOUD_DOG__MCP__SERVERS__0__MESSAGES_PATH"]

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.mcp, pytest.mark.fast]

