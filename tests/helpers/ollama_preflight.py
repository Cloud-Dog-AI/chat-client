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

import subprocess
from typing import Optional

from cloud_dog_chat_client.config import ConfigManager


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def curl_ollama_tags(cfg: ConfigManager) -> None:
    provider = str(_require_cfg(cfg, "llm.provider")).strip().lower()
    if provider != "ollama":
        return
    base_url = str(_require_cfg(cfg, "llm.base_url")).rstrip("/")
    cmd = ["curl", "-fsS", f"{base_url}/api/tags"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(
            f"CRITICAL ERROR: Ollama preflight failed for {base_url}/api/tags; output={p.stdout.strip()}"
        )
