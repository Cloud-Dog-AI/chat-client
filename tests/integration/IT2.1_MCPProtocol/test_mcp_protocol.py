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

import asyncio
import json
import time
from typing import Any, Dict

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_mcp import create_session, mcp_execute
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.docker_runner import DockerContainer, DockerContainerSpec
from tests.helpers.ollama_preflight import curl_ollama_tags


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _parse_args(value, key: str) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as e:
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON object") from e
        if not isinstance(parsed, dict):
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON object")
        return parsed
    raise RuntimeError(f"CRITICAL ERROR: {key} must be an object or JSON string")


def _require_seconds(cfg: ConfigManager, key: str) -> float:
    value = _require_cfg(cfg, key)
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise RuntimeError(f"CRITICAL ERROR: configuration key '{key}' must be a number") from e


@pytest.fixture(scope="module", autouse=True)
def _api_server(env_file):
    cfg = ConfigManager(env_file=env_file)
    curl_ollama_tags(cfg)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        yield None
    finally:
        stop_api(cfg, env_file=env_file)


@pytest.fixture(scope="module", autouse=True)
def _it2_1_docker(env_file):
    cfg = ConfigManager(env_file=env_file)
    use_docker = bool(cfg.get("mcp.it2_1.use_docker") or False)
    if not use_docker:
        yield None
        return

    image = str(_require_cfg(cfg, "mcp.it2_1.docker_image")).strip()
    docker_args = cfg.get("mcp.it2_1.docker.args") or []
    if not isinstance(docker_args, list) or not all(isinstance(x, str) for x in docker_args):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_1.docker.args must be a list of strings")
    env_extra = cfg.get("mcp.it2_1.docker.env") or {}
    if not isinstance(env_extra, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_1.docker.env must be an object")

    docker_start_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_start_seconds")
    docker_stop_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_stop_seconds")
    name_prefix = str(_require_cfg(cfg, "mcp.it2_1.docker.name_prefix")).strip()

    container = DockerContainer(
        DockerContainerSpec(
            image=image,
            name_prefix=name_prefix,
            env={str(k): str(v) for k, v in env_extra.items()},
            args=docker_args,
            remove=True,
        )
    )
    container.start(timeout_seconds=docker_start_seconds)
    try:
        yield None
    finally:
        container.stop(timeout_seconds=docker_stop_seconds)
@pytest.mark.IT
@pytest.mark.mcp
@pytest.mark.req("FR-011")


@pytest.mark.asyncio
async def test_it2_1_mcp_protocol(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    protocol_version = str(_require_cfg(cfg, "mcp.defaults.protocol_version")).strip()

    tool_name = str(_require_cfg(cfg, "mcp.it2_1.tools_call.name")).strip()
    tool_args = _parse_args(_require_cfg(cfg, "mcp.it2_1.tools_call.arguments"), "mcp.it2_1.tools_call.arguments")

    invalid_tool_name = str(_require_cfg(cfg, "mcp.it2_1.invalid_tools_call.name")).strip()
    invalid_tool_args = _parse_args(
        _require_cfg(cfg, "mcp.it2_1.invalid_tools_call.arguments"),
        "mcp.it2_1.invalid_tools_call.arguments",
    )
    invalid_method = str(_require_cfg(cfg, "mcp.it2_1.invalid_method")).strip()

    ready_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.ready_seconds")
    poll_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.poll_seconds")

    steps = [
        {"method": "tools/list"},
        {"method": "tools/call", "params": {"name": tool_name, "arguments": tool_args}},
        {"method": "tools/call", "params": {"name": invalid_tool_name, "arguments": invalid_tool_args}, "expect_error": True},
        {"method": invalid_method, "params": {}, "expect_error": True},
    ]

    server_index_raw = cfg.get("mcp.it2_1.server_index")
    server_index = 0
    if server_index_raw is not None:
        try:
            server_index = int(server_index_raw)
        except (TypeError, ValueError) as e:
            raise RuntimeError("CRITICAL ERROR: mcp.it2_1.server_index must be an integer") from e

    server_override = cfg.get("mcp.it2_1.server")
    if server_override is not None and not isinstance(server_override, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_1.server must be an object")

    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
        session_id = await create_session(client, metadata={"suite": "it2.1"})

        deadline = asyncio.get_running_loop().time() + ready_seconds
        while True:
            try:
                exec_resp = await mcp_execute(
                    client,
                    session_id=session_id,
                    server_index=None if isinstance(server_override, dict) else server_index,
                    server=server_override if isinstance(server_override, dict) else None,
                    protocol_version=protocol_version,
                    require_initialize=True,
                    steps=steps,
                )
                break
            except Exception:
                if asyncio.get_running_loop().time() >= deadline:
                    raise
                await asyncio.sleep(poll_seconds)

        results = exec_resp.get("results") or []
        if not results or not results[0].get("ok"):
            raise RuntimeError("CRITICAL ERROR: tools/list failed via API")
        if not results[1].get("ok"):
            raise RuntimeError(f"CRITICAL ERROR: tools/call failed via API: {results[1].get('error')}")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.llm, pytest.mark.mcp, pytest.mark.docker, pytest.mark.heavy]

