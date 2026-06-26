---
template-id: T-REQ
template-version: 1.1
applies-to: docs/REQUIREMENTS.md
project: chat-client
doc-last-updated: 2026-06-23T00:00:00Z
doc-git-commit: 5a120f91a1d400859ec3e1af24d5aa7eeaa4c24a
doc-git-branch: main
doc-age-policy: indefinite
doc-conformance-stamp: 2026-06-12T16:35:09Z
req-trace-version: 1.0
req-id-prefixes-used: [SV, BO, BR, FR, UC, CS, NF, R, F]
surface-coverage: [api, mcp, a2a, webui, cli, internal]
---

# Requirements

## Document Status

This file is the active canonical requirements source for chat-client. It was
de-dated and reconciled during W28A-751; historical topic documents are retained
under `docs/archive/w28a-751-canonicalisation/`.

## Scope

This repository provides a chat orchestration service that combines:

- LLM-backed responses (Ollama/OpenAI-compatible)
- MCP server integration over multiple transports
- Session persistence and API/UI operations
- Real-system validation across QT/UT/ST/IT/AT tiers

## Numbering and Standards Mapping

Functional requirement identifiers remain stable as `R1..R16`, `FR-P00x`, and `R-DB-xx` to preserve existing test/code traceability IDs. Platform standards coverage is tracked in `README.md` and quality suites (`tests/quality/QT_COMPLIANCE`).

## Functional Requirements

### R1. CLI Chat Client

- Provide a CLI entrypoint to run interactive chat.
- Support non-streaming and streaming responses.
- Support config-driven selection of LLM provider and model.
- Provide a default system prompt configurable per environment.

### R2. LLM Provider Support (Ollama + OpenAI-Compatible)

- Support an Ollama base URL (HTTP/S) and model name.
- Support streaming responses.
- Support configurable request timeouts.
- Support OpenAI-compatible endpoints with API key auth.
- Ollama must use the remote endpoint `https://llm1.your-domain.com`.
- Validate model coverage for `qwen3:14b`, `granite4:tiny-h`, and `gemma3:12b`.
- Support OpenRouter via OpenAI-compatible settings with `qwen/qwen3-14b`.

### R3. MCP Client Core Capabilities

- Must implement MCP initialization lifecycle (`initialise` then `notifications/initialized`).
- Must support:
  - `tools/list`
  - `tools/call`
  - `resources/list` (best-effort)
  - `resources/read` (best-effort)

### R4. MCP Transport Support

Transport selection must be config-driven and support:

- **Legacy HTTP JSON-RPC**: POST to `/messages` (health endpoint configurable).
- **Streamable HTTP**: `/mcp` POST + SSE stream for responses, including session termination (DELETE).
- **HTTP+SSE (deprecated)**: `/sse` + POST message endpoint.
- **STDIO**: subprocess stdio transport for local servers and docker-run servers.

### R5. MCP Conformance Harness

- Provide a single parameterized conformance suite that can run against multiple MCP targets.
- Targets must be defined by config (env/YAML), including:
  - Server name
  - Transport type
  - Connection details (base URL for HTTP, or command/args/env for STDIO)
  - Optional docker image + args/env/mounts (prebuilt-only)
- Conformance checks must be deterministic:
  - `initialise`
  - `tools/list`
  - `tools/call` with deterministic selection (prefer zero-arg tools; else require config specifying tool name + arguments)
  - `resources/*` best-effort

### R5.1 External MCP Coverage

- Provide tests against external MCP servers (e.g., Flight Search MCP at `https://flights.fctolabs.com/mcp`, Search MCP at `https://searchmcp0.your-domain.com`).

### R6. Docker-Based MCP Server Execution (Tests)

- Tests may start MCP servers in Docker **only using prebuilt images**.
- Tests must verify the required image exists locally before attempting to run it.
- Docker execution must be deterministic, and containers must be cleaned up reliably.

### R7. Local Client API Server

- Provide an HTTP API server runnable from CLI (`cloud-dog-chat api`) and as the dedicated API runtime in the 4-server deployment pattern.
- Must support:
  - Streaming endpoint(s)
  - Non-streaming endpoint(s)
- Must support config-driven API key authentication (header name + value).
- Must expose transcript retrieval for conversation validation.
- Must support session deletion via API and Web UI controls.
- Must expose MCP tool list/call endpoints for MCP validation.
- Must expose managed job status endpoints for MCP proxy tracking (`/api/v1/jobs`, `/api/v1/jobs/{job_id}`).
- Must expose MCP connectivity health checks for configured servers (red/green readiness).
- Must support backend MCP orchestration in `/sessions/{session_id}/messages` when session MCP servers are selected.
- MCP proxy operations must register managed jobs via `cloud_dog_jobs` so long-running external tool calls are observable by job ID and session ID.
- Backend MCP orchestration must support role-based server behaviour (`mcp.servers[].assist_role`), including:
  - search-style grounding context collection from selected search MCP servers.
  - translator flow (`assist_role=translator`) using MCP tools (`start_session` + `chat`) and returning translator output in `/messages` responses for translation-intent prompts.
