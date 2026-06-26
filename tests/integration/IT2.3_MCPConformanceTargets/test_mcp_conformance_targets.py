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
import re
from typing import Any, Dict, List

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.mcp.conformance import ConformanceTarget, load_targets
from tests.helpers.api_mcp import create_session, mcp_execute
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.docker_runner import ensure_image_present
from tests.helpers.ollama_preflight import curl_ollama_tags

_DOCKER_IMAGE_RE = re.compile(r"^[a-z0-9./_-]+:[A-Za-z0-9._-]+$")


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


def _server_spec(target: ConformanceTarget) -> Dict[str, Any]:
    server = target.server
    spec: Dict[str, Any] = {"name": target.name, "transport": target.transport}
    if target.transport in ("streamable_http", "http_jsonrpc"):
        spec.update(
            {
                "base_url": server.base_url,
                "mcp_path": server.mcp_path,
                "messages_path": server.messages_path,
                "health_path": server.health_path,
                "api_key_header": server.api_key_header,
                "api_key": server.api_key,
                "accept_header": server.accept_header,
                "timeout_seconds": server.timeout_seconds,
                "verify_tls": server.verify_tls,
            }
        )
    if target.transport == "stdio":
        spec.update(
            {
                "command": server.command,
                "args": server.args or [],
                "env": server.env or {},
                "framing": server.framing,
            }
        )
    return spec


def _docker_image_from_target(target: ConformanceTarget) -> str | None:
    """Extract the Docker image name from a stdio conformance target, when present."""
    server = target.server
    if target.transport != "stdio":
        return None
    if str(server.command or "").strip() != "docker":
        return None
    for arg in server.args or []:
        candidate = str(arg or "").strip()
        if _DOCKER_IMAGE_RE.fullmatch(candidate):
            return candidate
    return None


@pytest.fixture(scope="module", autouse=True)
def _api_server(env_file):
    cfg = ConfigManager(env_file=env_file)
    for target in load_targets(cfg):
        image = _docker_image_from_target(target)
        if image:
            ensure_image_present(image, timeout_seconds=600.0)
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
async def test_it2_3_conformance_targets(env_file):
    cfg = ConfigManager(env_file=env_file)
    targets = load_targets(cfg)
    if not targets:
        raise RuntimeError("CRITICAL ERROR: no conformance targets configured")

    protocol_version = str(_require_cfg(cfg, "mcp.defaults.protocol_version")).strip()
    ready_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.ready_seconds")
    poll_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.poll_seconds")
    require_all_tools = bool(cfg.get("mcp.conformance.require_all_tools") or False)

    base_url = api_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
        session_id = await create_session(client, metadata={"suite": "it2.3"})

        for target in targets:
            steps: List[Dict[str, Any]] = [{"method": "tools/list"}]
            if target.server.tools_call:
                steps.append(
                    {
                        "method": "tools/call",
                        "params": {
                            "name": target.server.tools_call.name,
                            "arguments": target.server.tools_call.arguments,
                        },
                    }
                )
            if target.server.invalid_tools_call:
                steps.append(
                    {
                        "method": "tools/call",
                        "params": {
                            "name": target.server.invalid_tools_call.name,
                            "arguments": target.server.invalid_tools_call.arguments,
                        },
                        "expect_error": True,
                    }
                )
            if target.server.tool_cases:
                for case in sorted(target.server.tool_cases, key=lambda c: c.order):
                    steps.append(
                        {
                            "method": "tools/call",
                            "params": {"name": case.name, "arguments": case.arguments},
                            "expect_error": case.expect_error,
                        }
                    )

            deadline = asyncio.get_running_loop().time() + ready_seconds
            while True:
                try:
                    exec_resp = await mcp_execute(
                        client,
                        session_id=session_id,
                        server=_server_spec(target),
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
                raise RuntimeError(f"CRITICAL ERROR: tools/list failed for target {target.name}")

            if require_all_tools and target.server.tool_cases:
                for item in results[1:]:
                    if not item.get("ok") and not item.get("expect_error"):
                        raise RuntimeError(f"CRITICAL ERROR: tool call failed for target {target.name}")

            if target.server.resources_required:
                resources_resp = await mcp_execute(
                    client,
                    session_id=session_id,
                    server=_server_spec(target),
                    protocol_version=protocol_version,
                    require_initialize=True,
                    steps=[{"method": "resources/list"}],
                )
                res_list = resources_resp.get("results") or []
                if not res_list or not res_list[0].get("ok"):
                    raise RuntimeError(f"CRITICAL ERROR: resources/list failed for target {target.name}")

                resources = res_list[0].get("result") or {}
                uris = [
                    str(r.get("uri"))
                    for r in resources.get("resources") or []
                    if isinstance(r, dict) and r.get("uri")
                ]
                if not uris and target.server.invalid_resource_uri:
                    uris = [target.server.invalid_resource_uri]

                if target.server.resources_read_all:
                    for uri in uris:
                        read_resp = await mcp_execute(
                            client,
                            session_id=session_id,
                            server=_server_spec(target),
                            protocol_version=protocol_version,
                            require_initialize=True,
                            steps=[{"method": "resources/read", "params": {"uri": uri}}],
                        )
                        if not read_resp.get("results") or not read_resp["results"][0].get("ok"):
                            raise RuntimeError(f"CRITICAL ERROR: resources/read failed for target {target.name}")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.llm, pytest.mark.mcp, pytest.mark.heavy]
