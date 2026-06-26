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

import asyncio
import json
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from cloud_dog_logging import get_audit_logger, get_logger  # type: ignore[import-untyped]
from cloud_dog_logging.audit_schema import AuditEvent, Actor, Target  # type: ignore[import-untyped]

from ..agent.strategy import normalize_profile_session_defaults
from ..config import ConfigManager
from ..database.config_store import ConfigStoreError
from ..servers.common import server_id
from ..ui_spa import serve_spa_index
from .auth import principal_has_admin_capability, require_admin_key, require_api_key

if False:  # pragma: no cover
    from ..database.runtime import ChatDatabaseRuntime


class ChatProfileRequest(BaseModel):
    profile_id: Optional[str] = None
    name: str
    description: str = ""
    mcp_bindings: list[dict[str, Any]] = []
    session_defaults: dict[str, Any] = {}
    access_control: dict[str, Any] = {}


class ChatUserRequest(BaseModel):
    user_id: Optional[str] = None
    display_name: str = ""
    email: str = ""
    role: str = "viewer"
    status: str = "active"
    group_ids: list[str] = []
    metadata: dict[str, Any] = {}


class ChatGroupRequest(BaseModel):
    group_id: Optional[str] = None
    name: str
    description: str = ""
    roles: list[str] = []
    member_user_ids: list[str] = []
    metadata: dict[str, Any] = {}


class ChatAPIKeyRequest(BaseModel):
    key_id: Optional[str] = None
    user_id: Optional[str] = None
    name: str
    scopes: list[str] = []
    metadata: dict[str, Any] = {}


class ChatRoleRequest(BaseModel):
    role_id: Optional[str] = None
    name: str = ""
    description: str = ""
    permissions: list[str] = []


class ChatConfigToolCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = {}

def _principal(request: Request) -> dict[str, Any]:
    """Return the authenticated principal payload for the current request."""
    principal = getattr(request.state, "principal", None)
    return principal if isinstance(principal, dict) else {}


def _is_admin(request: Request) -> bool:
    """Determine whether the current authenticated principal is admin-capable."""
    return principal_has_admin_capability(_principal(request))


async def _auth_dep(config: ConfigManager, request: Request) -> None:
    """Authenticate any authorised caller for read operations."""
    await require_api_key(config, request)


async def _admin_dep(config: ConfigManager, request: Request) -> str:
    """Authenticate admin-only callers for mutating operations."""
    return await require_admin_key(config, request)


async def _event_stream(runtime: "ChatDatabaseRuntime", after_id: int) -> AsyncIterator[str]:
    """Yield server-sent config events from the persisted A2A event log."""
    last_seen = int(after_id or 0)
    while True:
        events = runtime.config_store.list_events(after_id=last_seen, limit=50)
        if events:
            for event in events:
                last_seen = max(last_seen, int(event.get("id") or 0))
                yield f"id: {last_seen}\n"
                yield "event: config_change\n"
                yield f"data: {json.dumps(event)}\n\n"
        await asyncio.sleep(1.0)


_TOOL_SPEC = [
    {"name": "profile_list", "entity": "profile", "verb": "list"},
    {"name": "profile_get", "entity": "profile", "verb": "get"},
    {"name": "profile_create", "entity": "profile", "verb": "create"},
    {"name": "profile_update", "entity": "profile", "verb": "update"},
    {"name": "profile_delete", "entity": "profile", "verb": "delete"},
    {"name": "user_list", "entity": "user", "verb": "list"},
    {"name": "user_get", "entity": "user", "verb": "get"},
    {"name": "user_create", "entity": "user", "verb": "create"},
    {"name": "user_update", "entity": "user", "verb": "update"},
    {"name": "user_delete", "entity": "user", "verb": "delete"},
    {"name": "group_list", "entity": "group", "verb": "list"},
    {"name": "group_get", "entity": "group", "verb": "get"},
    {"name": "group_create", "entity": "group", "verb": "create"},
    {"name": "group_update", "entity": "group", "verb": "update"},
    {"name": "group_delete", "entity": "group", "verb": "delete"},
    {"name": "api_key_list", "entity": "api_key", "verb": "list"},
    {"name": "api_key_create", "entity": "api_key", "verb": "create"},
    {"name": "api_key_revoke", "entity": "api_key", "verb": "revoke"},
    {"name": "role_list", "entity": "role", "verb": "list"},
    {"name": "role_get", "entity": "role", "verb": "get"},
    {"name": "role_create", "entity": "role", "verb": "create"},
    {"name": "role_update", "entity": "role", "verb": "update"},
    {"name": "role_delete", "entity": "role", "verb": "delete"},
]