- Must expose Web UI endpoints (`/ui`, `/ui/config`) for browser-based sessions.
- Web UI must support session swap with restoration of prior transcript/history when returning to an existing session.
- Web UI must show an in-flight request indicator with elapsed-time ticker while a message is pending.
- Web UI wait/ticker timeout must be configurable (default 300s) for long-running MCP/LLM responses.
- Web UI must provide a switchable panel that displays scrollable runtime/session log output for operator troubleshooting.
- Must expose a redacted full-config endpoint for UI inspection (`/ui/config/tree`) where sensitive values are masked.
- Must surface a simple, append-only log for session history and audit.

### R7.1 Four-Server Runtime Pattern

- The service MUST support a split 4-server runtime with dedicated listeners for:
  - API on port `8050`
  - Web on port `8051`
  - MCP on port `8052`
  - A2A on port `8053`
- The Web server MUST serve `/login`, `/ui`, and related UI/config pages separately from the API listener.
- The MCP server MUST expose `create_session`, `send_message`, `list_sessions`, and `get_history` as MCP tools.
- The A2A server MUST expose health plus session/message/config event fanout via polling and WebSocket endpoints.
- `server_control.sh` MUST manage `api`, `web`, `mcp`, `a2a`, and `all`.

### R7.2 MCP File Transfer Proxy

- The API server MUST expose authenticated file transfer helpers backed by the configured file-mcp server.
- Upload MUST support:
  - `POST /sessions/{session_id}/mcp/files/upload` with JSON/base64 payloads.
  - `POST /sessions/{session_id}/mcp/files/upload` with URL-reference payloads (`source_url`) that the chat-client fetches and forwards to file-mcp.
  - `POST /sessions/{session_id}/mcp/files/upload-multipart` with `multipart/form-data`.
- Download MUST support:
  - `POST /sessions/{session_id}/mcp/files/download` returning JSON with `content_base64`.
  - `GET /sessions/{session_id}/mcp/files/download/content` returning streamed file bytes with a browser-safe `Content-Disposition`.
- File storage and retrieval MUST be executed through file-mcp tools, not by direct local file writes from the browser client.
- File transfer operations MUST require normal API authentication and respect the active session's MCP server selection or explicit file-mcp server index.
- File transfer limits MUST be config-driven under `client_api.file_transfer.*`, including fetch timeout, maximum upload size, and allowed remote URL schemes.
- File transfer failures MUST surface meaningful HTTP status codes, including:
  - `401/403` for auth and allowlist/permission failures
  - `404` for missing files
  - `413` for oversize uploads
  - `502` for upstream file-mcp or remote-fetch failures

### R7.3 MCP Chat File Attachments And Artifact Links

- The main chat interface MUST allow a user or profile-driven workflow to attach a file to a chat message by value and by reference:
  - by value: browser file upload or JSON/base64 payload routed through the authenticated MCP file transfer proxy.
  - by reference: a policy-controlled file path, file-mcp path, or approved URL/source reference resolved through the authenticated MCP file transfer proxy.
- File-bearing chat messages MUST preserve attachment metadata in the session history, including filename or logical name, MIME type when known, size when known, storage path or source reference, upload method, and associated profile/session.
- The active chat profile MUST be able to declare file intake behaviour, including whether uploads are allowed, which MCP service receives files, allowed upload/reference modes, and maximum accepted size.
- Agent or MCP responses that create or return file artifacts MUST be rendered in the chat transcript using the shared `FileArtifactCard`/download-action pattern from `@cloud-dog/ui`.
- Markdown and profile-driven responses MUST be able to include a safe download link for returned artifacts. Links MUST resolve through authenticated chat-client download routes, not raw internal file paths or direct storage URLs.
- Returned artifact links MUST support Markdown/report workflows such as "confirm to a Markdown/profile a link to download" by rendering a visible title, path/reference, source MCP service, and download action in the transcript.
- Chat file upload/download MUST remain backed by file-mcp or the configured MCP service. The browser client MUST NOT write directly to local service storage.
- Chat file handling MUST follow platform standards PS-78/FH-07 and PS-94/FT-04/FT-05, using base64 JSON-safe MCP envelopes and explicit file reference fields.

### R8. CLI Entry Point + Env Defaults

- Provide a command-line script named `chat-client`.
- The CLI must accept `--env` and also support a standard env file location when `--env` is not provided.
- The standard env file location must be deterministic and documented.

### R9. Session Persistence + Restore

- Allow saving a session context snapshot to a file.
- Allow loading a context file at startup.
- Allow restoring a prior session by session id (append to existing session log).
- Session switching must preserve history per session and allow returning to prior session state without data loss.

