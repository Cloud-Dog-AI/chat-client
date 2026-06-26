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

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.ollama_preflight import curl_ollama_tags


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _run_cli(
    *,
    env_file: str,
    args: List[str],
    input_text: str,
    extra_env: dict,
) -> str:
    cmd = [sys.executable, "-m", "cloud_dog_chat_client.cli", "chat", "--no-rich"] + args
    env = dict(os.environ)
    env.update(extra_env)
    env["PYTHONPATH"] = "src"
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=240,
        env=env,
    )
    return proc.stdout or ""


def _find_session_log(log_folder: Path, session_id: str) -> Path:
    candidates = sorted((log_folder / "sessions").glob(f"*_{session_id}.jsonl"))
    if not candidates:
        raise RuntimeError("CRITICAL ERROR: session log file not found")
    return candidates[-1]
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
@pytest.mark.timeout(240)
async def test_st1_6_session_persistence_and_context(env_file, tmp_path):
    cfg = ConfigManager(env_file=env_file)
    curl_ollama_tags(cfg)

    prompt = str(_require_cfg(cfg, "chat_tests.single_turn_prompt"))
    expected_marker = str(_require_cfg(cfg, "chat_tests.expected_default_marker"))
    expected_thinking = str(_require_cfg(cfg, "chat_tests.expected_thinking_tag"))
    expected_reasoning = str(_require_cfg(cfg, "chat_tests.expected_reasoning_tag"))

    log_folder = tmp_path / "logs"
    context_file = tmp_path / "context.txt"
    context_file.write_text("seed context", encoding="utf-8")
    snapshot_file = tmp_path / "snapshot.json"

    output = _run_cli(
        env_file=env_file,
        args=[
            "--env",
            env_file,
            "--no-stream",
            "--print-session-id",
            "--context-file",
            str(context_file),
            "--save-context",
            str(snapshot_file),
            "--set",
            f"CLOUD_DOG__APP__LOGFOLDER={log_folder}",
        ],
        input_text=f"{prompt}\n/exit\n",
        extra_env={},
    )

    if expected_marker not in output:
        raise RuntimeError("CRITICAL ERROR: CLI output missing expected marker")
    if expected_thinking not in output:
        raise RuntimeError("CRITICAL ERROR: CLI output missing <thinking> tag")
    if expected_reasoning not in output:
        raise RuntimeError("CRITICAL ERROR: CLI output missing <reasoning> tag")

    session_line = next((line for line in output.splitlines() if line.startswith("[session-id] ")), None)
    if not session_line:
        raise RuntimeError("CRITICAL ERROR: CLI did not print session id")
    session_id = session_line.replace("[session-id] ", "").strip()

    log_path = _find_session_log(log_folder, session_id)
    lines_before = log_path.read_text(encoding="utf-8").splitlines()
    if not any('"event_type": "context_loaded"' in line for line in lines_before):
        raise RuntimeError("CRITICAL ERROR: context_loaded event not found in session log")

    if not snapshot_file.exists():
        raise RuntimeError("CRITICAL ERROR: context snapshot file not created")
    snapshot = json.loads(snapshot_file.read_text(encoding="utf-8"))
    if snapshot.get("session_id") != session_id:
        raise RuntimeError("CRITICAL ERROR: snapshot session_id mismatch")

    output2 = _run_cli(
        env_file=env_file,
        args=[
            "--env",
            env_file,
            "--no-stream",
            "--print-session-id",
            "--session-id",
            session_id,
            "--set",
            f"CLOUD_DOG__APP__LOGFOLDER={log_folder}",
        ],
        input_text=f"{prompt}\n/exit\n",
        extra_env={},
    )

    if f"[session-id] {session_id}" not in output2:
        raise RuntimeError("CRITICAL ERROR: resumed session id not printed")

    lines_after = log_path.read_text(encoding="utf-8").splitlines()
    if len(lines_after) <= len(lines_before):
        raise RuntimeError("CRITICAL ERROR: resumed session did not append events")
    if not any('"event_type": "session_resumed"' in line for line in lines_after):
        raise RuntimeError("CRITICAL ERROR: session_resumed event not found in session log")
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_6_default_env_file(env_file, tmp_path):
    cfg = ConfigManager(env_file=env_file)
    curl_ollama_tags(cfg)

    prompt = str(_require_cfg(cfg, "chat_tests.single_turn_prompt"))
    expected_marker = str(_require_cfg(cfg, "chat_tests.expected_default_marker"))

    log_folder = tmp_path / "logs-default-env"
    default_env = tmp_path / "default.env"
    default_env.write_text(Path(env_file).read_text(encoding="utf-8"), encoding="utf-8")

    output = _run_cli(
        env_file=env_file,
        args=[
            "--no-stream",
            "--set",
            f"CLOUD_DOG__APP__LOGFOLDER={log_folder}",
        ],
        input_text=f"{prompt}\n/exit\n",
        extra_env={"CLOUD_DOG__APP__ENV_FILE": str(default_env)},
    )

    if expected_marker not in output:
        raise RuntimeError("CRITICAL ERROR: CLI output missing expected marker using default env")
    if not (log_folder / "sessions").exists():
        raise RuntimeError("CRITICAL ERROR: log folder not created when using default env")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.llm, pytest.mark.heavy]
