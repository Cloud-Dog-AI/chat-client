# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Value-free, per-leaf configuration provenance for the Settings surface.

The platform package owns compilation and secret resolution.  This module reads
the same input layers only to identify which layer supplied each final leaf.  It
never includes configuration values in its output.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Mapping

from cloud_dog_config.coercion import coerce  # type: ignore[import-untyped]
from cloud_dog_config.env_parser import parse_env_file  # type: ignore[import-untyped]
from cloud_dog_config.loader import (  # type: ignore[import-untyped]
    _select_relevant_os_environ,
)
from cloud_dog_config.naming import env_to_path  # type: ignore[import-untyped]
from cloud_dog_config.yaml_loader import load_yaml  # type: ignore[import-untyped]


_EXPRESSION_RE = re.compile(r"\$\{([^}:\s]+)")
_VAULT_RE = re.compile(r"(?:^|[^A-Za-z0-9_])vault\.", re.IGNORECASE)
_SENSITIVE_TERMS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "ssh_authorized_key",
    "authorization",
    "cookie",
)
_NON_SECRET_SUFFIXES = ("_header", "_path", "_url")
_SERVER_NAMESPACES: dict[str, list[str]] = {
    "api_server": ["api"],
    "client_api": ["api"],
    "mcp_server": ["mcp"],
    "mcp": ["mcp"],
    "a2a_server": ["a2a"],
    "web_server": ["webui"],
    "web_login": ["webui"],
}
_ALL_SERVERS = ["api", "mcp", "a2a", "webui"]
_VAULT_ENV_PATHS = {
    "VAULT_ADDR": "vault.server",
    "VAULT_SERVER": "vault.server",
    "VAULT_TOKEN": "vault.key",
    "VAULT_KEY": "vault.key",
    "VAULT_MOUNT_POINT": "vault.mount_point",
    "VAULT_CONFIG_PATH": "vault.config_path",
}


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, child in value.items():
            child_path = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten(child, child_path))
        return out
    if isinstance(value, (list, tuple)):
        out = {}
        for index, child in enumerate(value):
            out.update(_flatten(child, f"{prefix}[{index}]"))
        return out
    return {prefix: value} if prefix else {}


def _environment_path(key: str, base: Mapping[str, Any]) -> str | None:
    upper = key.strip().upper()
    if upper in _VAULT_ENV_PATHS:
        return _VAULT_ENV_PATHS[upper]
    path = env_to_path(key, prefix="CLOUD_DOG") or env_to_path(key)
    if path:
        return path
    return key if key in base else None


def _source_for_raw(value: Any, fallback: str, available_env: set[str]) -> str:
    if not isinstance(value, str):
        return fallback
    if _VAULT_RE.search(value):
        return "vault"
    referenced = {match.group(1).strip() for match in _EXPRESSION_RE.finditer(value)}
    if referenced & available_env:
        return "env"
    return fallback


def _layer_sources(
    value: Any,
    *,
    fallback: str,
    available_env: set[str],
) -> dict[str, str]:
    return {
        path: _source_for_raw(raw, fallback, available_env)
        for path, raw in _flatten(value).items()
    }


def _env_sources(
    env_values: Mapping[str, str],
    *,
    base: Mapping[str, Any],
    available_env: set[str],
) -> dict[str, str]:
    sources: dict[str, str] = {}
    for key, raw in env_values.items():
        path = _environment_path(key, base)
        if not path:
            continue
        source = _source_for_raw(raw, "env", available_env)
        coerced = coerce(raw)
        flattened = _flatten(coerced, path)
        if flattened:
            sources.update({leaf: source for leaf in flattened})
        else:
            sources[path] = source
    return sources


def _source_for_final_path(path: str, layers: Iterable[dict[str, str]]) -> str:
    for layer in layers:
        if path in layer:
            return layer[path]
        # A collection supplied as one env/override value owns all compiled
        # descendants even when coercion did not expand it before compilation.
        for parent, source in layer.items():
            if path.startswith(f"{parent}.") or path.startswith(f"{parent}["):
                return source
    return "default"


def _is_secret_path(path: str) -> bool:
    key = path.rsplit(".", 1)[-1].split("[", 1)[0].lower()
    if key.endswith(_NON_SECRET_SUFFIXES):
        return False
    if path == "vault.key":
        return True
    return any(term in key for term in _SENSITIVE_TERMS)


def _servers_for_path(path: str) -> list[str]:
    top = path.split(".", 1)[0].split("[", 1)[0]
    return list(_SERVER_NAMESPACES.get(top, _ALL_SERVERS))


def build_config_provenance(
    *,
    final_tree: Mapping[str, Any],
    defaults_yaml: str,
    config_yaml: str,
    env_files: Iterable[str],
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return value-free metadata for every leaf of ``final_tree``."""
    defaults_raw = load_yaml(defaults_yaml, missing_ok=False)
    config_raw = load_yaml(config_yaml, missing_ok=True)
    defaults_raw = defaults_raw if isinstance(defaults_raw, dict) else {}
    config_raw = config_raw if isinstance(config_raw, dict) else {}

    env_values: dict[str, str] = {}
    for env_file in env_files:
        env_values.update(parse_env_file(str(env_file)))
    # Reuse the platform loader's own restricted environment selection so this
    # adapter cannot drift into a second configuration-loading implementation.
    relevant_os = _select_relevant_os_environ(base=dict(final_tree))
    available_env = set(env_values) | set(relevant_os)
    default_sources = _layer_sources(
        defaults_raw, fallback="default", available_env=available_env
    )
    config_sources = _layer_sources(
        config_raw, fallback="config", available_env=available_env
    )
    file_env_sources = _env_sources(
        env_values, base=defaults_raw, available_env=available_env
    )
    process_env_sources = _env_sources(
        relevant_os, base=defaults_raw, available_env=available_env
    )
    override_sources = {
        path: _source_for_raw(value, "env", available_env)
        for path, value in (overrides or {}).items()
    }

    # Highest-precedence layer is checked first.
    layers = (
        override_sources,
        process_env_sources,
        file_env_sources,
        config_sources,
        default_sources,
    )
    sources: dict[str, dict[str, Any]] = {}
    for path in _flatten(final_tree):
        sources[path] = {
            "source": _source_for_final_path(path, layers),
            "secret": _is_secret_path(path),
            "servers": _servers_for_path(path),
        }

    source_counts = Counter(str(meta["source"]) for meta in sources.values())
    counts: dict[str, int] = {
        "total": len(sources),
        "secret": sum(bool(meta["secret"]) for meta in sources.values()),
    }
    counts.update({name: source_counts.get(name, 0) for name in ("default", "config", "env", "vault")})
    return {"sources": sources, "counts": counts}
