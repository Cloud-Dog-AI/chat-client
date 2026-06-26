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

import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _isolated_env_file(source_env: str, cfg: ConfigManager) -> str:
    base_url = api_base_url(cfg).rstrip("/")
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8090)
    db_dialect = str(cfg.get("db.dialect") or cfg.get("cloud_dog.db.dialect") or "sqlite")
    db_database = str(cfg.get("db.database") or cfg.get("cloud_dog.db.database") or "")
    fd, path = tempfile.mkstemp(prefix="it220-chat-client-", suffix=".env")
    os.close(fd)
    Path(path).write_text(
        Path(source_env).read_text(encoding="utf-8").rstrip()
        + "\n"
        + "\n".join(
            [
                f"CLOUD_DOG__API_SERVER__HOST={host}",
                f"CLOUD_DOG__API_SERVER__PORT={port}",
                f"CLOUD_DOG__CLIENT_API__HOST={host}",
                f"CLOUD_DOG__CLIENT_API__PORT={port}",
                f"CLOUD_DOG__CLIENT_API__BASE_URL={base_url}",
                f"CLOUD_DOG_DB__DIALECT={db_dialect}",
                f"CLOUD_DOG__DB__DIALECT={db_dialect}",
                f"CLOUD_DOG_DB__DATABASE={db_database}",
                f"CLOUD_DOG__DB__DATABASE={db_database}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.IT
@pytest.mark.cli
@pytest.mark.req("FR-011")


@pytest.mark.asyncio
async def test_it_db_01_full_api_startup_reports_database_ready(
    env_file, monkeypatch, tmp_path: Path
):
    cfg = ConfigManager(env_file=env_file)
    if not (
        cfg.get("db.dialect")
        or cfg.get("db.url")
        or cfg.get("cloud_dog.db.dialect")
        or cfg.get("cloud_dog.db.url")
    ):
        db_path = tmp_path / "it_db.sqlite3"
        monkeypatch.setenv("CLOUD_DOG_DB__DIALECT", "sqlite")
        monkeypatch.setenv("CLOUD_DOG_DB__DATABASE", str(db_path))
        cfg = ConfigManager(env_file=env_file)

    isolated_env = _isolated_env_file(env_file, cfg)
    cfg = ConfigManager(env_file=isolated_env)
    start_api(cfg, env_file=isolated_env)
    try:
        wait_for_api(cfg)
        base_url = api_base_url(cfg)
        timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
        async with httpx.AsyncClient(
            base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds
        ) as client:
            ready = await client.get("/ready")
            assert ready.status_code == 200
            ready_payload = ready.json()
            assert str(ready_payload.get("status") or "") in {"ok", "degraded"}
            assert str(
                ((ready_payload.get("checks") or {}).get("db") or {}).get("status") or ""
            ) == "ok"

            health = await client.get("/health")
            assert health.status_code == 200
            health_payload = health.json()
            assert str(health_payload.get("status") or "") in {"ok", "degraded"}
            assert str(
                ((health_payload.get("checks") or {}).get("db") or {}).get("status") or ""
            ) == "ok"
    finally:
        stop_api(cfg, env_file=isolated_env)
        Path(isolated_env).unlink(missing_ok=True)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.integration, pytest.mark.db, pytest.mark.heavy]
