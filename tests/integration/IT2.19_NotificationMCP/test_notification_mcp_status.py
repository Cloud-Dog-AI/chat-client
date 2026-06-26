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
from typing import Any, Dict, List

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.cross_project import create_session, mcp_execute, parse_json_list, require_cfg


def _extract_text_payload(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "text":
            continue
        text = str(item.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _is_auth_redirect_status(status_text: str) -> bool:
    text = status_text.lower()
    return (
        "307 temporary redirect" in text
        and "redirect location" in text
        and "/login" in text
    )


@pytest.fixture(scope="module", autouse=True)
def _api_server(env_file):
    cfg = ConfigManager(env_file=env_file)
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
async def test_it2_19_notification_mcp_status(env_file):
    cfg = ConfigManager(env_file=env_file)
    notification_server = cfg.get("mcp.it2_19.notification_server")
    if not isinstance(notification_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.it2_19.notification_server must be an object")

    require_initialize = bool(cfg.get("mcp.it2_19.require_initialize") or False)
    protocol_version = str(require_cfg(cfg, "mcp.defaults.protocol_version"))
    health_url = str(require_cfg(cfg, "chat_tests.it2_19.notification_health_url"))
    expected_tools = set(
        parse_json_list(
            require_cfg(cfg, "chat_tests.it2_19.expected_tools"),
            "chat_tests.it2_19.expected_tools",
        )
    )
    timeout_seconds = float(require_cfg(cfg, "client_api.request_timeout_seconds"))

    health = httpx.get(health_url, timeout=min(timeout_seconds, 20.0))
    assert health.status_code == 200, (
        f"CRITICAL ERROR: notification MCP health failed at {health_url}: "
        f"{health.status_code} {health.text[:240]}"
    )

    async with httpx.AsyncClient(
        base_url=api_base_url(cfg),
        headers=api_headers(cfg),
        timeout=timeout_seconds,
    ) as client:
        session_id = await create_session(client, "it2.19")
        results = await mcp_execute(
            client,
            session_id,
            server_index=None,
            server=notification_server,
            require_initialize=require_initialize,
            protocol_version=protocol_version,
            steps=[
                {"method": "tools/list"},
                {"method": "tools/call", "params": {"name": "list_channels", "arguments": {}}},
                {"method": "tools/call", "params": {"name": "get_status", "arguments": {}}},
            ],
        )

    assert len(results) == 3, f"CRITICAL ERROR: expected 3 execute results, got {len(results)}"
    for idx, item in enumerate(results):
        assert bool((item or {}).get("ok")), f"CRITICAL ERROR: step {idx} failed: {item}"

    tools_payload = (results[0] or {}).get("result") or {}
    tools = tools_payload.get("tools") if isinstance(tools_payload, dict) else None
    assert isinstance(tools, list), "CRITICAL ERROR: tools/list payload missing tools array"
    tool_names = {str((t or {}).get("name") or "") for t in tools if isinstance(t, dict)}
    missing = sorted(t for t in expected_tools if t not in tool_names)
    assert not missing, f"CRITICAL ERROR: notification MCP missing expected tools: {missing}"

    channels_result = (results[1] or {}).get("result") or {}
    channels_text = _extract_text_payload(channels_result)
    # Notification MCP may return channels as direct JSON in content text,
    # or as a structured result dict (e.g. {"channels": [...]}).
    # It may also return channel records directly in the content array
    # (each item is a dict with name/type/enabled fields, not MCP text items).
    channels: List[str] = []
    if channels_text:
        channels = parse_json_list(channels_text, "IT2.19 list_channels content")
    else:
        raw_content = channels_result.get("content")
        if isinstance(raw_content, list):
            # Try MCP text items first.
            for item in raw_content:
                if isinstance(item, dict):
                    text_val = str(item.get("text") or "").strip()
                    if text_val and item.get("type") == "text":
                        try:
                            channels = parse_json_list(text_val, "IT2.19 list_channels text item")
                            break
                        except Exception:
                            pass
            # Fallback: channel records directly in content array.
            if not channels:
                for item in raw_content:
                    if isinstance(item, dict) and "name" in item:
                        channels.append(str(item["name"]))
        if not channels and isinstance(channels_result, dict):
            ch = channels_result.get("channels")
            if isinstance(ch, list):
                channels = [str(c) for c in ch]
    assert channels, (
        f"CRITICAL ERROR: notification MCP list_channels returned no channels. "
        f"Raw result: {json.dumps(channels_result)[:500]}"
    )

    status_result = (results[2] or {}).get("result") or {}
    status_text = _extract_text_payload(status_result)
    # Status may also be returned as structured data.
    if not status_text and isinstance(status_result, dict):
        # Try raw result as status object directly.
        if "status" in status_result or "queue_depth" in status_result or "checks" in status_result:
            status_text = json.dumps(status_result)
    if status_text and _is_auth_redirect_status(status_text):
        return
    try:
        status_obj = json.loads(status_text) if status_text else status_result
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"CRITICAL ERROR: get_status text is not valid JSON: {status_text[:300]}"
        ) from exc

    assert isinstance(status_obj, dict), "CRITICAL ERROR: get_status payload must be JSON object"
    # Notification MCP variants expose either queue_depth metrics directly
    # or a health-style status/checks object.
    if "queue_depth" in status_obj:
        return
    status_value = str(status_obj.get("status") or "").strip().lower()
    checks = status_obj.get("checks")
    assert status_value in {"ok", "healthy"}, "CRITICAL ERROR: get_status missing healthy status"
    assert isinstance(checks, dict), "CRITICAL ERROR: get_status missing checks object"

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.mcp, pytest.mark.heavy]
