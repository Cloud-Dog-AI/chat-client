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

_ICON_ASSETS = {
    "/favicon.ico": ("assets/favicon-*.ico", "image/x-icon"),
    "/apple-touch-icon.png": ("assets/apple-touch-icon-*.png", "image/png"),
    "/apple-touch-icon-precomposed.png": ("assets/apple-touch-icon-*.png", "image/png"),
}

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
    "/source-connections",
    "/mcp-servers",
    "/tools",
    "/developer/api-docs",
    "/developer/mcp-console",
    "/developer/a2a-console",
    "/system/jobs",
    "/settings",
    "/admin",
    "/admin/rbac",
    "/admin/users",
    "/admin/groups",
    "/admin/api-keys",
    "/admin/roles",
    "/audit-log",
    # CC6 (W28C-1703): canonical PS-71 IDAM SPA routes (shared @cloud-dog/idam).
    "/idam/users",
    "/idam/groups",
    "/idam/roles",
    "/idam/api-keys",
    "/idam/rbac",
    "/mcp-console",
    "/a2a-console",
    "/files",
    "/catalogue",
}


def spa_entry_routes() -> set[str]:
    """Return the browser history routes served by the React SPA."""
    return set(_SPA_ENTRY_ROUTES)


def is_spa_entry_path(path: str) -> bool:
    """Determine whether a request path should resolve to the SPA entrypoint."""
    cleaned = "/" + str(path or "").strip().lstrip("/")
    return cleaned in _SPA_ENTRY_ROUTES


# CC-401 (W28E-1863): reserved server-side path prefixes that MUST NOT be served
# the SPA shell — these are proxied to the API / MCP / A2A upstreams, are health/
# readiness probes, static assets, or auth endpoints. Everything else that is a
# browser DOCUMENT navigation (a GET/HEAD with no file extension) resolves to the
# SPA index.html shell so React can render the requested route (or its own login
# gate for an anonymous visitor). This replaces the fragile enumerated allowlist
# (`_SPA_ENTRY_ROUTES` + per-route @app.get decorators) as the fallback of last
# resort: any React route missing from the allowlist (e.g. /system/settings,
# /system/about, /about, /research) previously fell through to the API proxy and
# returned a raw 401/404 JSON instead of the SPA shell. Matches the sql-agent /
# search-mcp catch-all pattern and AGENT-LESSONS §2.4.
_RESERVED_NON_SPA_PREFIXES = (
    "api",
    "v1",
    "webapi",
    "webmcp",
    "mcp",
    "messages",
    "weba2a",
    "a2a",
    "events",
    "tasks",
    "sessions",
    "auth",
    "assets",
    "login",  # /login/session is an auth bootstrap; /login itself is an explicit SPA route
)
_RESERVED_NON_SPA_EXACT = {
    "health",
    "ready",
    "live",
    "status",
    # W28E-1863 fix-wave-d (WSC-014): /version is the explicit build-identity API
    # route (served by the API tier and reached via the web-tier proxy fallthrough)
    # consumed by the shared About page. Reserve it so the SPA document-navigation
    # fallback can never shadow it with the index.html shell (chart-mcp precedent).
    "version",
    "runtime-config.js",
    "favicon.ico",
    "apple-touch-icon.png",
    "apple-touch-icon-precomposed.png",
}


def is_spa_document_navigation(path: str) -> bool:
    """Return True when a browser GET/HEAD for ``path`` should serve the SPA shell.

    A path is a SPA document navigation when it is NOT one of the reserved
    server-side surfaces (API / MCP / A2A proxy paths, health/readiness probes,
    auth endpoints, static assets) and does NOT look like a static file request
    (no ``.`` in the final path segment — those are asset/file GETs handled by the
    dedicated asset routes or a genuine 404). This is the fallback that guarantees
    every React history route — including ones not present in the enumerated
    allowlist — resolves to ``index.html`` on a hard navigation / refresh /
    bookmark, so the SPA renders (unauthenticated → its own login gate) rather
    than leaking a raw API 401/404 JSON body.
    """
    cleaned = str(path or "").strip().strip("/")
    if not cleaned:
        # Bare "/" is handled by the explicit root redirect; treat as non-doc here.
        return False
    first_segment = cleaned.split("/", 1)[0]
    if first_segment in _RESERVED_NON_SPA_PREFIXES:
        return False
    if cleaned in _RESERVED_NON_SPA_EXACT or first_segment in _RESERVED_NON_SPA_EXACT:
        return False
    # A dot in the LAST segment indicates a static file request (e.g. foo.js,
    # sitemap.xml) — never serve those the HTML shell; let the asset routes or a
    # genuine 404 handle them.
    if "." in cleaned.rsplit("/", 1)[-1]:
        return False
    return True


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


def _git_head_commit() -> str:
    """Best-effort git HEAD for dev/source runs (empty string if unavailable).

    Mirrors the deployed file-mcp / chart-mcp ``_git_head_commit`` reference so a
    local/source run still populates the WebUI About page when no container
    build-identity ENV is present. W28E-1863 fix-wave-d (WSC-014).
    """
    try:
        import subprocess

        repo_root = Path(__file__).resolve().parents[2]
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 - build identity must never crash a request
        return ""
    return ""


def build_identity(config: ConfigManager) -> dict[str, str]:
    """Return build/deploy identity for WSC-014 / PS-30 UI-R7.3.

    Source of truth is the container build: ``docker-build.sh`` stamps the image
    OCI ``org.opencontainers.image.revision`` label AND injects the matching
    runtime ENV, which ``cloud_dog_config`` surfaces as ``build.source_commit`` /
    ``build.source_branch`` / ``build.build_date`` / ``build.container_digest``
    (env keys ``CLOUD_DOG__BUILD__SOURCE_COMMIT`` … routed through cloud_dog_config,
    NOT direct process-environment reads — RULES §1.4.1). For a dev/source run (no container ENV)
    ``source_commit`` falls back to the working-tree git HEAD so the About page is
    still populated locally. Modelled on the deployed chart-mcp reference
    (``0e18aa8``). W28E-1863 fix-wave-d.
    """
    commit = str(config.get("build.source_commit") or "").strip()
    if not commit or commit == "unknown":
        commit = _git_head_commit()
    branch = str(config.get("build.source_branch") or "").strip()
    if branch == "unknown":
        branch = ""
    build_date = str(config.get("build.build_date") or "").strip()
    digest = str(config.get("build.container_digest") or "").strip()
    env_name = str(config.get("app.environment") or "").strip()
    return {
        "source_commit": commit,
        "source_branch": branch,
        "build_date": build_date,
        "container_digest": digest,
        "environment": env_name,
    }


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


def serve_spa_icon(config: ConfigManager, request_path: str) -> FileResponse:
    """Return browser-discovered root icon assets from the hashed UI bundle."""
    pattern, media_type = _ICON_ASSETS.get(request_path, ("", "application/octet-stream"))
    root = _ui_dist_root(config)
    matches = sorted(root.glob(pattern)) if pattern else []
    if not matches:
        raise HTTPException(status_code=404, detail="UI asset not found")
    return FileResponse(matches[0], media_type=media_type)


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
