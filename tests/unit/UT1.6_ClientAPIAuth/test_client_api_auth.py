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

from starlette.requests import Request
from fastapi import HTTPException

import pytest

from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.api.auth import require_api_key


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _request_with_header(header_name: str, header_value: str) -> Request:
    header_name = header_name.lower()
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "headers": [(header_name.encode("utf-8"), header_value.encode("utf-8"))],
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def _request_without_header() -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def _request_with_cookie(cookie_name: str, cookie_value: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "headers": [(b"cookie", f"{cookie_name}={cookie_value}".encode("utf-8"))],
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("CS-013")


@pytest.mark.asyncio
async def test_ut1_6_api_key_auth(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "dev-key")
    cfg = ConfigManager(env_file=env_file)
    header_name = str(_require_cfg(cfg, "client_api.api_key_header"))
    api_key = str(_require_cfg(cfg, "client_api.api_key"))

    with pytest.raises(HTTPException) as exc:
        await require_api_key(cfg, _request_without_header())
    assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as exc:
        await require_api_key(cfg, _request_with_header(header_name, "wrong"))
    assert exc.value.status_code == 403

    await require_api_key(cfg, _request_with_header(header_name, api_key))
    await require_api_key(cfg, _request_with_cookie("chat_client_api_key", api_key))
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")


@pytest.mark.asyncio
async def test_ut1_6_api_key_accepts_trusted_webui_admin_without_admin_key(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY_HEADER", "X-API-Key")
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__API_KEY", "dev-key")
    monkeypatch.delenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("CLOUD_DOG__CLIENT_API__ADMIN_API_KEY_HEADER", raising=False)

    cfg = ConfigManager(env_file=env_file)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/users",
            "headers": [
                (b"x-request-source", b"webui"),
                (b"x-request-user", b"admin"),
                (b"x-api-key", b"dev-key"),
            ],
            "client": ("127.0.0.1", 12345),
        }
    )

    await require_api_key(cfg, request)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.pure, pytest.mark.fast]
