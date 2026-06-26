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
import re
import socket
import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlsplit, urlunsplit

import pytest
from cloud_dog_config.compiler.vault_resolver import Unresolved, resolve_vault_identifier
from cloud_dog_config.vault.client import VaultClient, VaultConnectionConfig

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import APIServerError, precheck_remote_runtime_session_create


_REPO_ROOT = Path(__file__).resolve().parents[1]
_PRIVATE_DIR = _REPO_ROOT / "private"
_FORENSIC_WEB_ENV = _REPO_ROOT / "tests" / "env-FORENSIC-WEB"
_AGGREGATE_ENV_FILES = {"env-ST", "env-IT", "env-AT", "env-QT"}
_DISCOURAGED_VARIANT_TOKENS = (
    "openrouter",
    "preprod",
    "variant",
    "gemma",
    "granite",
    "cli-chat",
)
_ISOLATED_ENV_PREFIXES = ("CLOUD_DOG__", "CHAT_CLIENT_")
_DOWNSTREAM_SERVICES = ("sqlagent", "expertagent", "filemcp", "indexretriever")
_VAULT_REF_PATTERN = re.compile(r"^\$\{(vault\.[^}]+)\}$")


def _module_path_from_request(request) -> str:
    path = getattr(request.node, "path", None)
    if path is None:
        path = getattr(request.node, "fspath", None)
    return str(path or "")


def _suite_slug_from_path(path: str) -> Optional[str]:
    match = re.search(r"/(UT|ST|IT|AT)(\d+)\.(\d+)", path, flags=re.IGNORECASE)
    if not match:
        return None
    prefix = match.group(1).lower()
    major = match.group(2)
    minor = match.group(3)
    return f"{prefix}{major}-{minor}"


def _private_env_candidates(slug: str, service: str = "") -> list[Path]:
    """Return candidate private env files for a suite slug and optional service.

    When *service* is empty the lookup is the existing chat-client env
    resolution (``env-{slug}`` exact + ``env-{slug}-*`` glob).

    When *service* is provided (e.g. ``"sqlagent"``), only files matching
    ``env-{slug}-{service}-*`` are returned.  This allows downstream LLM
    pinning per service without conflating the chat-client's own env with
    the downstream override.
    """
    if not _PRIVATE_DIR.exists():
        return []
    candidates: list[Path] = []
    if service:
        # Service-specific override — no exact-match variant, only glob.
        candidates.extend(
            sorted(p for p in _PRIVATE_DIR.glob(f"env-{slug}-{service}-*") if p.is_file())
        )
    else:
        exact = _PRIVATE_DIR / f"env-{slug}"
        if exact.is_file():
            candidates.append(exact)
        candidates.extend(sorted(p for p in _PRIVATE_DIR.glob(f"env-{slug}-*") if p.is_file()))
    return candidates


def _candidate_score(path: Path) -> tuple[int, str]:
    name = path.name.lower()
    score = 0
    if "qwen3" in name:
        score += 50
    for token in _DISCOURAGED_VARIANT_TOKENS:
        if token in name:
            score -= 100
    return score, name


def _select_private_env(candidates: Iterable[Path]) -> Optional[Path]:
    ordered = sorted(candidates, key=_candidate_score, reverse=True)
    if not ordered:
        return None
    return ordered[0]


def _resolve_env_file(raw_env: str, request) -> str:
    env_path = Path(str(raw_env))
    if env_path.name not in _AGGREGATE_ENV_FILES:
        return str(env_path)

    module_path = _module_path_from_request(request)
    suite_slug = _suite_slug_from_path(module_path)
    if not suite_slug:
        return str(env_path)

    selected = _select_private_env(_private_env_candidates(suite_slug))
    if not selected:
        return str(env_path)
    return str(selected)


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = _resolve_env_value(key, value)
    return values


def _apply_optional_web_login_defaults(env_values: dict[str, str]) -> None:
    if not _FORENSIC_WEB_ENV.is_file():
        return
    forensic_values = _parse_env_file(_FORENSIC_WEB_ENV)
    for key in (
        "CLOUD_DOG_WEB_LOGIN_USERNAME",
        "CLOUD_DOG_WEB_LOGIN_PASSWORD",
        "CLOUD_DOG__WEB_LOGIN__USERNAME",
        "CLOUD_DOG__WEB_LOGIN__PASSWORD",
    ):
        value = str(forensic_values.get(key) or "").strip()
        if value:
            env_values.setdefault(key, value)


