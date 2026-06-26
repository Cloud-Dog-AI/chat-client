---
template-id: T-ENV
template-version: 1.0
applies-to: docs/ENV-REFERENCE.md
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

# chat-client — ENV-REFERENCE

> **Template version:** T-ENV v1.0 — every environment variable consumed by this service.

## 1. Required env vars

| Variable | Default | Scope | Vault placeholder | Description |
|---|---|---|---|---|
| `<VAR>` | `<default-or-NONE>` | runtime/build/test | `${VAULT_PATH}` | <purpose> |

## 2. Optional env vars

| Variable | Default | Description |
|---|---|---|

## 3. Vault integration
Path conventions, fallback order, how missing values surface (warn vs fail).

## 4. Cross-references
- [PARAMETERS.md](PARAMETERS.md) — defaults.yaml schema
- PS-80-config-mgmt.md
- vault-reference.md

## 5. Project-specific notes
