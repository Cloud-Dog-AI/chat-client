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

import hashlib
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from cloud_dog_chat_client.config import ConfigManager


class FileMCPRuntimeError(RuntimeError):
    pass


_RUNTIME_MODES = {"local-server", "local-docker", "remote-runtime"}


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise FileMCPRuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _runtime_mode(cfg: ConfigManager) -> str:
    raw = (
        cfg.get("chat_tests.runtime_mode")
        or cfg.get("tests.runtime_mode")
        or "local-server"
    )
    mode = str(raw).strip().lower()
    if mode not in _RUNTIME_MODES:
        raise FileMCPRuntimeError(
            f"CRITICAL ERROR: unsupported TEST_RUNTIME_MODE '{mode}'; expected one of {sorted(_RUNTIME_MODES)}"
        )
    return mode


def _use_external_runtime(cfg: ConfigManager, mode: str, *, key_prefix: str) -> bool:
    explicit = cfg.get(f"{key_prefix}.use_external_runtime")
    if explicit is not None:
        return _truthy(explicit)
    explicit = cfg.get("chat_tests.use_external_runtime")
    if explicit is not None:
        return _truthy(explicit)
    return mode in {"local-docker", "remote-runtime"}


def _run_cmd(cmd: list[str], timeout_seconds: float) -> None:
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            text=True,
        )
    except subprocess.TimeoutExpired as e:
        raise FileMCPRuntimeError(f"file-mcp lifecycle command timed out: {cmd}") from e
    if completed.returncode != 0:
        output = (completed.stdout or "").strip()
        raise FileMCPRuntimeError(f"file-mcp lifecycle command failed ({completed.returncode}): {cmd}; {output}")


