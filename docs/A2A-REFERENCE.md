---
template-id: T-A2A
template-version: 1.0
applies-to: docs/A2A-REFERENCE.md
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

# chat-client — A2A-REFERENCE

> **Template version:** T-A2A v1.0 — Agent-to-Agent endpoint surface.

## 1. Auth model
A2A auth (`api_key` typically); service-key vs role-key forwarding; RBAC enforcement point.

## 2. Endpoints

| Method | Path | Auth | RBAC | Summary |
|---|---|---|---|---|

## 3. Message envelope
A2A request/response shape; correlation IDs; streaming behaviour.

## 4. Tools (re-exposed)
List of tools available via A2A and their MCP-equivalent.

## 5. Examples
**You MUST include:** at least one worked A2A call from an upstream service.

## 6. Cross-references
- [API-REFERENCE.md](API-REFERENCE.md)
- [MCP-REFERENCE.md](MCP-REFERENCE.md)
- PS-72-mcp-a2a-webui.md
- PS-72b-agent-to-agent.md

## 7. Project-specific notes
