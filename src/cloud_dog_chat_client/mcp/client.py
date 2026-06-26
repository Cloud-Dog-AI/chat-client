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
import itertools
import json
import random
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

import httpx

from ..config import ConfigManager


@dataclass
class MCPServerConfig:
    name: str
    base_url: str
    messages_path: str
    health_path: str
    api_key_header: Optional[str] = None
    api_key: Optional[str] = None
    accept_header: Optional[str] = None
    timeout_seconds: float = 30.0
    verify_tls: bool = True
    retry_max_attempts: int = 1
    retry_backoff_seconds: float = 0.0
    retry_jitter_seconds: float = 0.0
    retry_on_status_codes: Optional[Set[int]] = None


class MCPClientError(RuntimeError):
    """Raised when an MCP transport request fails."""


class MCPClient:
    def __init__(self, server: MCPServerConfig):
        """Initialise MCPClient state and dependencies."""
        # Covers: R3, R4, NFR1, NFR4
        # Uses the real MCP transport client and surfaces target-specific failures.
        self.server = server
        self._id_iter = itertools.count(1)
        self._session_id: Optional[str] = None
        self._client = httpx.AsyncClient(
            base_url=str(server.base_url).rstrip("/"),
            timeout=httpx.Timeout(
                server.timeout_seconds, connect=server.timeout_seconds
            ),
            verify=server.verify_tls,
        )
        self._retry_on_status_codes = server.retry_on_status_codes or set()

    @classmethod
    def from_config(cls, config: ConfigManager, server_index: int = 0) -> "MCPClient":
        """Handle from config for the current runtime context."""
        servers = config.get("mcp.servers", [])
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

        defaults = config.get("mcp.defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}

        name = str(s.get("name") or "")
        base_url = str(s.get("base_url") or "")
        messages_path = str(
            s.get("messages_path") or defaults.get("messages_path") or ""
        )
        health_path = str(s.get("health_path") or defaults.get("health_path") or "")
        api_key_header: Optional[str] = str(
            s.get("api_key_header") or defaults.get("api_key_header") or ""
        )
        api_key: Optional[str] = str(s.get("api_key") or "")
        accept_header: Optional[str] = str(
            s.get("accept_header") or defaults.get("accept_header") or ""
        )
        timeout_seconds = float(
            s.get("timeout_seconds") or defaults.get("timeout_seconds") or 30.0
        )
        verify_tls = bool(
            s.get("verify_tls") if s.get("verify_tls") is not None else True
        )
        retry_max_attempts = int(
            s.get("retry_max_attempts") or defaults.get("retry_max_attempts") or 1
        )
        retry_backoff_seconds = float(
            s.get("retry_backoff_seconds")
            or defaults.get("retry_backoff_seconds")
            or 0.0
        )
        retry_jitter_seconds = float(
            s.get("retry_jitter_seconds") or defaults.get("retry_jitter_seconds") or 0.0
        )
        retry_on_status_codes = _parse_int_set(
            s.get("retry_on_status_codes")
            or defaults.get("retry_on_status_codes")
            or [],
            "mcp.defaults.retry_on_status_codes",
        )

        if not name:
            raise RuntimeError(
                f"CRITICAL ERROR: missing required configuration key: mcp.servers.{server_index}.name"
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

        return cls(
            MCPServerConfig(
                name=name,
                base_url=base_url,
                messages_path=messages_path,
                health_path=health_path,
                api_key_header=api_key_header,
                api_key=api_key,
                accept_header=accept_header,
                timeout_seconds=timeout_seconds,
                verify_tls=verify_tls,
                retry_max_attempts=retry_max_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
                retry_jitter_seconds=retry_jitter_seconds,
                retry_on_status_codes=retry_on_status_codes,
            )
        )

    async def health(self) -> Dict[str, Any]:
        """Handle health for the current runtime context."""
        resp = await self._client.get(self.server.health_path)
        if resp.status_code != 200:
            raise MCPClientError(
                f"MCP health check failed: GET {self.server.health_path} -> {resp.status_code}"
            )
        data = resp.json()
        if not isinstance(data, dict):
            raise MCPClientError("MCP health check returned non-object JSON")
        return data

    def _headers(self) -> Dict[str, str]:
        """Internal helper to headers for this module."""
        headers: Dict[str, str] = {}
        if self.server.api_key_header and self.server.api_key:
            headers[self.server.api_key_header] = self.server.api_key
        if self.server.accept_header:
            headers["accept"] = self.server.accept_header
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    def _capture_session(self, resp: httpx.Response) -> None:
        """Internal helper to capture session for this module."""
        session_id = resp.headers.get("mcp-session-id")
        if session_id and not self._session_id:
            self._session_id = session_id

    async def _post_with_retry(self, payload: Dict[str, Any]) -> httpx.Response:
        """Internal helper to post with retry for this module."""
        max_attempts = max(1, int(self.server.retry_max_attempts))
        last_error: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            try:
                resp = await self._client.post(
                    self.server.messages_path, json=payload, headers=self._headers()
                )
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < max_attempts:
                    await self._sleep_before_retry(attempt)
                    continue
                raise MCPClientError(
                    "MCP JSON-RPC request failed after retries"
                ) from exc

            if resp.status_code != 200:
                if (
                    resp.status_code in self._retry_on_status_codes
                    and attempt < max_attempts
                ):
                    await self._sleep_before_retry(attempt)
                    continue
                raise MCPClientError(
                    f"MCP JSON-RPC failed: POST {self.server.messages_path} -> {resp.status_code}"
                )

            return resp

        if last_error:
            raise MCPClientError(
                "MCP JSON-RPC request failed after retries"
            ) from last_error
        raise MCPClientError("MCP JSON-RPC request failed after retries")

    async def _sleep_before_retry(self, attempt: int) -> None:
        """Internal helper to sleep before retry for this module."""
        base = max(0.0, float(self.server.retry_backoff_seconds))
        jitter = max(0.0, float(self.server.retry_jitter_seconds))
        delay = base * (2 ** (attempt - 1))
        if jitter:
            delay += random.uniform(0.0, jitter)
        if delay > 0:
            await asyncio.sleep(delay)

    async def jsonrpc(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Handle jsonrpc for the current runtime context."""
        req_id = next(self._id_iter)
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params

        resp = await self._post_with_retry(payload)
        self._capture_session(resp)

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise MCPClientError("MCP JSON-RPC returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise MCPClientError("MCP JSON-RPC returned non-object JSON")

        if data.get("jsonrpc") != "2.0":
            raise MCPClientError("MCP JSON-RPC invalid response: jsonrpc must be '2.0'")

        if data.get("id") != req_id:
            raise MCPClientError("MCP JSON-RPC response id mismatch")

        if "error" in data and data["error"] is not None:
            raise MCPClientError(f"MCP JSON-RPC error: {data['error']}")

        if "result" not in data:
            raise MCPClientError("MCP JSON-RPC missing result")

        result = data.get("result")
        if not isinstance(result, dict):
            raise MCPClientError("MCP JSON-RPC result must be an object")
        return result

    async def notify(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> None:
        """Handle notify for the current runtime context."""
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        resp = await self._post_with_retry(payload)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise MCPClientError(
                f"MCP JSON-RPC notify failed: POST {self.server.messages_path} -> {resp.status_code}"
            )
        self._capture_session(resp)

    async def tools_list(self) -> Dict[str, Any]:
        """Handle tools list for the current runtime context."""
        return await self.jsonrpc("tools/list")

    async def resources_list(self) -> Dict[str, Any]:
        """Handle resources list for the current runtime context."""
        return await self.jsonrpc("resources/list")

    async def resources_read(self, uri: str) -> Dict[str, Any]:
        """Handle resources read for the current runtime context."""
        return await self.jsonrpc("resources/read", params={"uri": uri})

    async def tools_call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools call for the current runtime context."""
        return await self.jsonrpc(
            "tools/call", params={"name": name, "arguments": arguments}
        )

    async def aclose(self) -> None:
        """Handle aclose for the current runtime context."""
        await self._client.aclose()


def _parse_int_set(value: Any, key: str) -> Set[int]:
    """Internal helper to int set for this module."""
    if value is None:
        return set()
    if isinstance(value, set):
        return {int(item) for item in value}
    if isinstance(value, list):
        return {int(item) for item in value}
    if isinstance(value, tuple):
        return {int(item) for item in value}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"CRITICAL ERROR: {key} must be a JSON list of integers"
            ) from exc
        if isinstance(parsed, list):
            return {int(item) for item in parsed}
    raise RuntimeError(f"CRITICAL ERROR: {key} must be a list of integers")
