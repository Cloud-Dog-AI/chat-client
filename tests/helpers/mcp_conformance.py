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

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import httpx

from cloud_dog_chat_client.mcp.conformance import ConformanceTarget, load_targets
from cloud_dog_api_kit.mcp.client_transport import (
    HTTPJSONRPCConfig,
    HTTPJSONRPCTransport,
    MCPTransport,
    StreamableHTTPConfig,
    StreamableHTTPTransport,
    StdioConfig,
    StdioTransport,
)

from tests.helpers.docker_runner import DockerContainer, DockerContainerSpec


def _require_cfg(cfg: Any, key: str) -> Any:
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _require_dict(cfg: Any, key: str) -> Dict[str, Any]:
    value = _require_cfg(cfg, key)
    if not isinstance(value, dict):
        raise RuntimeError(f"CRITICAL ERROR: configuration key '{key}' must be an object")
    return value


def _require_seconds(cfg: Any, key: str) -> float:
    value = _require_cfg(cfg, key)
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise RuntimeError(f"CRITICAL ERROR: configuration key '{key}' must be a number") from e


def create_transport(target: ConformanceTarget, defaults: Dict[str, Any]) -> MCPTransport:
    s = target.server

    api_key_header = s.api_key_header
    api_key = s.api_key
    accept_header = s.accept_header
    timeout_seconds = float(s.timeout_seconds)
    verify_tls = bool(s.verify_tls)

    if target.transport in ("streamable_http",):
        base_url = str(s.base_url or "")
        mcp_path = str(s.mcp_path or "")
        if not base_url:
            raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' missing server.base_url")
        if not mcp_path:
            raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' missing server.mcp_path")
        return StreamableHTTPTransport(
            StreamableHTTPConfig(
                base_url=base_url,
                mcp_path=mcp_path,
                api_key_header=api_key_header,
                api_key=api_key,
                accept_header=accept_header,
                timeout_seconds=timeout_seconds,
                verify_tls=verify_tls,
            )
        )

    if target.transport in ("http_jsonrpc",):
        base_url = str(s.base_url or "")
        messages_path = str(s.messages_path or defaults.get("messages_path") or "")
        health_path = str(s.health_path or defaults.get("health_path") or "")
        if not base_url:
            raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' missing server.base_url")
        if not messages_path:
            raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' missing server.messages_path")
        if not health_path:
            raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' missing server.health_path")
        return HTTPJSONRPCTransport(
            HTTPJSONRPCConfig(
                base_url=base_url,
                messages_path=messages_path,
                health_path=health_path,
                api_key_header=api_key_header,
                api_key=api_key,
                timeout_seconds=timeout_seconds,
                verify_tls=verify_tls,
            )
        )

    if target.transport in ("stdio",):
        command = str(s.command or "")
        args = s.args
        env = s.env
        framing = str(s.framing or "")

        if not command:
            raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' missing server.command")
        if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' server.args must be a list of strings")
        if env is not None and not isinstance(env, dict):
            raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' server.env must be an object")
        if not framing:
            raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' missing server.framing")

        return StdioTransport(StdioConfig(command=command, args=args, env=env, framing=framing))

    raise RuntimeError(f"Unsupported target transport: {target.transport}")


def _tool_requires_no_args(tool: Dict[str, Any]) -> bool:
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        return False

    t = schema.get("type")
    if t is not None and t != "object":
        return False

    required = schema.get("required")
    if required is None:
        return True

    return isinstance(required, list) and len(required) == 0


def _tool_required_fields(tool: Dict[str, Any]) -> list[str]:
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        return []
    required = schema.get("required")
    if isinstance(required, list):
        return [str(x) for x in required]
    return []


