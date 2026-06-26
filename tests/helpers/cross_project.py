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

"""Cross-project test helpers for AT tests that span multiple MCP services.

Provides reusable utilities for:
- MCP tool calls via chat-client API
- LLM message exchange
- File upload/download via file-mcp or git-mcp
- SMTP send (direct, since imap-mcp is read-only)
- PDF/DOCX generation from markdown text
- Index-retriever ingest and search
"""
from __future__ import annotations

import base64
import email
import imaplib
import json
import re
import ssl
import smtplib
import struct
import subprocess
import time
import zlib
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

from cloud_dog_chat_client.config import ConfigManager


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def require_cfg(cfg: ConfigManager, key: str) -> Any:
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def parse_json_obj(value: Any, key: str) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as e:
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON object") from e
        if not isinstance(parsed, dict):
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON object")
        return parsed
    raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON object")


def parse_json_list(value: Any, key: str) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as e:
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list") from e
        if not isinstance(parsed, list):
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list")
        return [str(item) for item in parsed]
    raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list")


def _resolve_repo_path(path_value: str) -> Path:
    raw = Path(path_value)
    if raw.is_absolute():
        return raw
    repo_root = Path(__file__).resolve().parents[2]
    return (repo_root / raw).resolve()


def _ensure_local_server_runtime(
    cfg: ConfigManager,
    key_prefix: str,
    *,
    label: str,
) -> bool:
    control_script = cfg.get(f"{key_prefix}.control_script")
    env_path = cfg.get(f"{key_prefix}.env_path")
    control_target = str(cfg.get(f"{key_prefix}.control_target") or "").strip().lower()

    if not control_script and not env_path:
        return False
    if not control_script or not env_path:
        raise RuntimeError(
            f"CRITICAL ERROR: incomplete local-server runtime config for {label}; "
            f"expected {key_prefix}.control_script and {key_prefix}.env_path"
        )

    timeout_seconds = float(cfg.get(f"{key_prefix}.control_timeout_seconds") or 120)
    health_url = str(cfg.get(f"{key_prefix}.health_url") or "").strip()
    ready_timeout = float(cfg.get(f"{key_prefix}.ready_timeout_seconds") or 30)
    poll = float(cfg.get(f"{key_prefix}.ready_poll_seconds") or 0.5)

    control_script_path = _resolve_repo_path(str(control_script))
    runtime_env_path = _resolve_repo_path(str(env_path))
    control_dir = str(control_script_path.parent)

    if not control_script_path.is_file():
        raise RuntimeError(
            f"CRITICAL ERROR: {label} control script not found: {control_script_path}"
        )
    if not runtime_env_path.is_file():
        raise RuntimeError(f"CRITICAL ERROR: {label} env file not found: {runtime_env_path}")

    def _run_control(action: str, *, with_all: bool) -> subprocess.CompletedProcess[str]:
        cmd = ["bash", str(control_script_path), "--env", str(runtime_env_path), action]
        if control_target:
            cmd.append(control_target)
        elif with_all:
            cmd.append("all")
        return subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            text=True,
            cwd=control_dir,
        )

    def _unsupported_all(result: subprocess.CompletedProcess[str]) -> bool:
        output = (result.stdout or "").lower()
        return result.returncode != 0 and "unknown server: all" in output

    use_all = not bool(control_target)

    # Ensure stale pidfiles/processes do not block startup.
    stopped = _run_control("stop", with_all=True)
    if use_all and _unsupported_all(stopped):
        use_all = False
        _run_control("stop", with_all=False)

    started = _run_control("start", with_all=use_all)
    if use_all and _unsupported_all(started):
        use_all = False
        started = _run_control("start", with_all=False)
    if started.returncode != 0:
        start_out = (started.stdout or "").strip()
        raise RuntimeError(
            f"CRITICAL ERROR: local-server runtime start failed for {label} "
            f"(rc={started.returncode})\n{start_out}"
        )

    if not health_url:
        return True

    deadline = time.time() + ready_timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(health_url, timeout=poll)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(poll)

    status = _run_control("status", with_all=use_all)
    if use_all and _unsupported_all(status):
        status = _run_control("status", with_all=False)
    start_out = (started.stdout or "").strip()
    status_out = (status.stdout or "").strip()
    raise RuntimeError(
        f"CRITICAL ERROR: local-server runtime not ready for {label} at {health_url}. "
        f"start={start_out} status={status_out}"
    )


