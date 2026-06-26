---
template-id: T-API
template-version: 1.0
applies-to: docs/API-REFERENCE.md
registry: service
required: must-have
when-applicable: ""
template-last-updated: 2026-06-12
template-owner: platform-standards

project: chat-client
doc-last-updated: 2026-06-12
doc-git-commit: 776e2872e01dabdce4e68383d19d05577601b836
doc-git-branch: main
doc-source-shas: []
doc-age-policy: 90d
doc-conformance-stamp: 2026-06-12T12:00:00Z
---

# chat-client — API-REFERENCE

> **Template version:** T-API v1.0 — REST surface authoritative reference. `openapi.json` is build-generated; this doc explains it.

## 1. Auth model
Auth modes accepted (`api_key`, `cookie`, `vault-bootstrap`), header name, RBAC mapping.

## 2. Routes

**You MUST include:** every route registered by the service. Group by section: Auth / Admin / Data / Health.

| Method | Path | Auth | RBAC | Summary | Request | Response |
|---|---|---|---|---|---|---|
| GET | `/health` | none | n/a | liveness | — | `{status:"ok"}` |

## 3. Error model
Standard error envelope, status codes, retryability.

## 4. Examples
**You MUST include:** at least one worked curl example per route group.

```
curl -H "X-API-Key: ${API_KEY}" https://<host>/api/v1/<route>
```

## 5. Cross-references
- [openapi.json](openapi.json)
- [MCP-REFERENCE.md](MCP-REFERENCE.md)
- [A2A-REFERENCE.md](A2A-REFERENCE.md)
- [WEBUI-REFERENCE.md](WEBUI-REFERENCE.md)
- PS-20-api.md

## 6. Project-specific notes



<!-- W28C-1710a recovery: full content from archive/2026-06-12/API.md (archived sha256=6134b132a4b4, 35 lines) -->

## Recovered domain content — `archive/2026-06-12/API.md` (35 lines)

_This section carries forward the full content of the archived predecessor doc verbatim. Topic checklist + SHA256 chain in `cloud-dog-ai-platform-standards/working/evidence/W28C-1710a/per-doc/chat-client/API.md.topics.tsv`. Archive contents are unchanged (sha256 stable)._

# API

## Canonical Surfaces

| Surface | Default base | Purpose | Auth model |
|---|---|---|---|
| API | `/v1` plus session routes | Chat sessions, profile/config CRUD, jobs, MCP proxy helpers, monitoring, OpenAPI. | API key through the shared IDAM guard; WebUI forwards cookie-backed API key context. |
| WebUI | `/`, `/login`, SPA routes | Browser shell, login, dashboards, consoles, IDAM admin pages. | Cookie login for `admin`, `read-write`, and `read-only`; API-key fallback remains server-side. |
| MCP | `/mcp`, `/webmcp` proxy | MCP protocol and config admin tool parity. | API key or authenticated WebUI session; anonymous health only. |
| A2A | `/a2a`, `/weba2a` proxy | Health and persisted config/session event visibility. | API key or authenticated WebUI session; anonymous health only. |

## Key Endpoint Groups

| Group | Endpoints | Notes |
|---|---|---|
| Health | `/health`, `/ready`, `/live`, `/status` | Available on API, Web, MCP, and A2A runtimes. |
| Auth | `/auth/login`, `/auth/me`, `/auth/logout`, `/login/session` | Web cookie login; runtime config uses `AUTH_MODE: "cookie"` and does not publish `API_KEY_HEADER`. |
| Runtime config | `/runtime-config.js`, `/ui/config`, `/ui/config/tree` | Public SPA bootstrap plus redacted config inspection. |
| Chat sessions | `/sessions`, `/sessions/{session_id}`, `/sessions/{session_id}/messages`, `/sessions/{session_id}/transcript`, `/sessions/{session_id}/preferences` | Chat workflow, transcript, preference, and delete operations. |
| File proxy | `/sessions/{session_id}/mcp/files/upload`, `/upload-multipart`, `/download`, `/download/content` | Delegated file-mcp transfer contract. |
| MCP proxy | `/mcp/servers`, `/mcp/servers/health`, `/sessions/{session_id}/mcp/tools/list`, `/sessions/{session_id}/mcp/tools/call` | Server status and per-session tool access. |
| Config profiles | `/v1/profiles` | Chat profile CRUD with admin-only mutation and authenticated reads. |
| IDAM admin | `/v1/users`, `/v1/groups`, `/v1/api-keys`, `/v1/roles` plus `/v1/admin/*` aliases | Shared IDAM WebUI compatibility routes. |
| Config MCP parity | `/mcp/admin/tools`, `/mcp/admin/tools/call` | Profile/user/group/API-key/role list/get/mutate tools. |
| Config A2A parity | `/a2a/events`, `/a2a/events/stream` | Persisted config change events. |
| Jobs | `/v1/jobs`, `/v1/jobs/{job_id}`, `/v1/jobs/{job_id}/cancel` | Managed MCP/job lifecycle visibility and control. |
| Documentation | `/openapi.json`, `/api-docs` | API schema and browser documentation route. |

## Reconciliation Notes

The pre-W28A-751 docs described the WebUI as API-key-entry first. Current main
implements W28A-727-R5 cookie login by default and intentionally avoids exposing
the API-key header name in public runtime config. API key authentication remains
the backend and MCP/A2A service contract.
