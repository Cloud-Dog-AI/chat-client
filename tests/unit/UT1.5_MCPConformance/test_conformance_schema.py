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

import pytest

from cloud_dog_chat_client.mcp.conformance import load_targets


class DummyCfg:
    def __init__(self, data):
        self._data = data

    def get(self, path: str, default=None):
        return self._data.get(path, default)
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_ut1_5_conformance_load_targets_parses_and_normalizes():
    # Covers: R5, R6, NFR2
    cfg = DummyCfg(
        {
            "mcp.defaults": {"timeout_seconds": 12.0, "verify_tls": False},
            "mcp.conformance.targets": [
                {
                    "name": "t1",
                    "transport": "mcp",
                    "server": {
                        "base_url": "http://example",
                        "mcp_path": "/mcp",
                        "timeout_seconds": 12.0,
                        "verify_tls": False,
                    },
                },
                {
                    "name": "t2",
                    "transport": "messages",
                    "server": {
                        "base_url": "http://example",
                        "messages_path": "/messages",
                        "health_path": "/health",
                        "timeout_seconds": 12.0,
                        "verify_tls": False,
                    },
                },
                {
                    "name": "t3",
                    "transport": "stdio",
                    "server": {
                        "command": "docker",
                        "args": ["run", "--rm", "-i", "image:latest"],
                        "framing": "newline",
                        "tools_call": {"name": "x", "arguments": "{\"a\":1}"},
                        "invalid_tools_call": {"name": "nope", "arguments": "{}"},
                        "tools": [
                            {"name": "t1", "arguments": {"x": 1}, "order": 2},
                            {"name": "t2", "arguments": "{\"y\":2}", "order": 1, "expect_error": True},
                        ],
                        "timeout_seconds": 12.0,
                        "verify_tls": False,
                    },
                    "docker": {"image": "image:latest", "name_prefix": "t3-docker", "args": ["--foo"]},
                },
            ],
        }
    )

    targets = load_targets(cfg)
    assert [t.name for t in targets] == ["t1", "t2", "t3"]

    assert targets[0].transport == "streamable_http"
    assert targets[0].server.base_url == "http://example"
    assert targets[0].server.timeout_seconds == 12.0
    assert targets[0].server.verify_tls is False

    assert targets[1].transport == "http_jsonrpc"
    assert targets[1].server.messages_path == "/messages"

    assert targets[2].transport == "stdio"
    assert targets[2].server.framing == "newline"
    assert targets[2].server.tools_call is not None
    assert targets[2].server.tools_call.name == "x"
    assert targets[2].server.tools_call.arguments == {"a": 1}
    assert targets[2].server.invalid_tools_call is not None
    assert targets[2].server.invalid_tools_call.name == "nope"
    assert targets[2].server.invalid_tools_call.arguments == {}
    assert targets[2].server.tool_cases is not None
    assert [c.name for c in sorted(targets[2].server.tool_cases, key=lambda c: c.order)] == ["t2", "t1"]
    assert [c.arguments for c in sorted(targets[2].server.tool_cases, key=lambda c: c.order)] == [{"y": 2}, {"x": 1}]
    assert [c.expect_error for c in sorted(targets[2].server.tool_cases, key=lambda c: c.order)] == [True, False]
    assert targets[2].docker is not None
    assert targets[2].docker.image == "image:latest"
    assert targets[2].docker.name_prefix == "t3-docker"
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_ut1_5_conformance_load_targets_rejects_invalid_tools_call_arguments():
    cfg = DummyCfg(
        {
            "mcp.defaults": {},
            "mcp.conformance.targets": [
                {
                    "name": "bad",
                    "transport": "stdio",
                    "server": {
                        "command": "x",
                        "args": [],
                        "tools_call": {"name": "x", "arguments": "not-json"},
                        "timeout_seconds": 12.0,
                        "verify_tls": False,
                    },
                }
            ],
        }
    )

    with pytest.raises(RuntimeError):
        load_targets(cfg)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.mcp, pytest.mark.docker, pytest.mark.fast]