def _stop_local_server_runtime(
    cfg: ConfigManager,
    key_prefix: str,
    *,
    label: str,
) -> bool:
    control_script = cfg.get(f"{key_prefix}.control_script")
    env_path = cfg.get(f"{key_prefix}.env_path")
    control_target = str(cfg.get(f"{key_prefix}.control_target") or "").strip().lower()

    if not control_script and not env_path:
        return False
    if not control_script or not env_path:
        raise RuntimeError(
            f"CRITICAL ERROR: incomplete local-server runtime config for {label}; "
            f"expected {key_prefix}.control_script and {key_prefix}.env_path"
        )

    timeout_seconds = float(cfg.get(f"{key_prefix}.control_timeout_seconds") or 120)
    control_script_path = _resolve_repo_path(str(control_script))
    runtime_env_path = _resolve_repo_path(str(env_path))
    control_dir = str(control_script_path.parent)

    if not control_script_path.is_file():
        raise RuntimeError(
            f"CRITICAL ERROR: {label} control script not found: {control_script_path}"
        )
    if not runtime_env_path.is_file():
        raise RuntimeError(f"CRITICAL ERROR: {label} env file not found: {runtime_env_path}")

    cmd = ["bash", str(control_script_path), "--env", str(runtime_env_path), "stop"]
    if control_target:
        cmd.append(control_target)
    else:
        cmd.append("all")

    try:
        completed = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            text=True,
            cwd=control_dir,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"CRITICAL ERROR: local-server runtime stop timed out for {label}: {cmd}"
        ) from exc

    if completed.returncode != 0:
        output = (completed.stdout or "").strip().lower()
        if "unknown server: all" in output and not control_target:
            fallback = subprocess.run(
                ["bash", str(control_script_path), "--env", str(runtime_env_path), "stop"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                text=True,
                cwd=control_dir,
            )
            if fallback.returncode == 0:
                return True
            completed = fallback
        raise RuntimeError(
            f"CRITICAL ERROR: local-server runtime stop failed for {label} "
            f"(rc={completed.returncode})\n{(completed.stdout or '').strip()}"
        )

    return True


def ensure_local_docker_runtime(
    cfg: ConfigManager,
    key_prefix: str,
    *,
    label: str,
) -> None:
    runtime_mode = str(
        cfg.get("chat_tests.runtime_mode")
        or cfg.get("tests.runtime_mode")
        or ""
    ).strip().lower()
    explicit_external_runtime = cfg.get(f"{key_prefix}.use_external_runtime")
    if explicit_external_runtime is None:
        use_external_runtime = str(
            cfg.get("chat_tests.use_external_runtime")
            or cfg.get("tests.use_external_runtime")
            or ""
        ).strip().lower() in {"1", "true", "yes", "on"}
    else:
        use_external_runtime = str(explicit_external_runtime).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    # local-server mode should manage local helper runtimes even when legacy
    # env overlays still set use_external_runtime=true.
    if runtime_mode == "local-server" and explicit_external_runtime is None:
        use_external_runtime = False

    # External/remote runtime modes must attach to already-running services.
    # They must not attempt local Docker orchestration from test helpers.
    if runtime_mode == "remote-runtime" or use_external_runtime:
        return

    script = cfg.get(f"{key_prefix}.docker_control_script")
    env_path = cfg.get(f"{key_prefix}.docker_env_path")

    if not script and not env_path:
        return
    if not script or not env_path:
        raise RuntimeError(
            f"CRITICAL ERROR: incomplete local-docker runtime config for {label}; "
            f"expected {key_prefix}.docker_control_script and {key_prefix}.docker_env_path"
        )

    timeout_seconds = float(cfg.get(f"{key_prefix}.docker_control_timeout_seconds") or 120)
    script_path = _resolve_repo_path(str(script))
    runtime_env_path = _resolve_repo_path(str(env_path))

    if not script_path.is_file():
        raise RuntimeError(f"CRITICAL ERROR: {label} docker control script not found: {script_path}")
    if not runtime_env_path.is_file():
        raise RuntimeError(f"CRITICAL ERROR: {label} docker env file not found: {runtime_env_path}")

    cmd = ["bash", str(script_path), "--env", str(runtime_env_path), "ensure"]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"CRITICAL ERROR: local-docker runtime ensure timed out for {label}: {cmd}"
        ) from exc

    if completed.returncode != 0:
        output = (completed.stdout or "").strip()
        if _ensure_local_server_runtime(cfg, key_prefix, label=label):
            return
        raise RuntimeError(
            f"CRITICAL ERROR: local-docker runtime ensure failed for {label} "
            f"(rc={completed.returncode}): {cmd}\n{output}"
        )


