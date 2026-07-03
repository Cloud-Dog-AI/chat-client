# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited

from __future__ import annotations

import json
import os
import re
import signal
import ssl
import subprocess
import time
import urllib.request
import uuid
from urllib.parse import urlparse
from pathlib import Path
from typing import Any

import httpx
import pytest
import re

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import (
    api_base_url,
    api_headers,
    start_api,
    start_all,
    stop_api,
    stop_all,
    wait_for_api,
    wait_for_base_url,
    web_base_url,
)
from tests.helpers.cross_project import create_session, ensure_local_docker_runtime, llm_message, require_cfg, utc_ts

pytestmark = [pytest.mark.application, pytest.mark.slow]


def _blocked(reason: str) -> None:
    pytest.fail(f"BLOCKED: {reason}")


def _parse_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    target = Path(path)
    if not target.exists():
        return values
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _load_vault_json() -> dict[str, Any]:
    addr = os.environ.get("VAULT_ADDR", "").strip()
    mount = os.environ.get("VAULT_MOUNT_POINT", "").strip().strip("/")
    config_path = os.environ.get("VAULT_CONFIG_PATH", "").strip().strip("/")
    token = os.environ.get("VAULT_TOKEN", "").strip()
    if not addr or not mount or not config_path or not token:
        _blocked("Vault environment not available for searchmcp lookup")

    url = f"{addr}/v1/{mount}/data/{config_path}"
    request = urllib.request.Request(url, headers={"X-Vault-Token": token})
    with urllib.request.urlopen(request, context=ssl.create_default_context()) as response:
        payload = json.loads(response.read())
    secret_data = payload.get("data", {}).get("data", {})
    blob = secret_data.get("json", secret_data)
    if isinstance(blob, str):
        try:
            blob = json.loads(blob)
        except json.JSONDecodeError:
            _blocked("Vault payload json field is not valid JSON")
    if not isinstance(blob, dict):
        _blocked("Vault payload missing json object")
    return blob


def _vault_path(blob: dict[str, Any], path: list[str], *, label: str) -> str:
    current: Any = blob
    for part in path:
        if not isinstance(current, dict) or part not in current:
            _blocked(f"{label} not available: missing vault path {'.'.join(path)}")
        current = current[part]
    value = str(current or "").strip()
    if not value:
        _blocked(f"{label} not available: empty vault value at {'.'.join(path)}")
    return value


def _search_mcp_url() -> str:
    blob = _load_vault_json()
    return _vault_path(blob, ["dev", "services", "searchmcp", "uri"], label="searchmcp")


