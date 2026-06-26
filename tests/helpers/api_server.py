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
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from cloud_dog_chat_client.config import ConfigManager


class APIServerError(RuntimeError):
    pass


_RUNTIME_MODES = {"local-server", "local-docker", "remote-runtime"}
_REMOTE_SESSION_PRECHECK_CACHE: dict[str, bool] = {}
_ISOLATED_ENV_PREFIXES = (
    "CLOUD_DOG__",
    "CLOUD_DOG_DB__",
    "CHAT_CLIENT_",
    "TEST_ENV_TIER",
    "TEST_RUNTIME_MODE",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _ensure_runtime_mount_writable(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o777)
    except OSError:
        pass
    for root, dirs, files in os.walk(path):
        root_path = Path(root)
        try:
            root_path.chmod(0o777)
        except OSError:
            pass
        for name in dirs:
            try:
                (root_path / name).chmod(0o777)
            except OSError:
                pass
        for name in files:
            try:
                (root_path / name).chmod(0o666)
            except OSError:
                pass


def _server_control_path() -> str:
    root = _repo_root()
    return str(root / "server_control.sh")


def _is_loopback_base_url(base_url: str) -> bool:
    host = (urlsplit(str(base_url or "")).hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost"}


def _local_docker_manage_runtime(cfg: ConfigManager) -> bool:
    explicit = cfg.get("chat_tests.local_docker.manage_runtime")
    if explicit is None:
        explicit = True

    if not _truthy(explicit):
        return False

    return _is_loopback_base_url(_base_url(cfg))


def _container_env_path_for_runtime(env_file: str) -> str:
    env_abs = Path(_normalise_path(env_file))
    root = _repo_root()
    try:
        rel = env_abs.relative_to(root)
    except ValueError as e:
        raise APIServerError(
            f"CRITICAL ERROR: local-docker env file must be inside repo root: {env_abs}"
        ) from e
    return f"/workspace/{rel.as_posix()}"


def _local_docker_runtime_name(env_file: str) -> str:
    env_abs = _normalise_path(env_file)
    digest = hashlib.sha256(f"{env_abs}|{os.getpid()}".encode("utf-8")).hexdigest()[:12]
    return f"chat-client-test-runtime-{digest}"


def _effective_env_target_dir(root: Path) -> Path:
    candidates = [
        root / ".pids" / "effective-env",
        root / "working" / "effective-env",
        Path(tempfile.gettempdir()) / "chat-client-effective-env",
    ]
    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("ok\n", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError as e:
            last_error = e
            continue
    raise APIServerError(
        "CRITICAL ERROR: unable to create writable effective env directory"
    ) from last_error


def _effective_env_file_path(
    env_file: str,
    target: str,
    service_env_overrides: Optional[Dict[str, Path]] = None,
) -> str:
    root = _repo_root()
    target_dir = _effective_env_target_dir(root)

    source = Path(_normalise_path(env_file))
    digest = hashlib.sha256(f"{source}|{target}".encode("utf-8")).hexdigest()[:12]
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", source.stem).strip("-") or "env"
    target_path = target_dir / f"{safe_name}-{target}-{digest}.env"

    effective_env: Dict[str, str] = {}
    if source.is_file():
        for raw in source.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            # If the source file value is a vault expression and os.environ
            # has a resolved value, use the resolved value.  The conftest's
            # env_file fixture resolves vault expressions before setting
            # os.environ, so the env var is the authoritative resolved form.
            if value.startswith("${vault.") and key in os.environ:
                env_val = os.environ[key].strip()
                if env_val and not env_val.startswith("${vault."):
                    value = env_val
            effective_env[key] = value

    for key, value in sorted(os.environ.items()):
        if key.startswith(_ISOLATED_ENV_PREFIXES) or key in _ISOLATED_ENV_PREFIXES:
            # Keep the target env file authoritative. Parent-process Cloud-Dog
            # vars exist to fill gaps, not to bleed one test module's runtime
            # settings into another module's effective env.
            effective_env.setdefault(key, value)

    client_host = str(effective_env.get("CLOUD_DOG__CLIENT_API__HOST") or "").strip()
    client_port = str(effective_env.get("CLOUD_DOG__CLIENT_API__PORT") or "").strip()
    if client_host and not str(effective_env.get("CLOUD_DOG__API_SERVER__HOST") or "").strip():
        effective_env["CLOUD_DOG__API_SERVER__HOST"] = client_host
    if client_port and not str(effective_env.get("CLOUD_DOG__API_SERVER__PORT") or "").strip():
        effective_env["CLOUD_DOG__API_SERVER__PORT"] = client_port

    lines = [f"{key}={value}" for key, value in sorted(effective_env.items())]
    target_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Generate per-service effective env files for downstream docker-mode containers.
    # Each service override file is written alongside the main effective env so that
    # _managed_local_docker_start can pass it via --env-file to the downstream container.
    if service_env_overrides:
        for svc_name, svc_path in service_env_overrides.items():
            if svc_path is None or not svc_path.is_file():
                continue
            svc_env: Dict[str, str] = {}
            for raw in svc_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key:
                    continue
                svc_env[key] = value.strip()
            if svc_env:
                svc_digest = hashlib.sha256(
                    f"{source}|{target}|{svc_name}".encode("utf-8")
                ).hexdigest()[:12]
                svc_target_path = target_dir / f"{safe_name}-{target}-{svc_name}-{svc_digest}.env"
                svc_lines = [f"{k}={v}" for k, v in sorted(svc_env.items())]
                svc_target_path.write_text("\n".join(svc_lines) + "\n", encoding="utf-8")

    return str(target_path)


def downstream_service_env_paths(
    env_file: str,
    target: str,
    service_env_overrides: Optional[Dict[str, Path]] = None,
) -> Dict[str, str]:
    """Return resolved effective env file paths for downstream service overrides.

    Intended for callers that need to pass ``--env-file`` to downstream
    docker containers.  Returns ``{service_name: effective_env_path}``.

    For preprod mode: downstream services use their own container env;
    overrides require a container restart or runtime API (future enhancement).
    This function documents the mapping but the caller must handle the
    injection mechanism appropriate to the runtime mode.
    """
    if not service_env_overrides:
        return {}
    root = _repo_root()
    target_dir = _effective_env_target_dir(root)
    source = Path(_normalise_path(env_file))
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", source.stem).strip("-") or "env"
    result: Dict[str, str] = {}
    for svc_name, svc_path in service_env_overrides.items():
        if svc_path is None or not svc_path.is_file():
            continue
        svc_digest = hashlib.sha256(
            f"{source}|{target}|{svc_name}".encode("utf-8")
        ).hexdigest()[:12]
        svc_target_path = target_dir / f"{safe_name}-{target}-{svc_name}-{svc_digest}.env"
        if svc_target_path.is_file():
            result[svc_name] = str(svc_target_path)
    return result


def _effective_env_file_candidates(env_file: str, target: str) -> set[str]:
    root = _repo_root()
    source = Path(_normalise_path(env_file))
    digest = hashlib.sha256(f"{source}|{target}".encode("utf-8")).hexdigest()[:12]
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", source.stem).strip("-") or "env"
    candidates = [
        root / ".pids" / "effective-env",
        root / "working" / "effective-env",
        Path(tempfile.gettempdir()) / "chat-client-effective-env",
    ]
    return {
        _normalise_path(str(candidate / f"{safe_name}-{target}-{digest}.env"))
        for candidate in candidates
    }


def _managed_local_docker_start(
    cfg: ConfigManager,
    *,
    env_file: str,
    service_env_overrides: Optional[Dict[str, Path]] = None,
) -> None:
    timeout_seconds = _require_number(cfg, "client_api.start_timeout_seconds")
    base_url = _base_url(cfg)
    parsed = urlsplit(base_url)
    port = parsed.port
    if not port:
        try:
            port = int(_require_cfg(cfg, "client_api.port"))
        except Exception as e:
            raise APIServerError(
                f"CRITICAL ERROR: local-docker runtime base_url must include explicit port: {base_url}"
            ) from e

    image = str(cfg.get("chat_tests.local_docker.image") or "cloud-dog-chat-client:latest").strip()
    if not image:
        raise APIServerError("CRITICAL ERROR: chat_tests.local_docker.image resolved empty")
    runtime_name = _local_docker_runtime_name(env_file)

    repo_root = str(_repo_root())
    scratch_root = Path(tempfile.gettempdir()) / runtime_name
    scratch_paths = {
        "logs": scratch_root / "logs",
        "working": scratch_root / "working",
        ".pids": scratch_root / ".pids",
    }
    for path in scratch_paths.values():
        _ensure_runtime_mount_writable(path)
    container_env_file = _container_env_path_for_runtime(env_file)
    runtime_mode = str(cfg.get("chat_tests.local_docker.mode") or "api").strip().lower()
    if runtime_mode not in {"api", "all", "web", "mcp", "a2a"}:
        raise APIServerError(
            f"CRITICAL ERROR: unsupported chat_tests.local_docker.mode '{runtime_mode}'"
        )

    # Recreate deterministic runtime so active env overrides are applied per test module.
    subprocess.run(
        ["docker", "rm", "-f", runtime_name],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
    )

    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        runtime_name,
        "--network=host",
        "-v",
        f"{repo_root}:/workspace",
        "-v",
        f"{scratch_paths['logs']}:/app/logs",
        "-v",
        f"{scratch_paths['working']}:/app/working",
        "-v",
        f"{scratch_paths['.pids']}:/app/.pids",
        "-e",
        f"CHAT_CLIENT_MODE={runtime_mode}",
        "-e",
        "CHAT_CLIENT_API_HOST=0.0.0.0",
        "-e",
        f"CHAT_CLIENT_API_PORT={port}",
        "-e",
        f"CHAT_CLIENT_ENV_FILE={container_env_file}",
    ]
    if runtime_mode == "all":
        cmd.extend(
            [
                "-e",
                f"CHAT_CLIENT_WEB_PORT={int(cfg.get('web_server.port') or 0)}",
                "-e",
                f"CHAT_CLIENT_MCP_PORT={int(cfg.get('mcp_server.port') or 0)}",
                "-e",
                f"CHAT_CLIENT_A2A_PORT={int(cfg.get('a2a_server.port') or 0)}",
            ]
        )

    protected = {
        "CHAT_CLIENT_MODE",
        "CHAT_CLIENT_API_HOST",
        "CHAT_CLIENT_API_PORT",
        "CHAT_CLIENT_WEB_PORT",
        "CHAT_CLIENT_MCP_PORT",
        "CHAT_CLIENT_A2A_PORT",
        "CHAT_CLIENT_ENV_FILE",
    }
    vault_passthrough = {
        "VAULT_ADDR",
        "VAULT_TOKEN",
        "VAULT_MOUNT_POINT",
        "VAULT_CONFIG_PATH",
    }
    for key, value in os.environ.items():
        if not (
            key.startswith("CLOUD_DOG__")
            or key.startswith("CLOUD_DOG_DB__")
            or key.startswith("CHAT_CLIENT_")
            or key in vault_passthrough
        ):
            continue
        if key in protected:
            continue
        cmd.extend(["-e", f"{key}={value}"])

    # Inject downstream service env overrides into docker container environment.
    # In docker mode, these override the downstream service's LLM configuration
    # so that AT matrix tests can pin downstream LLM independently of chat-client.
    # In preprod mode, downstream services run in their own containers and use
    # their own env; overrides would require a container restart or runtime API
    # call (future enhancement — documented here for completeness).
    if service_env_overrides:
        svc_paths = downstream_service_env_paths(
            env_file, runtime_mode, service_env_overrides
        )
        for svc_name, svc_env_path in svc_paths.items():
            svc_env_p = Path(svc_env_path)
            if svc_env_p.is_file():
                for raw in svc_env_p.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    svc_key, svc_value = line.split("=", 1)
                    svc_key = svc_key.strip()
                    if svc_key and svc_key not in protected:
                        cmd.extend(["-e", f"{svc_key}={svc_value.strip()}"])

    cmd.append(image)

    try:
        p = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            text=True,
        )
    except subprocess.TimeoutExpired as e:
        raise APIServerError(f"local-docker runtime start timed out: {e}") from e

    if p.returncode != 0:
        raise APIServerError(
            "CRITICAL ERROR: failed to start managed local-docker runtime: "
            f"{(p.stdout or '').strip()}"
        )


def _managed_local_docker_stop(cfg: ConfigManager, *, env_file: str) -> None:
    timeout_seconds = _require_number(cfg, "client_api.stop_timeout_seconds")
    try:
        subprocess.run(
            ["docker", "rm", "-f", _local_docker_runtime_name(env_file)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as e:
        raise APIServerError(f"local-docker runtime stop timed out: {e}") from e


    root = Path(__file__).resolve().parents[2]
    return str(root / "server_control.sh")


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise APIServerError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _base_url(cfg: ConfigManager) -> str:
    test_override = cfg.get("chat_tests.test_api_base_url")
    if test_override:
        return str(test_override).rstrip("/")

    explicit = cfg.get("client_api.base_url")
    if explicit:
        return str(explicit).rstrip("/")
    host = str(_require_cfg(cfg, "client_api.host"))
    port = int(_require_cfg(cfg, "client_api.port"))
    return f"http://{host}:{port}"


def _listener_pids_for_port(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-tiTCP:%s" % int(port), "-sTCP:LISTEN"],
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


def _evict_stale_api_listener(cfg: ConfigManager) -> None:
    base_url = _base_url(cfg)
    parsed = urlsplit(base_url)
    host = (parsed.hostname or "").strip().lower()
    port = parsed.port or int(_require_cfg(cfg, "client_api.port"))
    if host not in {"127.0.0.1", "localhost"}:
        return

    try:
        health = httpx.get(f"{base_url}/health", timeout=2.0)
    except Exception:
        return
    if health.status_code != 200:
        return

    try:
        probe = httpx.post(
            f"{base_url}/sessions",
            headers=_auth_headers(cfg),
            json={"metadata": {"suite": "preflight"}},
            timeout=5.0,
        )
    except Exception:
        return

    if probe.status_code not in {404, 405}:
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


def _stop_local_listener_for_base_url(cfg: ConfigManager) -> None:
    base_url = _base_url(cfg)
    parsed = urlsplit(base_url)
    host = (parsed.hostname or "").strip().lower()
    port = parsed.port or int(_require_cfg(cfg, "client_api.port"))
    if host not in {"127.0.0.1", "localhost"}:
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


def _server_base_url(cfg: ConfigManager, section: str, default_port: int = 0) -> str:
    host = str(cfg.get(f"{section}.host") or "127.0.0.1").strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    port = int(cfg.get(f"{section}.port") or default_port)
    return f"http://{host}:{port}"


def _require_number(cfg: ConfigManager, key: str) -> float:
    value = _require_cfg(cfg, key)
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise APIServerError(f"CRITICAL ERROR: configuration key '{key}' must be a number") from e


def _normalise_path(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    p = Path(raw)
    if not p.is_absolute():
        p = (Path(__file__).resolve().parents[2] / p).resolve()
    else:
        p = p.resolve()
    return str(p)


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
        raise APIServerError(
            f"CRITICAL ERROR: unsupported TEST_RUNTIME_MODE '{mode}'; expected one of {sorted(_RUNTIME_MODES)}"
        )
    return mode


def _use_external_runtime(cfg: ConfigManager, mode: str) -> bool:
    # `local-server` test mode is expected to be managed by server_control.sh.
    # Some legacy env overlays still set use_external_runtime=true while keeping
    # local-server mode, which causes false blocking on unreachable external URLs.
    if mode == "local-server":
        return False
    # `local-docker` mode is expected to be managed by the deterministic
    # local-docker runtime harness. Some env overlays still set
    # use_external_runtime=false, which incorrectly forces the local-server
    # path and times out on API startup.
    if mode == "local-docker":
        return True
    explicit = cfg.get("chat_tests.use_external_runtime")
    if explicit is not None:
        return _truthy(explicit)
    return mode in {"local-docker", "remote-runtime"}


def _read_tfvars_value(tfvars_path: str, key_name: str) -> Optional[str]:
    path = Path(str(tfvars_path or "").strip())
    if not path.is_file():
        return None

    pattern = re.compile(rf"^\s*{re.escape(str(key_name).strip())}\s*=\s*(.+?)\s*$")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = pattern.match(line)
        if not m:
            continue
        value = m.group(1).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
            value = value[1:-1]
        return value
    return None


def _resolve_remote_runtime_api_key(cfg: ConfigManager, current_key: str) -> str:
    source = str(cfg.get("chat_tests.remote_runtime_auth.source") or "").strip().lower()
    tfvars_path = str(cfg.get("chat_tests.remote_runtime_auth.tfvars_path") or "").strip()
    tfvars_key = str(cfg.get("chat_tests.remote_runtime_auth.tfvars_key") or "").strip()

    # In remote-runtime mode, allow explicit tfvars source OR sentinel key placeholder.
    is_placeholder = current_key.startswith("__") and current_key.endswith("__")
    if source != "tfvars" and not is_placeholder:
        return current_key

    if not tfvars_path or not tfvars_key:
        raise APIServerError(
            "BLOCKED: remote-runtime auth contract requires chat_tests.remote_runtime_auth.tfvars_path "
            "and chat_tests.remote_runtime_auth.tfvars_key"
        )

    resolved = _read_tfvars_value(tfvars_path, tfvars_key)
    if not resolved:
        raise APIServerError(
            f"BLOCKED: unable to resolve remote-runtime API key from tfvars source {tfvars_path} key {tfvars_key}"
        )
    return resolved


def _resolve_auth_header_and_key(cfg: ConfigManager) -> Tuple[str, str]:
    header_name = str(cfg.get("client_api.api_key_header") or "X-API-Key")
    api_key = str(_require_cfg(cfg, "client_api.api_key") or "").strip()

    if _runtime_mode(cfg) == "remote-runtime":
        api_key = _resolve_remote_runtime_api_key(cfg, api_key)

    if not api_key:
        raise APIServerError("CRITICAL ERROR: resolved client_api.api_key is empty")
    return header_name, api_key


def _auth_headers(cfg: ConfigManager) -> Dict[str, str]:
    header_name, api_key = _resolve_auth_header_and_key(cfg)
    return {header_name: api_key}


def _external_health_check(cfg: ConfigManager, *, mode: str) -> None:
    base_url = _base_url(cfg)
    request_timeout = _require_number(cfg, "client_api.request_timeout_seconds")
    try:
        resp = httpx.get(f"{base_url}/health", timeout=request_timeout)
    except Exception as e:
        raise APIServerError(
            f"BLOCKED: TEST_RUNTIME_MODE={mode} requires external runtime, but {base_url}/health is unreachable: {e}"
        ) from e
    if resp.status_code != 200:
        raise APIServerError(
            f"BLOCKED: TEST_RUNTIME_MODE={mode} requires external runtime, but {base_url}/health returned {resp.status_code}"
        )


def _local_api_session_preflight_ok(cfg: ConfigManager) -> bool:
    base_url = _base_url(cfg)
    if not _is_loopback_base_url(base_url):
        return False

    try:
        resp = httpx.post(
            f"{base_url}/sessions",
            headers=_auth_headers(cfg),
            json={"metadata": {"suite": "env-match-preflight"}},
            timeout=5.0,
        )
    except Exception:
        return False

    return resp.status_code == 200


def precheck_remote_runtime_session_create(cfg: ConfigManager) -> None:
    mode = _runtime_mode(cfg)
    if mode != "remote-runtime":
        return

    base_url = _base_url(cfg)
    header_name, api_key = _resolve_auth_header_and_key(cfg)
    key_fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]
    cache_key = f"{base_url}|{header_name}|{key_fingerprint}"
    if _REMOTE_SESSION_PRECHECK_CACHE.get(cache_key):
        return

    timeout_seconds = _require_number(cfg, "client_api.request_timeout_seconds")
    headers = {header_name: api_key, "Content-Type": "application/json"}
    payload = {"metadata": {"suite": "remote-runtime-auth-precheck"}}

    try:
        resp = httpx.post(f"{base_url}/sessions", headers=headers, json=payload, timeout=timeout_seconds)
    except Exception as e:
        raise APIServerError(
            f"BLOCKED: remote-runtime session precheck failed (network/request error) at {base_url}/sessions: {e}"
        ) from e

    if resp.status_code != 200:
        detail = ""
        try:
            body = resp.json()
            detail = str(body.get("detail") or "") if isinstance(body, dict) else ""
        except Exception:
            detail = (resp.text or "").strip()[:240]
        raise APIServerError(
            "BLOCKED: remote-runtime auth contract rejected session create: "
            f"status={resp.status_code}, header={header_name}, detail={detail or 'n/a'}"
        )

    _REMOTE_SESSION_PRECHECK_CACHE[cache_key] = True


def _running_runtime_env_file(cfg: ConfigManager) -> Optional[str]:
    base_url = _base_url(cfg)
    request_timeout = _require_number(cfg, "client_api.request_timeout_seconds")
    try:
        resp = httpx.get(f"{base_url}/health", timeout=request_timeout)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    env_file = str(payload.get("env_file") or "").strip()
    if not env_file:
        runtime = payload.get("runtime")
        if isinstance(runtime, dict):
            env_file = str(runtime.get("env_file") or "").strip()
    return env_file or None


def _wait_for_runtime_env_match(
    cfg: ConfigManager,
    *,
    allowed_envs: set[str],
    timeout_seconds: float,
    poll_seconds: float = 0.5,
) -> bool:
    deadline = time.time() + max(timeout_seconds, poll_seconds)
    while time.time() < deadline:
        running_env = _normalise_path(str(_running_runtime_env_file(cfg) or ""))
        if running_env and running_env in allowed_envs:
            return True
        if not running_env and _local_api_session_preflight_ok(cfg):
            return True
        time.sleep(poll_seconds)
    return False


def _reset_local_api_listener_for_env(
    cfg: ConfigManager,
    *,
    source_env: str,
    effective_env: str,
    timeout_seconds: float,
) -> None:
    desired_envs = {
        _normalise_path(source_env),
        _normalise_path(effective_env),
    }
    running_env = _normalise_path(str(_running_runtime_env_file(cfg) or ""))
    if running_env and running_env not in desired_envs:
        stop_cmd = ["bash", _server_control_path(), "--env", running_env, "stop", "api"]
        try:
            subprocess.run(
                stop_cmd,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                text=True,
            )
        except subprocess.TimeoutExpired as e:
            raise APIServerError(
                f"server_control.sh stop timed out while clearing stale API: {e}"
            ) from e
        _stop_local_listener_for_base_url(cfg)
        return

    if running_env:
        return

    base_url = _base_url(cfg)
    try:
        health = httpx.get(f"{base_url}/health", timeout=2.0)
    except Exception:
        health = None
    if health is not None and health.status_code == 200:
        _stop_local_listener_for_base_url(cfg)


def _start_target(cfg: ConfigManager, *, env_file: str, target: str) -> None:
    mode = _runtime_mode(cfg)
    if _use_external_runtime(cfg, mode):
        if mode == "local-docker" and _local_docker_manage_runtime(cfg):
            _managed_local_docker_start(cfg, env_file=env_file)
            return
        _external_health_check(cfg, mode=mode)
        if mode == "remote-runtime":
            precheck_remote_runtime_session_create(cfg)
        return

    timeout_seconds = _require_number(cfg, "client_api.start_timeout_seconds")
    if target in {"all", "api"}:
        timeout_seconds = max(timeout_seconds, 45.0)

    if target == "api":
        _evict_stale_api_listener(cfg)

    effective_env = _effective_env_file_path(env_file, target)
    desired_envs = {
        _normalise_path(env_file),
        _normalise_path(effective_env),
    }
    if target == "api":
        # Reuse a healthy loopback API instead of force-restarting it. This avoids
        # concurrent local test workers bouncing the shared API listener mid-run.
        running_env = _normalise_path(str(_running_runtime_env_file(cfg) or ""))
        if _is_loopback_base_url(_base_url(cfg)):
            try:
                health = httpx.get(f"{_base_url(cfg)}/health", timeout=2.0)
            except Exception:
                health = None
            if health is not None and health.status_code == 200:
                if running_env in desired_envs:
                    return
                if not running_env and _local_api_session_preflight_ok(cfg):
                    return

        _reset_local_api_listener_for_env(
            cfg,
            source_env=env_file,
            effective_env=effective_env,
            timeout_seconds=timeout_seconds,
        )

    if target == "api":
        stop_cmd = ["bash", _server_control_path(), "--env", effective_env, "stop", "api"]
        try:
            subprocess.run(
                stop_cmd,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                text=True,
            )
        except subprocess.TimeoutExpired as e:
            raise APIServerError(
                f"server_control.sh stop timed out while resetting API target: {e}"
            ) from e
        base_url = _base_url(cfg)
        try:
            health = httpx.get(f"{base_url}/health", timeout=2.0)
        except Exception:
            health = None
        if health is not None and health.status_code == 200:
            _stop_local_listener_for_base_url(cfg)
    cmd = ["bash", _server_control_path(), "--env", effective_env, "start", target]
    command_timeout = float(timeout_seconds)
    if target == "api":
        # server_control.sh already waits up to the configured API health timeout
        # before returning. Give the wrapper a small buffer so we do not kill a
        # slow-but-successful startup exactly at the health deadline.
        command_timeout += 10.0
    try:
        result = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=command_timeout,
            text=True,
        )
    except subprocess.TimeoutExpired as e:
        if target == "api":
            try:
                health = httpx.get(f"{_base_url(cfg)}/health", timeout=2.0)
                running_env = _running_runtime_env_file(cfg)
            except Exception:
                health = None
                running_env = None
            if health is not None and health.status_code == 200:
                running_norm = _normalise_path(str(running_env or ""))
                effective_norm = _normalise_path(effective_env)
                source_norm = _normalise_path(env_file)
                if running_norm in {effective_norm, source_norm}:
                    return
        raise APIServerError(f"server_control.sh start timed out: {e}") from e
    if result.returncode != 0:
        raise APIServerError(
            "CRITICAL ERROR: server_control.sh start failed: "
            f"{(result.stdout or '').strip()}"
        )
    if target == "api" and not _wait_for_runtime_env_match(
        cfg,
        allowed_envs=desired_envs,
        timeout_seconds=min(timeout_seconds, 15.0),
        poll_seconds=0.5,
    ):
        actual_env = _normalise_path(str(_running_runtime_env_file(cfg) or ""))
        _stop_local_listener_for_base_url(cfg)
        retry = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=command_timeout,
            text=True,
        )
        if retry.returncode != 0:
            raise APIServerError(
                "CRITICAL ERROR: server_control.sh retry start failed: "
                f"{(retry.stdout or '').strip()}"
            )
        if not _wait_for_runtime_env_match(
            cfg,
            allowed_envs=desired_envs,
            timeout_seconds=min(timeout_seconds, 15.0),
            poll_seconds=0.5,
        ):
            actual_env = _normalise_path(str(_running_runtime_env_file(cfg) or actual_env))
            raise APIServerError(
                "CRITICAL ERROR: API runtime started but did not bind the requested env profile: "
                f"actual={actual_env or 'unavailable'} expected={sorted(item for item in desired_envs if item)}"
            )


def _stop_target(cfg: ConfigManager, *, env_file: str, target: str) -> None:
    mode = _runtime_mode(cfg)
    if _use_external_runtime(cfg, mode):
        if mode == "local-docker" and _local_docker_manage_runtime(cfg):
            _managed_local_docker_stop(cfg, env_file=env_file)
        return

    timeout_seconds = _require_number(cfg, "client_api.stop_timeout_seconds")
    if target == "all":
        timeout_seconds = max(timeout_seconds, 45.0)
    effective_env = _effective_env_file_path(env_file, target)
    cmd = ["bash", _server_control_path(), "--env", effective_env, "stop", target]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            text=True,
        )
    except subprocess.TimeoutExpired as e:
        raise APIServerError(f"server_control.sh stop timed out: {e}") from e
    if result.returncode != 0:
        raise APIServerError(
            "CRITICAL ERROR: server_control.sh stop failed: "
            f"{(result.stdout or '').strip()}"
        )


def wait_for_base_url(cfg: ConfigManager, base_url: str) -> None:
    timeout_seconds = _require_number(cfg, "client_api.ready_timeout_seconds")
    poll_seconds = _require_number(cfg, "client_api.ready_poll_seconds")
    request_timeout = _require_number(cfg, "client_api.request_timeout_seconds")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url.rstrip('/')}/health", timeout=request_timeout)
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(poll_seconds)
    raise APIServerError(f"CRITICAL ERROR: server not ready at {base_url.rstrip('/')}/health")


