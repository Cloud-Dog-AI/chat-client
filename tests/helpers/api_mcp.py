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

from typing import Any, Dict, List, Optional

import httpx


async def create_session(client: httpx.AsyncClient, *, metadata: Optional[Dict[str, Any]] = None) -> str:
    resp = await client.post("/sessions", json={"metadata": metadata or {}})
    resp.raise_for_status()
    session_id = resp.json().get("session_id")
    if not session_id:
        raise RuntimeError("CRITICAL ERROR: API did not return session_id")
    return session_id


async def mcp_execute(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    steps: List[Dict[str, Any]],
    server_index: Optional[int] = None,
    server: Optional[Dict[str, Any]] = None,
    protocol_version: Optional[str] = None,
    require_initialize: Optional[bool] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"steps": steps}
    if server_index is not None:
        payload["server_index"] = server_index
    if server is not None:
        payload["server"] = server
    if protocol_version is not None:
        payload["protocol_version"] = protocol_version
    if require_initialize is not None:
        payload["require_initialize"] = require_initialize
    resp = await client.post(f"/sessions/{session_id}/mcp/execute", json=payload)
    resp.raise_for_status()
    return resp.json()


async def mcp_sse_open(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    server_index: Optional[int] = None,
    server: Optional[Dict[str, Any]] = None,
    protocol_version: Optional[str] = None,
    require_initialize: Optional[bool] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if server_index is not None:
        payload["server_index"] = server_index
    if server is not None:
        payload["server"] = server
    if protocol_version is not None:
        payload["protocol_version"] = protocol_version
    if require_initialize is not None:
        payload["require_initialize"] = require_initialize
    resp = await client.post(f"/sessions/{session_id}/mcp/sse/open", json=payload)
    resp.raise_for_status()
    return resp.json()


async def mcp_terminate(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    server_index: Optional[int] = None,
    server: Optional[Dict[str, Any]] = None,
    protocol_version: Optional[str] = None,
    require_initialize: Optional[bool] = None,
    verify_method: Optional[str] = None,
    verify_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if server_index is not None:
        payload["server_index"] = server_index
    if server is not None:
        payload["server"] = server
    if protocol_version is not None:
        payload["protocol_version"] = protocol_version
    if require_initialize is not None:
        payload["require_initialize"] = require_initialize
    if verify_method is not None:
        payload["verify_method"] = verify_method
    if verify_params is not None:
        payload["verify_params"] = verify_params
    resp = await client.post(f"/sessions/{session_id}/mcp/session/terminate", json=payload)
    resp.raise_for_status()
    return resp.json()
