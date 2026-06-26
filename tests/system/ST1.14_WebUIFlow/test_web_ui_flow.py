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

import os

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import (
    api_headers,
    start_all,
    stop_all,
    wait_for_api,
    wait_for_base_url,
    web_base_url,
)

# Covers: R16.1, R16.2, R16.3, R16.4, R16.5, R16.6, R16.7


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


@pytest.fixture(scope="module", autouse=True)
def _api_server(env_file):
    os.environ["CLOUD_DOG__CLIENT_API__API_KEY_HEADER"] = "X-API-Key"
    os.environ["CLOUD_DOG__CLIENT_API__API_KEY"] = "dev-key"
    cfg = ConfigManager(env_file=env_file)
    start_all(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        wait_for_base_url(cfg, web_base_url(cfg))
        yield None
    finally:
        stop_all(cfg, env_file=env_file)
@pytest.mark.ST
@pytest.mark.webui
@pytest.mark.req("FR-001")


@pytest.mark.asyncio
async def test_st1_14_web_ui_render_and_chat_flow(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = web_base_url(cfg)
    timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    headers = api_headers(cfg)

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
        ui_resp = await client.get("/ui")
        assert ui_resp.status_code == 200
        ui_html = ui_resp.text
        assert "<div id=\"root\"></div>" in ui_html
        assert "<title>cloud-dog" in ui_html
        assert "/runtime-config.js" in ui_html
        assert "/assets/index-" in ui_html

        runtime_config = await client.get("/runtime-config.js")
        assert runtime_config.status_code == 200
        assert "window.__RUNTIME_CONFIG__" in runtime_config.text
        assert "API_KEY_HEADER" not in runtime_config.text
        assert '"API_BASE_URL": __origin' in runtime_config.text

        cfg_tree = await client.get("/ui/config/tree", headers=headers)
        assert cfg_tree.status_code == 200
        cfg_payload = cfg_tree.json()
        assert cfg_payload.get("application", {}).get("name")
        assert isinstance(cfg_payload.get("config"), dict)

        ui_cfg = await client.get("/ui/config", headers=headers)
        assert ui_cfg.status_code == 200
        assert (
            int(ui_cfg.json().get("client_api", {}).get("ui_wait_timeout_seconds", 0))
            >= 30
        )
        assert int(ui_cfg.json().get("a2a", {}).get("port", 0)) >= 1
        assert ui_cfg.json().get("a2a", {}).get("ws_path") == "/a2a/ws"
        assert ui_cfg.json().get("test_harness", {}).get("enabled") is True
        assert ui_cfg.json().get("application", {}).get("release")
        llm_cfg = ui_cfg.json().get("llm") or {}
        assert llm_cfg.get("provider")
        assert "temperature" in llm_cfg
        assert "top_k" in llm_cfg
        assert "num_ctx" in llm_cfg
        assert "max_tokens" in llm_cfg

        login = await client.get("/login")
        assert login.status_code == 200
        assert "<div id=\"root\"></div>" in login.text

        files_page = await client.get("/files")
        assert files_page.status_code == 200
        assert "<div id=\"root\"></div>" in files_page.text

        create = await client.post(
            "/sessions", json={"metadata": {"suite": "st1.14"}}, headers=headers
        )
        assert create.status_code == 200
        session_id = create.json().get("session_id")
        assert session_id

        send = await client.post(
            f"/sessions/{session_id}/messages",
            json={"content": "Reply with exactly ST1_14_UI_OK", "stream": False},
            headers=headers,
        )
        assert send.status_code == 200
        body = send.json()
        assert body.get("session_id") == session_id
        assert isinstance(body.get("content"), str)
        assert body.get("content", "").strip()

        transcript = await client.get(
            f"/sessions/{session_id}/transcript", headers=headers
        )
        assert transcript.status_code == 200
        events = transcript.json().get("events") or []
        assert any(e.get("event_type") == "user_message" for e in events)
        assert any(e.get("event_type") == "assistant_message" for e in events)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.pure, pytest.mark.slow]
