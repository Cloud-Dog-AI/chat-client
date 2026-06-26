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
from typing import Any, Dict, List, Optional

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


def _parse_tool_cases(cfg: ConfigManager, key: str) -> List[Dict[str, Any]]:
    raw = _require_cfg(cfg, key)
    if not isinstance(raw, list):
        raise RuntimeError(f"CRITICAL ERROR: {key} must be a list")

    cases: List[Dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RuntimeError(f"CRITICAL ERROR: {key}.{i} must be an object")
        name = str(item.get("name") or "").strip()
        if not name:
            raise RuntimeError(f"CRITICAL ERROR: {key}.{i}.name is required")
        args = _parse_args(item.get("arguments") or {}, f"{key}.{i}.arguments")
        order_raw = item.get("order", i)
        try:
            order = int(order_raw)
        except (TypeError, ValueError) as e:
            raise RuntimeError(f"CRITICAL ERROR: {key}.{i}.order must be an integer") from e
        cases.append({"name": name, "arguments": args, "order": order})

    return sorted(cases, key=lambda c: c["order"])


def _first_text(result: Dict[str, Any]) -> Optional[str]:
    content = result.get("content")
    if not isinstance(content, list):
        return None
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            return str(item.get("text") or "")
    return None


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
@pytest.mark.mcp
@pytest.mark.req("FR-011")


@pytest.mark.asyncio
async def test_it2_6_multi_mcp_servers(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = api_base_url(cfg)
    protocol_version = str(_require_cfg(cfg, "mcp.defaults.protocol_version")).strip()

    docker_servers = cfg.get("mcp.it2_6.docker.servers") or []
    if not isinstance(docker_servers, list):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_6.docker.servers must be a list")

    docker_start_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_start_seconds")
    docker_stop_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_stop_seconds")
    request_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.request_seconds")
    ready_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.ready_seconds")
    poll_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.poll_seconds")

    containers: List[DockerContainer] = []
    for item in docker_servers:
        if not isinstance(item, dict):
            raise RuntimeError("CRITICAL ERROR: mcp.it2_6.docker.servers entries must be objects")
        image = str(item.get("image") or "").strip()
        name_prefix = str(item.get("name_prefix") or "").strip()
        if not name_prefix:
            raise RuntimeError("CRITICAL ERROR: mcp.it2_6.docker.servers.name_prefix is required")
        if not image:
            raise RuntimeError("CRITICAL ERROR: mcp.it2_6.docker.servers.image is required")
        args = item.get("args") or []
        if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            raise RuntimeError("CRITICAL ERROR: mcp.it2_6.docker.servers.args must be a list of strings")
        env = item.get("env") or {}
        if not isinstance(env, dict):
            raise RuntimeError("CRITICAL ERROR: mcp.it2_6.docker.servers.env must be an object")
        ready_log = str(item.get("ready_log_substring") or "").strip()
        container = DockerContainer(
            DockerContainerSpec(
                image=image,
                name_prefix=name_prefix,
                env={str(k): str(v) for k, v in env.items()},
                args=args,
                remove=True,
            )
        )
        container.start(timeout_seconds=docker_start_seconds)
        if ready_log:
            container.wait_for_log_substring(
                ready_log,
                timeout_seconds=ready_seconds,
                poll_seconds=poll_seconds,
            )
        containers.append(container)

    try:
        timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
        async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
            session_id = await create_session(client, metadata={"suite": "it2.6"})

            server0_cases = _parse_tool_cases(cfg, "mcp.it2_6.servers.0.tools")
            server1_cases = _parse_tool_cases(cfg, "mcp.it2_6.servers.1.tools")

            require_initialize = bool(cfg.get("mcp.api.require_initialize") or False)

            server0_cfg_raw = cfg.get("mcp.it2_6.server0") or {}
            if server0_cfg_raw and not isinstance(server0_cfg_raw, dict):
                raise RuntimeError("CRITICAL ERROR: mcp.it2_6.server0 must be an object")
            server0_cfg: Dict[str, Any] = dict(server0_cfg_raw) if isinstance(server0_cfg_raw, dict) else {}
            if not server0_cfg:
                server0_cfg = {
                    "name": str(_require_cfg(cfg, "mcp.servers.0.name")).strip(),
                    "transport": str(_require_cfg(cfg, "mcp.servers.0.transport")).strip(),
                    "base_url": str(_require_cfg(cfg, "mcp.servers.0.base_url")).strip(),
                    "mcp_path": str(_require_cfg(cfg, "mcp.servers.0.mcp_path")).strip(),
                    "accept_header": str(_require_cfg(cfg, "mcp.servers.0.accept_header")).strip(),
                    "sse_accept_header": str(_require_cfg(cfg, "mcp.servers.0.sse_accept_header")).strip(),
                    "enable_sse": bool(cfg.get("mcp.servers.0.enable_sse") if cfg.get("mcp.servers.0.enable_sse") is not None else False),
                    "timeout_seconds": float(_require_cfg(cfg, "mcp.servers.0.timeout_seconds")),
                    "verify_tls": bool(cfg.get("mcp.servers.0.verify_tls") if cfg.get("mcp.servers.0.verify_tls") is not None else True),
                }

            server1_cfg_raw = cfg.get("mcp.it2_6.server1") or {}
            if server1_cfg_raw and not isinstance(server1_cfg_raw, dict):
                raise RuntimeError("CRITICAL ERROR: mcp.it2_6.server1 must be an object")
            server1_cfg: Dict[str, Any] = dict(server1_cfg_raw) if isinstance(server1_cfg_raw, dict) else {}

            server1_index_raw = cfg.get("mcp.it2_6.server1_index")
            try:
                server1_index = int(server1_index_raw) if server1_index_raw is not None else 1
            except (TypeError, ValueError) as e:
                raise RuntimeError("CRITICAL ERROR: mcp.it2_6.server1_index must be an integer") from e

            server0_base = str(server0_cfg.get("base_url") or "").rstrip("/")
            server0_path = str(server0_cfg.get("mcp_path") or "/mcp").strip()
            oauth_metadata_path = str(_require_cfg(cfg, "mcp.it2_6.servers.0.oauth.metadata_path")).strip()
            await _wait_http_reachable(
                f"{server0_base}{server0_path}",
                timeout_seconds=ready_seconds,
                request_timeout_seconds=request_seconds,
                poll_seconds=poll_seconds,
            )
            await _wait_http_reachable(
                f"{server0_base}{oauth_metadata_path}",
                timeout_seconds=ready_seconds,
                request_timeout_seconds=request_seconds,
                poll_seconds=poll_seconds,
            )

            oauth_payload: Dict[str, Any] = {"server": server0_cfg}
            deadline = asyncio.get_running_loop().time() + ready_seconds
            while True:
                oauth_resp = await client.post(
                    f"/sessions/{session_id}/mcp/oauth/example-remote",
                    json=oauth_payload,
                )
                if oauth_resp.status_code == 200:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeError(
                        f"CRITICAL ERROR: OAuth flow failed for IT2.6 (status={oauth_resp.status_code})"
                    )
                await asyncio.sleep(poll_seconds)

            access_token = str(oauth_resp.json().get("access_token") or "")
            if not access_token:
                raise RuntimeError("CRITICAL ERROR: OAuth token response missing access_token")

            server0_override = {
                "name": str(server0_cfg.get("name") or "example-remote-server").strip(),
                "transport": str(server0_cfg.get("transport") or "streamable_http").strip(),
                "base_url": str(server0_cfg.get("base_url") or "").strip(),
                "mcp_path": str(server0_cfg.get("mcp_path") or "/mcp").strip(),
                "accept_header": str(server0_cfg.get("accept_header") or "application/json, text/event-stream").strip(),
                "sse_accept_header": str(server0_cfg.get("sse_accept_header") or "application/json, text/event-stream").strip(),
                "protocol_version": protocol_version,
                "auth_bearer_token": access_token,
                "enable_sse": bool(server0_cfg.get("enable_sse") if server0_cfg.get("enable_sse") is not None else False),
                "timeout_seconds": float(server0_cfg.get("timeout_seconds") or 30.0),
                "verify_tls": bool(server0_cfg.get("verify_tls") if server0_cfg.get("verify_tls") is not None else True),
            }

            if server1_cfg:
                server1_base = str(server1_cfg.get("base_url") or "").rstrip("/")
                server1_path = str(server1_cfg.get("mcp_path") or "/mcp").strip()
            else:
                server1_base = str(_require_cfg(cfg, f"mcp.servers.{server1_index}.base_url")).rstrip("/")
                server1_path = str(_require_cfg(cfg, f"mcp.servers.{server1_index}.mcp_path")).strip()
            await _wait_http_reachable(
                f"{server1_base}{server1_path}",
                timeout_seconds=ready_seconds,
                request_timeout_seconds=request_seconds,
                poll_seconds=poll_seconds,
            )

            deadline = asyncio.get_running_loop().time() + ready_seconds
            while True:
                try:
                    await mcp_execute(
                        client,
                        session_id=session_id,
                        server=server0_override,
                        protocol_version=protocol_version,
                        require_initialize=require_initialize,
                        steps=[{"method": "tools/list"}],
                    )
                    await mcp_execute(
                        client,
                        session_id=session_id,
                        server_index=None if server1_cfg else server1_index,
                        server=server1_cfg if server1_cfg else None,
                        protocol_version=protocol_version,
                        require_initialize=require_initialize,
                        steps=[{"method": "tools/list"}],
                    )
                    break
                except Exception:
                    if asyncio.get_running_loop().time() >= deadline:
                        raise
                    await asyncio.sleep(poll_seconds)

            results0 = await mcp_execute(
                client,
                session_id=session_id,
                server=server0_override,
                protocol_version=protocol_version,
                require_initialize=require_initialize,
                steps=[{"method": "tools/call", "params": {"name": c["name"], "arguments": c["arguments"]}} for c in server0_cases],
            )

            for item in results0.get("results") or []:
                if not item.get("ok"):
                    raise RuntimeError(f"CRITICAL ERROR: server0 tool failed via API: {item.get('error')}")

            chain_text = None
            if results0.get("results"):
                chain_text = _first_text(results0["results"][0].get("result") or {})

            chained_cases = []
            for case in server1_cases:
                args = dict(case["arguments"])
                if "{{from_server0}}" in json.dumps(args):
                    if not chain_text:
                        raise RuntimeError("CRITICAL ERROR: missing chained tool output from server0")
                    args_json = json.dumps(args).replace("{{from_server0}}", chain_text)
                    args = json.loads(args_json)
                chained_cases.append({"name": case["name"], "arguments": args})

            results1 = await mcp_execute(
                client,
                session_id=session_id,
                server_index=None if server1_cfg else server1_index,
                server=server1_cfg if server1_cfg else None,
                protocol_version=protocol_version,
                require_initialize=require_initialize,
                steps=[{"method": "tools/call", "params": {"name": c["name"], "arguments": c["arguments"]}} for c in chained_cases],
            )

            for item in results1.get("results") or []:
                if not item.get("ok"):
                    raise RuntimeError(f"CRITICAL ERROR: server1 tool failed via API: {item.get('error')}")

            transcript_resp = await client.get(f"/sessions/{session_id}/transcript")
            if transcript_resp.status_code != 200:
                raise RuntimeError("CRITICAL ERROR: transcript endpoint failed")
            transcript = transcript_resp.json()
            events = transcript.get("events") or []
            if not isinstance(events, list):
                raise RuntimeError("CRITICAL ERROR: transcript events must be a list")

            event_type = str(_require_cfg(cfg, "mcp.it2_6.transcript.event_type")).strip()
            min_events = int(_require_cfg(cfg, "mcp.it2_6.transcript.min_events"))
            count = 0
            for event in events:
                if isinstance(event, dict) and event.get("event_type") == event_type:
                    count += 1
            if count < min_events:
                raise RuntimeError(
                    f"CRITICAL ERROR: expected >= {min_events} transcript events of type {event_type}, got {count}"
                )
    except Exception as exc:
        logs = []
        for container in containers:
            try:
                log = container.logs()
            except Exception:
                log = "<unable to fetch logs>"
            logs.append(f"[{container.name}] {log[-2000:]}")
        raise RuntimeError(
            "CRITICAL ERROR: IT2.6 failed; container logs captured for debugging:\n" + "\n\n".join(logs)
        ) from exc
    finally:
        for container in containers:
            container.stop(timeout_seconds=docker_stop_seconds)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.llm, pytest.mark.mcp, pytest.mark.docker, pytest.mark.heavy]
