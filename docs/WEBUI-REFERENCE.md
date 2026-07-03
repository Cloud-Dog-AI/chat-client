---
template-id: T-WUI
template-version: 1.0
applies-to: docs/WEBUI-REFERENCE.md
registry: service
required: conditional
when-applicable: "chat-client ships a React SPA at ui/dist served by the WebUI server process"
template-last-updated: 2026-06-12
template-owner: public-standards

project: chat-client
doc-last-updated: 2026-06-18
doc-git-commit: e90ac9d3bf1dab0bef345fa9dfc45c6937dae386
doc-git-branch: w28c-1715-fix-docs
doc-source-shas:
  - src/cloud_dog_chat_client/ui_spa.py
  - src/cloud_dog_chat_client/servers/web_flat_roles.py
  - src/cloud_dog_chat_client/servers/web_server.py
doc-age-policy: 90d
doc-conformance-stamp: 2026-06-18T00:00:00Z
---

# chat-client — WEBUI-REFERENCE

> **Template version:** T-WUI v1.0 — conditional: service has a WebUI panel.

## 1. Panel structure

The chat-client WebUI is a React SPA (built artefact at `ui/dist`) served by
the dedicated WebUI server process (`servers/web_server.py`). All routes below
are registered in `_SPA_ENTRY_ROUTES` (`src/cloud_dog_chat_client/ui_spa.py`)
and served as the SPA entry-point (`ui/dist/index.html`).

| Route | Panel | Roles | Backend route |
|---|---|---|---|
| `/` | Dashboard — service health, session summary | All authenticated | `/status`, `/ui/monitoring` |
| `/login` | Login — username + password cookie auth | Anonymous (public) | `POST /auth/login` |
| `/chat` | Chat — send messages, streaming responses | `read-write`, `admin` (view-only for `read-only`) | `POST /sessions/{id}/messages`, `GET /sessions/{id}/transcript` |
| `/sessions` | Sessions — list, view, delete sessions | All authenticated (write roles delete) | `GET /sessions`, `DELETE /sessions/{id}` |
| `/profiles` | Chat Profiles — CRUD profile management | `admin` mutate; read roles view | `GET/POST/PUT/DELETE /v1/profiles` |
| `/mcp-servers` | External Services — configured MCP server health | All authenticated; `admin` manage | `GET /mcp/servers`, `GET /mcp/servers/health` |
| `/tools` | Tools — list and call MCP tools for active session | `read-write`, `admin` call; `read-only` list | `GET /sessions/{id}/mcp/tools/list`, `POST /sessions/{id}/mcp/tools/call` |
| `/docs` or `/api-docs` | API Docs — OpenAPI schema rendered in-browser | All authenticated | `GET /openapi.json` |
| `/jobs` | Jobs — view managed background job status | All authenticated (via `job-control` / `admin`) | `GET /v1/jobs` |
| `/settings` | Settings — redacted effective-config display | All authenticated | `GET /api/v1/admin/effective-config` |
| `/admin` | Admin — top-level admin navigation | `admin` | — |
| `/admin/rbac` | RBAC — permission/role assignment overview | `admin` | IDAM RBAC routes |
| `/admin/users` | Users — user CRUD | `admin` | `GET/POST/PUT/DELETE /v1/admin/users` |
| `/admin/groups` | Groups — group CRUD with role assignment | `admin` | `GET/POST/PUT/DELETE /v1/admin/groups` |
| `/admin/api-keys` | API Keys — create/list/revoke API keys | `admin` | `GET/POST/DELETE /v1/admin/api-keys` |
| `/admin/roles` | Roles — role catalog view | `admin` | IDAM roles routes |
| `/idam/users` | IDAM Users (canonical PS-71 shared route) | `admin` | shared `@cloud-dog/idam` component |
| `/idam/groups` | IDAM Groups (canonical PS-71 shared route) | `admin` | shared `@cloud-dog/idam` component |
| `/idam/roles` | IDAM Roles (canonical PS-71 shared route) | `admin` | shared `@cloud-dog/idam` component |
| `/idam/api-keys` | IDAM API Keys (canonical PS-71 shared route) | `admin` | shared `@cloud-dog/idam` component |
| `/idam/rbac` | IDAM RBAC (canonical PS-71 shared route) | `admin` | shared `@cloud-dog/idam` component |
| `/mcp-console` | MCP Console — raw JSON-RPC tool calls via WebUI proxy | `read-write`, `admin` | `POST /webmcp` |
| `/a2a-console` | A2A Console — event feed view | All authenticated | `GET /weba2a/events` |
| `/monitoring` | Monitoring — audit log and metric dashboards | `admin`, `audit-log` | `GET /ui/monitoring` |
| `/files` | Files — file upload/download proxy to file-mcp | `read-write`, `admin` | `GET/POST /v1/files/*` |

## 2. Login

**Flow:** The chat-client WebUI uses **cookie-based authentication** (`AUTH_MODE: "cookie"`).
Username and password are submitted to `POST /auth/login`, which sets an
`HttpOnly` session cookie (`chat_client_api_key`). There is no public API-key
entry form in the default configuration.

