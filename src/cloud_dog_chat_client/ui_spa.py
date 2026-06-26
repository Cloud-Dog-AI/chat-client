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

"""Shared helpers for serving the PS-30 React SPA from ui/dist."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from . import __version__
from .config import ConfigManager

_SPA_ENTRY_ROUTES = {
    # Static SPA shell must be PUBLIC to anon so the login box renders
    # (W28A-727-R5 flat-login template item 1 — anon-gate trap delta).
    "/index.html",
    "/login",
    "/ui",
    "/dashboard",
    "/chat",
    "/sessions",
    "/profiles",
    "/mcp-servers",
    "/tools",
    "/docs",
    "/api-docs",
    "/jobs",
    "/settings",
    "/admin",
    "/admin/rbac",
    "/admin/users",
    "/admin/groups",
    "/admin/api-keys",
    "/admin/roles",
    # CC6 (W28C-1703): canonical PS-71 IDAM SPA routes (shared @cloud-dog/idam).
    "/idam/users",
    "/idam/groups",
    "/idam/roles",
    "/idam/api-keys",
    "/idam/rbac",
    "/mcp-console",
    "/a2a-console",
    "/monitoring",
    "/files",
}


def spa_entry_routes() -> set[str]:
    """Return the browser history routes served by the React SPA."""
    return set(_SPA_ENTRY_ROUTES)


def is_spa_entry_path(path: str) -> bool:
    """Determine whether a request path should resolve to the SPA entrypoint."""
    cleaned = "/" + str(path or "").strip().lstrip("/")
    return cleaned in _SPA_ENTRY_ROUTES


def _ui_dist_root(config: ConfigManager) -> Path:
    """Resolve the checked-in React build output directory."""
    return (config.project_root / "ui" / "dist").resolve()


def _application_release(config: ConfigManager) -> str:
    """Resolve the application release for runtime-config.js: ``app.release``
    override else the package single-source ``__version__`` (CC8, W28C-1703)."""
    configured = str(config.get("app.release") or "").strip()
    if configured:
        return configured
    return __version__


def _dist_file(config: ConfigManager, relative_path: str) -> Path:
    """Resolve one UI dist file and enforce path confinement."""
    root = _ui_dist_root(config)
    candidate = (root / str(relative_path or "").lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="UI asset not found") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="UI asset not found")
    return candidate


def serve_spa_index(config: ConfigManager) -> HTMLResponse:
    """Return the built SPA index.html content verbatim.

    W28A-727-R5 (corruption reopen): serve the clean built login/SPA shell with
    NO server-side injection. A prior lane (W28A-889-B-R2 / W28A-892) injected a
    'Cloud Dog demo inventory' panel before the SPA root, which corrupted the
    user-facing login/background surface. The login surface must render exactly
    the built ``ui/dist/index.html`` — nothing prepended, no demo/background
    content, no extra same-origin /v1/* probes.
    """
    index_path = _dist_file(config, "index.html")
    html = index_path.read_text(encoding="utf-8")
    return HTMLResponse(html)


def serve_spa_asset(config: ConfigManager, relative_path: str) -> FileResponse:
    """Return one built SPA static asset from ui/dist."""
    asset_path = _dist_file(config, relative_path)
    return FileResponse(asset_path)


def _runtime_environment(config: ConfigManager) -> str:
    """Map runtime environment strings to the shared config enum."""
    raw = str(config.get("app.environment") or "").strip().lower()
    if raw in {"production", "prod"}:
        return "production"
    if raw in {"staging", "stage"}:
        return "staging"
    return "dev"


def serve_runtime_config(config: ConfigManager, request: Request) -> Response:
    """Return runtime-config.js for the SPA bootstrap contract.

    Uses JavaScript expressions for URL values so the browser resolves the
    correct protocol and host behind a reverse proxy (Traefik / HTTPS).
    """
    env = _runtime_environment(config)
    auth_mode = "cookie"
    app_version = _application_release(config)
    # PS-92 (W28A-970g-V2): configurable A2A base path for runtime-config A2A_WS_URL.
    a2a_base_path = str(config.get("a2a_server.base_path") or "/a2a").rstrip("/") or "/a2a"
    try:
        session_timeout_minutes = int(
            float(
                config.get("session.timeout_minutes")
                or config.get("session_timeout_minutes")
                or 30
            )
        )
    except (TypeError, ValueError):
        session_timeout_minutes = 30
    if session_timeout_minutes < 5:
        session_timeout_minutes = 5
    body = (
        "const __origin = window.location.origin;\n"
        'const __wsOrigin = window.location.origin.replace(/^http/, "ws");\n'
        "window.__RUNTIME_CONFIG__ = {\n"
        f'  "ENV": "{env}",\n'
        '  "API_BASE_URL": __origin,\n'
        '  "MCP_BASE_URL": __origin + "/webmcp",\n'
        '  "A2A_EVENTS_URL": __origin + "/weba2a/events",\n'
        f'  "A2A_WS_URL": __wsOrigin + "{a2a_base_path}/ws",\n'
        # req: FR-001
        f'  "AUTH_MODE": "{auth_mode}",\n'
        f'  "APP_VERSION": "{app_version}",\n'
        f'  "SESSION_TIMEOUT_MINUTES": {session_timeout_minutes},\n'
        '  "SESSION_WARNING_MINUTES": 5\n'
        "};\n"
    )
    return Response(content=body, media_type="application/javascript")