### R10. Server-Only / Test-Server Mode

- Provide CLI options to run the API server without any UI.
- Provide an explicit “test server” CLI entry point/alias to start API-only mode.

### R11. Installation / Run Script

- Provide a simple script to install the chat-client locally.
- Provide a script to run the chat-client CLI.

### R12. Docker Image Build

- Provide a Dockerfile for building the chat-client image.
- Provide a `docker-build.sh` path to build that image in the same manner as other components.

### R13. Logging + Overrides

- All activity and conversations must be logged to standard user/system logs.
- CLI must support additional overrides for env-file settings (command-line key/value overrides).
- Logs must support audit correlation across:
  - Request metadata (IP, path, method, status).
  - Session activity (session id, user message, assistant message, MCP tool-call/result events, timestamps).

### R14. Container Runtime + Operations

- Provide container runtime options for:
  - API host/port binding.
  - Web host/port binding.
  - MCP host/port binding.
  - A2A host/port binding.
  - SSH enablement and SSH port/user credentials or authorized key.
  - Host-network execution for local validation environments.
- `/health` must return runtime identity fields: application name and active env file path.
- Runtime configuration must expose a deterministic `server_id` used in logs and managed-job metadata.
- All-in-one container mode MUST start API/Web/MCP/A2A together and keep per-server logs.

### R15. MCP Server Administration (RBAC)

- Runtime MCP server add/update/remove operations must be authorisation-gated.
- Non-admin roles must not be able to mutate MCP server configuration.
- Admin actions must be auditable in logs with actor identity and timestamp.

### R16. Monorepo Web UI Closeout (`UI-P5-CHAT-REQ`)

The React UI at `cloud-dog-ai-ui-monorepo/apps/chat-client` is an in-scope delivery surface and must satisfy the following.

#### R16.1 Routes and Pages

- The app must expose routable pages for:
  - `/`
  - `/chat`
  - `/sessions`
  - `/mcp-servers`
  - `/tools`
  - `/files`
  - `/monitoring`
  - `/api-docs`
  - `/mcp-console`
  - `/a2a-console`
  - `/jobs`
  - `/settings`
  - `/admin/rbac`
  - `/admin/users`
  - `/admin/groups`
  - `/admin/api-keys`
- The app must expose `/login` as the unauthenticated sign-in page.
- Root route `/` must render the dashboard page.
- `/dashboard` must redirect to `/`.
- `/docs` must redirect to `/api-docs`.
- `/admin` must redirect to `/admin/rbac`.
- Unknown routes must redirect to `/`.

#### R16.2 Runtime Config Contract

- Runtime config must be loaded from browser runtime config (`window.__RUNTIME_CONFIG__`) and include:
  - `API_BASE_URL`
  - `AUTH_MODE`
  - optional `APP_VERSION`
- When `AUTH_MODE` is `cookie`, runtime config must not expose API-key secrets or the API-key header name.
- UI must not hardcode backend host/port in code paths that perform API calls; backend base URL is runtime-config driven.

#### R16.3 Auth Expectations

- UI auth mode is cookie login by default, with flat `admin`, `read-write`, and `read-only` roles.
- Backend API/MCP/A2A service authentication remains API-key based; proxied browser requests attach server-side credentials from the authenticated WebUI session or explicit API-key caller.
- API-key auth failures must surface as explicit UI errors; no silent success/fallback.

#### R16.4 Backend API Contract Expectations

- UI must call real backend endpoints for:
  - `GET /ui/config`
  - `GET /ui/config/tree`
  - `GET /sessions`
  - `POST /sessions`
  - `DELETE /sessions/{session_id}`
  - `GET /sessions/{session_id}/transcript`
  - `POST /sessions/{session_id}/messages/stream`
  - `GET /mcp/servers`
  - `GET /mcp/servers/health`
  - `POST /sessions/{session_id}/mcp/tools/list`
  - `POST /sessions/{session_id}/mcp/tools/call`
  - `GET /sessions/{session_id}/preferences`
  - `PUT /sessions/{session_id}/preferences`
- Session create/switch/delete behaviour must remain consistent with backend session persistence.

#### R16.5 Streaming and Failure Behaviour

- Chat send must use streaming endpoint and append incremental deltas.
- While a request is active, UI must show in-flight/busy state with elapsed-time ticker.
- Stream timeout behaviour must be bounded by config (`client_api.ui_wait_timeout_seconds`, default 300s).
- Backend or stream failures must be shown as explicit UI errors and must not be masked by fake success responses.

#### R16.6 MCP UX Behaviour

- MCP servers page must display health status (red/green/unknown) from backend health endpoint.
- MCP selection must be session-scoped and persisted through backend session preferences APIs.
- Tools page must support listing tools and executing tool calls with JSON arguments against selected server/session.

