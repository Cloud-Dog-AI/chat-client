---
doc-id: WARRANTY-1.0RC01
project: /opt/iac/Development/cloud-dog-ai/chat-client
generated: 2026-06-23T09:13:32Z
generator: scripts/build-warranty-table.py v1.0
standard: PS-CLOSEOUT-WARRANTY v1.0
---

# /opt/iac/Development/cloud-dog-ai/chat-client — 1.0RC01 Release Warranty Table

Per PS-CLOSEOUT-WARRANTY: every row must reach `verdict=PASS` before the lane may close.

## Section A — Requirements + UseCases + Test-Design coverage

| id | kind | title | since | source_evidence | design_row_present | binding_row_present | cross_surface_covered | webui_observation_bound | verdict |
|---|---|---|---|---|---|---|---|---|---|
| `CS-001` | CS | `CS-001` \| Anon attempts data read \| `api`, `mcp`, `a2a`, `webui` \| `anon` \| | 2026-06-23 | `docs:line 501` | YES | YES | YES | YES | **PASS** |
| `CS-002` | CS | `CS-002` \| read-only attempts write \| `api`, `mcp` \| `read-only` \| `403` \| | 2026-06-23 | `docs:line 502` | YES | YES | YES | YES | **PASS** |
| `CS-003` | CS | `CS-003` \| Missing required param \| `api` \| `admin` \| `422` | 2026-06-23 | `docs:line 503` | YES | YES | YES | YES | **PASS** |
| `CS-004` | CS | `CS-004` \| Wrong-role privileged op \| `mcp` \| `read-write` \| `403` | 2026-06-23 | `docs:line 504` | YES | YES | YES | YES | **PASS** |
| `CS-005` | CS | `CS-005` \| anon-denied \| `api` \| `401` \| `anon` | 2026-06-23 | `docs:line 528` | YES | YES | YES | YES | **PASS** |
| `CS-006` | CS | `CS-006` \| anon-denied \| `mcp` \| `401` \| `anon` | 2026-06-23 | `docs:line 529` | YES | YES | YES | YES | **PASS** |
| `CS-007` | CS | `CS-007` \| anon-denied \| `webui` \| `401` \| `anon` | 2026-06-23 | `docs:line 530` | YES | YES | YES | YES | **PASS** |
| `CS-008` | CS | `CS-008` \| wrong-role-denied \| `api` \| `403` \| `read-only` | 2026-06-23 | `docs:line 531` | YES | YES | YES | YES | **PASS** |
| `CS-009` | CS | `CS-009` \| wrong-role-denied \| `mcp` \| `403` \| `read-only` | 2026-06-23 | `docs:line 532` | YES | YES | YES | YES | **PASS** |
| `CS-010` | CS | `CS-010` \| wrong-role-denied \| `webui` \| `403` \| `read-only` | 2026-06-23 | `docs:line 533` | YES | YES | YES | YES | **PASS** |
| `CS-011` | CS | `CS-011` \| missing-param-error \| `api` \| `422` \| `*` | 2026-06-23 | `docs:line 534` | YES | YES | YES | YES | **PASS** |
| `CS-012` | CS | `CS-012` \| missing-param-error \| `mcp` \| `422` \| `*` | 2026-06-23 | `docs:line 535` | YES | YES | YES | YES | **PASS** |
| `CS-013` | CS | `CS-013` \| missing-param-error \| `webui` \| `422` \| `*` | 2026-06-23 | `docs:line 536` | YES | YES | YES | YES | **PASS** |
| `FR-001` | FR | Platform requirement traceability | 2026-06-23 | `docs:line 545` | YES | YES | YES | YES | **PASS** |
| `FR-002` | FR | Four-server runtime pattern | 2026-06-23 | `docs:line 550` | YES | YES | YES | YES | **PASS** |
| `FR-003` | FR | Notification/LLM integration | 2026-06-23 | `docs:line 551` | YES | YES | YES | YES | **PASS** |
| `FR-004` | FR | R5/R16 auth gates | 2026-06-23 | `docs:line 552` | YES | YES | YES | YES | **PASS** |
| `FR-005` | FR | File attachment/download contract | 2026-06-23 | `docs:line 553` | YES | YES | YES | YES | **PASS** |
| `FR-006` | FR | Chat/session/MCP orchestration | 2026-06-23 | `docs:line 554` | YES | YES | YES | YES | **PASS** |
| `FR-007` | FR | Live IDAM cascade | 2026-06-23 | `docs:line 555` | YES | YES | YES | YES | **PASS** |
| `FR-008` | FR | Application WebUI coverage | 2026-06-23 | `docs:line 556` | YES | YES | YES | YES | **PASS** |
| `FR-009` | FR | System WebUI/API flow | 2026-06-23 | `docs:line 557` | YES | YES | YES | YES | **PASS** |
| `FR-010` | FR | Live route smoke | 2026-06-23 | `docs:line 558` | YES | YES | YES | YES | **PASS** |
| `FR-011` | FR | MCP conformance | 2026-06-23 | `docs:line 545` | YES | YES | YES | YES | **PASS** |
| `FR-012` | FR | Harness flow | 2026-06-23 | `docs:line 545` | YES | YES | YES | YES | **PASS** |
| `FR-013` | FR | Test-design audit supplement | 2026-06-23 | `docs:line 561` | YES | YES | YES | YES | **PASS** |
| `FR-014` | FR | GWN CL-26 `/chat` submit | 2026-06-23 | `docs:line 562` | YES | YES | YES | YES | **PASS** |
| `FR-015` | FR | GWN CL-04..CL-18, CL-23, CL-32, CL-33 | 2026-06-23 | `docs:line 563` | YES | YES | YES | YES | **PASS** |
| `FR-016` | FR | GWN CL-19..CL-22, CL-29, CL-31 | 2026-06-23 | `docs:line 564` | YES | YES | YES | YES | **PASS** |
| `FR-017` | FR | GWN CL-25, CL-27, CL-28, CL-30 | 2026-06-23 | `docs:line 565` | YES | YES | YES | YES | **PASS** |
| `FR-018` | FR | Release traceability | 2026-06-23 | `docs:line 545` | YES | YES | YES | YES | **PASS** |
| `NF-001` | NF | GWN CL-34 four-sentinel smoke | 2026-06-23 | `docs:line 572` | YES | YES | YES | YES | **PASS** |
| `NF-002` | NF | PS-COMMON-SVC-REQ | 2026-06-23 | `docs:line 573` | YES | YES | YES | YES | **PASS** |
| `NF-003` | NF | Audit/logging | 2026-06-23 | `docs:line 574` | YES | YES | YES | YES | **PASS** |
| `NF-004` | NF | Confidentiality controls | 2026-06-23 | `docs:line 575` | YES | YES | YES | YES | **PASS** |
| `NF-005` | NF | Canonical docs/traceability | 2026-06-23 | `docs:line 576` | YES | YES | YES | YES | **PASS** |
| `NF-006` | NF | RULES.md conformance | 2026-06-23 | `docs:line 577` | YES | YES | YES | YES | **PASS** |
| `NF-007` | NF | Common service migration controls | 2026-06-23 | `docs:line 578` | YES | YES | YES | YES | **PASS** |
| `NF-008` | NF | Security hygiene | 2026-06-23 | `docs:line 579` | YES | YES | YES | YES | **PASS** |
| `UC-001` | UC | WebUI login, dashboard, and canonical navigation | 2026-06-23 | `docs:line 42` | YES | YES | YES | YES | **PASS** |
| `UC-002` | UC | Browser chat submit and response | 2026-06-23 | `docs:line 43` | YES | YES | YES | YES | **PASS** |
| `UC-003` | UC | Harness injects conversation into a session | 2026-06-23 | `docs:line 44` | YES | YES | YES | YES | **PASS** |
| `UC-004` | UC | Profile/session/chat workspace operation | 2026-06-23 | `docs:line 45` | YES | YES | YES | YES | **PASS** |
| `UC-005` | UC | External services and canonical consoles | 2026-06-23 | `docs:line 46` | YES | YES | YES | YES | **PASS** |
| `UC-006` | UC | Chat operations, errors, downloads, and model test | 2026-06-23 | `docs:line 47` | YES | YES | YES | YES | **PASS** |
| `UC-007` | UC | Live IDAM cascade and denial handling | 2026-06-23 | `docs:line 48` | YES | YES | YES | YES | **PASS** |
| `UC-008` | UC | Four-sentinel post-deploy browser smoke | 2026-06-23 | `docs:line 49` | YES | YES | YES | YES | **PASS** |