def _extract_json_array_candidate(text: str) -> Optional[str]:
    source = (text or "").strip()
    # Strip common reasoning wrappers before JSON extraction.
    source = re.sub(r"</?(thinking|reasoning)>", " ", source, flags=re.IGNORECASE)
    if not source:
        return None

    # 1) Direct payload.
    if source.startswith('[') and source.endswith(']'):
        return source

    # 2) Fenced blocks (```json ... ``` or ``` ... ```).
    if "```" in source:
        parts = source.split("```")
        for idx in range(1, len(parts), 2):
            block = parts[idx].strip()
            if block.lower().startswith("json"):
                block = block[4:].strip()
            if block.startswith('[') and block.endswith(']'):
                return block

    # 3) Balanced first JSON array in free text.
    start = source.find('[')
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(source)):
            ch = source[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue

            if ch == '"':
                in_str = True
            elif ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    return source[start : i + 1]
        start = source.find('[', start + 1)

    return None


def parse_json_array_from_text(text: str) -> Optional[List[Any]]:
    candidate = _extract_json_array_candidate(text)
    if not candidate:
        return None
    try:
        parsed = json.loads(candidate)
    except Exception:
        return None
    if not isinstance(parsed, list):
        return None
    return parsed


def _infer_company_array_from_text(text: str, *, min_items: int) -> Optional[List[Dict[str, str]]]:
    source = (text or "").strip()
    if not source:
        return None

    source = re.sub(r"</?(thinking|reasoning)>", " ", source, flags=re.IGNORECASE)
    source = source.replace("\r", "")

    rows: List[Dict[str, str]] = []
    seen: set[str] = set()

    # Pattern 1: numbered/bulleted lines: "1. BAE Systems - Description"
    for line in source.split("\n"):
        txt = line.strip().lstrip("-* ")
        if not txt:
            continue
        m = re.match(
            r"^(?:\d+[\.)]\s*)?([A-Z][A-Za-z0-9&'()\-.,/ ]{2,80}?)\s*[-:–]\s*(.{12,400})$",
            txt,
        )
        if not m:
            continue
        name = re.sub(r"\s+", " ", m.group(1)).strip(" ,.-")
        desc = re.sub(r"\s+", " ", m.group(2)).strip()
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append({"name": name, "description": desc})

    # Pattern 2: sentence forms: "BAE Systems is ..."
    if len(rows) < min_items:
        for m in re.finditer(
            r"([A-Z][A-Za-z0-9&'()\-.,/ ]{2,80}?)\s+(?:is|are)\s+(.{12,220}?)(?:\.|\n|$)",
            source,
            flags=re.IGNORECASE,
        ):
            name = re.sub(r"\s+", " ", m.group(1)).strip(" ,.-")
            name = re.sub(
                r"^(first|second|third|next|then)\s*,\s*",
                "",
                name,
                flags=re.IGNORECASE,
            )
            desc = re.sub(r"\s+", " ", m.group(2)).strip()
            if len(name) < 3 or len(desc) < 8:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            rows.append({"name": name, "description": desc})
            if len(rows) >= max(min_items, 10):
                break

    if len(rows) < min_items:
        stop = {
            "okay", "first", "second", "third", "next", "then", "the", "and", "i", "uk", "json", "array"
        }
        for m in re.finditer(
            r"\b([A-Z]{2,}(?:\s+[A-Z][A-Za-z]+){0,2}|[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b",
            source,
        ):
            name = re.sub(r"\s+", " ", m.group(1)).strip(" ,.-")
            lower = name.lower()
            if lower in stop:
                continue
            if any(tok in stop for tok in lower.split()):
                continue
            if len(name) < 3:
                continue
            key = lower
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "name": name,
                "description": "UK defence company referenced by model output.",
            })
            if len(rows) >= max(min_items, 10):
                break

    if len(rows) >= min_items:
        return rows
    return None


# ---------------------------------------------------------------------------
# MCP tool call helpers
# ---------------------------------------------------------------------------

def extract_tool_text(result: Dict[str, Any]) -> str:
    text = ""
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text += str(item.get("text") or "")
    return text


def extract_tool_json(result: Dict[str, Any]) -> Dict[str, Any]:
    text = extract_tool_text(result).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}

    # Normalise common service envelope shape: {ok, result, warnings, errors, meta}
    # so tests can access payload fields directly (messages/results/tools/etc).
    if isinstance(parsed.get("result"), dict):
        envelope_keys = ("ok", "errors", "warnings", "meta")
        direct_payload_keys = ("messages", "results", "tools", "attachments", "parts", "commits", "entries")
        if any(key in parsed for key in envelope_keys) or not any(key in parsed for key in direct_payload_keys):
            inner = parsed.get("result")
            return inner if isinstance(inner, dict) else {}

    return parsed


def _is_local_index_retriever_server(server: Dict[str, Any] | None) -> bool:
    if not isinstance(server, dict):
        return False
    name = str(server.get("name") or "").strip().lower()
    base_url = str(server.get("base_url") or "").strip()
    host = (urlsplit(base_url).hostname or "").strip().lower()
    return "index" in name and "retriever" in name and host in {"127.0.0.1", "localhost"}


def _is_local_git_server(server: Dict[str, Any] | None) -> bool:
    if not isinstance(server, dict):
        return False
    name = str(server.get("name") or "").strip().lower()
    base_url = str(server.get("base_url") or "").strip()
    host = (urlsplit(base_url).hostname or "").strip().lower()
    return "git" in name and host in {"127.0.0.1", "localhost"}


