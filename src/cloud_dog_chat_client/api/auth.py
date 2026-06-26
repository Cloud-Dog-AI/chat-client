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

import ipaddress
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Dict

from fastapi import HTTPException, Request

from cloud_dog_idam import APIKeyOnlyProvider, ProviderRegistry, RBACEngine  # type: ignore[import-untyped]
from cloud_dog_idam.domain.errors import AuthenticationError  # type: ignore[import-untyped]
from cloud_dog_idam.domain.models import AuthRequest as IDAMAuthRequest  # type: ignore[import-untyped]

from ..config import ConfigManager

# PS-70 / W28A-700 — chat service permission strings (authorisation via RBACEngine.has_permission)
CHAT_MESSAGE_SEND = "chat:message:send"
CHAT_HISTORY_READ = "chat:history:read"
CHAT_CONVERSATION_LIST = "chat:conversation:list"
CHAT_CONVERSATION_DELETE = "chat:conversation:delete"
CHAT_CONFIG_WRITE = "chat:config:write"
CHAT_ADMIN_ALL = "chat:admin:*"
MCP_SERVERS_MANAGE = "mcp:servers:manage"


def build_chat_rbac_engine() -> RBACEngine:
    """Construct the RBAC matrix for chat-client roles (cloud_dog_idam.rbac.RBACEngine)."""
    return RBACEngine(
        role_permissions={
            "viewer": {
                CHAT_MESSAGE_SEND,
                CHAT_HISTORY_READ,
                CHAT_CONVERSATION_LIST,
                "api:access",
                "config:read",
            },
            "admin": {
                "*",
                CHAT_MESSAGE_SEND,
                CHAT_HISTORY_READ,
                CHAT_CONVERSATION_LIST,
                CHAT_CONVERSATION_DELETE,
                CHAT_CONFIG_WRITE,
                CHAT_ADMIN_ALL,
                MCP_SERVERS_MANAGE,
            },
        }
    )


@dataclass(frozen=True)
class AuthPrincipal:
    user_id: str
    role: str
    actor: str
    key_fingerprint: str
    key_id: str = ""
    scopes: tuple[str, ...] = ()


