---
template-id: T-DOK
template-version: 1.0
applies-to: docs/DOCKER.md
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

# chat-client — DOCKER

> **Template version:** T-DOK v1.0 — image layout, ports, healthcheck.

## 1. Image layers
Base image, system deps, python/node deps, app code.

## 2. Ports
| Port | Purpose | Bound to |
|---|---|---|

## 3. Healthcheck
Path, expected response, retry/period.

## 4. Volumes
Mounted paths, ownership, content.

## 5. Network
Mode (host/bridge), service-discovery name, allow-listed peers.

## 6. Variants
`Dockerfile.dev`, `Dockerfile.public` — when each is used.

## 7. Cross-references
- [BUILD.md](BUILD.md), [DEPLOY.md](DEPLOY.md)
- PS-91-docker-containerization.md

## 8. Project-specific notes