#### R16.7 Settings and Config Visibility

- Settings page must include:
  - masked API key handling
  - LLM runtime settings visibility
  - redacted global config tree viewer from backend (`/ui/config/tree`)
- Sensitive values must remain redacted in UI-exposed config payloads.

#### R16.8 Accessibility Requirements

- UI must pass automated accessibility checks (WCAG 2A/2AA via Playwright + axe-core).
- `npm run a11y -- --filter=@cloud-dog/app-chat-client` must execute at least one real accessibility test (not placeholder/no-op).
- Keyboard navigation must remain functional across main views and navigation controls.

### FR-P001: No-Auth Mode

- The server SHALL support a no-auth mode (`API_KEY_REQUIRED=false`) for local development.
- In no-auth mode, API key validation SHALL be bypassed on all endpoints.

### FR-P002: OpenAPI Specification

- The API server SHALL expose an OpenAPI 3.x specification at `/openapi.json`.
- The specification SHALL describe REST endpoints, request/response schemas, and authentication requirements.

### FR-P003: Notification MCP Integration

- The client SHALL support notification-agent connectivity via MCP transport.
- Notification MCP integration SHALL support notifications triggered by chat operations.

### R-DB-01. Database access abstraction

- All database access MUST use `cloud_dog_db` engine/session/CRUD abstractions.

### R-DB-02. Engine creation

- Engine creation MUST use `cloud_dog_db.engine.build_sync_engine()` via project DB runtime.

### R-DB-03. Session management

- Session management MUST use `cloud_dog_db.session.SyncSessionManager`.

### R-DB-04. Migration runner

- Schema migrations MUST use `cloud_dog_db.migrations` runner.

### R-DB-05. Forbidden direct DB APIs

- Direct `sqlite3`/`create_engine()`/`sessionmaker()`/raw `Session()` are FORBIDDEN in application code.

### R-DB-06. DB readiness probe

- DB health readiness MUST use `cloud_dog_db.health.probe_database()`.

### R-DB-07. DB config contract

- DB connection config MUST come from `cloud_dog_config` + env contract (`CLOUD_DOG_DB__*` and `CLOUD_DOG__DB__*`).

### R-DB-08. Multi-dialect schema versioning

- Schema versioning MUST be tested across SQLite, MySQL, and PostgreSQL.

### R-DB-09. Multi-dialect migration validation

- Schema upgrade/downgrade MUST be validated with at least two migrations per dialect.

### R-DB-10. Multi-dialect CRUD consistency

- CRUD outcomes MUST be consistent across SQLite, MySQL, and PostgreSQL.

## Non-Functional Requirements

### NFR1. Real Systems / No Mocks

- Integration and acceptance tests must use real systems (e.g., real MCP servers, real Ollama).
- Avoid mocks for network/system integrations unless explicitly allowed by `RULES.md`.

### NFR2. Determinism

- Tests must be repeatable and stable:
  - fixed tool inputs
  - stable test data / mounts
  - bounded timeouts

### NFR3. Safety

- Follow `RULES.md` strictly.
- Test utilities must not mutate external systems unless explicitly intended and configured.

### NFR4. Observability

- Provide clear error reporting per target (which target failed and why).
- Ensure subprocess/docker failures surface actionable diagnostics.

### NFR5. Configuration

- Configuration must be layered:
  - `defaults.yaml` base
  - optional `config.yaml`
  - optional env file via `--env` / `CLOUD_DOG__APP__ENV_FILE`
- Config lifecycle must be implemented through `cloud_dog_config` (no bespoke env/config loaders).
- Runtime config state is immutable after load; mutable runtime concerns must be modelled as separate in-memory state.

## Traceability to Tests

See `docs/TESTS.md` for the full mapping and env profiles.

## Configuration CRUD Requirements (CFG)

Profile concept for this project: chat profiles with MCP service bindings, session defaults, retention controls, and WebUI preferences.

| ID | Requirement |
|----|-------------|
| CFG-01 | The system SHALL support creating a new chat profile via the API with all profile settings that would otherwise be available via environment variables or env-file configuration. |
| CFG-02 | The system SHALL support reading chat profiles via the API, including both list and detail retrieval. |
| CFG-03 | The system SHALL support updating an existing chat profile via the API. |
| CFG-04 | The system SHALL support deleting a chat profile via the API. |
| CFG-05 | Chat profile CRUD operations SHALL be available as MCP tools with equivalent functionality. |
| CFG-06 | Chat profile change events SHALL be broadcast via the A2A interface per **PS-72 §A2A-change-events** (canonical envelope `{type, topic, timestamp, payload}`; reference implementation `cloud_dog_api_kit.a2a.events` ≥0.11.0; see platform-standards `docs/standards/PS-72-agent-to-agent.md`). |
| CFG-07 | Chat profile CRUD operations SHALL be available in the WebUI with RBAC enforcement. |
| CFG-08 | The system SHALL support creating, reading, updating, and deleting users via the API. |
| CFG-09 | The system SHALL support creating, reading, updating, and deleting groups with role assignments via the API. |
| CFG-10 | The system SHALL support creating, listing, and revoking API keys with per-key capability scoping via the API. |
| CFG-11 | User, group, and API-key management SHALL be available via MCP, A2A, and WebUI with RBAC. |
| CFG-12 | All CRUD operations SHALL be audit logged with user identity, action, timestamp, and outcome. |
| CFG-13 | Only admin users SHALL be able to create, update, and delete chat profiles and manage users or groups; read-only access SHALL be available to authorised non-admin users. |