def _listener_pids_for_port(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", f"-tiTCP:{int(port)}", "-sTCP:LISTEN"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    pids: list[int] = []
    for raw in (result.stdout or "").splitlines():
        raw = raw.strip()
        if raw.isdigit():
            pids.append(int(raw))
    return pids


def _runtime_pidfile(cfg: ConfigManager, *, key_prefix: str) -> str:
    raw_pidfile = str(_require_cfg(cfg, f"{key_prefix}.pidfile"))
    runtime_env = str(_require_cfg(cfg, f"{key_prefix}.env_path"))
    health_url = str(_require_cfg(cfg, f"{key_prefix}.health_url"))

    base, ext = os.path.splitext(raw_pidfile)
    fingerprint = hashlib.sha256(
        f"{runtime_env}|{health_url}".encode("utf-8")
    ).hexdigest()[:12]
    if ext:
        return f"{base}-{fingerprint}{ext}"
    return f"{raw_pidfile}-{fingerprint}"


def _stop_local_listener_for_health_url(url: str) -> None:
    parsed = urlsplit(str(url or "").strip())
    host = (parsed.hostname or "").strip().lower()
    port = parsed.port
    if host not in {"127.0.0.1", "localhost"} or not port:
        return
    for pid in _listener_pids_for_port(port):
        try:
            os.kill(pid, 15)
        except OSError:
            continue
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not _listener_pids_for_port(port):
            return
        time.sleep(0.2)
    for pid in _listener_pids_for_port(port):
        try:
            os.kill(pid, 9)
        except OSError:
            continue


def wait_for_file_mcp_health(url: str, *, timeout_seconds: float, poll_seconds: float) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            resp = httpx.get(url, timeout=poll_seconds)
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(poll_seconds)
    raise FileMCPRuntimeError(f"CRITICAL ERROR: File MCP not ready at {url}")


def _file_mcp_is_healthy(url: str, *, timeout_seconds: float = 1.0) -> bool:
    try:
        resp = httpx.get(url, timeout=timeout_seconds)
        return resp.status_code == 200
    except Exception:
        return False


def _file_mcp_runtime_env(url: str, *, timeout_seconds: float = 1.0) -> str:
    try:
        resp = httpx.get(url, timeout=timeout_seconds)
        if resp.status_code != 200:
            return ""
        payload = resp.json() or {}
    except Exception:
        return ""
    return str(((payload.get("runtime") or {}).get("env_file")) or "").strip()


def maybe_start_file_mcp(cfg: ConfigManager, *, key_prefix: str = "chat_tests.file_mcp") -> bool:
    control_script = cfg.get(f"{key_prefix}.control_script")
    if not control_script:
        return False

    mode = _runtime_mode(cfg)
    health_url = str(_require_cfg(cfg, f"{key_prefix}.health_url"))
    poll_seconds = float(cfg.get(f"{key_prefix}.ready_poll_seconds") or 0.5)
    runtime_env = str(_require_cfg(cfg, f"{key_prefix}.env_path"))

    if _use_external_runtime(cfg, mode, key_prefix=key_prefix):
        if _file_mcp_is_healthy(health_url, timeout_seconds=min(2.0, poll_seconds or 0.5)):
            return False
        raise FileMCPRuntimeError(
            f"BLOCKED: TEST_RUNTIME_MODE={mode} requires external file-mcp runtime, but health check failed at {health_url}"
        )

    runtime_config = str(_require_cfg(cfg, f"{key_prefix}.config_path"))
    runtime_defaults = str(_require_cfg(cfg, f"{key_prefix}.defaults_path"))
    runtime_pidfile = _runtime_pidfile(cfg, key_prefix=key_prefix)
    timeout_seconds = float(_require_cfg(cfg, f"{key_prefix}.control_timeout_seconds"))
    ready_timeout_seconds = float(_require_cfg(cfg, f"{key_prefix}.ready_timeout_seconds"))

    # In local-docker mode we require deterministic runtime state; an already
    # healthy endpoint may be an external/auth-protected service on the same port.
    # For local-server mode we still allow reuse to avoid pidfile drift.
    if _file_mcp_is_healthy(health_url, timeout_seconds=min(2.0, poll_seconds or 0.5)):
        active_runtime_env = Path(_file_mcp_runtime_env(health_url, timeout_seconds=min(2.0, poll_seconds or 0.5))).resolve()
        expected_runtime_env = Path(runtime_env).resolve()
        if mode not in {"local-server", "local-docker"} and active_runtime_env == expected_runtime_env:
            return False

    # Always clear stale pid/port state before starting a managed test runtime.
    _run_cmd(
        [
            "bash",
            str(control_script),
            "--env",
            runtime_env,
            "--config",
            runtime_config,
            "--defaults",
            runtime_defaults,
                "--pidfile",
                runtime_pidfile,
                "stop",
                "mcp",
            ],
        timeout_seconds,
    )
    if _file_mcp_is_healthy(health_url, timeout_seconds=min(2.0, poll_seconds or 0.5)):
        _stop_local_listener_for_health_url(health_url)

    try:
        _run_cmd(
            [
                "bash",
                str(control_script),
                "--env",
                runtime_env,
                "--config",
                runtime_config,
                "--defaults",
                runtime_defaults,
                "--pidfile",
                runtime_pidfile,
                "start",
                "mcp",
            ],
            timeout_seconds,
        )
    except FileMCPRuntimeError as e:
        # If startup raced with an already-running instance, continue when health is green.
        deadline = time.time() + ready_timeout_seconds
        while time.time() < deadline:
            if _file_mcp_is_healthy(health_url, timeout_seconds=min(2.0, poll_seconds or 0.5)):
                return False
            time.sleep(poll_seconds)
        if "running (pid" in str(e):
            _stop_local_listener_for_health_url(health_url)
            _run_cmd(
                [
                    "bash",
                    str(control_script),
                    "--env",
                    runtime_env,
                    "--config",
                    runtime_config,
                    "--defaults",
                    runtime_defaults,
                    "--pidfile",
                    runtime_pidfile,
                    "stop",
                    "mcp",
                ],
                timeout_seconds,
            )
            _run_cmd(
                [
                    "bash",
                    str(control_script),
                    "--env",
                    runtime_env,
                    "--config",
                    runtime_config,
                    "--defaults",
                    runtime_defaults,
                    "--pidfile",
                    runtime_pidfile,
                    "start",
                    "mcp",
                ],
                timeout_seconds,
            )
            wait_for_file_mcp_health(
                health_url,
                timeout_seconds=ready_timeout_seconds,
                poll_seconds=poll_seconds,
            )
            return True
        raise

    wait_for_file_mcp_health(health_url, timeout_seconds=ready_timeout_seconds, poll_seconds=poll_seconds)
    return True


def maybe_stop_file_mcp(cfg: ConfigManager, *, key_prefix: str = "chat_tests.file_mcp") -> None:
    control_script = cfg.get(f"{key_prefix}.control_script")
    if not control_script:
        return

    mode = _runtime_mode(cfg)
    if _use_external_runtime(cfg, mode, key_prefix=key_prefix):
        return

    runtime_env = str(_require_cfg(cfg, f"{key_prefix}.env_path"))
    runtime_config = str(_require_cfg(cfg, f"{key_prefix}.config_path"))
    runtime_defaults = str(_require_cfg(cfg, f"{key_prefix}.defaults_path"))
    runtime_pidfile = _runtime_pidfile(cfg, key_prefix=key_prefix)
    timeout_seconds = float(_require_cfg(cfg, f"{key_prefix}.control_timeout_seconds"))

    _run_cmd(
        [
            "bash",
            str(control_script),
            "--env",
            runtime_env,
            "--config",
            runtime_config,
            "--defaults",
            runtime_defaults,
            "--pidfile",
            runtime_pidfile,
            "stop",
            "mcp",
        ],
        timeout_seconds,
    )
