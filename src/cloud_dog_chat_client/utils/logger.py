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
import socket
import logging as _stdlib_logging  # Required for handler introspection only — all logging goes through cloud_dog_logging
from posixpath import dirname, join
from pathlib import Path
from types import MethodType
from typing import Any, Optional

from cloud_dog_logging import (  # type: ignore[import-untyped]
    get_integrity_verifier,
    get_logger,
    setup_logging as platform_setup_logging,
)

from ..storage_fs import file_name, file_stem, storage_for_root


def _audit_log_path(app_log_file: Optional[str]) -> Optional[str]:
    """Internal helper to audit log path for this module."""
    if not app_log_file:
        return None
    stem = file_stem(app_log_file) or file_name(app_log_file)
    return join(dirname(str(app_log_file)), f"{stem}.audit.jsonl")


def _resolve_service_instance(service_instance: Optional[str]) -> str:
    """Resolve a stable non-empty service instance for application/audit logs."""
    explicit = str(service_instance or "").strip()
    if explicit:
        return explicit
    hostname = str(socket.gethostname() or "").strip()
    if hostname:
        return hostname
    return "chat-client-local"


def _apply_mode(path: Optional[str], mode: int) -> None:
    """Best-effort chmod for runtime log files."""
    candidate = str(path or "").strip()
    if not candidate:
        return
    file_path = Path(candidate)
    if not file_path.exists():
        return
    try:
        os.chmod(file_path, mode)
    except OSError:
        return


def _ensure_log_file(path: Optional[str], mode: int) -> None:
    """Create an active log file eagerly so handler defaults cannot drift its mode."""
    candidate = str(path or "").strip()
    if not candidate:
        return
    file_path = Path(candidate)
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.touch(exist_ok=True)
    except OSError:
        return
    _apply_mode(candidate, mode)


def _normalise_log_permissions(
    *,
    app_log_file: Optional[str],
    audit_log_file: Optional[str],
) -> None:
    """Enforce PS-40 runtime permissions for active log files."""
    _apply_mode(app_log_file, 0o644)
    _apply_mode(audit_log_file, 0o600)

    verifier = get_integrity_verifier()
    if verifier is None:
        return
    integrity_path = getattr(verifier, "_integrity_log_path", None)
    _apply_mode(str(integrity_path) if integrity_path is not None else None, 0o600)


def _iter_app_handlers() -> list[_stdlib_logging.Handler]:
    """Return concrete file-backed application handlers, including wrapped dual handlers."""
    handlers: list[_stdlib_logging.Handler] = []
    for handler in _stdlib_logging.root.handlers:
        nested = getattr(handler, "file_handler", None)
        if isinstance(nested, _stdlib_logging.Handler):
            handlers.append(nested)
            continue
        if hasattr(handler, "baseFilename"):
            handlers.append(handler)
    return handlers


def _wrap_app_handler_permissions(app_log_file: Optional[str]) -> None:
    """Force PS-40 app-log permissions after every emit for platform versions without file-mode control."""
    candidate = str(app_log_file or "").strip()
    if not candidate:
        return
    for handler in _iter_app_handlers():
        if getattr(handler, "_cloud_dog_mode_wrapped", False):
            continue
        if not hasattr(handler, "emit"):
            continue
        original_emit = handler.emit

        def _patched_emit(self, record, _orig=original_emit, _path=candidate):
            _orig(record)
            _apply_mode(_path, 0o644)

        handler.emit = MethodType(_patched_emit, handler)
        setattr(handler, "_cloud_dog_mode_wrapped", True)
        _apply_mode(candidate, 0o644)


def setup_logging(
    *,
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    audit_log_file: Optional[str] = None,
    log_console: bool = True,
    app_name: str = "cloud_dog_chat_client",
    service_instance: Optional[str] = None,
    environment: str = "dev",
    log_format: str = "json",
    log_max_bytes: int = 10 * 1024 * 1024,
    log_backup_count: int = 5,
    use_queue: bool = True,
) -> Any:
    """Handle setup logging for the current runtime context."""
    # Covers: R13, NFR4
    # Standard and audit log streams are configured together for actionable diagnostics.
    del use_queue  # Queue handling is managed internally by cloud_dog_logging.

    if log_file:
        storage_for_root(dirname(str(log_file)))
    _ensure_log_file(log_file, 0o644)
    _ensure_log_file(audit_log_file or _audit_log_path(log_file), 0o600)

    cfg = {
        "service_name": app_name,
        "environment": str(environment or "dev"),
        "log": {
            "level": str(log_level or "INFO"),
            "format": str(log_format or "json"),
            "app_log": log_file,
            "audit_log": audit_log_file or _audit_log_path(log_file) or "logs/audit.log.jsonl",
            "console": bool(log_console),
            "rotation_max_bytes": int(log_max_bytes),
            "rotation_backup_count": int(log_backup_count),
            "levels": {
                "httpcore": "WARNING",
                "httpx": "WARNING",
                "urllib3": "WARNING",
                "asyncio": "WARNING",
            },
        },
    }
    cfg["log"]["service_instance"] = _resolve_service_instance(service_instance)
    platform_setup_logging(cfg)
    _wrap_app_handler_permissions(log_file)
    _normalise_log_permissions(
        app_log_file=log_file,
        audit_log_file=str(cfg["log"]["audit_log"] or "").strip(),
    )
    return get_logger(app_name)
