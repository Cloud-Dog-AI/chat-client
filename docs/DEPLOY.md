---
template-id: T-DEP
template-version: 1.0
applies-to: docs/DEPLOY.md
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

# chat-client — DEPLOY

> **Template version:** T-DEP v1.0 — TF pin → digest → preprod → prod roll.

## 1. Service identity
Container name, port, healthcheck path, network.

## 2. Terraform pin location

```
/path/to/example-deploy-repo/terraform/.../<NN-service>/
```

## 3. Roll procedure

1. Build image (see [BUILD.md](BUILD.md)).
2. Push to internal registry; capture digest.
3. Update TF pin (var or main.tf).
4. Run `terraform plan` → expected 1 change (image digest).
5. Operator runs `terraform apply` (agents do NOT apply).
6. Verify `/health` 200.
7. Run AT smoke from [TESTS.md](TESTS.md).

## 4. Preprod vs prod
- Preprod target: <name>
- Prod target: <name>
- Promotion gate: <criteria>

## 5. Rollback
Previous digest source; how to revert TF pin.

## 6. Cross-references
- [BUILD.md](BUILD.md), [DOCKER.md](DOCKER.md), [PREPROD.md](PREPROD.md)
- PREPROD-ROUTING-REFERENCE.md

## 7. Project-specific notes
