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
from typing import Dict
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


async def _wait_http_reachable(
    url: str,
    *,
    timeout_seconds: float,
    request_timeout_seconds: float,
    poll_seconds: float,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    async with httpx.AsyncClient(timeout=request_timeout_seconds, follow_redirects=True) as client:
        while True:
            try:
                resp = await client.get(url)
                if 200 <= resp.status_code < 500:
                    return
            except Exception:
                pass
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(f"CRITICAL ERROR: timed out waiting for HTTP reachability at {url}")
            await asyncio.sleep(poll_seconds)


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
@pytest.mark.cli
@pytest.mark.req("FR-011")


@pytest.mark.asyncio
async def test_it2_10_legacy_sse(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    protocol_version = str(_require_cfg(cfg, "mcp.defaults.protocol_version")).strip()

    server_cfg_raw = cfg.get("mcp.it2_10.server") or {}
    if server_cfg_raw and not isinstance(server_cfg_raw, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_10.server must be an object")
    server_cfg: Dict[str, object] = dict(server_cfg_raw) if isinstance(server_cfg_raw, dict) else {}
    if not server_cfg:
        server_cfg = {
            "name": str(_require_cfg(cfg, "mcp.servers.0.name")).strip(),
            "base_url": str(_require_cfg(cfg, "mcp.servers.0.base_url")).strip(),
            "sse_path": str(_require_cfg(cfg, "mcp.servers.0.sse_path")).strip(),
            "messages_path": str(_require_cfg(cfg, "mcp.servers.0.messages_path")).strip(),
            "accept_header": str(_require_cfg(cfg, "mcp.servers.0.accept_header")).strip(),
            "timeout_seconds": float(_require_cfg(cfg, "mcp.servers.0.timeout_seconds")),
            "verify_tls": bool(cfg.get("mcp.servers.0.verify_tls") if cfg.get("mcp.servers.0.verify_tls") is not None else True),
        }

    use_docker = bool(cfg.get("mcp.it2_10.use_docker") or False)
    ready_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.ready_seconds")
    request_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.request_seconds")
    poll_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.poll_seconds")
    docker_start_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_start_seconds")
    docker_stop_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_stop_seconds")

    container = None
    if use_docker:
        image = str(_require_cfg(cfg, "mcp.it2_10.docker_image")).strip()
        mcp_base_url = str(server_cfg.get("base_url") or "").strip()
        port = int(urlparse(mcp_base_url).port or 0)
        if not port:
            raise RuntimeError(f"CRITICAL ERROR: base_url must include explicit port: {mcp_base_url}")

        env_extra = cfg.get("mcp.it2_10.docker.env") or {}
        if not isinstance(env_extra, dict):
            raise RuntimeError("CRITICAL ERROR: mcp.it2_10.docker.env must be an object")
        name_prefix = str(_require_cfg(cfg, "mcp.it2_10.docker.name_prefix")).strip()
        docker_network = str(cfg.get("mcp.it2_10.docker.network") or "host").strip()
        docker_args = cfg.get("mcp.it2_10.docker.args") or []
        if not isinstance(docker_args, list) or not all(isinstance(x, str) for x in docker_args):
            raise RuntimeError("CRITICAL ERROR: mcp.it2_10.docker.args must be a list of strings")

        container = DockerContainer(
            DockerContainerSpec(
                image=image,
                name_prefix=name_prefix,
                env={str(k): str(v) for k, v in env_extra.items()},
                args=docker_args,
                network=docker_network,
                remove=True,
            )
        )
        container.start(timeout_seconds=docker_start_seconds)
        ready_log = str(cfg.get("mcp.it2_10.docker.ready_log_substring") or "").strip()
        if ready_log:
            container.wait_for_log_substring(
                ready_log,
                timeout_seconds=ready_seconds,
                poll_seconds=poll_seconds,
            )

    try:
        timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
        async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
            session_id = await create_session(client, metadata={"suite": "it2.10"})

            server_base = str(server_cfg.get("base_url") or "").rstrip("/")
            oauth_metadata_path = str(_require_cfg(cfg, "mcp.it2_10.oauth.metadata_path")).strip()
            await _wait_http_reachable(
                f"{server_base}{oauth_metadata_path}",
                timeout_seconds=ready_seconds,
                request_timeout_seconds=request_seconds,
                poll_seconds=poll_seconds,
            )

            deadline = asyncio.get_running_loop().time() + ready_seconds
            while True:
                oauth_resp = await client.post(f"/sessions/{session_id}/mcp/oauth/example-remote", json={"server": server_cfg})
                if oauth_resp.status_code == 200:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeError(
                        f"CRITICAL ERROR: OAuth flow failed for IT2.10 (status={oauth_resp.status_code})"
                    )
                await asyncio.sleep(poll_seconds)
            access_token = str(oauth_resp.json().get("access_token") or "")
            if not access_token:
                raise RuntimeError("CRITICAL ERROR: OAuth token response missing access_token")

            tool_name = str(_require_cfg(cfg, "mcp.it2_10.tools_call.name")).strip()
            tool_args = _parse_args(_require_cfg(cfg, "mcp.it2_10.tools_call.arguments"), "mcp.it2_10.tools_call.arguments")
            invalid_tool_name = str(_require_cfg(cfg, "mcp.it2_10.invalid_tools_call.name")).strip()
            invalid_tool_args = _parse_args(
                _require_cfg(cfg, "mcp.it2_10.invalid_tools_call.arguments"),
                "mcp.it2_10.invalid_tools_call.arguments",
            )

            server_override: Dict[str, object] = {
                "name": str(server_cfg.get("name") or "example-remote-legacy-sse").strip(),
                "transport": "legacy_sse",
                "base_url": str(server_cfg.get("base_url") or "").strip(),
                "sse_path": str(server_cfg.get("sse_path") or "/sse").strip(),
                "messages_path": str(server_cfg.get("messages_path") or "/message").strip(),
                "accept_header": str(server_cfg.get("accept_header") or "application/json").strip(),
                "protocol_version": protocol_version,
                "auth_bearer_token": access_token,
                "timeout_seconds": float(server_cfg.get("timeout_seconds") or 30.0),
                "verify_tls": bool(server_cfg.get("verify_tls") if server_cfg.get("verify_tls") is not None else True),
            }

            steps = [
                {"method": "tools/list"},
                {"method": "tools/call", "params": {"name": tool_name, "arguments": tool_args}},
                {"method": "tools/call", "params": {"name": invalid_tool_name, "arguments": invalid_tool_args}, "expect_error": True},
            ]

            deadline = asyncio.get_running_loop().time() + ready_seconds
            while True:
                try:
                    exec_resp = await mcp_execute(
                        client,
                        session_id=session_id,
                        server=server_override,
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
                raise RuntimeError("CRITICAL ERROR: tools/list failed via legacy SSE")
            if not results[1].get("ok"):
                raise RuntimeError("CRITICAL ERROR: tools/call failed via legacy SSE")
    finally:
        if container:
            container.stop(timeout_seconds=docker_stop_seconds)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.llm, pytest.mark.mcp, pytest.mark.docker, pytest.mark.heavy]