def _file_mcp_runtime(cfg: ConfigManager) -> dict[str, Any]:
    base_url = str(cfg.get("mcp.servers.1.base_url") or "").strip()
    if not base_url:
        _blocked("file-mcp MCP server base_url missing from AT config")
    token = str(cfg.get("mcp.servers.1.auth_bearer_token") or "").strip()
    if not token:
        token = str(cfg.get("mcp.servers.1.api_key") or "").strip()
    if not token:
        _blocked("file-mcp auth token missing from AT config")
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        _blocked(f"file-mcp base_url is invalid: {base_url}")
    endpoint_url = base_url.rstrip("/")
    if not endpoint_url.endswith("/mcp"):
        endpoint_url = f"{endpoint_url}/mcp"
    return {
        "endpoint_url": endpoint_url,
        "auth_config": {"type": "bearer", "value": token},
        "headers": {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    }


def _resolve_repo_path(path_value: str) -> Path:
    target = Path(path_value)
    if target.is_absolute():
        return target
    repo_root = Path(__file__).resolve().parents[3]
    candidates = [(repo_root / target).resolve()]
    if target.parts and target.parts[0] == "..":
        candidates.append((repo_root.parent / Path(*target.parts[1:])).resolve())
    else:
        candidates.append((repo_root.parent / target).resolve())
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _read_env_value(env_path: Path, key: str) -> str:
    if not env_path.is_file():
        return ""
    prefix = f"{key}="
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _write_resolved_env_copy(source_env: Path, cfg: ConfigManager) -> Path:
    target_dir = Path(__file__).resolve().parents[3] / "working" / "w28a-363"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{source_env.stem}.resolved.env"
    placeholder = re.compile(r"\$\{([^}]+)\}")
    local_expert_api_key = str(
        cfg.get("chat_tests.orchestration.expert.api_key")
        or "w28a363-local-expert-key"
    ).strip()
    unresolved_overrides = {
        "vault.dev.services.expertagent0.api_key": local_expert_api_key,
    }

    def _replace(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        if expr in unresolved_overrides:
            return unresolved_overrides[expr]
        value = cfg.get(expr)
        if value is None and expr.startswith("vault.dev.vdbs."):
            # The expert-agent AT env templates include optional vector-backend
            # secrets even when the selected backend does not require them.
            # Render those missing vault-backed values as empty strings so the
            # local orchestration runtime can boot with the active backend.
            return ""
        if value is None:
            raise RuntimeError(
                f"CRITICAL ERROR: unable to resolve orchestration expert env placeholder: {expr}"
            )
        return str(value)

    rendered: list[str] = []
    for raw in source_env.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\n")
        if "${" in line:
            line = placeholder.sub(_replace, line)
        rendered.append(line)
    target_path.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    return target_path


def _local_orchestration_expert_settings(cfg: ConfigManager) -> dict[str, Any] | None:
    control_script = str(cfg.get("chat_tests.orchestration.expert.control_script") or "").strip()
    env_path = str(cfg.get("chat_tests.orchestration.expert.env_path") or "").strip()
    fallback_env = Path(__file__).resolve().parents[3] / "tests" / "env-AT-local-docker"
    if not control_script and not env_path and fallback_env.is_file():
        control_script = _read_env_value(
            fallback_env,
            "CLOUD_DOG__CHAT_TESTS__ORCHESTRATION__EXPERT__CONTROL_SCRIPT",
        )
        env_path = _read_env_value(
            fallback_env,
            "CLOUD_DOG__CHAT_TESTS__ORCHESTRATION__EXPERT__ENV_PATH",
        )
    if not control_script and not env_path:
        return None
    if not control_script or not env_path:
        raise RuntimeError(
            "CRITICAL ERROR: incomplete orchestration expert runtime config; "
            "expected chat_tests.orchestration.expert.control_script and env_path"
        )

    control_script_path = _resolve_repo_path(control_script)
    env_file_path = _resolve_repo_path(env_path)
    if not control_script_path.is_file():
        raise RuntimeError(
            f"CRITICAL ERROR: orchestration expert control script not found: {control_script_path}"
        )
    if not env_file_path.is_file():
        raise RuntimeError(
            f"CRITICAL ERROR: orchestration expert env file not found: {env_file_path}"
        )

    resolved_env_path = _write_resolved_env_copy(env_file_path, cfg)
    expert_cfg = ConfigManager(env_file=str(resolved_env_path))
    api_host = str(expert_cfg.get("expert.api_server.host") or "127.0.0.1").strip()
    api_port = int(expert_cfg.get("expert.api_server.port") or 8030)
    mcp_host = str(expert_cfg.get("expert.mcp_server.host") or api_host).strip()
    mcp_port = int(expert_cfg.get("expert.mcp_server.port") or 8032)
    api_key = str(
        expert_cfg.get("expert.test.api_key")
        or expert_cfg.get("expert.api_key")
        or ""
    ).strip()
    if not api_key:
        raise RuntimeError("CRITICAL ERROR: orchestration expert API key resolved empty")

    return {
        "control_script": str(control_script_path),
        "env_path": str(resolved_env_path),
        "control_timeout_seconds": float(
            cfg.get("chat_tests.orchestration.expert.control_timeout_seconds")
            or _read_env_value(
                fallback_env,
                "CLOUD_DOG__CHAT_TESTS__ORCHESTRATION__EXPERT__CONTROL_TIMEOUT_SECONDS",
            )
            or 180
        ),
        "ready_timeout_seconds": float(
            cfg.get("chat_tests.orchestration.expert.ready_timeout_seconds")
            or _read_env_value(
                fallback_env,
                "CLOUD_DOG__CHAT_TESTS__ORCHESTRATION__EXPERT__READY_TIMEOUT_SECONDS",
            )
            or 60
        ),
        "ready_poll_seconds": float(
            cfg.get("chat_tests.orchestration.expert.ready_poll_seconds")
            or _read_env_value(
                fallback_env,
                "CLOUD_DOG__CHAT_TESTS__ORCHESTRATION__EXPERT__READY_POLL_SECONDS",
            )
            or 1
        ),
        "api_health_url": f"http://{api_host}:{api_port}/health",
        "mcp_health_url": f"http://{mcp_host}:{mcp_port}/mcp/health",
        "api_base_url": f"http://{api_host}:{api_port}",
        "mcp_base_url": f"http://{mcp_host}:{mcp_port}",
        "api_key_header": "X-API-Key",
        "api_key": api_key,
    }


def _run_server_control(
    control_script: str,
    env_path: str,
    action: str,
    target: str | None,
    *,
    timeout_seconds: float,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    cmd = ["bash", control_script, "--env", env_path, action]
    if target:
        cmd.append(target)
    child_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CLOUD_DOG__EXPERT__")
    }
    completed = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout_seconds,
        env=child_env,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"CRITICAL ERROR: orchestration expert server_control failed "
            f"({action}{f' {target}' if target else ''}, rc={completed.returncode}): "
            f"{(completed.stdout or '').strip()}"
        )
    return completed


def _run_server_control_best_effort(
    control_script: str,
    env_path: str,
    action: str,
    target: str | None,
    *,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    """Run server_control with a hard timeout that cannot stall fixture teardown."""
    cmd = ["bash", control_script, "--env", env_path, action]
    if target:
        cmd.append(target)
    child_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CLOUD_DOG__EXPERT__")
    }
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=child_env,
        start_new_session=True,
    )
    try:
        stdout, _ = proc.communicate(timeout=timeout_seconds)
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout=stdout, stderr=None)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return subprocess.CompletedProcess(
            cmd,
            124,
            stdout=(
                "CRITICAL WARNING: orchestration expert server_control "
                f"{action}{f' {target}' if target else ''} timed out after {timeout_seconds:.1f}s"
            ),
            stderr=None,
        )


def _expert_health_ready(*health_urls: str, timeout_seconds: float) -> bool:
    try:
        for health_url in health_urls:
            response = httpx.get(health_url, timeout=timeout_seconds)
            if response.status_code != 200:
                return False
    except Exception:
        return False
    return True


