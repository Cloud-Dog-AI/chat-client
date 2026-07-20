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

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ConformanceToolsCall:
    name: str
    arguments: Dict[str, Any]


@dataclass
class ConformanceToolCase:
    name: str
    arguments: Dict[str, Any]
    order: int = 0
    expect_error: bool = False


@dataclass
class ConformanceServer:
    timeout_seconds: Optional[float] = None
    read_timeout_seconds: Optional[float] = None
    verify_tls: Optional[bool] = None
    api_key_header: Optional[str] = None
    api_key: Optional[str] = None
    auth_bearer_token: Optional[str] = None
    accept_header: Optional[str] = None
    sse_accept_header: Optional[str] = None
    enable_sse: Optional[bool] = None

    base_url: Optional[str] = None
    mcp_path: Optional[str] = None
    messages_path: Optional[str] = None
    health_path: Optional[str] = None
    ready_url: Optional[str] = None

    command: Optional[str] = None
    args: Optional[list[str]] = None
    env: Optional[Dict[str, str]] = None
    framing: Optional[str] = None

    tools_call: Optional[ConformanceToolsCall] = None
    invalid_tools_call: Optional[ConformanceToolsCall] = None
    tool_cases: Optional[list[ConformanceToolCase]] = None

    resources_required: Optional[bool] = None
    resources_read_all: Optional[bool] = None
    invalid_resource_uri: Optional[str] = None


@dataclass
class ConformanceDocker:
    image: str
    name_prefix: str
    env: Optional[Dict[str, str]] = None
    args: Optional[list[str]] = None


@dataclass
class ConformanceTarget:
    name: str
    transport: str
    server: ConformanceServer
    docker: Optional[ConformanceDocker]


def _parse_tools_call(target_name: str, raw: Any) -> ConformanceToolsCall:
    """Internal helper to tools call for this module."""
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"CRITICAL ERROR: target '{target_name}' server.tools_call must be an object"
        )

    name = str(raw.get("name") or "").strip()
    arguments: Any = raw.get("arguments")
    if not name:
        raise RuntimeError(
            f"CRITICAL ERROR: target '{target_name}' server.tools_call.name is required"
        )

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception as e:
            raise RuntimeError(
                f"CRITICAL ERROR: target '{target_name}' server.tools_call.arguments is not valid JSON"
            ) from e

    if not isinstance(arguments, dict):
        raise RuntimeError(
            f"CRITICAL ERROR: target '{target_name}' server.tools_call.arguments must be an object or JSON string"
        )

    return ConformanceToolsCall(name=name, arguments=arguments)


def _parse_tool_cases(
    target_name: str, raw: Any
) -> Optional[list[ConformanceToolCase]]:
    """Internal helper to tool cases for this module."""
    # Covers: R5, R6, NFR2
    # Conformance tool execution is normalised into deterministic ordered cases.
    if raw is None:
        return None

    if not isinstance(raw, list):
        raise RuntimeError(
            f"CRITICAL ERROR: target '{target_name}' server.tools must be a list"
        )

    cases: list[ConformanceToolCase] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"CRITICAL ERROR: target '{target_name}' server.tools.{i} must be an object"
            )
        base = _parse_tools_call(target_name, item)
        order_raw = item.get("order", i)
        try:
            order = int(order_raw)
        except (TypeError, ValueError) as e:
            raise RuntimeError(
                f"CRITICAL ERROR: target '{target_name}' server.tools.{i}.order must be an integer"
            ) from e
        expect_error = (
            bool(item.get("expect_error")) if "expect_error" in item else False
        )
        cases.append(
            ConformanceToolCase(
                name=base.name,
                arguments=base.arguments,
                order=order,
                expect_error=expect_error,
            )
        )

    return cases


def _normalize_transport(raw: str) -> str:
    """Internal helper to transport for this module."""
    t = str(raw or "").strip().lower()
    if t in ("mcp", "streamablehttp", "streamable_http"):
        return "streamable_http"
    if t in ("http", "messages", "http_jsonrpc"):
        return "http_jsonrpc"
    return t


