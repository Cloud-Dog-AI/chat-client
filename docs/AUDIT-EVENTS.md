---
template-id: T-AUD
template-version: 1.0
applies-to: docs/AUDIT-EVENTS.md
registry: service
required: must-have
when-applicable: ""
template-last-updated: 2026-06-12
template-owner: public-standards

project: chat-client
doc-last-updated: 2026-06-12
doc-git-commit: 776e2872e01dabdce4e68383d19d05577601b836
doc-git-branch: main
doc-source-shas: []
doc-age-policy: 90d
doc-conformance-stamp: 2026-06-12T12:00:00Z
---

# chat-client — AUDIT-EVENTS

> **Template version:** T-AUD v1.0 — NIST SP 800-53 AU-2 / AU-3 event-row schema.

## 1. Required fields (AU-3)
Every audit record contains:
- **What** — event_type (login, file_delete, privilege_escalation, ...)
- **When** — ISO 8601 timestamp (`YYYY-MM-DDThh:mm:ss.mmmZ`)
- **Where** — server/component/IP/module
- **Source** — caller IP / terminal / upstream service
- **Outcome** — success / failure
- **Subject** — user_id (real principal, not service key)

## 2. Event catalogue
**You MUST include:** every audit event this service emits.

| Event ID | Category (AU-2) | Trigger | Fields |
|---|---|---|---|
| `auth.login.success` | Authentication | successful login | user_id, ip, role |
| `auth.login.failure` | Authentication | failed login | attempted_user, ip, reason |
| `object.<action>` | Object Access | data change | user_id, object_id, action |
| `privileged.<action>` | Privileged Use | admin op | user_id, action, target |
| `system.<event>` | System Changes | reboot/shutdown | who, when |

## 3. Audit log integrity
- CRC/HASH frequency: <interval>
- Separate log file: <path>
- Rotation: <size/age trigger>

## 4. Cross-references
- PS-40-logging-observability.md
- [TESTS.md](TESTS.md) — audit tests
- packages/backend/platform-logging

## 5. Project-specific notes
