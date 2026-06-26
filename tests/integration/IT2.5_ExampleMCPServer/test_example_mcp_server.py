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
from typing import Any, Dict, List
from urllib.parse import urlparse

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


def _require_seconds(cfg: ConfigManager, key: str) -> float:
    value = _require_cfg(cfg, key)
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise RuntimeError(f"CRITICAL ERROR: configuration key '{key}' must be a number") from e


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


def _it2_5_server(cfg: ConfigManager) -> Dict[str, Any]:
    raw = cfg.get("mcp.it2_5.server") or {}
    if raw and not isinstance(raw, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_5.server must be an object")
    if raw:
        return dict(raw)

    return {
        "name": str(_require_cfg(cfg, "mcp.servers.0.name")).strip(),
        "transport": str(_require_cfg(cfg, "mcp.servers.0.transport")).strip(),
        "base_url": str(_require_cfg(cfg, "mcp.servers.0.base_url")).strip(),
        "mcp_path": str(_require_cfg(cfg, "mcp.servers.0.mcp_path")).strip(),
        "accept_header": str(_require_cfg(cfg, "mcp.servers.0.accept_header")).strip(),
        "sse_accept_header": str(_require_cfg(cfg, "mcp.servers.0.sse_accept_header")).strip(),
        "timeout_seconds": float(_require_cfg(cfg, "mcp.servers.0.timeout_seconds")),
        "verify_tls": bool(
            cfg.get("mcp.servers.0.verify_tls")
            if cfg.get("mcp.servers.0.verify_tls") is not None
            else True
        ),
        "enable_sse": bool(
            cfg.get("mcp.servers.0.enable_sse")
            if cfg.get("mcp.servers.0.enable_sse") is not None
            else False
        ),
    }

def _parse_tool_cases(cfg: ConfigManager) -> List[Dict[str, Any]]:
    raw = _require_cfg(cfg, "mcp.it2_5.tools")
    if not isinstance(raw, list):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_5.tools must be a list")

    cases: List[Dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RuntimeError(f"CRITICAL ERROR: mcp.it2_5.tools.{i} must be an object")
        name = str(item.get("name") or "").strip()
        if not name:
            raise RuntimeError(f"CRITICAL ERROR: mcp.it2_5.tools.{i}.name is required")
        args = _parse_args(item.get("arguments") or {}, f"mcp.it2_5.tools.{i}.arguments")
        order_raw = item.get("order", i)
        try:
            order = int(order_raw)
        except (TypeError, ValueError) as e:
            raise RuntimeError(f"CRITICAL ERROR: mcp.it2_5.tools.{i}.order must be an integer") from e
        cases.append({"name": name, "arguments": args, "order": order})

    return sorted(cases, key=lambda c: c["order"])


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
@pytest.mark.IT
@pytest.mark.mcp
@pytest.mark.req("FR-011")


@pytest.mark.asyncio
async def test_it2_5_example_mcp_server_streamable_http(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    protocol_version = str(_require_cfg(cfg, "mcp.defaults.protocol_version")).strip()
    server_cfg = _it2_5_server(cfg)

    use_docker = bool(cfg.get("mcp.it2_5.use_docker") or False)
    ready_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.ready_seconds")
    poll_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.poll_seconds")
    docker_start_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_start_seconds")
    docker_stop_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_stop_seconds")

    container = None
    if use_docker:
        image = str(_require_cfg(cfg, "mcp.it2_5.docker_image")).strip()
        docker_args = cfg.get("mcp.it2_5.docker.args") or []
        if not isinstance(docker_args, list) or not all(isinstance(x, str) for x in docker_args):
            raise RuntimeError("CRITICAL ERROR: mcp.it2_5.docker.args must be a list of strings")

        mcp_base_url = str(server_cfg.get("base_url") or "").strip()
        if not mcp_base_url:
            raise RuntimeError("CRITICAL ERROR: mcp.it2_5.server.base_url is required")
        port = int(urlparse(mcp_base_url).port or 0)
        if not port:
            raise RuntimeError(f"CRITICAL ERROR: base_url must include explicit port: {mcp_base_url}")

        env_extra = cfg.get("mcp.it2_5.docker.env") or {}
        if not isinstance(env_extra, dict):
            raise RuntimeError("CRITICAL ERROR: mcp.it2_5.docker.env must be an object")
        name_prefix = str(_require_cfg(cfg, "mcp.it2_5.docker.name_prefix")).strip()

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
        timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
        async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
            session_id = await create_session(client, metadata={"suite": "it2.5"})
            steps = [{"method": "tools/list"}]
            for case in _parse_tool_cases(cfg):
                steps.append({"method": "tools/call", "params": {"name": case["name"], "arguments": case["arguments"]}})

            deadline = asyncio.get_running_loop().time() + ready_seconds
            while True:
                try:
                    exec_resp = await mcp_execute(
                        client,
                        session_id=session_id,
                        server=server_cfg,
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

            for idx, case in enumerate(_parse_tool_cases(cfg), start=1):
                item = results[idx]
                if not item.get("ok"):
                    raise RuntimeError(f"CRITICAL ERROR: tool '{case['name']}' failed via API: {item.get('error')}")
                result = item.get("result") or {}
                if result.get("isError") is True:
                    raise RuntimeError(f"CRITICAL ERROR: tool '{case['name']}' returned isError=true")
    finally:
        if container:
            container.stop(timeout_seconds=docker_stop_seconds)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.llm, pytest.mark.mcp, pytest.mark.docker, pytest.mark.heavy]

