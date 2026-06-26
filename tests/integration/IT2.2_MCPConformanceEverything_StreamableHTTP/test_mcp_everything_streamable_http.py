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


def _port_from_base_url(base_url: str) -> int:
    u = urlparse(base_url)
    if not u.port:
        raise RuntimeError(f"CRITICAL ERROR: base_url must include explicit port for docker-run readiness: {base_url}")
    return int(u.port)


def _it2_2_server(cfg: ConfigManager) -> dict:
    raw = cfg.get("mcp.it2_2.server") or {}
    if raw and not isinstance(raw, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_2.server must be an object")
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
async def test_it2_2_everything_streamable_http_conformance(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    protocol_version = str(_require_cfg(cfg, "mcp.defaults.protocol_version")).strip()

    server_cfg = _it2_2_server(cfg)
    transport = str(server_cfg.get("transport") or "").lower().strip()
    if transport not in ("streamable_http", "streamablehttp", "mcp"):
        raise RuntimeError(
            "CRITICAL ERROR: IT2.2 requires mcp.servers.0.transport=streamable_http (or alias). "
            f"Got: {transport}"
        )

    use_docker = bool(cfg.get("mcp.conformance.use_docker") or False)
    ready_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.ready_seconds")
    poll_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.poll_seconds")
    docker_start_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_start_seconds")
    docker_stop_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_stop_seconds")

    container = None
    if use_docker:
        image = str(cfg.get("mcp.conformance.docker_image") or "").strip()
        if not image:
            raise RuntimeError("CRITICAL ERROR: missing required configuration key: mcp.conformance.docker_image")

        target_base_url = str(server_cfg.get("base_url") or "").strip()
        if not target_base_url:
            raise RuntimeError("CRITICAL ERROR: mcp.it2_2.server.base_url is required")
        port = _port_from_base_url(target_base_url)
        port_env_key = str(_require_cfg(cfg, "mcp.it2_2.docker.port_env_key")).strip()
        if not port_env_key:
            raise RuntimeError("CRITICAL ERROR: mcp.it2_2.docker.port_env_key is required")
        docker_args = _require_cfg(cfg, "mcp.it2_2.docker.args")
        if not isinstance(docker_args, list) or not all(isinstance(x, str) for x in docker_args):
            raise RuntimeError("CRITICAL ERROR: mcp.it2_2.docker.args must be a list of strings")
        name_prefix = str(_require_cfg(cfg, "mcp.it2_2.docker.name_prefix")).strip()

        container = DockerContainer(
            DockerContainerSpec(
                image=image,
                name_prefix=name_prefix,
                env={port_env_key: str(port)},
                args=docker_args,
                remove=True,
            )
        )
        container.start(timeout_seconds=docker_start_seconds)

    try:
        timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
        async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
            session_id = await create_session(client, metadata={"suite": "it2.2"})

            tool_name = str(_require_cfg(cfg, "mcp.it2_2.tools_call.name")).strip()
            if not tool_name:
                raise RuntimeError("CRITICAL ERROR: mcp.it2_2.tools_call.name is required")
            tool_args = _parse_args(_require_cfg(cfg, "mcp.it2_2.tools_call.arguments"), "mcp.it2_2.tools_call.arguments")

            steps = [
                {"method": "tools/list"},
                {"method": "tools/call", "params": {"name": tool_name, "arguments": tool_args}},
                {"method": "resources/list"},
            ]

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
            if not results[1].get("ok"):
                raise RuntimeError(f"CRITICAL ERROR: tools/call failed via API: {results[1].get('error')}")
            if not results[2].get("ok"):
                raise RuntimeError("CRITICAL ERROR: resources/list failed via API")

            resources = results[2].get("result") or {}
            uri = None
            for r in resources.get("resources") or []:
                if isinstance(r, dict) and r.get("uri"):
                    uri = str(r["uri"])
                    break
            if not uri:
                raise RuntimeError("CRITICAL ERROR: resources/list returned no usable uri")

            read_resp = await mcp_execute(
                client,
                session_id=session_id,
                server=server_cfg,
                protocol_version=protocol_version,
                require_initialize=True,
                steps=[{"method": "resources/read", "params": {"uri": uri}}],
            )
            if not read_resp.get("results") or not read_resp["results"][0].get("ok"):
                raise RuntimeError("CRITICAL ERROR: resources/read failed via API")
    finally:
        if container is not None:
            container.stop(timeout_seconds=docker_stop_seconds)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.llm, pytest.mark.mcp, pytest.mark.docker, pytest.mark.heavy]

