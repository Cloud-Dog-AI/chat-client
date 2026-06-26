---
template-id: T-DMT
template-version: 1.0
applies-to: docs/DATA-MODEL.md
registry: service
required: must-have
when-applicable: ""
template-last-updated: 2026-06-12
template-owner: platform-standards

project: chat-client
doc-last-updated: 2026-06-18
doc-git-commit: e90ac9d3bf1dab0bef345fa9dfc45c6937dae386
doc-git-branch: w28c-1715-fix-docs
doc-source-shas: []
doc-age-policy: indefinite
doc-conformance-stamp: 2026-06-18T00:00:00Z
---

# Data Model

## Domain Entities

| Entity | Storage model | Identity relation | Cascade / deletion behaviour |
|---|---|---|---|
| `ChatSession` | Persisted session metadata and log path. | Owned by session/user metadata, not an IDAM FK. | Session events and preferences are session-scoped. |
| `ChatSessionEvent` | Ordered event stream per session. | References `ChatSession`. | Removed with session lifecycle where database constraints apply. |
| `ChatSessionPreference` | Per-session selected MCP server indices. | References `ChatSession`. | Deleted with session lifecycle where database constraints apply. |
| `ChatAuditLog` | Append-only audit record with action/status/request details. | Actor stored in event detail; no user FK. | Retained for audit. |
| `ChatProfile` | `profile_id`, name, description, MCP bindings, session defaults, access control JSON. | Domain resource; no service-local user/group FK. | Soft delete; config event and audit event emitted. |
| `ChatConfigEvent` | Persisted config change event. | Actor is captured through audit/context metadata. | Read through A2A feed. |

## Identity/Admin Entities

| Entity | Storage model | Relation | Cascade / effective role behaviour |
|---|---|---|---|
| `ChatUser` | `user_id`, display name, email, role, status, metadata. | Memberships through `ChatGroupMembership`; API keys optionally reference user. | Soft delete also deletes membership rows; disabled/locked users cannot authenticate through stored API keys. |
| `ChatGroup` | `group_id`, name, description, roles JSON, metadata. | Memberships through `ChatGroupMembership`. | Soft delete clears membership rows. Group role `admin` promotes API-key principal to admin. |
| `ChatGroupMembership` | Join table between `ChatUser` and `ChatGroup`. | Service-local identity relationship only. | Replaced atomically on user/group updates; flushed before response serialization. |
| `ChatAPIKey` | Hashed API key with prefix, scopes, optional user reference, revoked state. | Optional `ChatUser` owner. | Scopes `*`, `admin`, or `config:write` promote admin capability; revoked keys fail auth. |
| `Role` | Shared `cloud_dog_idam` SQLAlchemy role store. | Central role catalogue, not chat bespoke storage. | Baseline roles are seeded and baseline-protected by `cloud_dog_idam`. |

## Thread-B Identity/Domain Boundary

The service intentionally does not add per-service foreign keys from identity
objects to `ChatProfile`. Group-to-resource cascade is a central IDAM concern:
Thread-B W28A-741 owns resource-aware bindings and default-deny enforcement.
Chat-client consumes that by resolving authenticated principals through the
shared guard, exposing group/user/API-key/role CRUD consistently, and testing
that group membership changes affect live access without adding service-local
schema.