def _local_index_api_base_url(server: Dict[str, Any]) -> str:
    parsed = urlsplit(str(server.get("base_url") or "").strip())
    if not parsed.scheme or not parsed.hostname or parsed.port is None:
        raise RuntimeError("CRITICAL ERROR: local index server is missing a valid base_url")
    api_port = parsed.port - 2
    if api_port <= 0:
        raise RuntimeError("CRITICAL ERROR: unable to derive local index API port from MCP base_url")
    return urlunsplit((parsed.scheme, f"{parsed.hostname}:{api_port}", "", "", ""))


def _local_git_api_base_url(server: Dict[str, Any]) -> str:
    explicit = str(server.get("api_base_url") or "").strip()
    if explicit:
        return explicit
    parsed = urlsplit(str(server.get("base_url") or "").strip())
    if not parsed.scheme or not parsed.hostname or parsed.port is None:
        raise RuntimeError("CRITICAL ERROR: local git server is missing a valid base_url")
    api_port = parsed.port - 6
    if api_port <= 0:
        raise RuntimeError("CRITICAL ERROR: unable to derive local git API port from MCP base_url")
    return urlunsplit((parsed.scheme, f"{parsed.hostname}:{api_port}", "", "", ""))


def _restart_local_index_runtime(api_base_url: str) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    control_script = repo_root / "index-retriever-mcp-server" / "server_control.sh"
    env_path = repo_root / "index-retriever-mcp-server" / "tests" / "env-AT-local-server"
    timeout_seconds = 180

    subprocess.run(
        ["bash", str(control_script), "--env", str(env_path), "stop", "api"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
        text=True,
        cwd=str(control_script.parent),
    )
    started = subprocess.run(
        ["bash", str(control_script), "--env", str(env_path), "start", "api"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
        text=True,
        cwd=str(control_script.parent),
    )
    health_url = f"{api_base_url.rstrip('/')}/health"
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            resp = httpx.get(health_url, timeout=5.0)
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1.0)
    if started.returncode != 0:
        raise RuntimeError(
            "CRITICAL ERROR: local index runtime restart failed: "
            f"{(started.stdout or '').strip()}"
        )
    raise RuntimeError(f"CRITICAL ERROR: local index runtime not ready after restart at {health_url}")


def _restart_local_git_runtime(api_base_url: str) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    control_script = repo_root / "git-mcp-server" / "server_control.sh"
    env_path = repo_root / "chat-client" / "tests" / "private" / "deps" / "git-at-assigned.env"
    timeout_seconds = 180

    subprocess.run(
        ["bash", str(control_script), "--env", str(env_path), "stop"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
        text=True,
        cwd=str(control_script.parent),
    )
    started = subprocess.run(
        ["bash", str(control_script), "--env", str(env_path), "start"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
        text=True,
        cwd=str(control_script.parent),
    )
    if started.returncode != 0:
        raise RuntimeError(
            "CRITICAL ERROR: local git runtime restart failed: "
            f"{(started.stdout or '').strip()}"
        )

    health_url = f"{api_base_url.rstrip('/')}/health"
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            resp = httpx.get(health_url, timeout=5.0)
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1.0)
    raise RuntimeError(f"CRITICAL ERROR: local git runtime not ready after restart at {health_url}")


async def _local_index_api_tool_call(
    server: Dict[str, Any],
    name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    api_base_url = _local_index_api_base_url(server).rstrip("/")
    timeout_seconds = float(server.get("timeout_seconds") or 180)
    headers: Dict[str, str] = {}
    api_key = str(server.get("api_key") or "").strip()
    api_key_header = str(server.get("api_key_header") or "X-API-Key").strip() or "X-API-Key"
    if api_key:
        headers[api_key_header] = api_key
    accept_header = str(server.get("accept_header") or "").strip()
    if accept_header:
        headers["Accept"] = accept_header

    async def _post_once() -> httpx.Response:
        async with httpx.AsyncClient(
            base_url=api_base_url,
            headers=headers,
            timeout=timeout_seconds,
        ) as direct_client:
            return await direct_client.post(f"/v1/tools/{name}", json=arguments)

    def _is_transient_shutdown(resp: httpx.Response) -> bool:
        if resp.status_code != 503:
            return False
        body = (resp.text or "").lower()
        return "service is shutting down" in body or '"retryable":true' in body

    try:
        resp = await _post_once()
    except httpx.ConnectError:
        _restart_local_index_runtime(api_base_url)
        resp = await _post_once()
    if _is_transient_shutdown(resp):
        _restart_local_index_runtime(api_base_url)
        resp = await _post_once()
    assert resp.status_code == 200, (
        f"CRITICAL ERROR: local index API tool call '{name}' failed: {resp.status_code} {resp.text}"
    )
    payload = resp.json() or {}
    if isinstance(payload, dict) and payload.get("isError") is True:
        raise RuntimeError(
            f"CRITICAL ERROR: local index API tool '{name}' returned isError=true: {extract_tool_text(payload)}"
        )
    if isinstance(payload, dict) and "content" in payload:
        return payload
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload),
            }
        ]
    }


