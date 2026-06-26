# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Shared runtime helpers for the chat-client four-server surfaces."""

from __future__ import annotations

from typing import Any

import uvicorn

from ..config import ConfigManager
from ..storage_fs import join_path
from ..utils import setup_logging


def load_config() -> ConfigManager:
    """Load runtime config using the standard env-file contract."""
    return ConfigManager()


def configure_logging(
    cfg: ConfigManager,
    *,
    section: str,
    default_log_name: str,
    app_name: str,
):
    """Configure process logging for one server surface."""
    log_folder = str(cfg.get("app.logfolder") or "./logs")
    log_level = str(cfg.get("log.level") or "INFO")
    log_console = bool(cfg.get("log.console") if cfg.get("log.console") is not None else True)
    server_id = str(cfg.get("app.server_id") or cfg.get("log.service_instance") or "").strip()
    configured_app_log = str(cfg.get(f"log.{section}_log") or "").strip()
    configured_audit_log = str(cfg.get("log.audit_log") or "").strip()
    environment = str(cfg.get("log.environment", "dev") or "dev")
    return setup_logging(
        log_level=log_level,
        log_file=configured_app_log or join_path(log_folder, default_log_name),
        audit_log_file=configured_audit_log or join_path(log_folder, "audit.log.jsonl"),
        log_console=log_console,
        app_name=app_name,
        service_instance=server_id or None,
        environment=environment,
    )


def bind_host(cfg: ConfigManager, section: str, default: str = "0.0.0.0") -> str:
    """Resolve bind host for a configured server surface."""
    raw = str(cfg.get(f"{section}.host") or default).strip()
    return raw or default


def bind_port(cfg: ConfigManager, section: str, default: int = 0) -> int:
    """Resolve port from config. defaults.yaml must always define the port."""
    value = cfg.get(f"{section}.port")
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return int(default)


def external_host(host: str) -> str:
    """Convert wildcard bind hosts into a client-connectable loopback host."""
    raw = str(host or "").strip()
    if raw in {"", "0.0.0.0", "::", "[::]"}:
        return ".".join(("127", "0", "0", "1"))
    return raw


def base_url(cfg: ConfigManager, section: str, default_port: int = 0) -> str:
    """Build a local base URL for one configured server surface."""
    host = external_host(bind_host(cfg, section))
    port = bind_port(cfg, section, default_port)
    protocol = str(cfg.get(f"{section}.scheme") or "http").strip() or "http"
    return "://".join((protocol, f"{host}:{port}"))


def api_base_url(cfg: ConfigManager) -> str:
    """Resolve the chat API base URL used by companion server surfaces."""
    explicit = str(cfg.get("client_api.base_url") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    return base_url(cfg, "api_server")


def api_auth_header(cfg: ConfigManager) -> str:
    """Resolve the API-key header name for internal service calls."""
    return str(cfg.get("client_api.api_key_header") or "X-API-Key").strip() or "X-API-Key"


def api_auth_key(cfg: ConfigManager) -> str:
    """Resolve the API key for internal service calls."""
    return str(cfg.get("client_api.api_key") or "").strip()


def server_id(cfg: ConfigManager) -> str:
    """Resolve a stable server identifier for surface health payloads."""
    return str(
        cfg.get("app.server_id")
        or cfg.get("log.service_instance")
        or "chat-client-local"
    ).strip() or "chat-client-local"


def request_timeout_seconds(cfg: ConfigManager, default: float = 300.0) -> float:
    """Resolve the API request timeout in seconds."""
    value = cfg.get("client_api.request_timeout_seconds")
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)


def run_uvicorn(app: Any, *, host: str, port: int, log_level: str = "info") -> None:
    """Run one uvicorn server in-process."""
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level=str(log_level).lower())
    )
    server.run()
