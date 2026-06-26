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
from typing import Any, Dict, List

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.docker_runner import DockerContainer, DockerContainerSpec
from tests.helpers.ollama_preflight import curl_ollama_tags


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _require_seconds(cfg: ConfigManager, key: str) -> float:
    value = _require_cfg(cfg, key)
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise RuntimeError(f"CRITICAL ERROR: configuration key '{key}' must be a number") from e


def _parse_tool_cases(cfg: ConfigManager) -> List[Dict[str, Any]]:
    raw = _require_cfg(cfg, "mcp.at1_2.tools")
    if not isinstance(raw, list):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_2.tools must be a list")
    cases: List[Dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RuntimeError(f"CRITICAL ERROR: mcp.at1_2.tools.{i} must be an object")
        name = str(item.get("name") or "").strip()
        if not name:
            raise RuntimeError(f"CRITICAL ERROR: mcp.at1_2.tools.{i}.name is required")
        args = item.get("arguments") or {}
        if not isinstance(args, dict):
            try:
                args = json.loads(args)
            except Exception as e:
                raise RuntimeError(
                    f"CRITICAL ERROR: mcp.at1_2.tools.{i}.arguments must be an object"
                ) from e
        cases.append({"name": name, "arguments": args})
    return cases


def _parse_ready_status_codes(cfg: ConfigManager) -> set[int]:
    raw = cfg.get("mcp.at1_2.ready_status_codes")
    if raw is None:
        return {200}
    values = raw if isinstance(raw, list) else [raw]
    parsed: set[int] = set()
    for value in values:
        try:
            parsed.add(int(value))
        except (TypeError, ValueError) as e:
            raise RuntimeError(
                "CRITICAL ERROR: mcp.at1_2.ready_status_codes must be a list of integers"
            ) from e
    if not parsed:
        raise RuntimeError(
            "CRITICAL ERROR: mcp.at1_2.ready_status_codes must contain at least one status code"
        )
    return parsed


async def _wait_http_ready(
    url: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
    ready_status_codes: set[int],
) -> None:
    deadline = time.time() + timeout_seconds
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
        while time.time() < deadline:
            try:
                resp = await client.get(url)
                if resp.status_code in ready_status_codes:
                    return
            except Exception:
                pass
            await asyncio.sleep(poll_seconds)
    raise RuntimeError(f"CRITICAL ERROR: timed out waiting for HTTP readiness: {url}")


@pytest.fixture(scope="module", autouse=True)
def _at1_2_docker(env_file):
    cfg = ConfigManager(env_file=env_file)
    use_docker = bool(cfg.get("mcp.at1_2.use_docker") or False)
    if not use_docker:
        yield None
        return

    server_index_raw = cfg.get("mcp.at1_2.server_index")
    try:
        server_index = int(server_index_raw) if server_index_raw is not None else 0
    except (TypeError, ValueError) as e:
        raise RuntimeError("CRITICAL ERROR: mcp.at1_2.server_index must be an integer") from e

    image = str(_require_cfg(cfg, "mcp.at1_2.docker_image")).strip()
    docker_args = cfg.get("mcp.at1_2.docker.args") or []
    if not isinstance(docker_args, list) or not all(isinstance(x, str) for x in docker_args):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_2.docker.args must be a list of strings")
    env_extra = cfg.get("mcp.at1_2.docker.env") or {}
    if not isinstance(env_extra, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_2.docker.env must be an object")
    name_prefix = str(_require_cfg(cfg, "mcp.at1_2.docker.name_prefix")).strip()

    docker_start_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_start_seconds")
    docker_stop_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_stop_seconds")

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
        ready_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.ready_seconds")
        poll_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.poll_seconds")
        health_path = str(_require_cfg(cfg, "mcp.at1_2.health_path")).strip()
        base_url = str(_require_cfg(cfg, f"mcp.servers.{server_index}.base_url")).strip()
        ready_url = f"{base_url.rstrip('/')}{health_path}"
        ready_status_codes = _parse_ready_status_codes(cfg)
        asyncio.run(
            _wait_http_ready(
                ready_url,
                timeout_seconds=ready_seconds,
                poll_seconds=poll_seconds,
                ready_status_codes=ready_status_codes,
            )
        )
        yield None
    finally:
        container.stop(timeout_seconds=docker_stop_seconds)


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
@pytest.mark.AT
@pytest.mark.mcp
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_2_chat_client_mcp_tools(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    server_index_raw = cfg.get("mcp.at1_2.server_index")
    try:
        server_index = int(server_index_raw) if server_index_raw is not None else 0
    except (TypeError, ValueError) as e:
        raise RuntimeError("CRITICAL ERROR: mcp.at1_2.server_index must be an integer") from e
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
        headers = api_headers(cfg)
        resp = await client.post("/sessions", json={"metadata": {"suite": "at1.2"}}, headers=headers)
        assert resp.status_code == 200
        session_id = resp.json().get("session_id")
        assert session_id

        list_resp = await client.post(
            f"/sessions/{session_id}/mcp/tools/list",
            json={"server_index": server_index, "require_initialize": True},
            headers=headers,
        )
        assert list_resp.status_code == 200
        data = list_resp.json()
        tools = data.get("tools")
        assert isinstance(tools, list)
        assert len(tools) > 0

        expected_tools = cfg.get("mcp.at1_2.expected_tools") or []
        if not isinstance(expected_tools, list):
            raise RuntimeError("CRITICAL ERROR: mcp.at1_2.expected_tools must be a list")
        for name in expected_tools:
            if name not in [t.get("name") for t in tools if isinstance(t, dict)]:
                raise RuntimeError(f"CRITICAL ERROR: missing expected tool: {name}")

        for case in _parse_tool_cases(cfg):
            call_resp = await client.post(
                f"/sessions/{session_id}/mcp/tools/call",
                json={
                    "server_index": server_index,
                    "name": case["name"],
                    "arguments": case["arguments"],
                    "require_initialize": True,
                },
                headers=headers,
            )
            assert call_resp.status_code == 200
            result = call_resp.json()
            assert isinstance(result, dict)
            if result.get("isError") is True:
                raise RuntimeError(f"CRITICAL ERROR: tool '{case['name']}' returned isError=true")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.llm, pytest.mark.mcp, pytest.mark.docker, pytest.mark.heavy]
