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

from typing import Any

from cloud_dog_db.config.models import DatabaseSettings

from ..config import ConfigManager


def _config_first(config: ConfigManager, *paths: str) -> Any:
    """Return the first non-empty config value across canonical and legacy paths."""
    for path in paths:
        value = config.get(path)
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return value
            continue
        return value
    return None


def get_database_settings(config: ConfigManager) -> DatabaseSettings:
    """Bridge chat-client config tree to `cloud_dog_db` settings payload."""

    payload: dict[str, Any] = {}

    config_map = {
        "dialect": ("cloud_dog_db.dialect", "db.dialect"),
        "database": ("cloud_dog_db.database", "db.database"),
        "path": ("cloud_dog_db.path", "db.path"),
        "url": ("cloud_dog_db.url", "db.url"),
        "schema_name": ("cloud_dog_db.schema", "db.schema"),
        "host": ("cloud_dog_db.host", "db.host"),
        "port": ("cloud_dog_db.port", "db.port"),
        "username": ("cloud_dog_db.username", "db.username"),
        "password": ("cloud_dog_db.password", "db.password"),
        "driver": ("cloud_dog_db.driver", "db.driver"),
    }
    for field_name, cfg_keys in config_map.items():
        value = _config_first(config, *cfg_keys)
        if value is None:
            continue
        rendered = str(value).strip()
        if rendered:
            payload[field_name] = rendered

    if not str(payload.get("database") or "").strip() and not str(
        payload.get("url") or ""
    ).strip():
        payload["database"] = "./database/chat-client.db"

    return DatabaseSettings.model_validate(payload)
