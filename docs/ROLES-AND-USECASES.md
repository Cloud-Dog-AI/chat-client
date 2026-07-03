---
template-id: T-RUC
template-version: 1.1
applies-to: docs/ROLES-AND-USECASES.md
registry: service
required: must-have
when-applicable: ""
template-last-updated: 2026-06-12
template-owner: public-standards
extends-version: 1.0
extends-via: PS-REQ-TEST-TRACE v1.0

project: chat-client
doc-last-updated: 2026-06-23
doc-git-commit: 5a120f91a1d400859ec3e1af24d5aa7eeaa4c24a
doc-git-branch: main
doc-source-shas: []
doc-age-policy: indefinite
doc-conformance-stamp: 2026-06-18T00:00:00Z
---

# Roles and Use Cases

## Role Catalogue

| Role | Scope | Source | Effective permission intent | Reconciliation |
|---|---|---|---|---|
| `admin` | central flat/WebUI and API | `src/cloud_dog_chat_client/servers/web_flat_roles.py`, `src/cloud_dog_chat_client/api/auth.py` | Full administration, wildcard or admin config permissions. | AGREE with `CFG-13`. |
| `read-write` | chat-client flat WebUI | `src/cloud_dog_chat_client/servers/web_flat_roles.py` | Shared `user` baseline plus chat use permissions and config read. | AGREE with WebUI operator use. |
| `read-only` | chat-client flat WebUI | `src/cloud_dog_chat_client/servers/web_flat_roles.py` | Shared `user` baseline only; write methods on data paths return 403. | AGREE with `CFG-13`. |
| `viewer` | API/config-store user | `src/cloud_dog_chat_client/api/auth.py`, `src/cloud_dog_chat_client/database/config_store.py` | API access, conversation list/history/message read-send floor. | FLAGGED: legacy API role name maps to Thread-A flat `read-only` only on the WebUI surface. |
| `user` | central baseline | `cloud_dog_idam` role store via `ConfigStore.ensure_roles_seed()` | Baseline shared user grant for central role pages. | AGREE with Thread-B central catalog consumption. |
| `group-admin` | central keystone | IDAM Thread-B W28A-741 | Group-scoped administration through central resource-aware bindings. | CONSUMED; no service-local FK added. |
| `restricted` | central keystone | IDAM Thread-B W28A-741 | Default-deny/reduced access role. | CONSUMED; enforced by shared guard/API key role resolution. |
| `job-control` | central keystone | IDAM Thread-B W28A-741 | Job read/control grant. | CONSUMED on `/v1/jobs` and WebUI Jobs parity. |
| `audit-log` | central keystone | IDAM Thread-B W28A-741 | Audit/log read grant. | CONSUMED on monitoring/audit surfaces. |

## W28E-1801A Use-Case Catalogue

