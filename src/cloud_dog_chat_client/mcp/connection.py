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

from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

from ..config import ConfigManager
from cloud_dog_api_kit.mcp.client_transport import (
    HTTPJSONRPCConfig,
    HTTPJSONRPCTransport,
    LegacySSEConfig,
    LegacySSETransport,
    MCPTransport,
    StreamableHTTPConfig,
    StreamableHTTPTransport,
    StdioConfig,
    StdioTransport,
)

_MCP_SESSION_HEADER = "Mcp-Session-Id"


def normalize_http_endpoint(base_url: str, request_path: str) -> tuple[str, str]:
    """Avoid appending an MCP request path already present in an endpoint URI."""
    base = str(base_url or "").rstrip("/")
    path = "/" + str(request_path or "").strip("/")
    parsed = urlsplit(base)
    base_path = parsed.path.rstrip("/")
    if path != "/" and (base_path == path or base_path.endswith(path)):
        retained_path = base_path[: -len(path)].rstrip("/")
        base = urlunsplit((parsed.scheme, parsed.netloc, retained_path, parsed.query, parsed.fragment)).rstrip("/")
    return base, path


class SessionHTTPJSONRPCTransport(HTTPJSONRPCTransport):
    """HTTP JSON-RPC transport that preserves MCP session ids across requests."""

    def __init__(self, cfg: HTTPJSONRPCConfig):
        super().__init__(cfg)
        self._session_id: Optional[str] = None

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        if self._session_id:
            headers[_MCP_SESSION_HEADER] = self._session_id
        return headers

    async def _post_jsonrpc(self, payload: dict[str, Any]) -> tuple[Any, str]:
        response, request_path = await super()._post_jsonrpc(payload)
        session_id = str(
            response.headers.get(_MCP_SESSION_HEADER)
            or response.headers.get(_MCP_SESSION_HEADER.lower())
            or ""
        ).strip()
        if session_id:
            self._session_id = session_id
        return response, request_path


@dataclass
class MCPServerSpec:
    name: str
    transport: str
    config: Dict[str, Any]