def _expert_api_auth_ready(settings: dict[str, Any], *, timeout_seconds: float) -> bool:
    try:
        response = httpx.get(
            f"{str(settings['api_base_url']).rstrip('/')}/experts",
            headers={str(settings["api_key_header"]): str(settings["api_key"])},
            timeout=timeout_seconds,
        )
        return response.status_code == 200
    except Exception:
        return False


def _ensure_local_orchestration_expert_runtime(cfg: ConfigManager) -> None:
    settings = _local_orchestration_expert_settings(cfg)
    if not settings:
        return

    timeout_seconds = float(settings["control_timeout_seconds"])
    if _expert_health_ready(
        str(settings["api_health_url"]),
        str(settings["mcp_health_url"]),
        timeout_seconds=5.0,
    ) and _expert_api_auth_ready(settings, timeout_seconds=5.0):
        return

    _run_server_control(
        settings["control_script"],
        settings["env_path"],
        "force-stop",
        None,
        timeout_seconds=min(timeout_seconds, 15.0),
        check=False,
    )
    _run_server_control(
        settings["control_script"],
        settings["env_path"],
        "start",
        None,
        timeout_seconds=timeout_seconds,
        check=True,
    )

    deadline = time.time() + float(settings["ready_timeout_seconds"])
    poll_seconds = float(settings["ready_poll_seconds"])
    while time.time() < deadline:
        if _expert_health_ready(
            str(settings["api_health_url"]),
            str(settings["mcp_health_url"]),
            timeout_seconds=poll_seconds,
        ):
            return
        time.sleep(poll_seconds)

    # `server_control.sh start` can leave the expert runtime partially started
    # (for example web/a2a up while api/mcp are still down). Retry the critical
    # surfaces directly once before declaring the orchestration runtime broken.
    for target in ("api", "mcp"):
        _run_server_control(
            settings["control_script"],
            settings["env_path"],
            "start",
            target,
            timeout_seconds=timeout_seconds,
            check=False,
        )

    retry_deadline = time.time() + min(float(settings["ready_timeout_seconds"]), 30.0)
    while time.time() < retry_deadline:
        if _expert_health_ready(
            str(settings["api_health_url"]),
            str(settings["mcp_health_url"]),
            timeout_seconds=poll_seconds,
        ):
            return
        time.sleep(poll_seconds)

    status = _run_server_control(
        settings["control_script"],
        settings["env_path"],
        "status",
        None,
        timeout_seconds=timeout_seconds,
        check=False,
    )
    raise RuntimeError(
        "CRITICAL ERROR: orchestration expert runtime not ready at "
        f"{settings['api_health_url']} / {settings['mcp_health_url']}. "
        f"status={(status.stdout or '').strip()}"
    )


def _stop_local_orchestration_expert_runtime(cfg: ConfigManager) -> None:
    settings = _local_orchestration_expert_settings(cfg)
    if not settings:
        return
    timeout_seconds = min(float(settings["control_timeout_seconds"]), 30.0)
    stop_result = _run_server_control_best_effort(
        settings["control_script"],
        settings["env_path"],
        "stop",
        None,
        timeout_seconds=timeout_seconds,
    )
    stop_timed_out = stop_result.returncode == 124

    status_output = ""
    try:
        status = _run_server_control(
            settings["control_script"],
            settings["env_path"],
            "status",
            None,
            timeout_seconds=5.0,
            check=False,
        )
        status_output = str(status.stdout or "")
    except subprocess.TimeoutExpired:
        stop_timed_out = True

    has_stale_process = (
        "Process PID:" in status_output and "running" in status_output.lower()
    )
    still_healthy = _expert_health_ready(
        str(settings["api_health_url"]),
        str(settings["mcp_health_url"]),
        timeout_seconds=1.5,
    )
    if stop_timed_out or has_stale_process or still_healthy:
        _run_server_control_best_effort(
            settings["control_script"],
            settings["env_path"],
            "force-stop",
            None,
            timeout_seconds=timeout_seconds,
        )


def _expert_agent_runtime(cfg: ConfigManager) -> dict[str, Any]:
    local_settings = _local_orchestration_expert_settings(cfg)
    if local_settings:
        return {
            "mcp_base_url": str(local_settings["mcp_base_url"]),
            "api_base_url": str(local_settings["api_base_url"]),
            "headers": {str(local_settings["api_key_header"]): str(local_settings["api_key"])},
            "api_key_header": str(local_settings["api_key_header"]),
            "api_key": str(local_settings["api_key"]),
        }

    servers_cfg = cfg.get("mcp.servers")
    candidates: list[dict[str, Any]] = []
    if isinstance(servers_cfg, dict):
        ordered_values = [servers_cfg[key] for key in sorted(servers_cfg.keys(), key=str)]
    elif isinstance(servers_cfg, list):
        ordered_values = servers_cfg
    else:
        ordered_values = []

    for item in ordered_values:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        if "expert" in name:
            candidates.append(item)

    if candidates:
        server = candidates[0]
        base_url = str(server.get("base_url") or "").strip()
        api_base_url = str(
            server.get("assist_api_base_url")
            or server.get("api_base_url")
            or ""
        ).strip()
        api_key = str(server.get("api_key") or "").strip()
        api_key_header = str(server.get("api_key_header") or "X-API-Key").strip()
    else:
        base_url = str(
            cfg.get("mcp.servers.2.base_url")
            or cfg.get("mcp.servers.1.base_url")
            or ""
        ).strip()
        api_base_url = str(
            cfg.get("mcp.servers.2.assist_api_base_url")
            or cfg.get("mcp.servers.2.api_base_url")
            or cfg.get("mcp.servers.1.assist_api_base_url")
            or cfg.get("mcp.servers.1.api_base_url")
            or ""
        ).strip()
        api_key = str(
            cfg.get("mcp.servers.2.api_key")
            or cfg.get("mcp.servers.1.api_key")
            or ""
        ).strip()
        api_key_header = str(
            cfg.get("mcp.servers.2.api_key_header")
            or cfg.get("mcp.servers.1.api_key_header")
            or "X-API-Key"
        ).strip()
    if not base_url or not api_key:
        pytest.fail("CRITICAL ERROR: expert-agent MCP config not available in env file")

    if not api_base_url:
        parsed = httpx.URL(base_url)
        api_base_url = f"{parsed.scheme}://{parsed.host}/api"
    return {
        "mcp_base_url": base_url,
        "api_base_url": api_base_url.rstrip("/"),
        "headers": {api_key_header: api_key},
        "api_key_header": api_key_header,
        "api_key": api_key,
    }