| Use case | Primary actor | Role(s) | Surfaces | Requirement mapping | Test-design owner | Notes |
|---|---|---|---|---|---|---|
| `UC-001` WebUI login, dashboard, and canonical navigation | Operator | `admin`, `read-write`, `read-only` | `webui`, `api` | `FR-001`, `FR-015`, `CS-007`, `CS-010`, `CS-013` | `AT-WEBUI-CANONICAL-NAV`, `T0-LIVE-SMOKE-CL34` | Covers CL-04..CL-18 dashboard cleanup, profile navigation visibility, canonical console links, and runtime-config secrecy. |
| `UC-002` Browser chat submit and response | Chat operator | `read-write`, `admin` | `webui`, `api` | `FR-014`, `FR-016`, `FR-017` | `AT-WEBUI-CHAT-SUBMIT-CL26`, `ST-WEBUI-FLOW` | CL-26 requires browser proof: composer fill, Send click, backend POST, visible prompt, and assistant response. Curl-only proof is rejected. |
| `UC-003` Harness injects conversation into a session | Test operator | `admin`, `read-write` | `api`, `a2a`, `webui` | `FR-012`, `FR-013`, `CS-001`, `CS-003` | `UT-HARNESS-FLOW`, `ST-HARNESS-ROUTES`, `AT-HARNESS-A2A` | From Test-Design-Audit-Jun26: inject prompts/responses, pause for operator, continue, and attach both sides to session/profile or joined existing session. |
| `UC-004` Profile/session/chat workspace operation | Chat operator | `read-only`, `read-write`, `admin` | `webui`, `api` | `FR-016`, `FR-001` | `AT-WEBUI-SESSION-MODEL` | Covers CL-19..CL-22, CL-29, and CL-31 model clarity: profile picker, filtered sessions, new session, and transcript refresh on switch. |
| `UC-005` External services and canonical consoles | Operator | `read-only`, `read-write`, `admin` | `webui`, `api`, `mcp`, `a2a` | `FR-015`, `FR-011` | `AT-WEBUI-CANONICAL-NAV`, `IT-MCP-CONFORMANCE` | Covers CL-09, CL-10, CL-12, CL-23, CL-32, and CL-33: External Services wording and canonical MCP/A2A console routing. |
| `UC-006` Chat operations, errors, downloads, and model test | Chat operator | `read-write`, `admin` | `webui`, `api` | `FR-017`, `NF-004` | `AT-WEBUI-CANONICAL-NAV` | Covers CL-25, CL-27, CL-28, and CL-30; Stream-B/C must extend browser assertions for user/time headers, history download, retry, and LLM/model test action. |
| `UC-007` Live IDAM cascade and denial handling | Admin and constrained users | `admin`, `read-write`, `read-only`, `anon` | `api`, `mcp`, `a2a`, `webui` | `FR-007`, `CS-001`..`CS-013` | `T1-T3-LIVE-IDAM` | Preserves W28A-751 live cascade and negative-flow ownership across roles and surfaces. |
| `UC-008` Four-sentinel post-deploy browser smoke | Release verifier | authorised browser user | `webui`, `api`, `mcp`, `a2a` | `NF-001`, `FR-010`, `FR-015` | `T0-LIVE-SMOKE-CL34` plus Stream-C aggregate smoke | CL-34 requires browser smoke across `chatclient0`, `expertagent0`, `notificationagent0`, and `filemcpserver0` after UX work is in flight. |

## b-3 Traceability Matrix