class MCPConnection:
    def __init__(self, spec: MCPServerSpec, transport: MCPTransport):
        """Initialise MCPConnection state and dependencies."""
        self.spec = spec
        self.transport = transport

    @classmethod
    def from_config(
        cls,
        cfg: ConfigManager,
        server_index: int = 0,
        servers_override: Optional[list[Dict[str, Any]]] = None,
    ) -> "MCPConnection":
        """Handle from config for the current runtime context."""
        servers = (
            servers_override
            if servers_override is not None
            else cfg.get("mcp.servers", [])
        )
        if not isinstance(servers, list) or not servers:
            raise RuntimeError(
                "CRITICAL ERROR: missing required configuration key: mcp.servers"
            )
        if server_index < 0 or server_index >= len(servers):
            raise RuntimeError(
                f"CRITICAL ERROR: mcp.servers index out of range: {server_index}"
            )

        s = servers[server_index]
        if not isinstance(s, dict):
            raise RuntimeError(
                f"CRITICAL ERROR: mcp.servers.{server_index} must be an object"
            )

        defaults = cfg.get("mcp.defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}

        name = str(s.get("name") or "")
        if not name:
            raise RuntimeError(
                f"CRITICAL ERROR: missing required configuration key: mcp.servers.{server_index}.name"
            )

        transport = (
            str(s.get("transport") or defaults.get("transport") or "http_jsonrpc")
            .lower()
            .strip()
        )

        if transport in ("streamable_http", "streamablehttp", "mcp"):
            base_url = str(s.get("base_url") or "")
            mcp_path = str(s.get("mcp_path") or defaults.get("mcp_path") or "/mcp")
            base_url, mcp_path = normalize_http_endpoint(base_url, mcp_path)
            api_key_header: Optional[str] = str(
                s.get("api_key_header") or defaults.get("api_key_header") or ""
            )
            api_key: Optional[str] = str(s.get("api_key") or "")
            accept_header: Optional[str] = str(
                s.get("accept_header") or defaults.get("accept_header") or ""
            )
            sse_accept_header: Optional[str] = str(
                s.get("sse_accept_header") or defaults.get("sse_accept_header") or ""
            )
            protocol_version: Optional[str] = str(
                s.get("protocol_version") or defaults.get("protocol_version") or ""
            )
            auth_bearer_token: Optional[str] = str(
                s.get("auth_bearer_token") or defaults.get("auth_bearer_token") or ""
            )
            enable_sse = bool(
                s.get("enable_sse")
                if s.get("enable_sse") is not None
                else defaults.get("enable_sse", True)
            )
            timeout_seconds = float(
                s.get("timeout_seconds")
                or defaults.get("timeout_seconds")
                or cfg.get("client_api.request_timeout_seconds")
                or 30.0
            )
            read_timeout_seconds: Optional[float] = s.get("read_timeout_seconds")
            verify_tls = bool(
                s.get("verify_tls") if s.get("verify_tls") is not None else True
            )
            extra_headers = s.get("extra_headers")
            if not isinstance(extra_headers, dict):
                extra_headers = None

            if not base_url:
                raise RuntimeError(
                    f"CRITICAL ERROR: missing required configuration key: mcp.servers.{server_index}.base_url"
                )
            if not mcp_path:
                raise RuntimeError(
                    "CRITICAL ERROR: missing required configuration key: mcp.defaults.mcp_path"
                )
            if not api_key_header:
                api_key_header = None
            if not api_key:
                api_key = None
            if not accept_header:
                accept_header = None
            if not sse_accept_header:
                sse_accept_header = None
            if not protocol_version:
                protocol_version = None
            if not auth_bearer_token:
                auth_bearer_token = None
            if read_timeout_seconds is not None:
                try:
                    read_timeout_seconds = float(read_timeout_seconds)
                except (TypeError, ValueError):
                    read_timeout_seconds = None

            transport_impl: MCPTransport = StreamableHTTPTransport(
                StreamableHTTPConfig(
                    base_url=base_url,
                    mcp_path=mcp_path,
                    api_key_header=api_key_header,
                    api_key=api_key,
                    accept_header=accept_header,
                    sse_accept_header=sse_accept_header,
                    protocol_version=protocol_version,
                    auth_bearer_token=auth_bearer_token,
                    enable_sse=enable_sse,
                    timeout_seconds=timeout_seconds,
                    read_timeout_seconds=read_timeout_seconds,
                    verify_tls=verify_tls,
                    extra_headers=extra_headers,
                )
            )
            return cls(
                MCPServerSpec(name=name, transport=transport, config=s), transport_impl
            )

        if transport in ("http_jsonrpc", "http", "messages"):
            base_url = str(s.get("base_url") or "")
            messages_path = str(
                s.get("messages_path") or defaults.get("messages_path") or ""
            )
            health_path = str(s.get("health_path") or defaults.get("health_path") or "")
            api_key_header = str(
                s.get("api_key_header") or defaults.get("api_key_header") or ""
            )
            api_key = str(s.get("api_key") or "")
            accept_header = str(
                s.get("accept_header") or defaults.get("accept_header") or ""
            )
            timeout_seconds = float(
                s.get("timeout_seconds")
                or defaults.get("timeout_seconds")
                or cfg.get("client_api.request_timeout_seconds")
                or 30.0
            )
            verify_tls = bool(
                s.get("verify_tls") if s.get("verify_tls") is not None else True
            )
            extra_headers = s.get("extra_headers")
            if not isinstance(extra_headers, dict):
                extra_headers = None
            async_jobs_enabled = bool(
                s.get("async_jobs_enabled")
                if s.get("async_jobs_enabled") is not None
                else defaults.get("async_jobs_enabled") or False
            )
            async_jobs_api_base_url: Optional[str] = str(
                s.get("async_jobs_api_base_url")
                or defaults.get("async_jobs_api_base_url")
                or ""
            )
            async_jobs_status_path = str(
                s.get("async_jobs_status_path")
                or defaults.get("async_jobs_status_path")
                or "/jobs/{job_id}"
            )
            async_jobs_timeout_seconds = float(
                s.get("async_jobs_timeout_seconds")
                or defaults.get("async_jobs_timeout_seconds")
                or timeout_seconds
            )
            async_jobs_poll_interval_seconds = float(
                s.get("async_jobs_poll_interval_seconds")
                or defaults.get("async_jobs_poll_interval_seconds")
                or 2.0
            )

            if not base_url:
                raise RuntimeError(
                    f"CRITICAL ERROR: missing required configuration key: mcp.servers.{server_index}.base_url"
                )
            if not messages_path:
                raise RuntimeError(
                    "CRITICAL ERROR: missing required configuration key: mcp.defaults.messages_path"
                )
            if not health_path:
                raise RuntimeError(
                    "CRITICAL ERROR: missing required configuration key: mcp.defaults.health_path"
                )
            if not api_key_header:
                api_key_header = None
            if not api_key:
                api_key = None
            if not accept_header:
                accept_header = None
            if not async_jobs_api_base_url:
                async_jobs_api_base_url = None

            transport_impl = SessionHTTPJSONRPCTransport(
                HTTPJSONRPCConfig(
                    base_url=base_url,
                    messages_path=messages_path,
                    health_path=health_path,
                    api_key_header=api_key_header,
                    api_key=api_key,
                    accept_header=accept_header,
                    timeout_seconds=timeout_seconds,
                    verify_tls=verify_tls,
                    async_jobs_enabled=async_jobs_enabled,
                    async_jobs_api_base_url=async_jobs_api_base_url,
                    async_jobs_status_path=async_jobs_status_path,
                    async_jobs_timeout_seconds=async_jobs_timeout_seconds,
                    async_jobs_poll_interval_seconds=async_jobs_poll_interval_seconds,
                    extra_headers=extra_headers,
                )
            )
            return cls(
                MCPServerSpec(name=name, transport=transport, config=s), transport_impl
            )

        if transport in ("legacy_sse", "http_sse", "sse"):
            base_url = str(s.get("base_url") or "")
            sse_path = str(s.get("sse_path") or defaults.get("sse_path") or "")
            messages_path = str(
                s.get("messages_path") or defaults.get("messages_path") or ""
            )
            api_key_header = str(
                s.get("api_key_header") or defaults.get("api_key_header") or ""
            )
            api_key = str(s.get("api_key") or "")
            accept_header = str(
                s.get("accept_header") or defaults.get("accept_header") or ""
            )
            auth_bearer_token = str(
                s.get("auth_bearer_token") or defaults.get("auth_bearer_token") or ""
            )
            protocol_version = str(
                s.get("protocol_version") or defaults.get("protocol_version") or ""
            )
            timeout_seconds = float(
                s.get("timeout_seconds")
                or defaults.get("timeout_seconds")
                or cfg.get("client_api.request_timeout_seconds")
                or 30.0
            )
            verify_tls = bool(
                s.get("verify_tls") if s.get("verify_tls") is not None else True
            )

            if not base_url:
                raise RuntimeError(
                    f"CRITICAL ERROR: missing required configuration key: mcp.servers.{server_index}.base_url"
                )
            if not sse_path:
                raise RuntimeError(
                    "CRITICAL ERROR: missing required configuration key: mcp.defaults.sse_path"
                )
            if not messages_path:
                raise RuntimeError(
                    "CRITICAL ERROR: missing required configuration key: mcp.defaults.messages_path"
                )
            if not api_key_header:
                api_key_header = None
            if not api_key:
                api_key = None
            if not accept_header:
                accept_header = None
            if not auth_bearer_token:
                auth_bearer_token = None
            if not protocol_version:
                protocol_version = None

            transport_impl = LegacySSETransport(
                LegacySSEConfig(
                    base_url=base_url,
                    sse_path=sse_path,
                    messages_path=messages_path,
                    api_key_header=api_key_header,
                    api_key=api_key,
                    accept_header=accept_header,
                    auth_bearer_token=auth_bearer_token,
                    protocol_version=protocol_version,
                    timeout_seconds=timeout_seconds,
                    verify_tls=verify_tls,
                )
            )
            return cls(
                MCPServerSpec(name=name, transport=transport, config=s), transport_impl
            )

        if transport in ("stdio",):
            command = str(s.get("command") or "")
            args = s.get("args") or []
            if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
                raise RuntimeError(
                    f"CRITICAL ERROR: mcp.servers.{server_index}.args must be a list of strings"
                )

            if not command:
                raise RuntimeError(
                    f"CRITICAL ERROR: missing required configuration key: mcp.servers.{server_index}.command"
                )

            env = s.get("env")
            if env is not None and not isinstance(env, dict):
                raise RuntimeError(
                    f"CRITICAL ERROR: mcp.servers.{server_index}.env must be an object"
                )

            framing = str(
                s.get("framing") or defaults.get("framing") or "content_length"
            )

            transport_impl = StdioTransport(
                StdioConfig(command=command, args=args, env=env, framing=framing)
            )
            return cls(
                MCPServerSpec(name=name, transport=transport, config=s), transport_impl
            )

        raise RuntimeError(f"Unsupported mcp transport: {transport}")

    async def connect(self) -> None:
        """Handle connect for the current runtime context."""
        await self.transport.connect()

    async def close(self) -> None:
        """Close close for the current runtime context."""
        await self.transport.close()