def _normalise_fingerprint(value: str) -> str:
    """Internal helper to fingerprint for this module."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("sha256:"):
        return raw
    if len(raw) >= 12:
        return f"sha256:{raw[:12]}"
    digest = sha256(raw.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _request_actor(config: ConfigManager, request: Request) -> str:
    """Internal helper to request actor for this module."""
    user_header = str(config.get("client_api.user_header") or "X-User")
    actor = str(request.headers.get(user_header) or "").strip()
    if actor:
        return actor
    return "unknown"


def _is_loopback_host(host: str) -> bool:
    """Return True when the supplied client host is local-only."""
    raw = str(host or "").strip().lower()
    try:
        return ipaddress.ip_address(raw).is_loopback
    except ValueError:
        return raw == "".join(("local", "host"))


def _trusted_web_admin_principal(
    config: ConfigManager, request: Request
) -> AuthPrincipal | None:
    """Allow authenticated local web-admin sessions when no admin API key exists."""
    configured_admin_key = str(config.get("client_api.admin_api_key") or "").strip()
    if configured_admin_key:
        return None

    source = str(request.headers.get("X-Request-Source") or "").strip().lower()
    if source != "webui":
        return None

    proxied_user = str(request.headers.get("X-Request-User") or "").strip()
    if not proxied_user:
        return None

    client = getattr(request, "client", None)
    client_host = str(getattr(client, "host", "") or "").strip()
    if not _is_loopback_host(client_host):
        return None

    return AuthPrincipal(
        user_id=f"webui:{proxied_user}",
        role="admin",
        actor=proxied_user,
        key_fingerprint="sha256:webui-session",
        scopes=("*", "admin", "config:write"),
    )


def _api_key_mapping(config: ConfigManager) -> Dict[str, str]:
    """Internal helper to API key mapping for this module."""
    mapping: Dict[str, str] = {}

    user_key = str(config.get("client_api.api_key") or "").strip()
    if user_key:
        mapping[user_key] = "viewer"

    admin_key = str(config.get("client_api.admin_api_key") or "").strip()
    if admin_key:
        mapping[admin_key] = "admin"

    return mapping


def _principal_from_config_store(
    store: Any,
    provided: str,
    *,
    actor: str,
) -> AuthPrincipal | None:
    """Resolve a presented API key via persisted config store (hashed keys)."""
    resolved = store.resolve_api_key(provided)
    if not isinstance(resolved, dict):
        return None

    request_actor = str(actor or "").strip()
    stored_actor = str(resolved.get("actor") or "").strip()
    effective_actor = request_actor if request_actor and request_actor != "unknown" else stored_actor

    return AuthPrincipal(
        user_id=str(resolved.get("user_id") or "anonymous"),
        role=str(resolved.get("role") or "viewer"),
        actor=effective_actor or str(resolved.get("user_id") or "anonymous"),
        key_fingerprint=_normalise_fingerprint(str(resolved.get("key_fingerprint") or "")),
        key_id=str(resolved.get("key_id") or ""),
        scopes=tuple(str(item) for item in (resolved.get("scopes") or []) if str(item).strip()),
    )


def _config_store_principal(request: Request, provided: str) -> AuthPrincipal | None:
    """Resolve a presented API key against persisted chat-client API keys."""
    scope = getattr(request, "scope", None)
    app = scope.get("app") if isinstance(scope, dict) else None
    runtime = getattr(getattr(app, "state", None), "chat_db_runtime", None)
    store = getattr(runtime, "config_store", None)
    if store is None:
        return None

    config = getattr(getattr(app, "state", None), "config", None)
    actor = _request_actor(config, request) if config is not None else "unknown"
    return _principal_from_config_store(store, provided, actor=actor)


async def _authenticate_with_idam_registry(
    config: ConfigManager,
    provided: str,
    *,
    header_name: str,
    request_path: str,
    request_method: str,
) -> AuthPrincipal:
    """Validate `provided` via cloud_dog_idam ProviderRegistry + APIKeyOnlyProvider."""
    key_mapping = _api_key_mapping(config)
    if not key_mapping:
        raise AuthenticationError("API key mapping empty")
    provider = APIKeyOnlyProvider(key_role_mapping=key_mapping, default_role="viewer")
    registry = ProviderRegistry()
    registry.register(provider, priority=10)
    auth_result = await registry.authenticate(
        IDAMAuthRequest(
            auth_type="api_key",
            secret=provided,
            metadata={
                "header_name": header_name,
                "path": request_path,
                "method": request_method,
            },
        )
    )
    return AuthPrincipal(
        user_id=str(getattr(auth_result.user, "user_id", "") or "anonymous"),
        role=str(getattr(auth_result.user, "role", "") or "viewer"),
        actor="unknown",
        key_fingerprint=_normalise_fingerprint(
            str(auth_result.claims.get("fingerprint") or "")
        ),
    )


async def _try_resolve_principal(
    config: ConfigManager,
    request: Request | None,
    *,
    provided: str,
    header_name: str,
    require_actor_from_request: bool,
    config_store: Any | None = None,
    request_actor_fallback: str = "unknown",
) -> AuthPrincipal | None:
    """Return authenticated principal or None (cloud_dog_idam + optional config store)."""
    raw = str(provided or "").strip()
    if not raw:
        return None

    actor = request_actor_fallback
    if request is not None and require_actor_from_request:
        actor = _request_actor(config, request)

    principal: AuthPrincipal | None = None
    if config_store is not None:
        principal = _principal_from_config_store(config_store, raw, actor=actor)
    elif request is not None:
        principal = _config_store_principal(request, raw)

    if principal is not None and actor and actor != "unknown" and principal.actor != actor:
        principal = AuthPrincipal(
            user_id=principal.user_id,
            role=principal.role,
            actor=actor,
            key_fingerprint=principal.key_fingerprint,
            key_id=principal.key_id,
            scopes=principal.scopes,
        )

    if principal is None:
        key_mapping = _api_key_mapping(config)
        if not key_mapping:
            return None
        try:
            principal = await _authenticate_with_idam_registry(
                config,
                raw,
                header_name=header_name,
                request_path=str(request.url.path or "") if request else "",
                request_method=str(request.method or "") if request else "",
            )
        except AuthenticationError:
            return None
        principal = AuthPrincipal(
            user_id=principal.user_id,
            role=principal.role,
            actor=actor,
            key_fingerprint=principal.key_fingerprint,
        )

    configured_admin_key = str(config.get("client_api.admin_api_key") or "").strip()
    if configured_admin_key and raw == configured_admin_key:
        principal = AuthPrincipal(
            user_id=principal.user_id,
            role="admin",
            actor=principal.actor,
            key_fingerprint=principal.key_fingerprint,
            key_id=principal.key_id,
            scopes=tuple(sorted(set(principal.scopes) | {"*", "admin", "config:write"})),
        )

    return principal


def _apply_principal_to_request(request: Request, principal: AuthPrincipal) -> None:
    request.state.principal = {
        "user_id": principal.user_id,
        "role": principal.role,
        "actor": principal.actor,
        "key_fingerprint": principal.key_fingerprint,
        "key_id": principal.key_id,
        "scopes": list(principal.scopes),
    }
    request.state.actor = principal.actor


async def _authenticate_header(
    config: ConfigManager,
    request: Request,
    *,
    header_name: str,
    require_admin_permission: bool,
) -> AuthPrincipal:
    """Internal helper to authenticate header for this module."""
    # Covers: R15, NFR3
    # Safety-critical auth and RBAC gates prevent unauthorised runtime mutation.
    provided = str(request.headers.get(header_name) or "").strip()
    if not provided:
        provided = str(request.cookies.get("chat_client_api_key") or "").strip()
    if not provided:
        if require_admin_permission:
            # CC9 (W28C-1703): admin scope is defence-in-depth — it requires the
            # user credential (X-API-Key) AND the admin-scope header (X-Admin-Key).
            # The bare "Missing X-API-Key" was misleading: an admin caller
            # presenting only one header could not tell BOTH were required.
            raise HTTPException(
                status_code=401,
                detail=(
                    "This endpoint requires X-API-Key (user creds) AND "
                    "X-Admin-Key (admin scope) headers"
                ),
            )
        raise HTTPException(
            status_code=401,
            detail=f"Missing required header: {header_name}",
        )

    principal = await _try_resolve_principal(
        config,
        request,
        provided=provided,
        header_name=header_name,
        require_actor_from_request=True,
    )
    if principal is None:
        key_mapping = _api_key_mapping(config)
        if not key_mapping:
            raise HTTPException(
                status_code=401,
                detail=f"API key authentication is not configured for header: {header_name}",
            )
        if require_admin_permission:
            raise HTTPException(status_code=403, detail="Invalid admin API key")
        raise HTTPException(status_code=403, detail="Invalid API key")

    rbac = build_chat_rbac_engine()
    rbac.assign_role_to_user(principal.user_id, principal.role)

    if require_admin_permission and not (
        rbac.has_permission(principal.user_id, CHAT_CONFIG_WRITE)
        or rbac.has_permission(principal.user_id, MCP_SERVERS_MANAGE)
        or rbac.has_permission(principal.user_id, CHAT_ADMIN_ALL)
        or "config:write" in principal.scopes
        or "*" in principal.scopes
        or "admin" in principal.scopes
    ):
        raise HTTPException(status_code=403, detail="Admin permission required")

    _apply_principal_to_request(request, principal)
    return principal


async def validate_presented_api_key_for_service(
    config: ConfigManager,
    *,
    presented: str,
    header_name: str,
    path: str,
    method: str,
    config_store: Any | None,
    request_actor: str,
) -> bool:
    """
    Return True if the presented secret is accepted by cloud_dog_idam (or config store).

    Used by MCP and A2A surfaces so they do not duplicate inline key comparisons.
    """
    raw = str(presented or "").strip()

    # Match mcp_server._authorised: no user API key in config means auth is not enforced.
    if not str(config.get("client_api.api_key") or "").strip():
        return True
    if not raw:
        return False

    principal = await _try_resolve_principal(
        config,
        None,
        provided=raw,
        header_name=header_name,
        require_actor_from_request=False,
        config_store=config_store,
        request_actor_fallback=request_actor,
    )
    return principal is not None


def principal_has_admin_capability(principal: dict[str, Any]) -> bool:
    """True if the principal may perform admin config operations via cloud_dog_idam RBAC."""
    role = str(principal.get("role") or "").strip().lower()
    scopes = {str(item).strip() for item in (principal.get("scopes") or []) if str(item).strip()}
    uid = str(principal.get("user_id") or "").strip()
    if not uid:
        # Scope-only fallback for anonymous/service tokens.
        return bool({"config:write", "*"} & scopes)
    rbac = build_chat_rbac_engine()
    rbac.assign_role_to_user(uid, role)
    return bool(
        rbac.has_permission(uid, CHAT_CONFIG_WRITE)
        or rbac.has_permission(uid, MCP_SERVERS_MANAGE)
        or rbac.has_permission(uid, CHAT_ADMIN_ALL)
    )


async def require_api_key(config: ConfigManager, request: Request):
    """Handle require API key for the current runtime context."""
    trusted_principal = _trusted_web_admin_principal(config, request)
    if trusted_principal is not None:
        _apply_principal_to_request(request, trusted_principal)
        return

    header_name = str(config.get("client_api.api_key_header") or "X-API-Key")
    await _authenticate_header(
        config,
        request,
        header_name=header_name,
        require_admin_permission=False,
    )


def request_actor(config: ConfigManager, request: Request) -> str:
    """Handle request actor for the current runtime context."""
    state_actor = str(getattr(request.state, "actor", "") or "").strip()
    if state_actor:
        return state_actor
    return _request_actor(config, request)


async def require_admin_key(config: ConfigManager, request: Request) -> str:
    """Handle require admin key for the current runtime context."""
    trusted_principal = _trusted_web_admin_principal(config, request)
    if trusted_principal is not None:
        _apply_principal_to_request(request, trusted_principal)
        return trusted_principal.actor

    header_name = str(
        config.get("client_api.admin_api_key_header")
        or config.get("client_api.api_key_header")
        or "X-API-Key"
    )
    principal = await _authenticate_header(
        config,
        request,
        header_name=header_name,
        require_admin_permission=True,
    )
    return principal.actor