async def _local_git_api_tool_call(
    server: Dict[str, Any],
    name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    api_base_url = _local_git_api_base_url(server).rstrip("/")
    timeout_seconds = float(server.get("timeout_seconds") or 180)
    if name == "repo_open":
        timeout_seconds = max(timeout_seconds, 300.0)
    headers: Dict[str, str] = {}
    api_key = str(server.get("api_key") or "").strip()
    api_key_header = str(server.get("api_key_header") or "X-API-Key").strip() or "X-API-Key"
    if api_key:
        headers[api_key_header] = api_key
    accept_header = str(server.get("accept_header") or "").strip()
    if accept_header:
        headers["Accept"] = accept_header

    async def _post_once() -> httpx.Response:
        async with httpx.AsyncClient(
            base_url=api_base_url,
            headers=headers,
            timeout=timeout_seconds,
        ) as direct_client:
            return await direct_client.post(f"/v1/tools/{name}", json=arguments)

    try:
        resp = await _post_once()
    except httpx.ConnectError:
        _restart_local_git_runtime(api_base_url)
        resp = await _post_once()
    assert resp.status_code == 200, (
        f"CRITICAL ERROR: local git API tool call '{name}' failed: {resp.status_code} {resp.text}"
    )
    payload = resp.json() or {}
    if isinstance(payload, dict) and payload.get("isError") is True:
        raise RuntimeError(
            f"CRITICAL ERROR: local git API tool '{name}' returned isError=true: {extract_tool_text(payload)}"
        )
    if isinstance(payload, dict) and "content" in payload:
        return payload
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload),
            }
        ]
    }