| Req | Entity / Action | Use Case From `REQUIREMENTS.md` | Role | Surface | Test ID | Reconciliation |
|---|---|---|---|---|---|---|
| R7 | ChatSession / create message | `Must support: Streaming endpoint(s)` | `read-write` | API/WebUI | T3-BUS-SESSION | AGREE |
| R7 | ChatSession / read transcript | `Must expose transcript retrieval for conversation validation.` | `read-only`, `read-write`, `admin` | API/WebUI | T2-RBAC-READONLY | AGREE |
| R7 | ChatSession / delete | `Must support session deletion via API and Web UI controls.` | `read-write`, `admin` | API/WebUI | T2-RBAC-READONLY | AGREE |
| R7 | MCP tools / list-call | `Must expose MCP tool list/call endpoints for MCP validation.` | `read-write`, `admin` | API/MCP/WebUI | T1-COMMON-IDAM, T3-BUS-MCP | AGREE |
| R7 | Jobs / read-control | `Must expose managed job status endpoints for MCP proxy tracking` | `job-control`, `admin` | API/WebUI | T3-BUS-JOBS | AGREE |
| R7 | Runtime config / read | `Must expose a redacted full-config endpoint for UI inspection` | `read-only`, `read-write`, `admin` | API/WebUI | T1-COMMON-IDAM | AGREE |
| R7.1 | Four server health | `The service MUST support a split 4-server runtime` | anonymous for health, authorised for data | API/MCP/A2A/WebUI | T0-SMOKE | AGREE |
| R7.2 | File transfer proxy / upload-download | `Chat API must proxy file upload/download operations to configured file-mcp servers.` | `read-write`, `admin` | API/WebUI/MCP proxy | T3-BUS-FILES | AGREE |
| R16.1 | SPA route / render | `The Web UI MUST include routed pages for: dashboard, chat, sessions, MCP servers, tools, API docs, settings.` | `read-only`, `read-write`, `admin` | WebUI | T0-SMOKE, T2-RBAC-READONLY | AGREE; current routes include additional Profiles, Files, Monitoring, MCP/A2A Console, Jobs, and IDAM pages. |
| R16.2 | Runtime config / bootstrap | `The backend MUST serve /runtime-config.js.` | anonymous | WebUI | T0-SMOKE | AGREE after W28A-727-R5; public `API_KEY_HEADER` injection is superseded by cookie auth. |
| R16.3 | Login / session | `The UI MUST support API-key-based authentication controls.` | `admin`, `read-write`, `read-only` | WebUI | T1-COMMON-IDAM | FLAGGED: current preprod default is cookie login with API-key fallback, not public API-key entry. |
| R16.4 | API docs / OpenAPI | `The UI API docs route MUST render content from the OpenAPI schema served by the backend.` | `read-only`, `read-write`, `admin` | WebUI/API | T0-SMOKE | AGREE |
| R16.5 | Streaming failure UX | `Streaming failures MUST surface visible errors in the chat UI.` | `read-write`, `admin` | WebUI/API | T3-BUS-SESSION | AGREE |
| R16.6 | MCP UX / server state | `The MCP servers page MUST display configured servers with health/status information.` | `read-only`, `read-write`, `admin` | WebUI/API/MCP | T3-BUS-MCP | AGREE |
| R16.7 | Settings / redacted config | `The settings/config view MUST display redacted configuration.` | `read-only`, `read-write`, `admin` | WebUI/API | T1-COMMON-IDAM | AGREE |
| CFG-01 | ChatProfile / create | `The system SHALL support creating a new chat profile via the API with all profile settings that would otherwise be available via environment variables or env-file configuration.` | `admin` | API/MCP/WebUI | T3-BUS-CASCADE | AGREE |
| CFG-02 | ChatProfile / read | `The system SHALL support reading chat profiles via the API, including both list and detail retrieval.` | authorised roles | API/MCP/WebUI | T3-BUS-CASCADE | AGREE |
| CFG-03 | ChatProfile / update | `The system SHALL support updating an existing chat profile via the API.` | `admin` | API/MCP/WebUI | T3-BUS-CASCADE | AGREE |
| CFG-04 | ChatProfile / delete | `The system SHALL support deleting a chat profile via the API.` | `admin` | API/MCP/WebUI | T3-BUS-CASCADE | AGREE |
| CFG-05 | Config tools / profile CRUD | `Chat profile CRUD operations SHALL be available as MCP tools with equivalent functionality.` | `admin` | MCP/API | T3-BUS-MCP | AGREE |
| CFG-06 | Config events / publish | `Chat profile change events SHALL be broadcast via the A2A interface per PS-72 §A2A-change-events` | authorised roles | A2A/API | T3-BUS-A2A | AGREE on persisted event feed; canonical PS-72 envelope compatibility is tracked by central API kit. |
| CFG-07 | Profiles page / CRUD | `Chat profile CRUD operations SHALL be available in the WebUI with RBAC enforcement.` | `admin`, read roles view | WebUI/API | T2-RBAC-READONLY, T3-BUS-CASCADE | AGREE |
| CFG-08 | Users / CRUD | `The system SHALL support creating, reading, updating, and deleting users via the API.` | `admin`, self-read for non-admin | API/WebUI/MCP | T3-BUS-CASCADE | AGREE |
| CFG-09 | Groups / CRUD roles | `The system SHALL support creating, reading, updating, and deleting groups with role assignments via the API.` | `admin` | API/WebUI/MCP | T3-BUS-CASCADE | AGREE |
| CFG-10 | API keys / create-list-revoke | `The system SHALL support creating, listing, and revoking API keys with per-key capability scoping via the API.` | `admin`, self-list for non-admin | API/WebUI/MCP | T3-BUS-CASCADE | AGREE |
| CFG-11 | IDAM admin parity | `User, group, and API-key management SHALL be available via MCP, A2A, and WebUI with RBAC.` | `admin`, constrained read roles | API/MCP/A2A/WebUI | T3-BUS-CASCADE | AGREE for API/MCP/WebUI and A2A change visibility. |
| CFG-12 | Audit / event persistence | `All CRUD operations SHALL be audit logged with user identity, action, timestamp, and outcome.` | `audit-log`, `admin` | API/A2A/WebUI monitoring | T3-BUS-A2A | AGREE |
| CFG-13 | Enforcement / admin-only mutate | `Only admin users SHALL be able to create, update, and delete chat profiles and manage users or groups; read-only access SHALL be available to authorised non-admin users.` | `admin`, `read-only` | API/MCP/WebUI | T2-RBAC-READONLY | AGREE |
| IDAM-CASCADE | Group membership / API-key principal | `add a user, they use the system by their role across all surfaces, the cascade works` | `admin`, group-derived role | API/MCP/A2A/WebUI | T3-BUS-CASCADE | AGREE: chat-client consumes central Thread-B by resolving group roles when API keys authenticate and by not adding service-local profile FKs. |