## W28A-883 PS-78 Cross-Platform File Handling Addendum

### Verified current state

- The API already proxies file transfer through `/sessions/{session_id}/mcp/files/upload`, `/sessions/{session_id}/mcp/files/upload-multipart`, `/sessions/{session_id}/mcp/files/download`, and `/sessions/{session_id}/mcp/files/download/content`.
- JSON/base64, multipart, and URL-backed upload inputs are already implemented in the chat-client API layer.
- The WebUI `FileBrowserPage` already uses `FileDropZone` and download actions against the delegated file-mcp runtime.
- The main `ChatPage` does not currently expose file attachment controls or file-result rendering inside the chat transcript.

### Required additions to satisfy PS-78

- Define the delegated PS-78 contract explicitly: chat-client file handling is service-owned API/WebUI behaviour backed by file-mcp storage.
- Add file attachment controls directly to the chat composer and transcript.
- Add A2A skills or payload conventions for file-bearing agent-to-agent chat tasks.
- Surface returned file artifacts from tool calls in the main conversation UI, not only in the separate file browser.
- Add Markdown/profile response rendering for authenticated artifact download links returned by MCP or agent workflows.

### Required PS-78 test plan

- API: JSON/base64 upload, multipart upload, URL-source upload, JSON download, streamed download.
- MCP proxy: verify upload/download against file-mcp with base64 payloads.
- A2A: transfer file references or base64 file payloads between chat agents.
- WebUI: `FileDropZone` upload, browser refresh, download action, RBAC.
- Chat: attach a file to a message, confirm the downstream tool receives it, and verify returned artifacts are downloadable from the chat surface.
- Chat Markdown/profile: return an MCP/agent-created Markdown artifact, render the authenticated download link in the transcript, and verify the downloaded content matches the artifact.

## W28A-895 UI Standards Merge

This section records the verified chat-client page inventory and merges the current shared WebUI standards into page-level requirements. Status means:

- `EXISTING`: requirement is already represented by the current page inventory and shared component usage.
- `NEW`: requirement is newly made explicit by the current standards merge and is not satisfied by the current source as-audited.

### Verified Page Inventory

- Authenticated route-backed pages: `/`, `/chat`, `/sessions`, `/mcp-servers`, `/tools`, `/files`, `/monitoring`, `/api-docs`, `/mcp-console`, `/a2a-console`, `/jobs`, `/settings`, `/admin/rbac`, `/admin/users`, `/admin/groups`, `/admin/api-keys`
- Unauthenticated page: `/login`
- Redirects: `/dashboard -> /`, `/docs -> /api-docs`, `/admin -> /admin/rbac`, `* -> /`
- `About` is currently a shell dialog action, not a route-backed page

### Page-Level Standards Requirements