def _extract_result_text(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    result = payload.get("result")
    if isinstance(result, dict):
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            for key in ("result", "content", "text", "output_text"):
                value = structured.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = str(item.get("text") or "").strip()
                    if text:
                        return text
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict):
                text = str(item.get("text") or item.get("content") or "").strip()
                if text:
                    return text
    return ""


def _parse_mcp_response_payload(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        return json.loads(text)
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        data_lines = [
            line[5:].strip()
            for line in block.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        candidate = "\n".join(data_lines).strip()
        if candidate.startswith("{"):
            return json.loads(candidate)
    raise RuntimeError(f"CRITICAL ERROR: unable to parse MCP response payload: {text[:240]}")


async def _mcp_call(url: str, tool_name: str, arguments: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        )
    response.raise_for_status()
    return _parse_mcp_response_payload(response.text)


async def _ensure_expert_orchestration_capability(runtime: ConfigManager) -> None:
    expert_runtime = _expert_agent_runtime(runtime)
    timeout = float(runtime.get("client_api.request_timeout_seconds") or 120)
    async with httpx.AsyncClient(
        base_url=expert_runtime["api_base_url"],
        headers=expert_runtime["headers"],
        timeout=timeout,
        verify=True,
    ) as client:
        response = await client.get("/openapi.json")
    if response.status_code != 200:
        _blocked(f"expert-agent OpenAPI unavailable: {response.status_code} {response.text[:200]}")
    payload = response.json() if response.text.strip() else {}
    paths = payload.get("paths") if isinstance(payload, dict) else {}
    if not isinstance(paths, dict):
        _blocked("expert-agent OpenAPI missing paths object")
    missing: list[str] = []
    if "/experts/{expert_id}/services" not in paths:
        missing.append("/experts/{expert_id}/services")
    if "/experts/{expert_id}/execute" not in paths:
        missing.append("/experts/{expert_id}/execute")
    if missing:
        _blocked("expert-agent deployed API lacks required orchestration routes: " + ", ".join(missing))


async def _chat_transcript(client: httpx.AsyncClient, session_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"/sessions/{session_id}/transcript")
    assert response.status_code == 200, response.text
    events = response.json().get("events") or []
    return [item for item in events if isinstance(item, dict)]


async def _create_profile(client: httpx.AsyncClient, admin_headers: dict[str, str], payload: dict[str, Any]) -> None:
    response = await client.post("/v1/profiles", json=payload, headers=admin_headers)
    assert response.status_code == 200, response.text


async def _delete_profile(client: httpx.AsyncClient, admin_headers: dict[str, str], profile_id: str) -> None:
    try:
        response = await client.delete(f"/v1/profiles/{profile_id}", headers=admin_headers)
    except httpx.ConnectError:
        return
    assert response.status_code in {200, 404}, response.text


async def _delete_session(client: httpx.AsyncClient, session_id: str) -> None:
    try:
        response = await client.delete(f"/sessions/{session_id}")
    except httpx.ConnectError:
        return
    assert response.status_code in {200, 404}, response.text


def _start_chat_runtime(env_file: str, *, local_docker_mode: str) -> ConfigManager:
    os.environ["CLOUD_DOG__CLIENT_API__START_TIMEOUT_SECONDS"] = "180"
    os.environ["CLOUD_DOG__CHAT_TESTS__USE_EXTERNAL_RUNTIME"] = "true"
    os.environ["CLOUD_DOG__CHAT_TESTS__LOCAL_DOCKER__MANAGE_RUNTIME"] = "true"
    os.environ["CLOUD_DOG__CHAT_TESTS__LOCAL_DOCKER__MODE"] = local_docker_mode
    cfg = ConfigManager(env_file=env_file)
    ensure_local_docker_runtime(cfg, "chat_tests.at1_23.file_mcp", label="W28A-294 file-mcp")
    if local_docker_mode != "all":
        start_api(cfg, env_file=env_file)
    return cfg


@pytest.fixture()
def _runtime(env_file: str):
    cfg = _start_chat_runtime(env_file, local_docker_mode="api")
    expert_started = False
    try:
        _ensure_local_orchestration_expert_runtime(cfg)
        expert_started = True
        wait_for_api(cfg)
        yield cfg
    finally:
        if expert_started:
            _stop_local_orchestration_expert_runtime(cfg)
        stop_api(cfg, env_file=env_file)


@pytest.fixture()
def _runtime_all(env_file: str):
    cfg = _start_chat_runtime(env_file, local_docker_mode="all")
    expert_started = False
    try:
        start_all(cfg, env_file=env_file)
        _ensure_local_orchestration_expert_runtime(cfg)
        expert_started = True
        wait_for_api(cfg)
        wait_for_base_url(cfg, web_base_url(cfg))
        yield cfg
    finally:
        if expert_started:
            _stop_local_orchestration_expert_runtime(cfg)
        stop_all(cfg, env_file=env_file)


async def _provision_orchestration_api(
    *,
    runtime: ConfigManager,
    mode: str,
    file_root: str,
    search_url: str,
    file_mcp: dict[str, Any],
) -> dict[str, Any]:
    expert_runtime = _expert_agent_runtime(runtime)
    suffix = uuid.uuid4().hex[:8]
    headers = expert_runtime["headers"]
    timeout = float(runtime.get("client_api.request_timeout_seconds") or 120)
    created: dict[str, Any] = {"services": [], "experts": []}

    async with httpx.AsyncClient(
        base_url=expert_runtime["api_base_url"],
        headers=headers,
        timeout=timeout,
        verify=True,
    ) as client:
        expert_list = await client.get("/experts")
        if expert_list.status_code != 200:
            _blocked(f"expert-agent experts API unavailable: {expert_list.status_code} {expert_list.text[:200]}")
        experts = expert_list.json().get("experts") or []
        if not experts:
            _blocked("expert-agent returned no experts to clone LLM settings from")
        template_expert = experts[0]

        if mode == "research":
            search_service = await client.post(
                "/services",
                json={
                    "name": f"w28a294-search-{suffix}",
                    "service_type": "mcp",
                    "endpoint_url": search_url,
                },
            )
            if search_service.status_code != 200:
                _blocked(f"searchmcp service registry unavailable: {search_service.status_code} {search_service.text[:200]}")
            search_service_id = int(search_service.json()["id"])
            created["services"].append(search_service_id)

            file_service = await client.post(
                "/services",
                json={
                    "name": f"w28a294-file-{suffix}",
                    "service_type": "mcp",
                    "endpoint_url": file_mcp["endpoint_url"],
                    "auth_config": file_mcp["auth_config"],
                },
            )
            assert file_service.status_code == 200, file_service.text
            file_service_id = int(file_service.json()["id"])
            created["services"].append(file_service_id)

            output_file = f"{file_root}/w28a294/{suffix}/quantum-summary.md"
            expert_resp = await client.post(
                "/experts",
                json={
                    "name": f"w28a294_research_{suffix}",
                    "title": "Research Orchestrator",
                    "description": "Searches for recent articles, summarises them, and persists the summary to file-mcp.",
                    "llm_provider": template_expert.get("llm_provider"),
                    "llm_model": template_expert.get("llm_model"),
                    "prompt_template": (
                        "You are an orchestration expert. Use the provided service invocation results to answer directly. "
                        "Summarise the three most relevant recent findings with source cues and explicitly state when the summary was saved to file."
                    ),
                },
            )
            assert expert_resp.status_code == 200, expert_resp.text
            expert_id = int(expert_resp.json()["id"])
            created["experts"].append(expert_id)

            assert (await client.post(f"/experts/{expert_id}/services", json={"service_id": search_service_id, "priority": 1})).status_code == 200
            assert (await client.post(f"/experts/{expert_id}/services", json={"service_id": file_service_id, "priority": 2})).status_code == 200
            return {
                "expert": expert_runtime,
                "expert_id": expert_id,
                "created": created,
                "output_file": output_file,
                "assist_execute_parameters": {
                    "persist_session": True,
                    "max_tokens": 384,
                    "service_tool_calls": [
                        {
                            "service_id": search_service_id,
                            "tool_name": "search",
                            "arguments": {"query": "${input_text}", "max_results": "3"},
                        }
                    ],
                    "post_service_tool_calls": [
                        {
                            "service_id": file_service_id,
                            "tool_name": "write_file",
                            "arguments": {
                                "path": output_file,
                                "content": "${output_text}",
                                "overwrite": True,
                            },
                        }
                    ],
                },
            }

        file_service = await client.post(
            "/services",
            json={
                "name": f"w28a294-file-{suffix}",
                "service_type": "mcp",
                "endpoint_url": file_mcp["endpoint_url"],
                "auth_config": file_mcp["auth_config"],
            },
        )
        assert file_service.status_code == 200, file_service.text
        file_service_id = int(file_service.json()["id"])
        created["services"].append(file_service_id)

        expert_resp = await client.post(
            "/experts",
            json={
                "name": f"w28a294_files_{suffix}",
                "title": "File Orchestrator",
                "description": "Lists markdown files, summarises them, and writes a combined summary file.",
                "llm_provider": template_expert.get("llm_provider"),
                "llm_model": template_expert.get("llm_model"),
                "prompt_template": (
                    "You are a file-processing orchestration expert. "
                    "A later system message titled 'Service invocation results' contains authoritative results from "
                    "file tools that have already run, including list_dir and read_file outputs. "
                    "Treat those service results as your file-system access. "
                    "Never say you cannot access the filesystem, directories, or files when those service results are present. "
                    "For the first request, identify alpha.md, beta.md, and gamma.md from the service results and explain what each file is about in one concise bullet per file. "
                    "For the second request, produce a combined summary suitable for saving via the write_file post-service call and confirm the summary path when saved."
                ),
            },
        )
        assert expert_resp.status_code == 200, expert_resp.text
        expert_id = int(expert_resp.json()["id"])
        created["experts"].append(expert_id)
        assert (await client.post(f"/experts/{expert_id}/services", json={"service_id": file_service_id, "priority": 1})).status_code == 200

        summary_file = f"{file_root}/w28a294/{suffix}/combined-summary.md"
        markdown_paths = [
            f"{file_root}/w28a294/{suffix}/alpha.md",
            f"{file_root}/w28a294/{suffix}/beta.md",
            f"{file_root}/w28a294/{suffix}/gamma.md",
        ]
        return {
            "expert": expert_runtime,
            "expert_id": expert_id,
            "created": created,
            "summary_file": summary_file,
            "markdown_paths": markdown_paths,
            "assist_execute_parameters": {
                "persist_session": True,
                "max_tokens": 768,
                "service_tool_calls": [
                    {
                        "service_id": file_service_id,
                        "tool_name": "list_dir",
                        "arguments": {"path": f"{file_root}/w28a294/{suffix}", "recursive": False},
                    },
                    *[
                        {
                            "service_id": file_service_id,
                            "tool_name": "read_file",
                            "arguments": {"path": path},
                        }
                        for path in markdown_paths
                    ],
                ],
                "post_service_tool_calls": [
                    {
                        "service_id": file_service_id,
                        "tool_name": "write_file",
                        "arguments": {
                            "path": summary_file,
                            "content": "${output_text}",
                            "overwrite": True,
                        },
                    }
                ],
            },
        }


async def _cleanup_orchestration_api(runtime: ConfigManager, created: dict[str, Any]) -> None:
    expert_runtime = _expert_agent_runtime(runtime)
    timeout = float(runtime.get("client_api.request_timeout_seconds") or 120)
    async with httpx.AsyncClient(
        base_url=expert_runtime["api_base_url"],
        headers=expert_runtime["headers"],
        timeout=timeout,
        verify=True,
    ) as client:
        for expert_id in reversed(created.get("experts", [])):
            try:
                await client.delete(f"/experts/{expert_id}")
            except Exception:
                pass
        for service_id in reversed(created.get("services", [])):
            try:
                await client.delete(f"/services/{service_id}")
            except Exception:
                pass


def _orchestration_binding(*, expert_runtime: dict[str, Any], expert_id: int, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": f"expert-orchestrator-{expert_id}",
        "transport": "http_jsonrpc",
        "base_url": expert_runtime["mcp_base_url"],
        "mcp_path": "/mcp",
        "health_path": "/mcp/health",
        "api_key_header": expert_runtime["api_key_header"],
        "api_key": expert_runtime["api_key"],
        "verify_tls": True,
        "assist_role": "expert_execute",
        "assist_api_base_url": expert_runtime["api_base_url"],
        "assist_expert_config_id": expert_id,
        "assist_execute_parameters": parameters,
        "assist_max_tokens": 384,
        "assist_history_messages": 8,
    }


async def _run_research_scenario(runtime: ConfigManager, *, keep_resources: bool = False) -> dict[str, Any]:
    cfg = runtime
    await _ensure_expert_orchestration_capability(cfg)
    file_mcp = _file_mcp_runtime(cfg)
    search_url = _search_mcp_url()
    timeout = float(require_cfg(cfg, "client_api.request_timeout_seconds"))
    api_header = str(cfg.get("client_api.admin_api_key_header") or cfg.get("client_api.api_key_header") or "X-API-Key").strip()
    admin_key = str(require_cfg(cfg, "client_api.admin_api_key") or "").strip()
    if not admin_key:
        _blocked("chat-client admin API key not configured")
    admin_headers = {api_header: admin_key}

    ts = utc_ts()
    profile_id = f"w28a294-research-{ts}"
    session_title = "w28a294-orch-a"
    session_id = ""
    provisioned: dict[str, Any] | None = None

    async with httpx.AsyncClient(
        base_url=api_base_url(cfg),
        headers=api_headers(cfg),
        timeout=timeout,
    ) as client:
        try:
            provisioned = await _provision_orchestration_api(
                runtime=cfg,
                mode="research",
                file_root=str(cfg.get("chat_tests.at1_23.file_root") or "/path/to/cloud-dog-ai/chat-client/working/file-mcp-runtime/root").rstrip("/"),
                search_url=search_url,
                file_mcp=file_mcp,
            )
            binding = _orchestration_binding(
                expert_runtime=provisioned["expert"],
                expert_id=int(provisioned["expert_id"]),
                parameters=provisioned["assist_execute_parameters"],
            )
            await _create_profile(
                client,
                admin_headers,
                {
                    "profile_id": profile_id,
                    "name": f"W28A-294 Research {ts}",
                    "description": "Chat-driven expert-agent research orchestration",
                    "mcp_bindings": [binding],
                    "session_defaults": {"selected_mcp_server_indices": [0]},
                    "access_control": {"roles": ["admin", "viewer"]},
                },
            )
            session_id = await create_session(client, session_title, metadata={"profile_id": profile_id})
            prefs = await client.put(f"/sessions/{session_id}/preferences", json={"selected_mcp_server_indices": [0]})
            assert prefs.status_code == 200, prefs.text

            prompt_1 = "Search for 3 recent articles about quantum computing and save the summaries to a file"
            reply_1 = await llm_message(client, session_id, prompt_1, stream=False)
            lowered_1 = reply_1.lower()
            assert "quantum" in lowered_1, reply_1
            assert any(token in lowered_1 for token in ["article", "summary", "saved"]), reply_1

            file_read = await _mcp_call(
                file_mcp["endpoint_url"],
                "read_file",
                {"path": provisioned["output_file"]},
                file_mcp["headers"],
            )
            file_text = _extract_result_text(file_read)
            assert file_text, file_read
            assert "quantum" in file_text.lower(), file_text

            prompt_2 = "Now summarise what you found in one paragraph"
            reply_2 = await llm_message(client, session_id, prompt_2, stream=False)
            lowered_2 = reply_2.lower()
            assert "quantum" in lowered_2, reply_2
            assert any(token in lowered_2 for token in ["summary", "article", "research"]), reply_2

            transcript = await _chat_transcript(client, session_id)
            convo = [
                item for item in transcript
                if str(item.get("event_type") or "") in {"user_message", "assistant_message"}
            ]
            assert len(convo) == 4, transcript
            assert any(
                str(item.get("event_type") or "") == "mcp_tool_call"
                and str((item.get("data") or {}).get("name") or "") == "expert_execute"
                for item in transcript
            ), transcript
            result = {
                "session_id": session_id,
                "profile_id": profile_id,
                "reply_1": reply_1,
                "reply_2": reply_2,
                "output_file": provisioned["output_file"],
                "session_title": session_title,
                "cleanup": {"provisioned": provisioned, "admin_headers": admin_headers},
            }
            if keep_resources:
                return result
            return result
        except Exception:
            if provisioned is not None:
                try:
                    await _mcp_call(
                        file_mcp["endpoint_url"],
                        "delete_file",
                        {"path": provisioned["output_file"], "missing_ok": True},
                        file_mcp["headers"],
                    )
                except Exception:
                    pass
                await _cleanup_orchestration_api(cfg, provisioned["created"])
            if session_id:
                await _delete_session(client, session_id)
            await _delete_profile(client, admin_headers, profile_id)
            raise
        finally:
            if not keep_resources and provisioned is not None:
                try:
                    await _mcp_call(
                        file_mcp["endpoint_url"],
                        "delete_file",
                        {"path": provisioned["output_file"], "missing_ok": True},
                        file_mcp["headers"],
                    )
                except Exception:
                    pass
                await _cleanup_orchestration_api(cfg, provisioned["created"])
            if not keep_resources and session_id:
                await _delete_session(client, session_id)
            if not keep_resources:
                await _delete_profile(client, admin_headers, profile_id)


async def _run_file_scenario(runtime: ConfigManager) -> dict[str, Any]:
    cfg = runtime
    await _ensure_expert_orchestration_capability(cfg)
    file_mcp = _file_mcp_runtime(cfg)
    timeout = float(require_cfg(cfg, "client_api.request_timeout_seconds"))
    api_header = str(cfg.get("client_api.admin_api_key_header") or cfg.get("client_api.api_key_header") or "X-API-Key").strip()
    admin_key = str(require_cfg(cfg, "client_api.admin_api_key") or "").strip()
    if not admin_key:
        _blocked("chat-client admin API key not configured")
    admin_headers = {api_header: admin_key}

    ts = utc_ts()
    profile_id = f"w28a294-files-{ts}"
    session_id = ""
    provisioned: dict[str, Any] | None = None
    file_root = str(cfg.get("chat_tests.at1_23.file_root") or "/path/to/cloud-dog-ai/chat-client/working/file-mcp-runtime/root").rstrip("/")

    async with httpx.AsyncClient(
        base_url=api_base_url(cfg),
        headers=api_headers(cfg),
        timeout=timeout,
    ) as client:
        try:
            provisioned = await _provision_orchestration_api(
                runtime=cfg,
                mode="files",
                file_root=file_root,
                search_url="",
                file_mcp=file_mcp,
            )
            for path, content in {
                provisioned["markdown_paths"][0]: "# Alpha\nAlpha covers safe deployment controls and review gates.",
                provisioned["markdown_paths"][1]: "# Beta\nBeta covers delegated workflows and expert coordination.",
                provisioned["markdown_paths"][2]: "# Gamma\nGamma covers file handling, summaries, and cleanup guarantees.",
            }.items():
                await _mcp_call(
                    file_mcp["endpoint_url"],
                    "write_file",
                    {"path": path, "content": content, "overwrite": True},
                    file_mcp["headers"],
                )

            binding = _orchestration_binding(
                expert_runtime=provisioned["expert"],
                expert_id=int(provisioned["expert_id"]),
                parameters=provisioned["assist_execute_parameters"],
            )
            await _create_profile(
                client,
                admin_headers,
                {
                    "profile_id": profile_id,
                    "name": f"W28A-294 File Processing {ts}",
                    "description": "Chat-driven expert-agent file orchestration",
                    "mcp_bindings": [binding],
                    "session_defaults": {"selected_mcp_server_indices": [0]},
                    "access_control": {"roles": ["admin", "viewer"]},
                },
            )
            session_id = await create_session(client, "w28a294-orch-b", metadata={"profile_id": profile_id})
            prefs = await client.put(f"/sessions/{session_id}/preferences", json={"selected_mcp_server_indices": [0]})
            assert prefs.status_code == 200, prefs.text

            reply_1 = await llm_message(
                client,
                session_id,
                (
                    "Use the file orchestration expert to inspect the provisioned markdown files "
                    "and tell me what alpha.md, beta.md, and gamma.md are about."
                ),
                stream=False,
            )
            lowered_1 = reply_1.lower()
            assert "alpha" in lowered_1 and "beta" in lowered_1 and "gamma" in lowered_1, reply_1

            reply_2 = await llm_message(
                client,
                session_id,
                (
                    "Use the same file orchestration expert to create and save one combined summary "
                    "document covering alpha.md, beta.md, and gamma.md."
                ),
                stream=False,
            )
            lowered_2 = reply_2.lower()
            assert any(token in lowered_2 for token in ["saved", "summary", "combined"]), reply_2

            summary_read = await _mcp_call(
                file_mcp["endpoint_url"],
                "read_file",
                {"path": provisioned["summary_file"]},
                file_mcp["headers"],
            )
            summary_text = _extract_result_text(summary_read)
            assert summary_text, summary_read
            assert "alpha" in summary_text.lower() and "beta" in summary_text.lower(), summary_text
            return {
                "session_id": session_id,
                "profile_id": profile_id,
                "summary_file": provisioned["summary_file"],
            }
        finally:
            if provisioned is not None:
                for path in provisioned.get("markdown_paths", []):
                    try:
                        await _mcp_call(
                            file_mcp["endpoint_url"],
                            "delete_file",
                            {"path": path, "missing_ok": True},
                            file_mcp["headers"],
                        )
                    except Exception:
                        pass
                try:
                    await _mcp_call(
                        file_mcp["endpoint_url"],
                        "delete_file",
                        {"path": provisioned["summary_file"], "missing_ok": True},
                        file_mcp["headers"],
                    )
                except Exception:
                    pass
                await _cleanup_orchestration_api(cfg, provisioned["created"])
            if session_id:
                await _delete_session(client, session_id)
            await _delete_profile(client, admin_headers, profile_id)
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_w28a294_scenario_a_chat_driven_web_research(_runtime: ConfigManager) -> None:
    await _run_research_scenario(_runtime)
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_w28a294_scenario_b_chat_driven_file_processing(_runtime: ConfigManager) -> None:
    await _run_file_scenario(_runtime)
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_w28a294_scenario_c_webui_observes_existing_session(_runtime_all: ConfigManager) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    cfg = _runtime_all
    timeout = float(require_cfg(cfg, "client_api.request_timeout_seconds"))
    api_key = str(require_cfg(cfg, "client_api.api_key") or "").strip()
    if not api_key:
        _blocked("chat-client API key not configured for WebUI scenario")
    scenario = await _run_research_scenario(cfg, keep_resources=True)
    session_id = scenario["session_id"]
    session_title = str(scenario.get("session_title") or session_id)
    cleanup = scenario["cleanup"]
    file_mcp = _file_mcp_runtime(cfg)
    try:
        async with playwright.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto(f"{web_base_url(cfg)}/ui", wait_until="networkidle")
            if await page.locator("#loginUsername").count():
                await page.fill("#loginUsername", "admin")
                await page.fill("#loginPassword", "OrangeRiverTable")
            elif await page.locator("#api-key").count():
                await page.fill("#api-key", api_key)
            elif await page.locator("#api-key-input").count():
                await page.fill("#api-key-input", api_key)
            else:
                raise AssertionError("CRITICAL ERROR: no recognised WebUI login form was rendered")
            await page.get_by_role("button", name="Sign in").click()
            await page.wait_for_url(re.compile(r".*/(chat|ui|dashboard)$"), timeout=30000)
            if not await page.locator("#chat-input").count():
                await page.get_by_role("link", name="Chat").click()
            await page.locator("#chat-input").wait_for(state="visible", timeout=30000)
            await page.get_by_role("link", name="Sessions").click()
            await page.locator("h1").filter(has_text="Sessions").wait_for(timeout=30000)
            await page.get_by_role("button", name="Refresh").click()
            session_card = page.locator("tr").filter(
                has_text=re.compile(
                    rf"({re.escape(session_id)}|{re.escape(session_title)})",
                    re.IGNORECASE,
                )
            ).first
            await session_card.wait_for(state="visible", timeout=30000)
            await session_card.get_by_role("button", name="Open").click()
            await page.get_by_role("link", name="Chat").click()
            await page.locator("#chat-input").wait_for(state="visible", timeout=30000)
            await page.wait_for_function(
                """([phrase1, phrase2]) => {
                const text = (document.body && document.body.innerText || "").toLowerCase();
                return text.includes(phrase1) && text.includes(phrase2);
            }""",
                arg=["quantum computing", "summarise what you found"],
                timeout=30000,
            )
            log_text = await page.locator("body").inner_text()
            assert "quantum computing" in log_text.lower()
            assert "summarise what you found" in log_text.lower()
            await browser.close()
    finally:
        async with httpx.AsyncClient(base_url=api_base_url(cfg), headers=api_headers(cfg), timeout=timeout) as client:
            await _delete_session(client, session_id)
            await _delete_profile(client, cleanup["admin_headers"], scenario["profile_id"])
        await _cleanup_orchestration_api(cfg, cleanup["provisioned"]["created"])
        try:
            await _mcp_call(
                file_mcp["endpoint_url"],
                "delete_file",
                {"path": scenario["output_file"], "missing_ok": True},
                file_mcp["headers"],
            )
        except Exception:
            pass
