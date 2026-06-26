---
template-id: T-MCP
template-version: 1.0
applies-to: docs/MCP-REFERENCE.md
registry: service
required: must-have
when-applicable: ""
template-last-updated: 2026-06-12
template-owner: platform-standards

project: chat-client
doc-last-updated: 2026-06-18
doc-git-commit: e90ac9d3bf1dab0bef345fa9dfc45c6937dae386
doc-git-branch: w28c-1715-fix-docs
doc-source-shas:
  - src/cloud_dog_chat_client/servers/mcp_server.py
doc-age-policy: 90d
doc-conformance-stamp: 2026-06-18T00:00:00Z
---

# chat-client — MCP-REFERENCE

> **Template version:** T-MCP v1.0 — MCP tool surface (JSON-RPC 2.0 at `/mcp`).

## 1. Auth model

The chat-client MCP surface operates in `api_key` mode. Every JSON-RPC call
(including `initialize` and `tools/list`) requires a valid API key presented via:

- `X-API-Key: <key>` header (default header name; configurable via `auth.header`), or
- `Authorization: Bearer <key>` header, or
- `chat_client_api_key` session cookie (WebUI proxy path only).

Anonymous callers receive HTTP 401 with JSON-RPC error code `-32001`.

RBAC mapping from API key to tool visibility:

| Role | Effective MCP permissions |
|---|---|
| `admin` | All tools + `chat:admin:*` wildcard |
| `user` / `read-write` | `create_session`, `send_message`, `list_sessions`, `get_history` |
| `viewer` / `read-only` | `list_sessions`, `get_history` (read-only tools only) |

Required permission per tool is checked by the `RBACEngine` in
`src/cloud_dog_chat_client/servers/mcp_server.py` via `_TOOL_PERMISSIONS`.

## 2. Tools

The chat-client MCP surface exposes four tools. All calls use JSON-RPC 2.0
at `POST /mcp` (configurable base path via `mcp_server.base_path`; default `/mcp`).

### 2.1 `create_session`

- **Description:** Create a new chat session in chat-client and return its session ID and metadata.
- **Required permission:** `chat:conversation:create`
- **RBAC:** `admin`, `user` (`read-write`)
- **Input schema:**
  ```json
  {
    "type": "object",
    "properties": {
      "metadata": {
        "type": "object",
        "description": "Optional key-value metadata to attach to the session."
      }
    }
  }
  ```
- **Output schema:**
  ```json
  {
    "content": [{"type": "text", "text": "<json-serialised session object>"}],
    "structuredContent": {
      "session_id": "<uuid>",
      "created_at": "<ISO-datetime>",
      "metadata": {}
    },
    "isError": false
  }
  ```
- **Errors:**
  - `-32001` HTTP 401 — missing or invalid API key, or tool not in permitted set.
  - `-32000` HTTP 400 — downstream API failure creating the session (details in `message`).
- **Example call:**
  ```bash
  curl -X POST https://<host>/mcp \
    -H "Content-Type: application/json" \
    -H "X-API-Key: ${API_KEY}" \
    -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"create_session","arguments":{"metadata":{"source":"agent"}}},"id":1}'
  ```

### 2.2 `send_message`

- **Description:** Send a message to an existing chat session, triggering LLM orchestration and MCP tool dispatch. Returns the assistant reply and any tool outputs.
- **Required permission:** `chat:message:send`
- **RBAC:** `admin`, `user` (`read-write`)
- **Input schema:**
  ```json
  {
    "type": "object",
    "required": ["session_id", "content"],
    "properties": {
      "session_id": {
        "type": "string",
        "description": "UUID of an existing chat session."
      },
      "content": {
        "type": "string",
        "description": "The user message text to send."
      },
      "stream": {
        "type": "boolean",
        "description": "If true, request streaming response. Default false.",
        "default": false
      },
      "system_prompt": {
        "type": "string",
        "description": "Optional system prompt override for this turn."
      }
    }
  }
  ```
- **Output schema:**
  ```json
  {
    "content": [{"type": "text", "text": "<json-serialised message response>"}],
    "structuredContent": {
      "message_id": "<uuid>",
      "session_id": "<uuid>",
      "role": "assistant",
      "content": "<reply text>",
      "created_at": "<ISO-datetime>"
    },
    "isError": false
  }
  ```