| Page | Route | Interaction Family | Applicable Standards / Pattern | Requirement | Status |
|---|---|---|---|---|---|
| LoginPage | `/login` | Shell/pane | Shared auth surface | Sign-in MUST use the shared `LoginPage` pattern with explicit auth error handling. | EXISTING |
| DashboardPage | `/` | Shell/pane | PS-77 dashboard | Dashboard MUST use shared summary patterns for service health, metrics, quick actions, and recent activity. | EXISTING |
| ChatPage | `/chat` | Shell/pane | chat interaction surface | Chat MUST keep shared transcript and tool-inspection patterns (`ChatTimeline`, `ToolCallPanel`) with session-scoped MCP selection and in-flight status. | EXISTING |
| SessionsPage | `/sessions` | List/detail | PS-77 list/detail | Sessions MUST use shared tabular list/detail patterns for create, open, delete, and restore flows. | EXISTING |
| McpHealthPage | `/mcp-servers` | List/detail | PS-77 list/detail/status | MCP server administration MUST use shared table/detail/status-card patterns with real health refresh. | EXISTING |
| ToolsPage | `/tools` | Shell/pane | PS-79, PS-81, PS-84 | Search-driven tool discovery MUST use `SearchPanel`; editable JSON arguments MUST use `CodeEditor`; structured tool result/schema inspection MUST use `JsonExplorer` or `CodeViewer` as appropriate. | NEW |
| FileBrowserPage | `/files` | Tree/workspace | tree/workspace family | File operations MUST use shared file workspace patterns for navigation, upload, delete, and download. | EXISTING |
| MonitoringPage | `/monitoring` | Shell/pane | PS-77 audit/log review | Monitoring MUST use shared runtime-health and audit/log review patterns with sortable/filterable tabular output. | EXISTING |
| UsersPage | `/admin/users` | List/detail | PS-77 admin CRUD | User administration MUST use shared table/dialog CRUD patterns and related-entity panels. | EXISTING |
| GroupsPage | `/admin/groups` | List/detail | PS-77 admin CRUD | Group administration MUST use shared table/dialog CRUD patterns and related-entity panels. | EXISTING |
| ApiKeysPage | `/admin/api-keys` | List/detail | PS-77 admin CRUD | API key administration MUST use shared table/dialog CRUD patterns and ownership panels. | EXISTING |
| AdminPage | `/admin/rbac` | List/detail | PS-77 admin/RBAC | RBAC administration MUST use shared list/detail patterns for role definitions and assignment workflows. | EXISTING |
| DocsPage | `/api-docs` | Viewer/editor | PS-74 amended | API and service docs MUST use `ApiDocsPanel`, tabbed supporting tables, and `DocumentViewer`. | EXISTING |
| McpConsolePage | `/mcp-console` | Shell/pane | console workbench | MCP console access MUST use the shared `McpConsole` workbench pattern. | EXISTING |
| A2aConsolePage | `/a2a-console` | Shell/pane | console workbench | A2A console access MUST use the shared `A2aConsole` workbench pattern. | EXISTING |
| JobsPageView | `/jobs` | List/detail | PS-77, PS-81 | Jobs queue views MUST use shared list/detail patterns, and nested full-record inspection MUST use `JsonExplorer` instead of `JsonBlock`. | NEW |
| SettingsPage | `/settings` | Viewer/editor | PS-73, PS-81 | Settings MUST use `SettingsPanel`, and nested config / LLM / health payload inspection MUST use `JsonExplorer` instead of `JsonBlock`. | NEW |

## PS-40 / W28A-619 Logging and Audit Requirements

The service MUST use `cloud_dog_logging` as the only application and audit logging implementation. Raw stdlib logging setup, direct `logging.getLogger()` calls, bespoke audit emitters, and print-based operational logging are not compliant except inside the platform logging package itself.

Every auditable event MUST emit a PS-40/NIST AU-3 audit record with: `event_type`, `action`, `timestamp`, `service`, `component`, `service_instance`, `environment`, `source_host`, `source_process`, `source_application`, `source_address` where available, `destination_address` where available, `outcome`, actor identity including user/service/system plus account/process/device identifiers where available, `target`, `process_id`, `affected_files` where relevant, `correlation_id`, `trace_id`, and `request_id`.

Auditable events MUST include authentication and authorisation decisions, user/group/API-key/RBAC changes, chat/session/message/file/artifact access, MCP/A2A/API calls, job lifecycle changes, configuration changes, data access and mutation, denials, failures, and privileged operations. Secrets MUST be redacted before persistence. Tests MUST cover schema fields, event coverage, redaction, append-only audit persistence, retention/integrity, and WebUI observability rendering/filtering.

## 5. Cyber Security & Negative Flows

Mandatory schema per PS-REQ-TEST-TRACE v1.0 §3.4. Every project covers anon-denied, wrong-role-denied, missing-param-error per declared surface. The CS rows below are platform-baseline; project-specific extensions append in §5.1.

| ID | Threat / negative scenario | Surface | Role(s) attempted | Expected | Tests |
|---|---|---|---|---|---|
| `CS-001` | Anon attempts data read | `api`, `mcp`, `a2a`, `webui` | `anon` | `401` | (to be bound in Instruction 4 by operator) |
| `CS-002` | read-only attempts write | `api`, `mcp` | `read-only` | `403` | (to be bound in Instruction 4 by operator) |
| `CS-003` | Missing required param | `api` | `admin` | `422` | (to be bound in Instruction 4 by operator) |
| `CS-004` | Wrong-role privileged op | `mcp` | `read-write` | `403` | (to be bound in Instruction 4 by operator) |


<!-- W28C-1710b design-delta additions (2026-06-14T18:01:23Z); SHA chain in working/W28C-1710b/KNOWLEDGE-PRESERVATION-DELTA.md -->

## PS-REQ-TEST-TRACE schema completion (W28C-1710b)

Per the binding contract (`docs/standards/PS-REQ-TEST-TRACE.md` §2 + §3), every FR-NNN row in this file declares the following schema (default values; operator amends per row in W28C-1711):

