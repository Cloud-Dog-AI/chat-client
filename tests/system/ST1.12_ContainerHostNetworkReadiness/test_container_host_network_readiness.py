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

import shutil
import socket
import subprocess
import time
from urllib.parse import urlparse

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _health_is_ok(*, base_url: str, health_path: str, timeout_seconds: float) -> bool:
    target = f"{base_url.rstrip('/')}{health_path}"
    try:
        resp = httpx.get(target, timeout=timeout_seconds)
    except Exception:
        return False
    if resp.status_code != 200:
        return False
    try:
        payload = resp.json()
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    application = payload.get("application")
    runtime = payload.get("runtime")
    return (
        payload.get("status") == "ok"
        and isinstance(application, dict)
        and isinstance(runtime, dict)
        and bool(str(runtime.get("env_file") or "").strip())
    )


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    proc = subprocess.run(
        ["docker", "version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=20,
        check=False,
    )
    return proc.returncode == 0


def _docker_rm_force(name: str) -> None:
    subprocess.run(
        ["docker", "rm", "-f", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
        check=False,
    )


def _tcp_port_open(*, host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


@pytest.fixture(scope="module", autouse=True)
def _ensure_host_network_container(env_file):
    cfg = ConfigManager(env_file=env_file)

    base_url = str(_require_cfg(cfg, "chat_tests.st1_12.base_url")).rstrip("/")
    health_path = str(_require_cfg(cfg, "chat_tests.st1_12.health_path"))
    request_timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))

    if _health_is_ok(base_url=base_url, health_path=health_path, timeout_seconds=request_timeout_seconds):
        yield None
        return

    if not _as_bool(cfg.get("chat_tests.st1_12.container_auto_start"), default=True):
        raise RuntimeError(
            "CRITICAL ERROR: ST1.12 target is not reachable and container auto-start is disabled"
        )
    if not _docker_available():
        raise RuntimeError("CRITICAL ERROR: docker is required for ST1.12 container readiness test")

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError(
            f"CRITICAL ERROR: refusing to auto-start hostnet container for non-local host: {host}"
        )
    api_port = int(parsed.port or 3001)

    container_name = str(cfg.get("chat_tests.st1_12.container_name") or "chat-client-st1-12-hostnet-local")
    container_image = str(cfg.get("chat_tests.st1_12.container_image") or "cloud-dog-chat-client:latest")
    container_env_file = str(cfg.get("chat_tests.st1_12.container_env_file") or "/app/env-docker-defaults")

    _docker_rm_force(container_name)

    env_pairs = {
        "CHAT_CLIENT_MODE": "api",
        "CHAT_CLIENT_API_HOST": "0.0.0.0",
        "CHAT_CLIENT_API_PORT": str(api_port),
        "CHAT_CLIENT_ENV_FILE": container_env_file,
        "CLOUD_DOG__CLIENT_API__REQUEST_TIMEOUT_SECONDS": str(
            int(float(cfg.get("client_api.request_timeout_seconds") or 300))
        ),
        "CLOUD_DOG__LLM__PROVIDER": str(cfg.get("llm.provider") or "ollama"),
        "CLOUD_DOG__LLM__BASE_URL": str(cfg.get("llm.base_url") or "https://llm.example.com"),
        "CLOUD_DOG__LLM__MODEL": str(cfg.get("llm.model") or "qwen3:14b"),
    }

    cmd = ["docker", "run", "-d", "--name", container_name, "--network", "host"]
    for key, value in env_pairs.items():
        cmd.extend(["-e", f"{key}={value}"])
    cmd.append(container_image)

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"CRITICAL ERROR: failed to start hostnet container: {proc.stdout}")

    ready_timeout_seconds = float(cfg.get("chat_tests.st1_12.container_ready_timeout_seconds") or 60)
    ready_poll_seconds = float(cfg.get("chat_tests.st1_12.container_ready_poll_seconds") or 1)
    deadline = time.time() + ready_timeout_seconds
    while time.time() < deadline:
        if _health_is_ok(base_url=base_url, health_path=health_path, timeout_seconds=request_timeout_seconds):
            yield None
            _docker_rm_force(container_name)
            return
        time.sleep(ready_poll_seconds)

    _docker_rm_force(container_name)
    raise RuntimeError(f"CRITICAL ERROR: hostnet container did not become ready at {base_url}{health_path}")
@pytest.mark.ST
@pytest.mark.cli
@pytest.mark.req("FR-009")


@pytest.mark.asyncio
async def test_st1_12_container_host_network_endpoints(env_file):
    cfg = ConfigManager(env_file=env_file)

    base_url = str(_require_cfg(cfg, "chat_tests.st1_12.base_url")).rstrip("/")
    root_path = str(cfg.get("chat_tests.st1_12.root_path") or "/")
    health_path = str(_require_cfg(cfg, "chat_tests.st1_12.health_path"))
    ui_path = str(_require_cfg(cfg, "chat_tests.st1_12.ui_path"))
    request_timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
    expect_root_redirect = _as_bool(cfg.get("chat_tests.st1_12.expect_root_redirect"), default=False)
    expected_root_redirect_status = int(cfg.get("chat_tests.st1_12.expected_root_redirect_status") or 307)

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8090)
    assert _tcp_port_open(host=host, port=port, timeout=request_timeout_seconds), (
        f"API port {port} is not reachable on {host}"
    )

    async with httpx.AsyncClient(base_url=base_url, timeout=request_timeout_seconds, follow_redirects=False) as client:
        root_resp = await client.get(root_path)
        if expect_root_redirect:
            assert root_resp.status_code == expected_root_redirect_status
            assert root_resp.headers.get("location") == ui_path
        else:
            assert root_resp.status_code in (200, 307, 308)

        health_resp = await client.get(health_path)
        assert health_resp.status_code == 200
        health = health_resp.json()
        assert health.get("status") == "ok"
        assert isinstance((health.get("application") or {}).get("name"), str)
        assert health.get("runtime", {}).get("env_file")

        ui_resp = await client.get(ui_path)
        assert ui_resp.status_code == 200
        ui_text = ui_resp.text
        assert "<!doctype html>" in ui_text.lower()
        assert "<div id=\"root\"></div>" in ui_text
        assert "/runtime-config.js" in ui_text

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.system, pytest.mark.llm, pytest.mark.docker, pytest.mark.slow]