def build_config_router(*, config: ConfigManager, db_runtime: "ChatDatabaseRuntime") -> APIRouter:
    """Build the config CRUD, login, and A2A router for chat-client."""
    router = APIRouter()
    store = db_runtime.config_store
    admin_logger = get_logger("cloud_dog_chat_api")
    audit_logger = get_audit_logger()

    # PS-92 (W28A-970g-V2): configurable base paths for api / mcp / a2a server surfaces.
    # Literal defaults live in defaults.yaml. Env override via CLOUD_DOG__<SERVER>__BASE_PATH.
    api_base_path = str(config.get("api_server.base_path") or "/v1").rstrip("/") or "/v1"
    mcp_base_path = str(config.get("mcp_server.base_path") or "/mcp").rstrip("/") or "/mcp"
    a2a_base_path = str(config.get("a2a_server.base_path") or "/a2a").rstrip("/") or "/a2a"
    # Covers: CFG-01, CFG-02, CFG-03, CFG-04, CFG-05, CFG-06, CFG-07,
    # CFG-08, CFG-09, CFG-10, CFG-11, CFG-13
    # This router is the persisted config management surface for API, MCP-style
    # tool access, A2A event exposure, and browser login/admin flows.

    async def auth_dep(request: Request) -> None:
        """Authenticate a read-only config CRUD request."""
        await _auth_dep(config, request)

    async def admin_dep(request: Request) -> str:
        """Authenticate an admin config CRUD request."""
        return await _admin_dep(config, request)

    def _request_id(request: Request) -> str:
        request_id = str(getattr(request.state, "request_id", "") or "").strip()
        if request_id:
            return request_id
        return str(request.headers.get("x-request-id") or "").strip()

    def _request_user_ip(request: Request) -> str:
        forwarded_for = str(request.headers.get("x-forwarded-for") or "").strip()
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()
        client = getattr(request, "client", None)
        return str(getattr(client, "host", "") or "").strip()

    def _service_instance() -> str:
        return (
            str(config.get("app.server_id") or "").strip()
            or str(config.get("server.id") or "").strip()
            or str(server_id(config) or "").strip()
            or "unknown"
        )

    def _emit_config_crud_event(
        *,
        request: Request,
        entity_type: str,
        entity_id: str,
        action: str,
        target_name: str = "",
        details: Optional[dict[str, Any]] = None,
        outcome: str = "success",
    ) -> None:
        principal = _principal(request)
        actor_id = str(principal.get("user_id") or principal.get("id") or "unknown").strip() or "unknown"
        role = str(principal.get("role") or "").strip().lower()
        scopes = [str(item).strip() for item in (principal.get("scopes") or []) if str(item).strip()]
        roles = [role] if role else []
        request_id = _request_id(request)
        request_meta = {
            "method": str(request.method or "").upper(),
            "path": str(request.url.path or ""),
            "transport": str(request.url.scheme or ""),
        }
        log_details = dict(details or {})
        log_details["request"] = request_meta
        admin_logger.info(
            "config_crud_action",
            actor=actor_id,
            action=action,
            entity_type=entity_type,
            target_id=entity_id,
            target_name=target_name,
            outcome=outcome,
            client_ip=_request_user_ip(request),
            request_id=request_id,
            event_type=f"config.{entity_type}.{action}",
            details=log_details,
        )
        audit_logger.emit(
            AuditEvent(
                event_type=f"config.{entity_type}.{action}",
                actor=Actor(
                    type="user",
                    id=actor_id,
                    roles=roles or scopes,
                    ip=_request_user_ip(request) or None,
                    user_agent=str(request.headers.get("user-agent") or "").strip() or None,
                ),
                action=action,
                outcome=outcome,
                correlation_id=request_id or None,
                request_id=request_id or None,
                trace_id=request_id or None,
                service=str(config.get("app.name") or "cloud_dog_chat_api"),
                service_instance=_service_instance(),
                environment=str(config.get("app.environment") or "unknown"),
                severity="INFO",
                target=Target(
                    type=entity_type,
                    id=str(entity_id or "").strip(),
                    name=str(target_name or "").strip() or None,
                ),
                details=log_details,
            )
        )

    @router.get("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login_page() -> HTMLResponse:
        """Serve the SPA login entrypoint from ui/dist."""
        return serve_spa_index(config)

    @router.get(f"{api_base_path}/profiles", dependencies=[Depends(auth_dep)])
    async def list_profiles() -> dict[str, Any]:
        """List chat profiles."""
        return {"profiles": store.list_profiles()}

    @router.get(f"{api_base_path}/profiles/{{profile_id}}", dependencies=[Depends(auth_dep)])
    async def get_profile(profile_id: str) -> dict[str, Any]:
        """Get one chat profile."""
        profile = store.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile not found")
        return {"profile": profile}

    @router.post(f"{api_base_path}/profiles", dependencies=[Depends(admin_dep)])
    async def create_profile(payload: ChatProfileRequest, request: Request) -> dict[str, Any]:
        """Create one chat profile."""
        data = payload.model_dump()
        try:
            data["session_defaults"] = normalize_profile_session_defaults(
                data.get("session_defaults")
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        profile = store.create_profile(data)
        _emit_config_crud_event(
            request=request,
            entity_type="profile",
            entity_id=str(profile.get("profile_id") or ""),
            target_name=str(profile.get("name") or ""),
            action="create",
            details={"profile": profile},
        )
        return {"profile": profile}

    @router.put(f"{api_base_path}/profiles/{{profile_id}}", dependencies=[Depends(admin_dep)])
    async def update_profile(profile_id: str, payload: ChatProfileRequest, request: Request) -> dict[str, Any]:
        """Update one chat profile."""
        data = payload.model_dump(exclude_unset=True)
        if "session_defaults" in data:
            try:
                data["session_defaults"] = normalize_profile_session_defaults(
                    data.get("session_defaults")
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            profile = store.update_profile(profile_id, data)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _emit_config_crud_event(
            request=request,
            entity_type="profile",
            entity_id=str(profile.get("profile_id") or profile_id),
            target_name=str(profile.get("name") or ""),
            action="update",
            details={"profile": profile},
        )
        return {"profile": profile}

    @router.delete(f"{api_base_path}/profiles/{{profile_id}}", dependencies=[Depends(admin_dep)])
    async def delete_profile(profile_id: str, request: Request) -> dict[str, Any]:
        """Delete one chat profile."""
        try:
            profile = store.delete_profile(profile_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _emit_config_crud_event(
            request=request,
            entity_type="profile",
            entity_id=str(profile.get("profile_id") or profile_id),
            target_name=str(profile.get("name") or ""),
            action="delete",
            details={"profile": profile},
        )
        return {"deleted": True, "profile": profile}

    @router.get(f"{api_base_path}/users", dependencies=[Depends(auth_dep)])
    async def list_users(request: Request) -> dict[str, Any]:
        """List users, filtered to self for non-admin callers."""
        principal = _principal(request)
        items = store.list_users()
        if not _is_admin(request):
            items = [item for item in items if str(item.get("user_id") or "") == str(principal.get("user_id") or "")]
        return {"users": items}

    @router.get(f"{api_base_path}/users/{{user_id}}", dependencies=[Depends(auth_dep)])
    async def get_user(user_id: str, request: Request) -> dict[str, Any]:
        """Get one user, with self-only access for non-admin callers."""
        principal = _principal(request)
        if not _is_admin(request) and str(principal.get("user_id") or "") != str(user_id):
            raise HTTPException(status_code=403, detail="read access limited to own user")
        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        return {"user": user}

    @router.post(f"{api_base_path}/users", dependencies=[Depends(admin_dep)])
    async def create_user(payload: ChatUserRequest, request: Request) -> dict[str, Any]:
        """Create one user."""
        user = store.create_user(payload.model_dump())
        _emit_config_crud_event(
            request=request,
            entity_type="user",
            entity_id=str(user.get("user_id") or ""),
            target_name=str(user.get("display_name") or ""),
            action="create",
            details={"user": user},
        )
        return {"user": user}

    @router.put(f"{api_base_path}/users/{{user_id}}", dependencies=[Depends(admin_dep)])
    async def update_user(user_id: str, payload: ChatUserRequest, request: Request) -> dict[str, Any]:
        """Update one user."""
        try:
            user = store.update_user(user_id, payload.model_dump(exclude_unset=True))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _emit_config_crud_event(
            request=request,
            entity_type="user",
            entity_id=str(user.get("user_id") or user_id),
            target_name=str(user.get("display_name") or ""),
            action="update",
            details={"user": user},
        )
        return {"user": user}

    @router.delete(f"{api_base_path}/users/{{user_id}}", dependencies=[Depends(admin_dep)])
    async def delete_user(user_id: str, request: Request) -> dict[str, Any]:
        """Delete one user."""
        try:
            user = store.delete_user(user_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _emit_config_crud_event(
            request=request,
            entity_type="user",
            entity_id=str(user.get("user_id") or user_id),
            target_name=str(user.get("display_name") or ""),
            action="delete",
            details={"user": user},
        )
        return {"deleted": True, "user": user}

    @router.get(f"{api_base_path}/groups", dependencies=[Depends(auth_dep)])
    async def list_groups() -> dict[str, Any]:
        """List groups."""
        return {"groups": store.list_groups()}

    @router.get(f"{api_base_path}/groups/{{group_id}}", dependencies=[Depends(auth_dep)])
    async def get_group(group_id: str) -> dict[str, Any]:
        """Get one group."""
        group = store.get_group(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="group not found")
        return {"group": group}

    @router.post(f"{api_base_path}/groups", dependencies=[Depends(admin_dep)])
    async def create_group(payload: ChatGroupRequest, request: Request) -> dict[str, Any]:
        """Create one group."""
        group = store.create_group(payload.model_dump())
        _emit_config_crud_event(
            request=request,
            entity_type="group",
            entity_id=str(group.get("group_id") or ""),
            target_name=str(group.get("name") or ""),
            action="create",
            details={"group": group},
        )
        return {"group": group}

    @router.put(f"{api_base_path}/groups/{{group_id}}", dependencies=[Depends(admin_dep)])
    async def update_group(group_id: str, payload: ChatGroupRequest, request: Request) -> dict[str, Any]:
        """Update one group."""
        try:
            group = store.update_group(group_id, payload.model_dump(exclude_unset=True))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _emit_config_crud_event(
            request=request,
            entity_type="group",
            entity_id=str(group.get("group_id") or group_id),
            target_name=str(group.get("name") or ""),
            action="update",
            details={"group": group},
        )
        return {"group": group}

    @router.delete(f"{api_base_path}/groups/{{group_id}}", dependencies=[Depends(admin_dep)])
    async def delete_group(group_id: str, request: Request) -> dict[str, Any]:
        """Delete one group."""
        try:
            group = store.delete_group(group_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _emit_config_crud_event(
            request=request,
            entity_type="group",
            entity_id=str(group.get("group_id") or group_id),
            target_name=str(group.get("name") or ""),
            action="delete",
            details={"group": group},
        )
        return {"deleted": True, "group": group}

    @router.get(f"{api_base_path}/api-keys", dependencies=[Depends(auth_dep)])
    async def list_api_keys(request: Request) -> dict[str, Any]:
        """List API keys, filtered to self for non-admin callers."""
        principal = _principal(request)
        user_id = None if _is_admin(request) else str(principal.get("user_id") or "")
        return {"api_keys": store.list_api_keys(user_id=user_id or None)}

    @router.post(f"{api_base_path}/api-keys", dependencies=[Depends(admin_dep)])
    async def create_api_key(payload: ChatAPIKeyRequest, request: Request) -> dict[str, Any]:
        """Create one API key and return the clear-text secret once."""
        try:
            item = store.create_api_key(payload.model_dump())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _emit_config_crud_event(
            request=request,
            entity_type="api_key",
            entity_id=str(item.get("key_id") or ""),
            target_name=str(item.get("name") or ""),
            action="create",
            details={"api_key": {key: value for key, value in item.items() if key != "api_key"}},
        )
        return {"api_key": item}

    @router.delete(f"{api_base_path}/api-keys/{{key_id}}", dependencies=[Depends(admin_dep)])
    async def revoke_api_key(key_id: str, request: Request) -> dict[str, Any]:
        """Revoke one API key."""
        try:
            item = store.revoke_api_key(key_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _emit_config_crud_event(
            request=request,
            entity_type="api_key",
            entity_id=str(item.get("key_id") or key_id),
            target_name=str(item.get("name") or ""),
            action="revoke",
            details={"api_key": item},
        )
        return {"revoked": True, "api_key": item}

    # Roles (PS-71 §IW3A; W28A-876 Gate 4b). Backed by the canonical
    # cloud_dog_idam SqlAlchemyRoleStore. Path mirrors the other admin entities
    # ({api_base_path}/<entity>); externally Traefik strips the /api prefix, so
    # this surfaces at /api/v1/roles for browser/proxy callers.
    @router.get(f"{api_base_path}/roles", dependencies=[Depends(auth_dep)])
    async def list_roles() -> dict[str, Any]:
        """List roles in the PS-71 §IW3A.1 shape."""
        return {"roles": store.list_roles()}

    @router.get(f"{api_base_path}/roles/{{role_id}}", dependencies=[Depends(auth_dep)])
    async def get_role(role_id: str) -> dict[str, Any]:
        """Get one role."""
        role = store.get_role(role_id)
        if role is None:
            raise HTTPException(status_code=404, detail="role not found")
        return {"role": role}

    @router.post(f"{api_base_path}/roles", dependencies=[Depends(admin_dep)])
    async def create_role(payload: ChatRoleRequest, request: Request) -> dict[str, Any]:
        """Create one role with its permission set."""
        try:
            role = store.create_role(payload.model_dump())
        except ConfigStoreError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        _emit_config_crud_event(
            request=request,
            entity_type="role",
            entity_id=str(role.get("role_id") or ""),
            target_name=str(role.get("name") or ""),
            action="create",
            details={"role": role},
        )
        return {"role": role}

    @router.put(f"{api_base_path}/roles/{{role_id}}", dependencies=[Depends(admin_dep)])
    async def update_role(role_id: str, payload: ChatRoleRequest, request: Request) -> dict[str, Any]:
        """Update one role (PUT)."""
        try:
            role = store.update_role(role_id, payload.model_dump(exclude_unset=True))
        except ConfigStoreError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        _emit_config_crud_event(
            request=request,
            entity_type="role",
            entity_id=str(role.get("role_id") or role_id),
            target_name=str(role.get("name") or ""),
            action="update",
            details={"role": role},
        )
        return {"role": role}

    @router.patch(f"{api_base_path}/roles/{{role_id}}", dependencies=[Depends(admin_dep)])
    async def patch_role(role_id: str, payload: ChatRoleRequest, request: Request) -> dict[str, Any]:
        """Update one role (PATCH; partial update of description/permissions)."""
        try:
            role = store.update_role(role_id, payload.model_dump(exclude_unset=True))
        except ConfigStoreError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        _emit_config_crud_event(
            request=request,
            entity_type="role",
            entity_id=str(role.get("role_id") or role_id),
            target_name=str(role.get("name") or ""),
            action="update",
            details={"role": role},
        )
        return {"role": role}

    @router.delete(f"{api_base_path}/roles/{{role_id}}", dependencies=[Depends(admin_dep)])
    async def delete_role(role_id: str, request: Request) -> dict[str, Any]:
        """Delete one role (baseline admin/user roles are protected, 403)."""
        try:
            role = store.delete_role(role_id)
        except ConfigStoreError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        _emit_config_crud_event(
            request=request,
            entity_type="role",
            entity_id=str(role.get("role_id") or role_id),
            action="delete",
            details={"role": role},
        )
        return {"deleted": True, "role": role}

    # PS-71 §IW3A / W28A-876 Gate 5: shared @cloud-dog/idam admin pages call
    # `/api/v1/admin/<entity>` (users/groups/api-keys/roles). Externally Traefik
    # strips the `/api` prefix, so they reach the backend at
    # `{api_base_path}/admin/<entity>` (= `/v1/admin/<entity>`) — a path the
    # canonical CRUD surface above does NOT serve (it lives at
    # `{api_base_path}/<entity>`). Register `/admin/<entity>` ALIASES that bind
    # the exact same handler closures (same store, same auth deps, same logic),
    # so the shared pages resolve against the backend instead of 404-ing.
    _admin_alias_routes: list[tuple[str, str, Any, list[Any]]] = [
        # (suffix-under-/admin, http method, handler, dependencies)
        ("/users", "GET", list_users, [Depends(auth_dep)]),
        ("/users/{user_id}", "GET", get_user, [Depends(auth_dep)]),
        ("/users", "POST", create_user, [Depends(admin_dep)]),
        ("/users/{user_id}", "PUT", update_user, [Depends(admin_dep)]),
        ("/users/{user_id}", "DELETE", delete_user, [Depends(admin_dep)]),
        ("/groups", "GET", list_groups, [Depends(auth_dep)]),
        ("/groups/{group_id}", "GET", get_group, [Depends(auth_dep)]),
        ("/groups", "POST", create_group, [Depends(admin_dep)]),
        ("/groups/{group_id}", "PUT", update_group, [Depends(admin_dep)]),
        ("/groups/{group_id}", "DELETE", delete_group, [Depends(admin_dep)]),
        ("/api-keys", "GET", list_api_keys, [Depends(auth_dep)]),
        ("/api-keys", "POST", create_api_key, [Depends(admin_dep)]),
        ("/api-keys/{key_id}", "DELETE", revoke_api_key, [Depends(admin_dep)]),
        ("/roles", "GET", list_roles, [Depends(auth_dep)]),
        ("/roles/{role_id}", "GET", get_role, [Depends(auth_dep)]),
        ("/roles", "POST", create_role, [Depends(admin_dep)]),
        ("/roles/{role_id}", "PUT", update_role, [Depends(admin_dep)]),
        ("/roles/{role_id}", "PATCH", patch_role, [Depends(admin_dep)]),
        ("/roles/{role_id}", "DELETE", delete_role, [Depends(admin_dep)]),
    ]
    for _suffix, _method, _handler, _deps in _admin_alias_routes:
        router.add_api_route(
            f"{api_base_path}/admin{_suffix}",
            _handler,
            methods=[_method],
            dependencies=_deps,
            include_in_schema=False,
        )

    @router.get(f"{a2a_base_path}/events", dependencies=[Depends(auth_dep)])
    async def list_a2a_events(after_id: int = 0, limit: int = 100) -> dict[str, Any]:
        """List persisted config change events through the A2A interface."""
        return {"events": store.list_events(after_id=after_id, limit=limit)}

    @router.get(f"{a2a_base_path}/events/stream", dependencies=[Depends(auth_dep)])
    async def stream_a2a_events(after_id: int = 0) -> StreamingResponse:
        """Stream config change events as a server-sent A2A feed."""
        return StreamingResponse(
            _event_stream(db_runtime, after_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get(f"{mcp_base_path}/admin/tools", dependencies=[Depends(auth_dep)])
    async def list_config_tools() -> dict[str, Any]:
        """Expose config CRUD tool metadata for MCP-style admin parity."""
        return {"tools": list(_TOOL_SPEC)}

    @router.post(f"{mcp_base_path}/admin/tools/call", dependencies=[Depends(auth_dep)])
    async def call_config_tool(payload: ChatConfigToolCallRequest, request: Request) -> dict[str, Any]:
        """Dispatch config CRUD actions through a single tool-call endpoint."""
        name = str(payload.name or "").strip()
        args = dict(payload.arguments or {})
        if name.endswith("_list"):
            if name == "profile_list":
                return {"result": store.list_profiles()}
            if name == "user_list":
                items = store.list_users()
                if not _is_admin(request):
                    principal = _principal(request)
                    items = [item for item in items if str(item.get("user_id") or "") == str(principal.get("user_id") or "")]
                return {"result": items}
            if name == "group_list":
                return {"result": store.list_groups()}
            if name == "api_key_list":
                principal = _principal(request)
                user_id = None if _is_admin(request) else str(principal.get("user_id") or "")
                return {"result": store.list_api_keys(user_id=user_id or None)}
        if name.endswith("_get"):
            lookup = str(args.get("id") or args.get("profile_id") or args.get("user_id") or args.get("group_id") or "").strip()
            if not lookup:
                raise HTTPException(status_code=400, detail="tool lookup id is required")
            if name == "profile_get":
                result = store.get_profile(lookup)
            elif name == "user_get":
                result = store.get_user(lookup)
            elif name == "group_get":
                result = store.get_group(lookup)
            else:
                result = None
            if result is None:
                raise HTTPException(status_code=404, detail="tool target not found")
            return {"result": result}
        if not _is_admin(request):
            raise HTTPException(status_code=403, detail="admin permission required")
        if name == "profile_create":
            return {"result": store.create_profile(args)}
        if name == "profile_update":
            return {"result": store.update_profile(str(args.get("profile_id") or args.get("id") or ""), args)}
        if name == "profile_delete":
            return {"result": store.delete_profile(str(args.get("profile_id") or args.get("id") or ""))}
        if name == "user_create":
            return {"result": store.create_user(args)}
        if name == "user_update":
            return {"result": store.update_user(str(args.get("user_id") or args.get("id") or ""), args)}
        if name == "user_delete":
            return {"result": store.delete_user(str(args.get("user_id") or args.get("id") or ""))}
        if name == "group_create":
            return {"result": store.create_group(args)}
        if name == "group_update":
            return {"result": store.update_group(str(args.get("group_id") or args.get("id") or ""), args)}
        if name == "group_delete":
            return {"result": store.delete_group(str(args.get("group_id") or args.get("id") or ""))}
        if name == "api_key_create":
            return {"result": store.create_api_key(args)}
        if name == "api_key_revoke":
            return {"result": store.revoke_api_key(str(args.get("key_id") or args.get("id") or ""))}
        raise HTTPException(status_code=404, detail=f"unsupported config tool: {name}")

    return router