## Section B — Functional delivery coverage

| id | impl_committed | unit_test | integration_test | acceptance_test | surface_api | surface_mcp | surface_a2a | idam_role_negative | audit_event_emitted | ajobs_integration | preprod_deployed | preprod_smoke | sibling_regression | variation_pinned | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `FR-001` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-002` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-003` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-004` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-005` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-006` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-007` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-008` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-009` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-010` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-011` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-012` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-013` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-014` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-015` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-016` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-017` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |
| `FR-018` | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | **PASS** |

## Section C — WebUI + E2E coverage

| item | surface | roles | raw evidence | verdict |
|---|---|---|---|---|
| Login and runtime config | webui | admin, anon | `working/evidence/W28E-1801C/current/local-playwright/at-webui-full-junit.xml`; `working/evidence/W28E-1801C/current/10a-four-sentinel-browser-smoke.tsv` | **PASS** |
| Dashboard and navigation | webui | admin | `working/evidence/W28E-1801C/current/local-playwright/at-webui-full-junit.xml`; `working/evidence/W28E-1801C/current/02-playwright-coverage-matrix.tsv` | **PASS** |
| `/chat` CL-26 positive submit | webui, api | admin | `working/evidence/W28E-1801C/current/local-playwright/at-webui-full-junit.xml`; `working/evidence/W28E-1801C/current/07-local-docker-playwright-junit.xml` | **PASS** |
| `/chat` CL-26 negative submit | webui, api | admin | `working/evidence/W28E-1801C/current/local-playwright/at-webui-full-junit.xml`; `working/evidence/W28E-1801C/current/07-local-docker-playwright-junit.xml` | **PASS** |
| Sessions history and table | webui, api | admin | `working/evidence/W28E-1801C/current/local-playwright/at-webui-full-junit.xml`; `working/evidence/W28E-1801C/current/02-playwright-coverage-matrix.tsv` | **PASS** |
| Admin users, groups, and API keys | webui, api | admin, read-only | `working/evidence/W28E-1801C/current/local-playwright/at-webui-full-junit.xml`; `working/evidence/W28E-1801C/current/02d-ps-idam-role-cascade-consumption.tsv` | **PASS** |
| MCP and A2A navigation | webui, mcp, a2a | admin | `working/evidence/W28E-1801C/current/local-playwright/at-webui-full-junit.xml`; `working/evidence/W28E-1801C/current/06-cross-svc-e2e-evidence.tsv` | **PASS** |
| Settings and API docs | webui | admin | `working/evidence/W28E-1801C/current/local-playwright/at-webui-full-junit.xml`; `working/evidence/W28E-1801C/current/02-playwright-coverage-matrix.tsv` | **PASS** |
| URL canonical and SPA deep links | webui | admin, anon | `working/evidence/W28E-1801C/current/04-url-canonical-audit.tsv`; `working/evidence/W28E-1801C/current/10-preprod-smoke-evidence.tsv` | **PASS** |
| Accessibility and landmark gate | webui | admin | `working/evidence/W28E-1801C/current/03-axe-a11y-evidence.tsv`; `ui/dist/index.html` | **PASS** |
| CL-34 four-sentinel post-deploy browser smoke | webui, api, mcp, a2a | anon, service smoke | `working/evidence/W28E-1801C/current/10a-four-sentinel-browser-smoke.tsv`; `working/evidence/W28E-1801C/current/10-preprod-smoke-evidence.tsv` | **PASS** |
| Stream-C release identity | release | n/a | `working/evidence/W28E-1801C/current/08-main-push-proof.txt`; `working/evidence/W28E-1801C/current/final-evidence-validator.txt` | **PASS** |
