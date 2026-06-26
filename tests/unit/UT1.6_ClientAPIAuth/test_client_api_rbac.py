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

from starlette.requests import Request
from fastapi import HTTPException

import pytest

from cloud_dog_chat_client.api.auth import require_admin_key
from cloud_dog_chat_client.config import ConfigManager


def _request(headers: list[tuple[str, str]]) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp/servers",
        "headers": [(k.lower().encode("utf-8"), v.encode("utf-8")) for k, v in headers],
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("CS-004")


@pytest.mark.asyncio
async def test_ut1_6_admin_key_enforced_when_configured(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "user-key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY_HEADER", "X-Admin-Key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY", "admin-key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__USER_HEADER", "X-User")

    cfg = ConfigManager(env_file=env_file)

    with pytest.raises(HTTPException) as exc:
        await require_admin_key(cfg, _request([("X-API-Key", "user-key"), ("X-User", "alice")]))
    assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as exc:
        await require_admin_key(cfg, _request([("X-Admin-Key", "wrong"), ("X-User", "alice")]))
    assert exc.value.status_code == 403

    actor = await require_admin_key(cfg, _request([("X-Admin-Key", "admin-key"), ("X-User", "alice")]))
    assert actor == "alice"
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("CS-009")


@pytest.mark.asyncio
async def test_ut1_6_admin_permission_required_when_admin_key_not_configured(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "user-key")
    monkeypatch.delenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY_HEADER", raising=False)
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__USER_HEADER", "X-User")

    cfg = ConfigManager(env_file=env_file)

    with pytest.raises(HTTPException) as exc:
        await require_admin_key(cfg, _request([("X-User", "bob")]))
    assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as exc:
        await require_admin_key(cfg, _request([("X-API-Key", "user-key"), ("X-User", "bob")]))
    assert exc.value.status_code == 403
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("CS-010")


@pytest.mark.asyncio
async def test_ut1_6_webui_loopback_admin_fallback_when_no_admin_key(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "user-key")
    monkeypatch.delenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY_HEADER", raising=False)
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__USER_HEADER", "X-User")

    cfg = ConfigManager(env_file=env_file)

    actor = await require_admin_key(
        cfg,
        _request(
            [
                ("X-API-Key", "user-key"),
                ("X-Request-Source", "webui"),
                ("X-Request-User", "admin"),
            ]
        ),
    )
    assert actor == "admin"

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.mcp, pytest.mark.fast]