- **Errors:**
  - `-32001` HTTP 401 — missing or invalid API key.
  - `-32000` HTTP 400 — `session_id` empty, session not found, or LLM/MCP error (details in response `isError: true`).
- **Example call:**
  ```bash
  curl -X POST https://<host>/mcp \
    -H "Content-Type: application/json" \
    -H "X-API-Key: ${API_KEY}" \
    -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"send_message","arguments":{"session_id":"<uuid>","content":"Hello, world!"}},"id":2}'
  ```

### 2.3 `list_sessions`

- **Description:** Return a list of all chat sessions visible to the caller. Admins see all sessions; other roles see sessions owned by or accessible to their API key principal.
- **Required permission:** `chat:conversation:read`
- **RBAC:** `admin`, `user` (`read-write`), `viewer` (`read-only`)
- **Input schema:**
  ```json
  {
    "type": "object",
    "properties": {}
  }
  ```
- **Output schema:**
  ```json
  {
    "content": [{"type": "text", "text": "<json-serialised sessions list>"}],
    "structuredContent": {
      "sessions": [
        {
          "session_id": "<uuid>",
          "created_at": "<ISO-datetime>",
          "metadata": {}
        }
      ]
    },
    "isError": false
  }
  ```
- **Errors:**
  - `-32001` HTTP 401 — missing or invalid API key.
- **Example call:**
  ```bash
  curl -X POST https://<host>/mcp \
    -H "Content-Type: application/json" \
    -H "X-API-Key: ${API_KEY}" \
    -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"list_sessions","arguments":{}},"id":3}'
  ```

### 2.4 `get_history`

- **Description:** Return the full transcript history for a single chat session, including all user and assistant turns plus tool call records.
- **Required permission:** `chat:conversation:read`
- **RBAC:** `admin`, `user` (`read-write`), `viewer` (`read-only`)
- **Input schema:**
  ```json
  {
    "type": "object",
    "required": ["session_id"],
    "properties": {
      "session_id": {
        "type": "string",
        "description": "UUID of the session whose transcript to retrieve."
      }
    }
  }
  ```
- **Output schema:**
  ```json
  {
    "content": [{"type": "text", "text": "<json-serialised transcript>"}],
    "structuredContent": {
      "session_id": "<uuid>",
      "events": [
        {
          "event_id": "<uuid>",
          "role": "user|assistant|tool",
          "content": "<text>",
          "created_at": "<ISO-datetime>"
        }
      ]
    },
    "isError": false
  }
  ```
- **Errors:**
  - `-32001` HTTP 401 — missing or invalid API key.
  - `-32000` HTTP 400 — `session_id` empty or session not found (details in `message`).
- **Example call:**
  ```bash
  curl -X POST https://<host>/mcp \
    -H "Content-Type: application/json" \
    -H "X-API-Key: ${API_KEY}" \
    -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_history","arguments":{"session_id":"<uuid>"}},"id":4}'
  ```

## 3. Cross-references
- [API-REFERENCE.md](API-REFERENCE.md)
- [A2A-REFERENCE.md](A2A-REFERENCE.md)
- PS-72-mcp-a2a-webui.md

## 4. Project-specific notes

The chat-client MCP surface acts as a **hub**: it exposes four conversation-management
tools directly (§2 above) and separately proxies the configured downstream MCP
servers to the WebUI via `/sessions/{id}/mcp/tools/list` and
`/sessions/{id}/mcp/tools/call`. The proxy surface uses the session-scoped MCP
client (`src/cloud_dog_chat_client/mcp/client.py`) and is documented under
`API-REFERENCE.md` (the REST `/sessions/{id}/mcp/*` routes).

Auth: every MCP call is default-deny (CC1, W28C-1703). The `initialize` and
`tools/list` methods also require a valid API key; anonymous callers receive
HTTP 401 before any session or tool data is returned.

Audit: each `tools/call` invocation emits a PS-40 audit log entry via
`_audit_tool_call()`. Message `content` and `system_prompt` fields are redacted
from audit records.
