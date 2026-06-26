# Changelog

All notable changes to `chat-client` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/); this project
versions from `pyproject.toml` (`cloud_dog_chat_client.__version__`).

## [Unreleased]

### W28C-1703 — forensic fixes (CC1, CC2, CC4–CC10)

#### Security (S-SECURITY P0)
- **CC1** — `POST /mcp` (+ `/messages`, web-proxied `/webmcp`) is now
  default-deny: every JSON-RPC call requires a valid `X-API-Key`, and a tool not
  in `_TOOL_PERMISSIONS` is refused (HTTP 401). The redundant api-kit
  `register_mcp_contract` transport mount — which shadowed the bespoke auth gate
  and exposed an anonymous `POST /mcp/tools/{tool}` execute path — was dropped.
  Closes the anonymous `tools/call list_sessions` 575-session leak.
- **CC2** — `GET /a2a/events` (web-proxied `/weba2a/events`) and the canonical
  `/a2a/events/sse` stream now require a valid credential; anonymous callers get
  HTTP 401 (handshake refused). The web proxy also refuses anonymous `/webmcp`
  and `/weba2a` handshakes.

#### Added
- **CC4** — `GET /api/sessions/{session_id}` returns a session's metadata plus
  its last-N transcript events (`200` found / `404` unknown / `401` anon).
  Previously only `DELETE` was registered, so a GET returned `405`.

#### Changed
- **CC5** — `GET /api/sessions` list rows now carry **`session_id`** (canonical,
  matching the `POST /api/sessions` create response). The legacy **`id`** key is
  retained as a **deprecated alias for one release cycle** and will be removed in
  a subsequent release; migrate consumers to `session_id`.
- **CC8** — `/version`, `/api/version`, `/api/status`, `/health` (and the SPA
  `runtime-config.js` `APP_VERSION`) all report a single source of truth,
  `cloud_dog_chat_client.__version__`. The `/api/status` + `/health` builders no
  longer fall back to a hardcoded `"0.1.0"`.
- **CC9** — the `X-Admin-Key` dual-key admin contract is now documented: the
  OpenAPI schema declares `ApiKeyAuth` (`X-API-Key`) and `AdminKeyAuth`
  (`X-Admin-Key`) security schemes and marks admin-scope mutations as requiring
  both; the admin error names both headers instead of the misleading bare
  "Missing X-API-Key"; the README documents the pairing.
- **CC7** — `arguments.profile` passes natively through the chat-client MCP
  forwarder to file-mcp (file-mcp honours the body arg as of W28C-1702); the
  contract is documented in `docs/MCP-FORWARDING.md`. No header bridge is needed.

#### Security — credential rotation
- **CC10** — the three `dev-build-key-change-in-production` placeholder API keys
  (code-runner inbound, notification-agent outbound, code-runner-mcp outbound)
  were rotated to three distinct secrets stored in Vault under
  `dev.services.<svc>.api_key`; Terraform sources them from Vault and the old
  literal was removed from every service accept-list.