def _is_unresolved_env_value(value: str | None) -> bool:
    if value is None:
        return True
    candidate = value.strip()
    if not candidate:
        return True
    return bool(_VAULT_REF_PATTERN.match(candidate))


def _resolve_env_value(key: str, raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        return value
    match = _VAULT_REF_PATTERN.match(value)
    if match is None:
        return value
    vault_identifier = match.group(1)

    # Per-module vault expressions MUST be resolved from Vault, not from
    # stale os.environ values leaked by the cloud_dog_config pytest plugin's
    # session-scoped _require_env fixture (which loads the aggregate env file
    # via setdefault).  The per-module private env file is authoritative when
    # it specifies a vault expression — always resolve it fresh.

    addr = os.environ.get("VAULT_ADDR", "").strip()
    token = os.environ.get("VAULT_TOKEN", "").strip()
    if not addr or not token:
        pytest.exit(
            "ERROR: unresolved Vault expression in --env file; source env-vault first:\n"
            "set -a; source /opt/iac/Development/cloud-dog-ai/env-vault; set +a",
            returncode=2,
        )

    mount = os.environ.get("VAULT_MOUNT_POINT", "").strip().strip("/")
    config_path = os.environ.get("VAULT_CONFIG_PATH", "").strip().strip("/")
    if config_path:
        mount = "/".join([part for part in (mount, config_path) if part])

    try:
        client = VaultClient(
            VaultConnectionConfig(
                server=addr,
                token=token,
                timeout_seconds=10.0,
                mount_point=mount,
            )
        )
        resolved = resolve_vault_identifier(vault_identifier, vault=client)
        if isinstance(resolved, Unresolved):
            resolved = _resolve_vault_identifier_from_root_json_string(
                vault_identifier, vault=client
            )
    except Exception as exc:
        pytest.exit(
            f"ERROR: failed to resolve Vault expression for {key}: {vault_identifier} ({exc})",
            returncode=2,
        )

    if isinstance(resolved, (str, int, float, bool)):
        resolved_text = str(resolved).strip()
        if resolved_text:
            return resolved_text

    pytest.exit(
        f"ERROR: Vault expression resolved empty for {key}: {vault_identifier}",
        returncode=2,
    )


def _resolve_vault_identifier_from_root_json_string(
    vault_identifier: str, *, vault: VaultClient
) -> Any:
    """Resolve root-blob Vault values when the payload stores `json` as a string.

    The platform resolver in the current test venv resolves root blobs with a
    dict `json` field or a `content` JSON string, but older config records may
    store the same tree as a string under `json`. Tests must still resolve the
    selected env file without leaking values from process env.
    """

    if not vault_identifier.startswith("vault."):
        return Unresolved(vault_identifier)
    parts = vault_identifier.split(".")
    if len(parts) < 3:
        return Unresolved(vault_identifier)

    try:
        root = vault.read("secret")
    except Exception:
        root = _read_vault_root_payload_from_env()
    if not isinstance(root, dict):
        return Unresolved(vault_identifier)

    raw_json = root.get("json")
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            tree = json.loads(raw_json)
        except Exception:
            return Unresolved(vault_identifier)
    else:
        return Unresolved(vault_identifier)

    current: Any = tree
    for part in parts[1:]:
        if not isinstance(current, dict) or part not in current:
            return Unresolved(vault_identifier)
        current = current[part]
    return current


def _read_vault_root_payload_from_env() -> dict[str, Any] | None:
    addr = os.environ.get("VAULT_ADDR", "").strip().rstrip("/")
    token = os.environ.get("VAULT_TOKEN", "").strip()
    mount = os.environ.get("VAULT_MOUNT_POINT", "").strip().strip("/")
    config_path = os.environ.get("VAULT_CONFIG_PATH", "").strip().strip("/")
    if not addr or not token or not mount or not config_path:
        return None

    url = f"{addr}/v1/{mount}/data/{config_path}"
    request = urllib.request.Request(url, headers={"X-Vault-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except Exception:
        return None

    data = payload.get("data", {}).get("data", {})
    return data if isinstance(data, dict) else None


def _truthy(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _allocate_local_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _rewrite_client_api_endpoint(env_values: dict[str, str]) -> None:
    """Avoid collisions with long-lived local API instances on 8090.

    Some developer sessions keep a local chat-client API process bound to 8090.
    For test isolation, remap to a free local port unless explicitly preserved.
    """

    if _truthy(env_values.get("CLOUD_DOG__TESTS__PRESERVE_API_SERVER_PORT")):
        return

    raw_port = str(env_values.get("CLOUD_DOG__API_SERVER__PORT") or "").strip()
    raw_base = str(env_values.get("CLOUD_DOG__CLIENT_API__BASE_URL") or "").strip()

    port_is_default = raw_port == "8090"
    base_is_default = False
    if raw_base:
        try:
            parsed = urlsplit(raw_base)
            base_is_default = (parsed.hostname in {"127.0.0.1", "localhost"}) and (
                parsed.port == 8090
            )
        except Exception:
            base_is_default = False

    if not (port_is_default or base_is_default):
        return

    free_port = _allocate_local_tcp_port()
    env_values["CLOUD_DOG__CLIENT_API__HOST"] = "127.0.0.1"
    env_values["CLOUD_DOG__API_SERVER__PORT"] = str(free_port)

    if raw_base:
        parsed = urlsplit(raw_base)
        scheme = parsed.scheme or "http"
        host = parsed.hostname or "127.0.0.1"
        netloc = f"{host}:{free_port}"
        env_values["CLOUD_DOG__CLIENT_API__BASE_URL"] = urlunsplit(
            (scheme, netloc, parsed.path or "", "", "")
        )
    else:
        env_values["CLOUD_DOG__CLIENT_API__BASE_URL"] = f"http://127.0.0.1:{free_port}"


def _resolve_cli_env_path(pytestconfig) -> Path:
    raw_env = pytestconfig.getoption("--env")
    if isinstance(raw_env, list):
        raw_env = raw_env[-1] if raw_env else None
    if not raw_env:
        pytest.exit("ERROR: --env parameter REQUIRED", returncode=2)

    p = Path(str(raw_env))
    if not p.is_absolute():
        p = (_REPO_ROOT / p).resolve()
    if not p.exists() or not p.is_file():
        pytest.exit(f"ERROR: --env file does not exist: {p}", returncode=2)
    return p


@pytest.fixture(scope="session", autouse=True)
def remote_runtime_auth_contract_precheck(pytestconfig):
    """Single-point fail-fast precheck for remote-runtime auth drift.

    For ST/IT/AT remote-runtime runs, verify /sessions session-create auth once,
    and abort the run with an explicit blocker if rejected.
    """

    env_path = _resolve_cli_env_path(pytestconfig)
    env_values = _parse_env_file(env_path)
    for key, value in env_values.items():
        if any(key.startswith(prefix) for prefix in _ISOLATED_ENV_PREFIXES):
            os.environ[key] = value

    mode = (
        str(env_values.get("TEST_RUNTIME_MODE") or "").strip().lower()
        or str(env_values.get("CLOUD_DOG__CHAT_TESTS__RUNTIME_MODE") or "").strip().lower()
    )
    tier = str(env_values.get("TEST_ENV_TIER") or "").strip().upper()

    if mode != "remote-runtime" or tier not in {"ST", "IT", "AT"}:
        return

    cfg = ConfigManager(env_file=str(env_path))
    try:
        precheck_remote_runtime_session_create(cfg)
    except APIServerError as e:
        pytest.exit(str(e), returncode=2)


@pytest.fixture(scope="module")
def env_file(pytestconfig, request):
    env = pytestconfig.getoption("--env")
    if isinstance(env, list):
        env = env[-1] if env else None
    if not env:
        pytest.fail("ERROR: --env parameter REQUIRED")

    resolved = _resolve_env_file(str(env), request)
    p = Path(resolved)
    if not p.exists() or not p.is_file():
        pytest.fail(f"ERROR: --env file does not exist: {p}")

    env_values = _parse_env_file(p)
    _apply_optional_web_login_defaults(env_values)
    _rewrite_client_api_endpoint(env_values)

    # Snapshot complete process environment so module teardown can restore exactly.
    original_env = dict(os.environ)

    # Prevent stale config keys from aggregate/previous module envs leaking into this module.
    for key in list(os.environ.keys()):
        if any(key.startswith(prefix) for prefix in _ISOLATED_ENV_PREFIXES) and key not in env_values:
            os.environ.pop(key, None)

    # Enforce deterministic test configuration: the selected --env file owns
    # all CLOUD_DOG__/CHAT_CLIENT_ keys for this module, regardless of any
    # pre-existing shell exports.
    for key, value in env_values.items():
        os.environ[key] = value

    try:
        yield str(p)
    finally:
        current_keys = set(os.environ.keys())
        original_keys = set(original_env.keys())

        for key in current_keys - original_keys:
            os.environ.pop(key, None)
        for key, value in original_env.items():
            os.environ[key] = value


@pytest.fixture(scope="module")
def downstream_env_overrides(request, env_file) -> Dict[str, Optional[Path]]:
    """Resolve per-downstream-service env override files for the current AT module.

    Returns a mapping of service name -> Path (or empty dict when no overrides
    are present).  The overrides are *not* applied to the process environment;
    they are intended for consumption by api_server helpers that start or
    configure downstream service containers (docker mode) or document the
    limitation (preprod mode).

    Naming convention: ``private/env-{suite_slug}-{service}-{variant}``
    e.g. ``private/env-at1-4-sqlagent-qwen3-14b``
    """
    overrides: Dict[str, Optional[Path]] = {}
    module_path = _module_path_from_request(request)
    suite_slug = _suite_slug_from_path(module_path)
    if not suite_slug:
        return overrides
    for svc in _DOWNSTREAM_SERVICES:
        candidates = _private_env_candidates(suite_slug, service=svc)
        selected = _select_private_env(candidates)
        if selected:
            overrides[svc] = selected
    return overrides


# --- PS-REQ-TEST-TRACE marker enforcement (W28E-1801A Stream-A refresh) ---
# See PS-REQ-TEST-TRACE v1.0 §6.2: every test must carry tier, surface, and req() markers.

import sys

_PS_REQ_TIER_MARKERS = {"QT", "UT", "ST", "IT", "AT"}
_PS_REQ_SURFACE_MARKERS = {"api", "mcp", "a2a", "webui", "cli", "internal"}

# Canonical marker definitions per PS-REQ-TEST-TRACE §6 + W28C-1715 compliance.
_CANONICAL_MARKERS = [
    # Tier markers (UPPER-CASE per PS-REQ-TEST-TRACE §6.1)
    "QT: quality-gate tier (static analysis, linting, package compliance)",
    "UT: unit-test tier (pure in-process, no external deps)",
    "ST: system-test tier (one running service, no downstream deps)",
    "IT: integration-test tier (two or more services wired together)",
    "AT: application-test tier (full stack, real browser / end-to-end)",
    # Surface markers (lower-case per PS-REQ-TEST-TRACE §6.1)
    "api: exercises the HTTP API surface",
    "mcp: exercises the MCP JSON-RPC surface",
    "a2a: exercises the A2A agent-to-agent surface",
    "webui: exercises the browser WebUI surface via Playwright",
    "cli: exercises CLI / in-process / internal-only surface",
    "internal: exercises internal library / helper logic",
    # Semantic markers
    "req(*ids): binds test to one or more requirement IDs (FR-NNN / CS-NNN / NF-NNN)",
    "negative: test asserts a denied / error / rejection outcome",
    # Non-functional / environment markers
    "slow: test takes 10-120 seconds",
    "heavy: test takes >120 seconds",
    "llm: requires live LLM endpoint (Ollama / OpenRouter)",
    "vdb: requires vector database (Chroma / Qdrant / OpenSearch)",
    "db: requires relational database (MySQL / PostgreSQL)",
    "smtp: requires SMTP/IMAP service",
    "mcp_server: requires external MCP server(s) to be running",
    "docker: requires Docker build/run capability",
]


def pytest_configure(config):
    """Register all canonical PS-REQ-TEST-TRACE markers (W28C-1715 compliance)."""
    for marker_def in _CANONICAL_MARKERS:
        config.addinivalue_line("markers", marker_def)


def pytest_collection_modifyitems(config, items):
    """PS-REQ-TEST-TRACE marker enforcement."""
    failures = []
    for item in items:
        marker_names = {m.name for m in item.iter_markers()}
        if not (marker_names & _PS_REQ_TIER_MARKERS):
            failures.append(f"{item.nodeid}: missing @pytest.mark.<tier> per PS-REQ-TEST-TRACE §6")
        if not (marker_names & _PS_REQ_SURFACE_MARKERS):
            failures.append(f"{item.nodeid}: missing @pytest.mark.<surface> per PS-REQ-TEST-TRACE §6")
        req_marker = item.get_closest_marker("req")
        if req_marker is None or not req_marker.args:
            failures.append(
                f"{item.nodeid}: missing @pytest.mark.req('FR-NNN') per PS-REQ-TEST-TRACE §6"
            )
    if failures:
        msg = "PS-REQ-TEST-TRACE marker enforcement failed for " + str(len(failures)) + " test(s):\n  " + "\n  ".join(failures[:20])
        if len(failures) > 20:
            msg += f"\n  ... and {len(failures) - 20} more"
        print(msg, file=sys.stderr)
        import pytest
        pytest.exit(msg, returncode=2)