def start_api(cfg: ConfigManager, *, env_file: str) -> None:
    _start_target(cfg, env_file=env_file, target="api")


def stop_api(cfg: ConfigManager, *, env_file: str) -> None:
    _stop_target(cfg, env_file=env_file, target="api")


def wait_for_api(cfg: ConfigManager) -> None:
    mode = _runtime_mode(cfg)
    use_external = _use_external_runtime(cfg, mode)
    base_url = _base_url(cfg)
    timeout_seconds = _require_number(cfg, "client_api.ready_timeout_seconds")
    poll_seconds = _require_number(cfg, "client_api.ready_poll_seconds")
    request_timeout = _require_number(cfg, "client_api.request_timeout_seconds")
    expected_envs: set[str] = set()
    if not use_external:
        source_env = _normalise_path(str(cfg.env_file or cfg.get("app.env_file") or ""))
        if source_env:
            expected_envs.add(source_env)
            expected_envs.update(_effective_env_file_candidates(source_env, "api"))
            expected_envs.update(_effective_env_file_candidates(source_env, "all"))
    deadline = time.time() + timeout_seconds
    last_runtime_env = ""
    reclaim_attempted = False
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url}/health", timeout=request_timeout)
            if resp.status_code == 200:
                if expected_envs:
                    payload = resp.json()
                    runtime_env = ""
                    if isinstance(payload, dict):
                        runtime_env = _normalise_path(str(payload.get("env_file") or ""))
                        if not runtime_env:
                            runtime = payload.get("runtime")
                            if isinstance(runtime, dict):
                                runtime_env = _normalise_path(str(runtime.get("env_file") or ""))
                    last_runtime_env = runtime_env
                    if runtime_env and runtime_env in expected_envs:
                        return
                    if not runtime_env and _local_api_session_preflight_ok(cfg):
                        return
                    if runtime_env not in expected_envs:
                        if (
                            not reclaim_attempted
                            and source_env
                            and _is_loopback_base_url(base_url)
                        ):
                            reclaim_attempted = True
                            _reset_local_api_listener_for_env(
                                cfg,
                                source_env=source_env,
                                effective_env=_effective_env_file_path(source_env, "api"),
                                timeout_seconds=max(timeout_seconds, 15.0),
                            )
                            _start_target(cfg, env_file=source_env, target="api")
                        time.sleep(poll_seconds)
                        continue
                return
        except Exception:
            pass
        time.sleep(poll_seconds)
    raise APIServerError(
        f"CRITICAL ERROR: API server not ready at {base_url}/health "
        f"(actual_env={last_runtime_env or 'unavailable'} expected={sorted(item for item in expected_envs if item)})"
    )


def api_headers(cfg: ConfigManager) -> Dict[str, str]:
    return _auth_headers(cfg)


def api_base_url(cfg: ConfigManager) -> str:
    return _base_url(cfg)


def web_base_url(cfg: ConfigManager) -> str:
    return _server_base_url(cfg, "web_server")


def mcp_base_url(cfg: ConfigManager) -> str:
    return _server_base_url(cfg, "mcp_server")


def a2a_base_url(cfg: ConfigManager) -> str:
    return _server_base_url(cfg, "a2a_server")


def start_all(cfg: ConfigManager, *, env_file: str) -> None:
    _start_target(cfg, env_file=env_file, target="all")


def stop_all(cfg: ConfigManager, *, env_file: str) -> None:
    _stop_target(cfg, env_file=env_file, target="all")