## b-5 WebUI Page and Action Justification

| Page / action | Route | Use case | Role | API parity | Status |
|---|---|---|---|---|---|
| Dashboard | `/` | R16.1 dashboard | authorised roles | `/status`, `/ui/monitoring` | mapped |
| Chat | `/chat` | R7 streaming and session operation | `read-write`, `admin`; read view for `read-only` | `/sessions/{id}/messages`, transcript endpoints | mapped |
| Sessions | `/sessions` | R7 list/read/delete sessions | authorised read; write roles delete | `/sessions`, `/sessions/{id}` | mapped |
| Profiles | `/profiles` | CFG-01..07 profile CRUD | `admin` mutate, read roles view | `/v1/profiles`, `/mcp/admin/tools/call`, `/a2a/events` | mapped |
| External Services | `/mcp-servers` | R16.6 server status/config | read roles view; admin manage | `/mcp/servers`, `/mcp/servers/health` | mapped |
| Tools | `/tools` | R7 MCP tool list/call validation | `read-write`, `admin`; read-only list | `/sessions/{id}/mcp/tools/list`, call endpoints | mapped |
| File Browser | `/files` | R7.2 delegated file transfer | `read-write`, `admin`; read-only download/list | file upload/download proxy endpoints | mapped |
| Monitoring | `/monitoring` | CFG-12 and NFR4 observability | `audit-log`, `admin`, authorised read | `/metrics`, `/ui/monitoring`, `/audit` | mapped |
| API Docs | `/api-docs` | FR-P002 and R16.4 OpenAPI docs | authorised roles | `/openapi.json` | mapped |
| MCP Console | `/mcp-console` | CFG-05/R7 MCP tool parity | `admin` for config tools, write roles for session tools | `/mcp/admin/tools`, `/webmcp/*` | mapped |
| A2A Console | `/a2a-console` | CFG-06 config event visibility | authorised roles | `/a2a/events`, `/weba2a/events` | mapped |
| Jobs | `/jobs` | R7 managed job tracking | `job-control`, `admin`, authorised read | `/v1/jobs` | mapped |
| Settings | `/settings` | R16.7 redacted config | authorised roles | `/ui/config/tree` | mapped |
| IDAM Users | `/idam/users`, legacy `/admin/users` | CFG-08 user management | `admin`, self-read for non-admin | `/v1/users`, `/v1/admin/users` | mapped |
| IDAM Groups | `/idam/groups`, legacy `/admin/groups` | CFG-09 group management | `admin` | `/v1/groups`, `/v1/admin/groups` | mapped |
| IDAM API Keys | `/idam/api-keys`, legacy `/admin/api-keys` | CFG-10 API-key lifecycle | `admin`, self-list for non-admin | `/v1/api-keys`, `/v1/admin/api-keys` | mapped |
| IDAM Roles | `/idam/roles` | central role catalogue administration | `admin` | `/v1/roles` | mapped |
| IDAM RBAC | `/idam/rbac`, legacy `/admin/rbac` | central binding/cascade administration | `admin`, `group-admin` | central IDAM/admin routes | mapped |
| About action | shell dialog, no route | release/support metadata | authorised roles | package/runtime version | action-only; not an orphan page |

There is no orphan WebUI page: every first-level navigation item and legacy
alias maps to a requirement row and a backend surface. `/about` is a shell
action rendered as a dialog, not a routed page.



<!-- W28C-1710b design-delta additions (2026-06-14T18:01:23Z) -->

## Cross-surface UC mappings (W28C-1710b)

Per T-RUC v1.1 + PS-REQ-TEST-TRACE §3.5, every UC-NNN maps to one OR MORE FR-NNN across surfaces.

This service's surface set: **api, mcp, webui**.

Detailed UC-by-UC operator-review pass + per-FR cross-surface mapping deferred to W28C-1711. The cross-surface declarations are enabled here.

```yaml
# Schema for every UC-NNN (default; operator amends per UC):
surfaces: ['api', 'mcp', 'webui']
roles: [admin, read-write, read-only, anon]
FR-mapping: []  # populated by W28C-1711
```
