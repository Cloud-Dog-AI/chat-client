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
import subprocess
import sys
from typing import Optional

import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.ollama_preflight import curl_ollama_tags


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _run_cli(env_file: str, *, no_rich: bool, stream_override: Optional[bool] = None) -> str:
    cfg = ConfigManager(env_file=env_file)
    prompt = str(_require_cfg(cfg, "chat_tests.single_turn_prompt"))
    expected_marker = str(_require_cfg(cfg, "chat_tests.expected_default_marker"))
    expected_thinking = str(_require_cfg(cfg, "chat_tests.expected_thinking_tag"))
    expected_reasoning = str(_require_cfg(cfg, "chat_tests.expected_reasoning_tag"))

    cmd = [
        sys.executable,
        "-m",
        "cloud_dog_chat_client.cli",
        "chat",
        "--env",
        env_file,
    ]
    if no_rich:
        cmd.append("--no-rich")
    if stream_override is True:
        cmd.append("--stream")
    elif stream_override is False:
        cmd.append("--no-stream")

    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    proc = subprocess.run(
        cmd,
        input=f"{prompt}\n/exit\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=240,
        env=env,
    )

    output = proc.stdout or ""
    if expected_marker not in output:
        raise RuntimeError("CRITICAL ERROR: CLI output missing expected marker")
    if expected_thinking not in output:
        raise RuntimeError("CRITICAL ERROR: CLI output missing <thinking> tag")
    if expected_reasoning not in output:
        raise RuntimeError("CRITICAL ERROR: CLI output missing <reasoning> tag")
    return output
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_5_cli_chat_no_rich(env_file):
    cfg = ConfigManager(env_file=env_file)
    curl_ollama_tags(cfg)
    _run_cli(env_file, no_rich=True)
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_5_cli_chat_rich(env_file):
    cfg = ConfigManager(env_file=env_file)
    curl_ollama_tags(cfg)
    _run_cli(env_file, no_rich=False)
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_5_cli_chat_streaming(env_file):
    cfg = ConfigManager(env_file=env_file)
    curl_ollama_tags(cfg)
    _run_cli(env_file, no_rich=True, stream_override=True)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.llm, pytest.mark.slow]