async def _local_index_api_list_tools(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    api_base_url = _local_index_api_base_url(server).rstrip("/")
    timeout_seconds = float(server.get("timeout_seconds") or 180)
    headers: Dict[str, str] = {}
    api_key = str(server.get("api_key") or "").strip()
    api_key_header = str(server.get("api_key_header") or "X-API-Key").strip() or "X-API-Key"
    if api_key:
        headers[api_key_header] = api_key
    accept_header = str(server.get("accept_header") or "").strip()
    if accept_header:
        headers["Accept"] = accept_header

    async def _get_once() -> httpx.Response:
        async with httpx.AsyncClient(
            base_url=api_base_url,
            headers=headers,
            timeout=timeout_seconds,
        ) as direct_client:
            return await direct_client.get("/v1/tools")

    try:
        resp = await _get_once()
    except httpx.ConnectError:
        _restart_local_index_runtime(api_base_url)
        resp = await _get_once()
    assert resp.status_code == 200, (
        f"CRITICAL ERROR: local index API tools/list failed: {resp.status_code} {resp.text}"
    )
    payload = resp.json() or []
    return [{"ok": True, "result": {"tools": payload}}]


async def _local_git_api_list_tools(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    api_base_url = _local_git_api_base_url(server).rstrip("/")
    timeout_seconds = float(server.get("timeout_seconds") or 180)
    headers: Dict[str, str] = {}
    api_key = str(server.get("api_key") or "").strip()
    api_key_header = str(server.get("api_key_header") or "X-API-Key").strip() or "X-API-Key"
    if api_key:
        headers[api_key_header] = api_key
    accept_header = str(server.get("accept_header") or "").strip()
    if accept_header:
        headers["Accept"] = accept_header

    async def _get_once() -> httpx.Response:
        async with httpx.AsyncClient(
            base_url=api_base_url,
            headers=headers,
            timeout=timeout_seconds,
        ) as direct_client:
            return await direct_client.get("/v1/tools")

    try:
        resp = await _get_once()
    except httpx.ConnectError:
        _restart_local_git_runtime(api_base_url)
        resp = await _get_once()
    assert resp.status_code == 200, (
        f"CRITICAL ERROR: local git API tools/list failed: {resp.status_code} {resp.text}"
    )
    payload = resp.json() or []
    return [{"ok": True, "result": {"tools": payload}}]


async def mcp_tools_call(
    client: httpx.AsyncClient,
    session_id: str,
    server_index: Optional[int],
    name: str,
    arguments: Dict[str, Any],
    require_initialize: bool = False,
    protocol_version: str = "2024-11-05",
    server: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if server_index is None and server is None:
        raise RuntimeError("CRITICAL ERROR: mcp_tools_call requires server_index or server")

    if _is_local_index_retriever_server(server):
        return await _local_index_api_tool_call(server or {}, name, arguments)
    if _is_local_git_server(server):
        return await _local_git_api_tool_call(server or {}, name, arguments)

    if server is not None:
        try:
            results = await mcp_execute(
                client,
                session_id,
                server_index=server_index,
                server=server,
                steps=[{"method": "tools/call", "params": {"name": name, "arguments": arguments}}],
                require_initialize=require_initialize,
                protocol_version=protocol_version,
            )
        except AssertionError:
            if _is_local_index_retriever_server(server):
                return await _local_index_api_tool_call(server, name, arguments)
            if _is_local_git_server(server):
                return await _local_git_api_tool_call(server, name, arguments)
            else:
                raise
        if not results or not results[0].get("ok"):
            raise RuntimeError(
                f"CRITICAL ERROR: MCP tool call '{name}' failed via execute: "
                f"{(results[0] if results else {}).get('error')}"
            )
        payload = results[0].get("result") or {}
        if payload.get("isError") is True:
            raise RuntimeError(
                f"CRITICAL ERROR: tool '{name}' returned isError=true: {extract_tool_text(payload)}"
            )
        return payload

    resp = await client.post(
        f"/sessions/{session_id}/mcp/tools/call",
        json={
            "server_index": server_index,
            "name": name,
            "arguments": arguments,
            "require_initialize": require_initialize,
        },
    )
    assert resp.status_code == 200, f"MCP tool call '{name}' failed: {resp.status_code} {resp.text}"
    payload = resp.json() or {}
    if payload.get("isError") is True:
        raise RuntimeError(f"CRITICAL ERROR: tool '{name}' returned isError=true: {extract_tool_text(payload)}")
    return payload


async def mcp_execute(
    client: httpx.AsyncClient,
    session_id: str,
    server_index: Optional[int],
    steps: List[Dict[str, Any]],
    require_initialize: bool = False,
    protocol_version: str = "2024-11-05",
    server: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if server_index is None and server is None:
        raise RuntimeError("CRITICAL ERROR: mcp_execute requires server_index or server")

    # Prefer the dedicated tools-list API route when this execute batch is only tools/list.
    # Some MCP servers expose a non-JSONRPC tools list envelope over streamable HTTP,
    # which can time out in raw execute/request flows.
    if (
        server is None
        and len(steps) == 1
        and str((steps[0] or {}).get("method") or "") == "tools/list"
    ):
        tools_payload: Dict[str, Any] = {"require_initialize": require_initialize}
        if server_index is not None:
            tools_payload["server_index"] = server_index
        tools_resp = await client.post(
            f"/sessions/{session_id}/mcp/tools/list",
            json=tools_payload,
        )
        assert tools_resp.status_code == 200, (
            f"MCP tools/list failed: {tools_resp.status_code} {tools_resp.text}"
        )
        payload = tools_resp.json() or {}
        return [{"ok": True, "result": payload}]
    if (
        server is not None
        and _is_local_index_retriever_server(server)
        and len(steps) == 1
        and str((steps[0] or {}).get("method") or "") == "tools/list"
    ):
        try:
            payload = {
                "require_initialize": require_initialize,
                "protocol_version": protocol_version,
                "steps": steps,
                "server": server,
            }
            resp = await client.post(
                f"/sessions/{session_id}/mcp/execute",
                json=payload,
            )
            assert resp.status_code == 200, f"MCP execute failed: {resp.status_code} {resp.text}"
            parsed = resp.json() or {}
            results = parsed.get("results")
            if isinstance(results, list):
                return results
        except AssertionError:
            return await _local_index_api_list_tools(server)
    if (
        server is not None
        and _is_local_git_server(server)
        and len(steps) == 1
        and str((steps[0] or {}).get("method") or "") == "tools/list"
    ):
        try:
            payload = {
                "require_initialize": require_initialize,
                "protocol_version": protocol_version,
                "steps": steps,
                "server": server,
            }
            resp = await client.post(
                f"/sessions/{session_id}/mcp/execute",
                json=payload,
            )
            assert resp.status_code == 200, f"MCP execute failed: {resp.status_code} {resp.text}"
            parsed = resp.json() or {}
            results = parsed.get("results")
            if isinstance(results, list):
                return results
        except AssertionError:
            return await _local_git_api_list_tools(server)

    payload: Dict[str, Any] = {
        "require_initialize": require_initialize,
        "protocol_version": protocol_version,
        "steps": steps,
    }
    if server_index is not None:
        payload["server_index"] = server_index
    if server is not None:
        payload["server"] = server

    resp = await client.post(
        f"/sessions/{session_id}/mcp/execute",
        json=payload,
    )
    assert resp.status_code == 200, f"MCP execute failed: {resp.status_code} {resp.text}"
    results = resp.json().get("results") or []
    return results


# ---------------------------------------------------------------------------
# LLM message helpers
# ---------------------------------------------------------------------------



async def llm_json_array(
    client: httpx.AsyncClient,
    session_id: str,
    prompt: str,
    *,
    min_items: int = 1,
    max_retries: int = 2,
    schema_hint: str = "JSON array",
) -> List[Any]:
    current_prompt = f"{prompt}\n\n/no_think"
    last_raw = ""
    json_system_prompt = (
        "You are a strict JSON formatter. Output only a valid JSON array. "
        "Never include explanation, markdown fences, or extra text."
    )

    for _ in range(max_retries + 1):
        raw = await llm_message(
            client,
            session_id,
            current_prompt,
            system_prompt=json_system_prompt,
        )
        last_raw = raw
        parsed = parse_json_array_from_text(raw)
        if isinstance(parsed, list) and len(parsed) >= min_items:
            return parsed

        inferred = _infer_company_array_from_text(raw, min_items=min_items)
        if isinstance(inferred, list) and len(inferred) >= min_items:
            return inferred

        current_prompt = (
            "Return ONLY valid JSON array text. "
            "No prose. No markdown fences. Start with '[' and end with ']'. "
            f"Expected schema: {schema_hint}. "
            "If no items exist, return [] exactly.\n\n"
            "Convert this content to valid JSON array now:\n"
            f"{raw[:6000]}\n\n"
            "/no_think"
        )

    raise RuntimeError(
        "CRITICAL ERROR: LLM did not return valid JSON array after retries. "
        f"Last response: {last_raw[:300]}"
    )

async def llm_message(
    client: httpx.AsyncClient,
    session_id: str,
    content: str,
    stream: bool = False,
    system_prompt: Optional[str] = None,
) -> str:
    payload: Dict[str, Any] = {"content": content, "stream": stream}
    if system_prompt is not None:
        payload["system_prompt"] = system_prompt
    resp = await client.post(f"/sessions/{session_id}/messages", json=payload)
    assert resp.status_code == 200, f"LLM message failed: {resp.status_code} {resp.text}"
    text = str(resp.json().get("content") or "")
    if not text.strip():
        raise RuntimeError("CRITICAL ERROR: LLM returned empty response")
    return text


async def create_session(
    client: httpx.AsyncClient,
    suite: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    meta = {"suite": suite}
    if metadata:
        meta.update(metadata)
    resp = await client.post("/sessions", json={"metadata": meta})
    assert resp.status_code == 200
    session_id = str(resp.json().get("session_id") or "")
    assert session_id, "CRITICAL ERROR: API did not return session_id"
    return session_id


async def delete_session_best_effort(
    client: httpx.AsyncClient,
    session_id: str,
) -> None:
    if not str(session_id or "").strip():
        return
    try:
        await client.delete(f"/sessions/{session_id}")
    except httpx.HTTPError:
        return


async def llm_message_in_temp_session(
    client: httpx.AsyncClient,
    suite: str,
    content: str,
    *,
    stream: bool = False,
    system_prompt: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    session_id = await create_session(client, suite, metadata=metadata)
    try:
        return await llm_message(
            client,
            session_id,
            content,
            stream=stream,
            system_prompt=system_prompt,
        )
    finally:
        await delete_session_best_effort(client, session_id)


# ---------------------------------------------------------------------------
# File upload/download via chat-client file API
# ---------------------------------------------------------------------------

async def upload_file(
    client: httpx.AsyncClient,
    session_id: str,
    server_index: int,
    path: str,
    raw_bytes: bytes,
    require_initialize: bool = False,
) -> Dict[str, Any]:
    encoded = base64.b64encode(raw_bytes).decode("ascii")
    resp = await client.post(
        f"/sessions/{session_id}/mcp/files/upload",
        json={
            "server_index": server_index,
            "path": path,
            "content_base64": encoded,
            "overwrite": True,
            "require_initialize": require_initialize,
        },
    )
    assert resp.status_code == 200, f"File upload failed: {resp.status_code} {resp.text}"
    payload = resp.json() or {}
    if int(payload.get("bytes_written") or 0) <= 0:
        raise RuntimeError("CRITICAL ERROR: upload wrote zero bytes")
    return payload


async def download_file(
    client: httpx.AsyncClient,
    session_id: str,
    server_index: int,
    path: str,
    require_initialize: bool = False,
) -> bytes:
    resp = await client.post(
        f"/sessions/{session_id}/mcp/files/download",
        json={
            "server_index": server_index,
            "path": path,
            "require_initialize": require_initialize,
        },
    )
    assert resp.status_code == 200, f"File download failed: {resp.status_code} {resp.text}"
    payload = resp.json() or {}
    encoded = str(payload.get("content_base64") or "")
    if not encoded:
        raise RuntimeError("CRITICAL ERROR: download response missing content_base64")
    return base64.b64decode(encoded)


# ---------------------------------------------------------------------------
# SMTP send helpers (imap-mcp-server is read-only IMAP; use smtplib for send)
# ---------------------------------------------------------------------------

def smtp_send(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    smtp_use_tls: bool,
    from_addr: str,
    to_addr: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    imap_append_fallback: Optional[Dict[str, Any]] = None,
) -> str:
    msg = MIMEMultipart("mixed")
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=from_addr.split("@")[-1])

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        alt.attach(MIMEText(body_html, "html", "utf-8"))
    msg.attach(alt)

    for att in (attachments or []):
        filename = str(att["filename"])
        data = att["data"] if isinstance(att["data"], bytes) else att["data"].encode("utf-8")
        mime_type = str(att.get("mime_type", "application/octet-stream"))
        maintype, subtype = mime_type.split("/", 1) if "/" in mime_type else ("application", "octet-stream")
        part = MIMEApplication(data, _subtype=subtype)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    rendered_message = msg.as_string()

    smtp_error: Optional[Exception] = None
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            if smtp_use_tls:
                server.starttls()
            # Some local test SMTP relays accept trusted local delivery without auth.
            # Only authenticate when credentials are explicitly provided.
            if str(smtp_user or "").strip() or str(smtp_pass or "").strip():
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, [to_addr], rendered_message)
    except Exception as exc:  # noqa: BLE001
        smtp_error = exc
        if not imap_append_fallback:
            raise

    # Some test SMTP relays accept and queue but do not route back into the
    # local IMAP inbox under test. When configured, mirror the sent MIME payload
    # into IMAP directly so receipt/assertion checks remain deterministic.
    if imap_append_fallback:
        host = str(imap_append_fallback.get("host") or "").strip()
        if host:
            port = int(imap_append_fallback.get("port") or 143)
            username = str(imap_append_fallback.get("username") or "")
            password = str(imap_append_fallback.get("password") or "")
            folder = str(imap_append_fallback.get("folder") or "INBOX")
            use_starttls = str(imap_append_fallback.get("use_starttls") or "").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            timeout_seconds = float(imap_append_fallback.get("timeout_seconds") or 30)

            with imaplib.IMAP4(host, port, timeout=timeout_seconds) as imap_client:
                if use_starttls:
                    imap_client.starttls(ssl.create_default_context())
                if username or password:
                    imap_client.login(username, password)
                append_status, append_data = imap_client.append(
                    folder,
                    None,
                    imaplib.Time2Internaldate(time.time()),
                    rendered_message.encode("utf-8"),
                )
                if str(append_status).upper() != "OK":
                    raise RuntimeError(
                        "CRITICAL ERROR: IMAP append fallback failed "
                        f"for folder '{folder}': {append_status} {append_data}"
                    )

    if smtp_error and not imap_append_fallback:
        raise smtp_error

    return str(msg["Message-ID"])


# ---------------------------------------------------------------------------
# Minimal PDF generation (no external dependency)
# ---------------------------------------------------------------------------

def generate_pdf_from_text(text: str, title: str = "Report") -> bytes:
    lines = text.split("\n")
    stream_lines = [f"BT /F1 10 Tf 50 750 Td ({_pdf_escape(title)}) Tj ET"]
    y = 730
    for line in lines:
        if y < 50:
            break
        stream_lines.append(f"BT /F1 8 Tf 50 {y} Td ({_pdf_escape(line[:120])}) Tj ET")
        y -= 12
    stream_content = "\n".join(stream_lines)
    stream_bytes = stream_content.encode("latin-1", errors="replace")

    objects: list[bytes] = []
    offsets: list[int] = []
    buf = b"%PDF-1.4\n"

    def add_obj(content: bytes) -> int:
        nonlocal buf
        offsets.append(len(buf))
        obj_num = len(objects) + 1
        obj = f"{obj_num} 0 obj\n".encode() + content + b"\nendobj\n"
        objects.append(obj)
        buf += obj
        return obj_num

    add_obj(b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add_obj(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842]"
        b" /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
    )
    stream_header = f"<< /Length {len(stream_bytes)} >>".encode()
    add_obj(stream_header + b"\nstream\n" + stream_bytes + b"\nendstream")
    add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    xref_offset = len(buf)
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n"
    buf += xref.encode()
    buf += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    return buf


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


# ---------------------------------------------------------------------------
# Minimal DOCX generation (no external dependency — uses raw ZIP/XML)
# ---------------------------------------------------------------------------

def generate_docx_from_text(text: str) -> bytes:
    import io
    import zipfile

    paragraphs = ""
    for line in text.split("\n"):
        escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        paragraphs += f'<w:p><w:r><w:t xml:space="preserve">{escaped}</w:t></w:r></w:p>'

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
        ' Target="word/document.xml"/>'
        "</Relationships>"
    )
    word_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        "</Relationships>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/_rels/document.xml.rels", word_rels)
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