```yaml
surface: ['api', 'mcp', 'webui']  # programme default for chat-client
priority: must  # default; operator amends per FR
since: 2026-06-14  # carried forward unless older anchor known
last-verified: 2026-06-14
tests: []  # populated by W28C-1711 binding
crud: N/A  # default; operator amends per FR
```

## Baseline CS-NNN rows (PS-REQ-TEST-TRACE §3.4 — added by W28C-1710b)

Every project MUST have CS-NNN rows for `anon-denied`, `wrong-role-denied`, `missing-param-error` per surface. Programme baseline:

| CS-NNN | Scenario | Surface | Expected | Roles |
|---|---|---|---|---|
| `CS-005` | anon-denied | `api` | `401` | `anon` |
| `CS-006` | anon-denied | `mcp` | `401` | `anon` |
| `CS-007` | anon-denied | `webui` | `401` | `anon` |
| `CS-008` | wrong-role-denied | `api` | `403` | `read-only` |
| `CS-009` | wrong-role-denied | `mcp` | `403` | `read-only` |
| `CS-010` | wrong-role-denied | `webui` | `403` | `read-only` |
| `CS-011` | missing-param-error | `api` | `422` | `*` |
| `CS-012` | missing-param-error | `mcp` | `422` | `*` |
| `CS-013` | missing-param-error | `webui` | `422` | `*` |

_These CS-NNN rows are pending W28C-1711 test binding. Each row binds to one or more `@pytest.mark.negative` tests with explicit expected denial code._


<!-- W28C-1711-R3 forensic: canonical FR-NNN rows derived from legacy R-NNN/FR1.NN test bindings (2026-06-15T15:21:28Z) -->

## Functional Requirements (W28E-1801A 1.0RC01 canonical set)

Per PS-REQ-TEST-TRACE v1.0, every active `FR-NNN` row is backtick-wrapped and bound from tests by `@pytest.mark.req(...)`. Rows `FR-001`..`FR-011` preserve the W28C identifiers already used in tests while replacing generic cluster labels with concrete service requirements. Rows `FR-012`..`FR-018` are W28E Stream-A additions from the chat-client knowledge file, the Test-Design-Audit-Jun26 supplement, and GarysWorkingNotes CL-04..CL-34.

| ID | Source | Primary surfaces | Priority | Requirement | Downstream owner |
|---|---|---|---|---|---|
| `FR-001` | R16.1/R16.2/R16.4 | `api`, `webui` | `must` | The WebUI shell must render authenticated routes, runtime config, redacted config views, and SPA assets without leaking API-key header details in cookie-auth mode. | Stream-B/C WebUI |
| `FR-002` | R7.1/PS-AJOBS | `api`, `mcp`, `a2a` | `must` | The four-server runtime must expose API, Web, MCP, and A2A listeners with managed-job observability for long-running MCP operations. | Stream-B runtime |
| `FR-003` | FR-P003/R2 | `mcp`, `internal` | `should` | Notification/LLM integration must be selectable through configured providers and MCP notification paths without hardcoded model or endpoint assumptions. | Stream-B integration |
| `FR-004` | R5/R16 auth gates | `api`, `webui`, `internal` | `must` | Authentication, flat-role login, demo-inventory removal, and MCP/A2A anonymous gates must fail closed and preserve canonical public/private route boundaries. | Stream-B WebUI/API |
| `FR-005` | R7.3/PS-78 | `api`, `mcp`, `webui` | `must` | Chat file attachments and returned artifacts must be routed through authenticated file-MCP transfer paths and rendered as downloadable transcript artifacts. | Stream-B/C file UX |
| `FR-006` | R1/R3/R4/R7/R9/R13/R15 | `api`, `mcp`, `a2a`, `cli`, `internal` | `must` | Core chat-client behaviour must cover CLI, configuration, MCP transport, session persistence, audit logging, request identity, agent dispatch, and API route contracts. | Stream-B regression |
| `FR-007` | W28A-751 | `api`, `mcp`, `a2a`, `webui` | `must` | Live IDAM cascade must prove admin/read-write/read-only roles, group membership promotion/revocation, API-key principal behaviour, MCP tool visibility, and A2A event visibility. | Stream-C live |
| `FR-008` | AT coverage/R16.8 | `api`, `mcp`, `a2a`, `webui` | `must` | Application flows must prove full-stack chat, WebUI navigation, MCP/A2A orchestration, file workflows, profile strategy, and browser-accessible session state. | Stream-C application |
| `FR-009` | ST coverage | `api`, `mcp`, `a2a`, `webui` | `must` | System flows must prove running-service API, WebUI, MCP, A2A, config CRUD, test harness, four-server pattern, file transfer, and audit endpoints. | Stream-B system |
| `FR-010` | W28A-751 T0 | `api`, `mcp`, `a2a`, `webui` | `must` | Live smoke must prove health, login, runtime config, route reachability, and `/webmcp`/`/weba2a` health without exposing public API-key header details. | Stream-C smoke |
| `FR-011` | IT coverage | `mcp`, `api`, `a2a` | `must` | Integration flows must prove MCP protocol/transport conformance, external MCP examples, file-MCP workflows, notification MCP status, database startup, and managed MCP jobs. | Stream-B integration |
| `FR-012` | Test-Design-Audit-Jun26 supplement | `api`, `internal` | `must` | The test harness must inject user and assistant turns into a running or newly created session, support pause/continue operator checkpoints, and record both prompt and response sides in transcript state. | Stream-B harness |
| `FR-013` | Test-Design-Audit-Jun26 supplement | `api`, `a2a`, `webui` | `must` | Injected harness conversations must remain attached to the target session/profile, be visible through transcript retrieval and A2A message/session events, and allow joining an existing session for continued test flow. | Stream-B/C harness |
| `FR-014` | GWN CL-26 | `webui`, `api` | `must` | `/chat` submit must work end-to-end through the browser: fill composer, click Send, observe the backend message request, render the user prompt, and render a non-empty assistant response without relying on curl-only proof. | Stream-C Playwright |
| `FR-015` | GWN CL-04..CL-18, CL-23, CL-32, CL-33 | `webui`, `api`, `mcp`, `a2a` | `must` | The WebUI must remove stale panels/warnings, expose canonical dashboard/profile/navigation entry points, label MCP-backed integrations as External Services, and route MCP/A2A links to canonical consoles instead of raw event or NOT_FOUND pages. | Stream-B/C WebUI |
| `FR-016` | GWN CL-19..CL-22, CL-29, CL-31 | `webui`, `api` | `must` | Profile, Session, and Chat must be modelled as distinct operator concepts with a wide chat workspace, profile picker, filtered recent sessions, new-session action, and session switching that refreshes the displayed transcript. | Stream-B/C WebUI |
| `FR-017` | GWN CL-25, CL-27, CL-28, CL-30 | `webui`, `api` | `should` | The chat surface must show the logged-in user and local-time message headers, support chat-history download, surface inline LLM/auth errors with retry, and provide a model/LLM test action for operators. | Stream-B/C WebUI |
| `FR-018` | PS-TEST-PACKS-REGISTRY | `api`, `mcp`, `a2a`, `webui`, `internal` | `must` | Stream-B/C tests must consume central `TP-COMMON` and `TP-INTEGRATION-EXAMPLES` by reference, keep generated fixtures local and regenerable, and avoid copying central pack dumps into this service. | Stream-B/C test design |

