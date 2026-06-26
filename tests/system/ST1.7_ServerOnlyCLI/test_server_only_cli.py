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
import time
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_7_server_only_cli(env_file):
    cfg = ConfigManager(env_file=env_file)
    base_url = str(_require_cfg(cfg, "client_api.base_url")).rstrip("/")
    ready_seconds = float(_require_cfg(cfg, "client_api.ready_timeout_seconds"))
    poll_seconds = float(_require_cfg(cfg, "client_api.ready_poll_seconds"))
    request_timeout = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    log_folder = str(_require_cfg(cfg, "app.logfolder"))

    cmd = [
        sys.executable,
        "-m",
        "cloud_dog_chat_client.cli",
        "test-server",
        "--env",
        env_file,
    ]
    env = dict(**os.environ)
    env["PYTHONPATH"] = "src"
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)

    try:
        deadline = time.time() + ready_seconds
        while time.time() < deadline:
            try:
                resp = httpx.get(f"{base_url}/health", timeout=request_timeout)
                if resp.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(poll_seconds)
        else:
            raise RuntimeError("CRITICAL ERROR: test-server did not become ready")

        log_path = Path(log_folder) / "client_api_test.log"
        if not log_path.exists():
            raise RuntimeError("CRITICAL ERROR: test-server log file not created")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.pure, pytest.mark.slow]