def pick_tools_call(target: ConformanceTarget, tools_result: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    call = target.server.tools_call
    if call is not None:
        return call.name, call.arguments

    tools = tools_result.get("tools")
    if not isinstance(tools, list):
        raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' tools/list returned invalid tools")

    for t in tools:
        if isinstance(t, dict) and t.get("name") and _tool_requires_no_args(t):
            return str(t["name"]), {}

    raise RuntimeError(
        f"CRITICAL ERROR: target '{target.name}' requires server.tools_call config because no tool with empty inputSchema was found"
    )


def _looks_like_method_not_found(exc: Exception) -> bool:
    msg = str(exc)
    return "-32601" in msg or "Method not found" in msg


async def wait_http_reachable(
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
                r = await client.get(url)
                if 200 <= r.status_code < 500:
                    return
            except Exception:
                pass

            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(f"Timed out waiting for HTTP reachability at {url}")
            await asyncio.sleep(poll_seconds)


async def run_conformance_for_target(cfg: Any, target: ConformanceTarget) -> None:
    defaults = cfg.get("mcp.defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}

    protocol_version = _require_cfg(cfg, "mcp.defaults.protocol_version")
    protocol_version = str(protocol_version).strip()
    if not protocol_version:
        raise RuntimeError("CRITICAL ERROR: mcp.defaults.protocol_version is required")

    _require_dict(cfg, "mcp.conformance.timeouts")
    require_all_tools = bool(_require_cfg(cfg, "mcp.conformance.require_all_tools"))
    ready_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.ready_seconds")
    request_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.request_seconds")
    poll_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.poll_seconds")
    docker_start_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_start_seconds")
    docker_stop_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_stop_seconds")
    docker_logs_seconds = _require_seconds(cfg, "mcp.conformance.timeouts.docker_logs_seconds")

    container: Optional[DockerContainer] = None
    if target.docker is not None:
        image = target.docker.image
        name_prefix = target.docker.name_prefix
        env = target.docker.env
        args = target.docker.args

        container = DockerContainer(
            DockerContainerSpec(
                image=image,
                name_prefix=name_prefix,
                env=env,
                args=args,
            )
        )
        container.start(timeout_seconds=docker_start_seconds)

    try:
        ready_url = str(target.server.ready_url or "").strip()
        if ready_url:
            await wait_http_reachable(
                ready_url,
                timeout_seconds=ready_seconds,
                request_timeout_seconds=request_seconds,
                poll_seconds=poll_seconds,
            )
        else:
            if target.transport in ("streamable_http",):
                base_url = str(target.server.base_url or "").rstrip("/")
                if base_url:
                    await wait_http_reachable(
                        f"{base_url}/mcp",
                        timeout_seconds=ready_seconds,
                        request_timeout_seconds=request_seconds,
                        poll_seconds=poll_seconds,
                    )
            if target.transport in ("http_jsonrpc",):
                base_url = str(target.server.base_url or "").rstrip("/")
                health = str(target.server.health_path or defaults.get("health_path") or "")
                if base_url and health:
                    await wait_http_reachable(
                        f"{base_url}{health}",
                        timeout_seconds=ready_seconds,
                        request_timeout_seconds=request_seconds,
                        poll_seconds=poll_seconds,
                    )

        transport = create_transport(target, defaults)
        await transport.connect()
        try:
            await transport.initialize(protocol_version=protocol_version)

            tools = await transport.tools_list()
            if not isinstance(tools, dict):
                raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' tools/list returned non-object")
            items = tools.get("tools")
            if not isinstance(items, list) or len(items) == 0:
                raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' tools/list returned empty tools")
            for tool in items:
                if not isinstance(tool, dict):
                    raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' tools/list tool is not an object")
                if not isinstance(tool.get("name"), str) or not tool["name"].strip():
                    raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' tools/list tool.name is required")
                if not isinstance(tool.get("description"), str) or not tool["description"].strip():
                    raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' tools/list tool.description is required")
                schema = tool.get("inputSchema")
                if not isinstance(schema, dict) or schema.get("type") != "object":
                    raise RuntimeError(
                        f"CRITICAL ERROR: target '{target.name}' tools/list tool.inputSchema must be object type"
                    )

            tool_cases = sorted(target.server.tool_cases or [], key=lambda c: c.order)
            tool_case_names = [c.name for c in tool_cases]

            tool_names = [str(t.get("name")) for t in items if isinstance(t, dict) and t.get("name")]
            case_names = set(tool_case_names)
            unknown_cases = case_names - set(tool_names)
            if unknown_cases:
                raise RuntimeError(
                    f"CRITICAL ERROR: target '{target.name}' server.tools contains unknown tools: {sorted(unknown_cases)}"
                )

            if require_all_tools:
                missing_cases = []
                for tool in items:
                    if not isinstance(tool, dict):
                        continue
                    name = str(tool.get("name") or "")
                    required_fields = _tool_required_fields(tool)
                    if required_fields and name not in case_names:
                        missing_cases.append(name)
                if missing_cases:
                    raise RuntimeError(
                        f"CRITICAL ERROR: target '{target.name}' missing server.tools entries for: {sorted(missing_cases)}"
                    )

            tool_lookup = {str(t.get("name")): t for t in items if isinstance(t, dict) and t.get("name")}

            async def _run_tool(name: str, args: Dict[str, Any], expect_error: bool) -> None:
                try:
                    result = await transport.tools_call(name, args)
                except Exception:
                    if expect_error:
                        return
                    raise
                if expect_error:
                    if not (isinstance(result, dict) and result.get("isError") is True):
                        raise RuntimeError(
                            f"CRITICAL ERROR: target '{target.name}' tool '{name}' expected error but succeeded"
                        )
                    return
                if not isinstance(result, dict):
                    raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' tool '{name}' returned non-object")
                if result.get("isError") is True:
                    raise RuntimeError(
                        f"CRITICAL ERROR: target '{target.name}' tool '{name}' returned isError=true: {result}"
                    )
                content = result.get("content")
                if not isinstance(content, list) or len(content) == 0:
                    raise RuntimeError(
                        f"CRITICAL ERROR: target '{target.name}' tool '{name}' returned empty content"
                    )

            for case in tool_cases:
                name = case.name
                if name not in tool_lookup:
                    raise RuntimeError(
                        f"CRITICAL ERROR: target '{target.name}' tool '{name}' not present in tools/list"
                    )
                args = case.arguments
                expect_error = bool(case.expect_error)
                await _run_tool(name, args, expect_error)

            for name, tool in tool_lookup.items():
                if name in case_names:
                    continue
                required_fields = _tool_required_fields(tool)
                if required_fields:
                    raise RuntimeError(
                        f"CRITICAL ERROR: target '{target.name}' missing server.tools args for tool '{name}'"
                    )
                await _run_tool(name, {}, False)

            tool_name, tool_args = pick_tools_call(target, tools)
            call = await transport.tools_call(tool_name, tool_args)
            if not isinstance(call, dict):
                raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' tools/call returned non-object")
            content = call.get("content")
            if not isinstance(content, list) or len(content) == 0:
                raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' tools/call returned empty content")
            for item in content:
                if not isinstance(item, dict):
                    raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' tools/call content item is not an object")
                if not isinstance(item.get("type"), str) or not item["type"].strip():
                    raise RuntimeError(
                        f"CRITICAL ERROR: target '{target.name}' tools/call content item.type is required"
                    )
                if item.get("text") is not None and not isinstance(item.get("text"), str):
                    raise RuntimeError(
                        f"CRITICAL ERROR: target '{target.name}' tools/call content item.text must be string"
                    )

            invalid_call = target.server.invalid_tools_call
            if invalid_call is None:
                raise RuntimeError(
                    f"CRITICAL ERROR: target '{target.name}' missing required server.invalid_tools_call configuration"
                )
            try:
                invalid_result = await transport.tools_call(invalid_call.name, invalid_call.arguments)
            except Exception:
                invalid_result = None

            if invalid_result is not None:
                if isinstance(invalid_result, dict) and invalid_result.get("isError") is True:
                    invalid_result = None

            if invalid_result is not None:
                raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' invalid tools/call unexpectedly succeeded")

            resources_required = target.server.resources_required
            if resources_required is None:
                raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' missing server.resources_required")
            resources_read_all = target.server.resources_read_all
            if resources_read_all is None:
                raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' missing server.resources_read_all")
            invalid_resource_uri = target.server.invalid_resource_uri

            try:
                resources = await transport.resources_list()
            except Exception as e:
                if not _looks_like_method_not_found(e):
                    raise
                resources = None

            if isinstance(resources, dict):
                items = resources.get("resources")
                if resources_required and (not isinstance(items, list) or len(items) == 0):
                    raise RuntimeError(f"CRITICAL ERROR: target '{target.name}' resources/list returned empty list")
                if isinstance(items, list) and len(items) > 0:
                    uri = None
                    for r in items:
                        if not isinstance(r, dict):
                            raise RuntimeError(
                                f"CRITICAL ERROR: target '{target.name}' resources/list item is not an object"
                            )
                        if r.get("uri"):
                            uri = str(r["uri"])
                            break

                    if uri:
                        uris_to_read = [uri]
                        if resources_read_all:
                            uris_to_read = [
                                str(r.get("uri"))
                                for r in items
                                if isinstance(r, dict) and r.get("uri")
                            ]
                        for read_uri in uris_to_read:
                            try:
                                read = await transport.resources_read(read_uri)
                            except Exception as e:
                                if not _looks_like_method_not_found(e):
                                    raise
                            else:
                                if not isinstance(read, dict):
                                    raise RuntimeError(
                                        f"CRITICAL ERROR: target '{target.name}' resources/read returned non-object"
                                    )
                                contents = read.get("contents")
                                if not isinstance(contents, list) or len(contents) == 0:
                                    raise RuntimeError(
                                        f"CRITICAL ERROR: target '{target.name}' resources/read returned empty contents"
                                    )
                                for content_item in contents:
                                    if not isinstance(content_item, dict):
                                        raise RuntimeError(
                                            f"CRITICAL ERROR: target '{target.name}' resources/read content item is not an object"
                                        )
                                    if not content_item.get("uri"):
                                        raise RuntimeError(
                                            f"CRITICAL ERROR: target '{target.name}' resources/read content uri is required"
                                        )
                                    if not content_item.get("mimeType"):
                                        raise RuntimeError(
                                            f"CRITICAL ERROR: target '{target.name}' resources/read content mimeType is required"
                                        )
                                    if content_item.get("text") is None and content_item.get("blob") is None:
                                        raise RuntimeError(
                                            f"CRITICAL ERROR: target '{target.name}' resources/read must include text or blob"
                                        )

            if invalid_resource_uri:
                try:
                    bad = await transport.resources_read(invalid_resource_uri)
                except Exception:
                    bad = None
                if bad is not None:
                    if not (isinstance(bad, dict) and bad.get("isError") is True):
                        raise RuntimeError(
                            f"CRITICAL ERROR: target '{target.name}' invalid resources/read unexpectedly succeeded"
                        )
        finally:
            await transport.close()
    finally:
        if container is not None:
            container.stop(timeout_seconds=docker_stop_seconds)