## Non-Functional Requirements (W28E-1801A 1.0RC01)

| ID | Source | Surface | Priority | Requirement | Downstream owner |
|---|---|---|---|---|---|
| `NF-001` | GWN CL-34 | `webui`, `api`, `mcp`, `a2a` | `must` | After WebUI UX changes are in flight, Stream-C must rerun browser-based four-sentinel smoke for `chatclient0`, `expertagent0`, `notificationagent0`, and `filemcpserver0`; curl/health-only proof is not sufficient for the aggregate gate. | Stream-C post-deploy |
| `NF-002` | PS-COMMON-SVC-REQ | `internal` | `must` | The service must use the standard Cloud-Dog platform packages for config, logging, API, IDAM, LLM, cache, database, jobs, storage, and agent integration instead of bespoke substitutes. | Stream-B regression |
| `NF-003` | PS-AUDIT-LOG/PS-40 | `api`, `webui`, `internal` | `must` | Audit, rotation, retention, and integrity controls must be configured and documented, with audit events retaining required identity, request, outcome, and target fields. | Stream-B regression |
| `NF-004` | Confidentiality controls | `cli`, `internal` | `must` | Secrets must remain outside source/default config, Vault-backed or scoped to private env files, and redacted from committed docs, logs, runtime config, and UI-exposed config payloads. | Stream-A/B |
| `NF-005` | PS-DOCS-CANONICAL/PS-REQ-TEST-TRACE | `internal` | `must` | Canonical docs must keep required files, stable requirement ID formats, unique test IDs, requirements-to-test coverage, delivery matrix coverage, and no orphan test files. | Stream-A/B |
| `NF-006` | RULES.md | `internal` | `must` | Code and tests must avoid hardcoded URLs/credentials, direct external imports, skip/mock use in IT/AT, missing file headers, and unreviewed public functions without docstrings. | Stream-B regression |
| `NF-007` | PS-COMMON-SVC-REQ migration controls | `internal` | `must` | Migration completeness must reject raw YAML config loading, raw FastAPI/auth replacements, and direct `os.environ` configuration access outside accepted boundary helpers. | Stream-B regression |
| `NF-008` | Security hygiene | `cli`, `internal` | `must` | Security quality checks must prevent secret logging, path traversal, SQL injection-risk patterns, unsafe domain-specific behaviour, and non-UK-English operator-facing copy. | Stream-B regression |