def _parse_server(
    target_name: str, raw: Any, defaults: Dict[str, Any]
) -> ConformanceServer:
    """Internal helper to server for this module."""
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"CRITICAL ERROR: target '{target_name}' server must be an object"
        )

    timeout_raw = (
        raw.get("timeout_seconds")
        if raw.get("timeout_seconds") is not None
        else defaults.get("timeout_seconds")
    )
    if timeout_raw is None:
        raise RuntimeError(
            f"CRITICAL ERROR: target '{target_name}' server.timeout_seconds is required"
        )
    timeout_seconds = float(timeout_raw)

    read_timeout_raw = raw.get("read_timeout_seconds")
    read_timeout_seconds = (
        float(read_timeout_raw) if read_timeout_raw is not None else None
    )

    verify_tls_raw = (
        raw.get("verify_tls")
        if raw.get("verify_tls") is not None
        else defaults.get("verify_tls")
    )
    if verify_tls_raw is None:
        raise RuntimeError(
            f"CRITICAL ERROR: target '{target_name}' server.verify_tls is required"
        )
    verify_tls = bool(verify_tls_raw)

    api_key_header: Optional[str] = str(
        raw.get("api_key_header") or defaults.get("api_key_header") or ""
    ).strip()
    api_key: Optional[str] = str(raw.get("api_key") or "").strip()
    auth_bearer_token: Optional[str] = str(
        raw.get("auth_bearer_token") or ""
    ).strip()
    accept_header: Optional[str] = str(
        raw.get("accept_header") or defaults.get("accept_header") or ""
    ).strip()
    sse_accept_header: Optional[str] = str(
        raw.get("sse_accept_header") or defaults.get("sse_accept_header") or ""
    ).strip()
    enable_sse_raw = (
        raw.get("enable_sse")
        if raw.get("enable_sse") is not None
        else defaults.get("enable_sse")
    )
    enable_sse = bool(enable_sse_raw) if enable_sse_raw is not None else None
    if not api_key_header:
        api_key_header = None
    if not api_key:
        api_key = None
    if not auth_bearer_token:
        auth_bearer_token = None
    if not accept_header:
        accept_header = None
    if not sse_accept_header:
        sse_accept_header = None

    tools_call_raw = raw.get("tools_call")
    tools_call = (
        _parse_tools_call(target_name, tools_call_raw)
        if tools_call_raw is not None
        else None
    )
    invalid_tools_call_raw = raw.get("invalid_tools_call")
    invalid_tools_call = (
        _parse_tools_call(target_name, invalid_tools_call_raw)
        if invalid_tools_call_raw is not None
        else None
    )
    tool_cases = _parse_tool_cases(target_name, raw.get("tools"))

    base_url = raw.get("base_url")
    base_url = str(base_url).strip() if base_url is not None else None
    mcp_path = (
        raw.get("mcp_path")
        if raw.get("mcp_path") is not None
        else defaults.get("mcp_path")
    )
    mcp_path = str(mcp_path).strip() if mcp_path is not None else None
    messages_path = raw.get("messages_path")
    messages_path = str(messages_path).strip() if messages_path is not None else None
    health_path = raw.get("health_path")
    health_path = str(health_path).strip() if health_path is not None else None
    ready_url = raw.get("ready_url")
    ready_url = str(ready_url).strip() if ready_url is not None else None

    command = raw.get("command")
    command = str(command).strip() if command is not None else None
    args = raw.get("args")
    if args is not None:
        if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            raise RuntimeError(
                f"CRITICAL ERROR: target '{target_name}' server.args must be a list of strings"
            )

    env = raw.get("env")
    if env is not None:
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise RuntimeError(
                f"CRITICAL ERROR: target '{target_name}' server.env must be an object"
            )

    framing = raw.get("framing")
    framing = str(framing).strip() if framing is not None else None

    resources_required_raw = raw.get("resources_required")
    resources_required = (
        bool(resources_required_raw) if resources_required_raw is not None else None
    )
    resources_read_all_raw = raw.get("resources_read_all")
    resources_read_all = (
        bool(resources_read_all_raw) if resources_read_all_raw is not None else None
    )
    invalid_resource_uri = raw.get("invalid_resource_uri")
    invalid_resource_uri = (
        str(invalid_resource_uri).strip() if invalid_resource_uri is not None else None
    )

    return ConformanceServer(
        timeout_seconds=timeout_seconds,
        read_timeout_seconds=read_timeout_seconds,
        verify_tls=verify_tls,
        api_key_header=api_key_header,
        api_key=api_key,
        auth_bearer_token=auth_bearer_token,
        accept_header=accept_header,
        sse_accept_header=sse_accept_header,
        enable_sse=enable_sse,
        base_url=base_url,
        mcp_path=mcp_path,
        messages_path=messages_path,
        health_path=health_path,
        ready_url=ready_url,
        command=command,
        args=args,
        env=env,
        framing=framing,
        tools_call=tools_call,
        invalid_tools_call=invalid_tools_call,
        tool_cases=tool_cases,
        resources_required=resources_required,
        resources_read_all=resources_read_all,
        invalid_resource_uri=invalid_resource_uri,
    )


def _parse_docker(target_name: str, raw: Any) -> ConformanceDocker:
    """Internal helper to docker for this module."""
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"CRITICAL ERROR: target '{target_name}' docker must be an object"
        )

    image = str(raw.get("image") or "").strip()
    if not image:
        raise RuntimeError(
            f"CRITICAL ERROR: target '{target_name}' docker.image is required"
        )

    name_prefix = str(raw.get("name_prefix") or "").strip()
    if not name_prefix:
        raise RuntimeError(
            f"CRITICAL ERROR: target '{target_name}' docker.name_prefix is required"
        )

    env = raw.get("env")
    if env is not None:
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise RuntimeError(
                f"CRITICAL ERROR: target '{target_name}' docker.env must be an object"
            )

    args = raw.get("args")
    if args is not None:
        if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            raise RuntimeError(
                f"CRITICAL ERROR: target '{target_name}' docker.args must be a list of strings"
            )

    return ConformanceDocker(image=image, name_prefix=name_prefix, env=env, args=args)


def load_targets(cfg: Any) -> list[ConformanceTarget]:
    """Load targets for the current runtime context."""
    raw = cfg.get("mcp.conformance.targets", [])
    if not isinstance(raw, list):
        raise RuntimeError("CRITICAL ERROR: mcp.conformance.targets must be a list")

    defaults = cfg.get("mcp.defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}

    out: list[ConformanceTarget] = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            raise RuntimeError(
                f"CRITICAL ERROR: mcp.conformance.targets.{i} must be an object"
            )

        name = str(t.get("name") or "").strip()
        transport = _normalize_transport(str(t.get("transport") or ""))
        server = _parse_server(
            name or f"mcp.conformance.targets.{i}", t.get("server"), defaults
        )
        docker = t.get("docker")

        if not name:
            raise RuntimeError(
                f"CRITICAL ERROR: mcp.conformance.targets.{i}.name is required"
            )
        if not transport:
            raise RuntimeError(
                f"CRITICAL ERROR: mcp.conformance.targets.{i}.transport is required"
            )

        docker_spec = _parse_docker(name, docker) if docker is not None else None

        out.append(
            ConformanceTarget(
                name=name, transport=transport, server=server, docker=docker_spec
            )
        )

    return out