Steps:
1. Browser loads `/login` — served as the public SPA shell (no auth required).
2. User enters username + password.
3. SPA `POST /auth/login` → server validates credentials, sets cookie on success, returns 200.
4. SPA stores session state in-memory; `AUTH_MODE = "cookie"` means subsequent API calls carry the cookie automatically.
5. Logout: `POST /auth/logout` clears the session cookie.

**Session timeout:** Configurable via `session.timeout_minutes` (default 30 minutes;
floor 5 minutes). The SPA shows a warning 5 minutes before expiry
(`SESSION_WARNING_MINUTES: 5` injected via `/runtime-config.js`).

**Runtime-config:** The SPA bootstraps via `/runtime-config.js` which injects
`window.__RUNTIME_CONFIG__` with `API_BASE_URL`, `MCP_BASE_URL`,
`A2A_EVENTS_URL`, `A2A_WS_URL`, `AUTH_MODE`, `APP_VERSION`, and
`SESSION_TIMEOUT_MINUTES`. The `API_KEY_HEADER` key is NOT advertised
in cookie mode (W28A-727-R5).

**Flat roles:** Three flat roles are enforced via `web_flat_roles.py`:
- `admin` — full access, wildcard permission (`*`).
- `read-write` — shared `user` baseline plus chat-use permissions (`chat:message:send`, `chat:history:read`, `chat:conversation:list`, `chat:conversation:delete`, `api:access`, `config:read`).
- `read-only` — shared `user` baseline only; all POST/PUT/PATCH/DELETE on data paths return 403.

## 3. RBAC visibility matrix

| Panel | admin | read-write | read-only | anonymous |
|---|---|---|---|---|
| Login | public | public | public | public |
| Dashboard | full | full | full | — |
| Chat | full (send + manage) | send + view | view only (transcript) | — |
| Sessions | full (delete any) | view + delete own | view only | — |
| Profiles | full CRUD | view only | view only | — |
| External Services | full (manage + test) | view | view | — |
| Tools | full (list + call) | list + call | list only | — |
| API Docs | view | view | view | — |
| Jobs | full (cancel + control) | view | view | — |
| Settings | view + redacted config | view + redacted config | view + redacted config | — |
| Admin (all `/admin/*`) | full | — | — | — |
| IDAM (all `/idam/*`) | full | — | — | — |
| MCP Console | full | call + list | list only | — |
| A2A Console | full | view | view | — |
| Monitoring | full | — | — | — |
| Files | full | upload + download | — | — |

Write-gate enforcement: `is_write_gated_data_path()` in `web_flat_roles.py`
applies a 403 on POST/PUT/PATCH/DELETE for `read-only` principals on all data
prefixes (`/api`, `/v1`, `/webapi`, `/weba2a`, `/a2a`, `/webmcp`, `/mcp`,
`/messages`, `/sessions`, `/admin`, `/events`, `/tasks`, `/profiles`).
Auth/login endpoints and health probes are explicitly excluded from the write gate.

## 4. Static routes

The following routes are registered as SPA entry-points in `_SPA_ENTRY_ROUTES`
and served as `ui/dist/index.html` (public — browser renders the SPA shell
before authentication so the login page works correctly):

```
/index.html  /login  /ui  /dashboard  /chat  /sessions  /profiles
/mcp-servers  /tools  /docs  /api-docs  /jobs  /settings  /admin
/admin/rbac  /admin/users  /admin/groups  /admin/api-keys  /admin/roles
/idam/users  /idam/groups  /idam/roles  /idam/api-keys  /idam/rbac
/mcp-console  /a2a-console  /monitoring  /files
```

**Anon-gate trap (AGENT-LESSONS):** All SPA entry-routes are served publicly
so the login box renders before authentication. A previous lane accidentally
blocked `/login` itself, preventing the login page from loading. Do not add
auth gates to routes in `_SPA_ENTRY_ROUTES`.

## 5. Cross-references
- [API-REFERENCE.md](API-REFERENCE.md)
- [ROLES-AND-USECASES.md](ROLES-AND-USECASES.md)
- PS-77-webui-comprehensive.md
- PS-30-ui.md

## 6. Project-specific notes

The WebUI is built from `cloud-dog-ai-ui-monorepo/apps/chat-client` and the
compiled output is committed at `ui/dist`. The build artefact version is
validated via tree-SHA ancestry (not vite rebuild, which is non-deterministic
due to toolchain drift — see W28K-1409 AGENT-LESSONS).

The WebUI server (`servers/web_server.py`) also proxies:
- `GET/POST /webmcp/*` → internal MCP server (auth-gated, session-cookie forwarded)
- `GET/POST /weba2a/*` → internal A2A server (auth-gated)
- `GET /webapi/*` → internal API server (auth-gated)

These proxy paths allow the SPA to call all three backend servers through a
single WebUI origin, avoiding CORS complexity behind Traefik.
