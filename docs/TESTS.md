---
template-id: T-TST
template-version: 1.1
applies-to: docs/TESTS.md
project: chat-client
doc-last-updated: 2026-06-24T00:00:00Z
doc-git-commit: 5a120f91a1d400859ec3e1af24d5aa7eeaa4c24a
doc-git-branch: main
doc-age-policy: 90d
doc-conformance-stamp: 2026-06-12T16:35:09Z
req-trace-version: 1.0
total-tests: 147
coverage-percent: 100
---

# Tests

## W28A-751 b-4 Live Thread-B Tests

The PS-95 Thread-B live tests live in:

- `tests/smoke/test_w28a751_t0_live_smoke.py`
- `tests/e2e/test_w28a751_t1_t3_live_idam.py`

Run them against preprod after deployment:

```bash
CHAT_CLIENT_BMETHOD_BASE_URL=https://chatclient0.cloud-dog.net \
CHAT_CLIENT_ADMIN_USERNAME=admin \
CHAT_CLIENT_ADMIN_PASSWORD=OrangeRiverTable \
CHAT_CLIENT_RW_USERNAME=read-write \
CHAT_CLIENT_RW_PASSWORD=BlueRiverChair \
CHAT_CLIENT_RO_USERNAME=read-only \
CHAT_CLIENT_RO_PASSWORD=GreenRiverDesk \
pytest -q --env tests/env-W28A-751-live \
  tests/smoke/test_w28a751_t0_live_smoke.py \
  tests/e2e/test_w28a751_t1_t3_live_idam.py
```

| Test ID | Tier | Coverage | Surfaces |
|---|---|---|---|
| T0-SMOKE | T0 | Live health, login page, SPA runtime config, API/MCP/A2A/WebUI route reachability. | API/MCP/A2A/WebUI |
| T1-COMMON-IDAM | T1 | Cookie login, `/auth/me`, runtime config not leaking `API_KEY_HEADER`, authenticated config reads. | API/WebUI |
| T2-RBAC-READONLY | T2 | `read-only` can read but receives 403 on data writes; `read-write` can perform allowed session preference writes. | API/WebUI |
| T3-BUS-CASCADE | T3 | Admin creates user/group/API key, group role membership promotes API-key access, membership removal revokes admin mutation, config events are visible through A2A, config MCP tools list. | API/MCP/A2A/WebUI |

## Service Scope
Session-centric conversation orchestration that brokers LLM responses and downstream MCP services through API, Web, MCP, and A2A runtimes.

## Test Inventory
| Tier | Present | Notes |
|------|---------|-------|
| `quality` | Yes | Repository contains the `quality` test tier. |
| `unit` | Yes | Repository contains the `unit` test tier. |
| `system` | Yes | Repository contains the `system` test tier. |
| `integration` | Yes | Repository contains the `integration` test tier. |
| `application` | Yes | Repository contains the `application` test tier. |
| `helpers` | Yes | Repository contains the `helpers` test tier. |
| `private` | Yes | Repository contains the `private` test tier. |

## Documentation Anchors
| Tier | Test ID | Requirements | Notes |
|------|---------|--------------|-------|
| `application` | AT900.1 | `R9`, `R16.8` | Regression anchor |
| `integration` | IT900.1 | `R5.1`, `R7.2` | Regression anchor |
| `system` | ST900.1 | `R10`, `R15` | Regression anchor |
| `unit` | UT900.1 | `R-DB-01`, `R-DB-10` | Regression anchor |
| `quality` | QT900.1 | `R5.1`, `R15` | Regression anchor |

## Current Evidence Model
- The repository keeps execution evidence in repo-local working reports and rerunnable pytest suites.
- Before release, rerun the relevant `QT`, `UT`, `ST`, `IT`, and `AT` tiers against the intended environment overlays.
- This document records the current catalogue rather than claiming a release verdict.

## W28E-1801C Stream-C WebUI Evidence
- `tests/application/AT_WEBUI_E2E/test_webui_e2e.py::test_t13_cl26_chat_submit_positive_and_negative` covers CL-26 browser `/chat` submit positive and negative paths for `FR-014`.
- Final evidence is archived under `cloud-dog-ai-platform-standards/working/evidence/W28E-1801C/current/`.
- CL-34 is proven by the four-sentinel post-deploy browser smoke evidence after main merge and preprod deployment.

## Standard Commands
```bash
python3 -m pytest tests/quality --env tests/env-QT -q
python3 -m pytest tests/unit --env tests/env-UT -q
python3 -m pytest tests/system --env tests/env-ST -q
python3 -m pytest tests/integration --env tests/env-IT -q
python3 -m pytest tests/application --env tests/env-AT -q
```

## Notes
- Top-level test directories present: `application`, `helpers`, `integration`, `private`, `quality`, `system`, `unit`.
- Environment overlays and private credentials are intentionally not published in this document set.

## Requirement Coverage Register
- `R5.1` is exercised by the external MCP coverage and multi-server integration coverage.
- `R7.2` is exercised by the MCP file transfer proxy coverage across system, integration, and application tiers.
- `R9` is exercised by session persistence and orchestration coverage.
- `R10` is exercised by server-only and harness startup coverage.
- `R11` is exercised by installation and server-control coverage.
- `R12` is exercised by Docker build and container readiness coverage.
- `R15` is exercised by MCP server administration, RBAC, and audit coverage.
- `R16.8` is exercised by Web UI and accessibility-sensitive interaction coverage.
- `R-DB-01`, `R-DB-02`, `R-DB-03`, `R-DB-04`, `R-DB-05`, `R-DB-06`, `R-DB-07`, `R-DB-08`, `R-DB-09`, and `R-DB-10` are exercised by database abstraction, migration, startup, and end-to-end coverage.
- `W28A-118D` non-LLM package/UI closure is exercised by `tests/quality/QT_COMPLIANCE/test_qt_package_adoption.py` for package declaration drift and `cloud-dog-ai-ui-monorepo/apps/chat-client/tests/e2e/non-llm-standards.spec.ts` for rendered settings, MCP/A2A console metadata, file-transfer/proxy, docs/about/profile metadata, and empty/error states. Generated answers, chat response quality, Ragflow, translation, summarisation, and model-backed chat flows are intentionally deferred to LLM-gated work.

## Suite Catalogue
- Application suites (requirement refs: R1, R3, R7, R7.2, R9, R10, R-DB-01..R-DB-10, FR-P003): `AT1.1_ChatClientConversation`, `AT1.2_ChatClientMCP`, `AT1.3_ClientAPIStreaming`, `AT1.4_ChatClientSQLAgent`, `AT1.5_SearchMCPMultiStage`, `AT1.6_SQLAgentMultiStage`, `AT1.7_SQLAgentThinking`, `AT1.8_SearchFileMCPWorkflows`, `AT1.9_SearchFileResumeRecovery`, `AT1.10_FileArtifactsDeterminism`, `AT1.11_FileMCPPathOps`, `AT1.12_MessageFileOperations`, `AT1.13_SearchMCPNewsSummaryUIAssist`, `AT1.14_SearchNewsHungarianBridge`, `AT1.15_HungarianTranslatorHardFailure`, `AT1.16_Fail2banEmailAnalysis`, `AT1.17_UkrainianNewsBriefing`, `AT1.18_EmailAttachmentIndexVerify`, `AT1.19_UkrainianNewsEnhanced`, `AT1.20_DefenceCompaniesFileStore`, `AT1.21_DefenceCompaniesGitStore`, `AT1.22_DatabaseE2E`, `AT1.23_CrossProjectSpamAnalysis`, `AT1.24_WebSearchAgentSourceDiscovery`, `AT1.25_FileMCPUploadDownload`, `AT1.26_FileMCPPathOpsWorkflow`, `AT1.27_PeriodicResearchMonitor`, `AT_CROSS_SERVICE`, `AT_CrossServiceIntegration`, `AT_ORCHESTRATION`, `AT_TEST_HARNESS`, `AT_WEBUI_E2E`, `AT_WebSearchAgent`.
- Integration suites (requirement refs: R3, R4, R5, R5.1, R7, R7.2, R-DB-01..R-DB-10, FR-P003): `IT2.1_MCPProtocol`, `IT2.2_MCPConformanceEverything_StreamableHTTP`, `IT2.3_MCPConformanceTargets`, `IT2.4_MCPExampleRemoteServer`, `IT2.5_ExampleMCPServer`, `IT2.6_MultiMCPServers`, `IT2.7_StreamableHTTPJsonRPC`, `IT2.8_FlightSearchMCP`, `IT2.9_SearchMCP`, `IT2.10_LegacySSE`, `IT2.11_SQLAgentMCP`, `IT2.12_FileMCP`, `IT2.13_FileMCPToolCoverage`, `IT2.14_FileMCPErrorHandling`, `IT2.15_FileMCPConfigVariants`, `IT2.16_FileMCPToolSurface`, `IT2.17_CrossMCPSelectiveSource`, `IT2.18_ExpertAgentHungarianMCP`, `IT2.19_NotificationMCP`, `IT2.20_DatabaseStartup`, `IT2.21_FileMCPTools`, `IT2.22_FileMCPLimitsErrors`, `IT2.23_JobsManagedMCP`.
- System suites (requirement refs: R2, R7, R7.1, R7.2, R9, R10, R11, R15, R16.8, CFG-01..CFG-13): `ST1.1_OllamaReadiness`, `ST1.2_ClientAPIReadiness`, `ST1.3_ClientAPILLM`, `ST1.4_OllamaModels`, `ST1.5_CLIChat`, `ST1.6_SessionPersistence`, `ST1.7_ServerOnlyCLI`, `ST1.8_ResponseFormatting`, `ST1.9_ResponseFormattingRaw`, `ST1.10_ResponseFormattingDefault`, `ST1.11_FileMCPRuntimeReadiness`, `ST1.12_ContainerHostNetworkReadiness`, `ST1.13_MCPServerAdminRBAC`, `ST1.14_WebUIFlow`, `ST1.15_DatabaseAbstraction`, `ST1.16_OpenAPISpec`, `ST1.17_ConfigCrud`, `ST1.18_FourServerPattern`, `ST1.19_TestHarness`, `ST1.20_FileTransferProxy`, `ST_AuditLog`, `ST_IntegrityVerifier`, `ST_LogRotation`.
- Unit suites (requirement refs: R2, R3, R4, R5, R7, R9, CFG-01..CFG-13): `UT1.1_ConfigTests`, `UT1.2_SessionLogging`, `UT1.3_OllamaProvider`, `UT1.4_MCPClient`, `UT1.5_MCPConformance`, `UT1.6_ClientAPIAuth`, `UT1.7_CLIInteractive`, `UT1.8_DatabaseAbstraction`, `UT1.9_DeliveryArtifacts`, `UT1.10_JobsPackage`, `UT1.11_TestHarness`, `UT_AuditLogFormat`.
- Quality suites: `QT_COMPLIANCE`, `QT_LoggingCompliance`, `QT_PACKAGE_COMPLIANCE`.

## Traceability Matrix

| Requirement | Test File | Test Function/Class | Status |
|---|---|---|---|
| R1 (CLI Chat Client) | `tests/application/AT1.1_ChatClientConversation/test_chat_client_conversation.py` | `test_at1_1_chat_client_default_prompt`, `test_at1_1_chat_client_override_prompt`, `test_at1_1_chat_client_multi_step_history`, `test_at1_1_chat_client_stop_token` | COVERED |
| R2 (LLM Provider Support) | `tests/unit/UT1.3_OllamaProvider/test_ollama_provider_contract.py` | `test_ut1_3_ollama_service_constructs` | COVERED |
| R2 (LLM Provider Support) | `tests/system/ST1.1_OllamaReadiness/` | ST1.1 suite | COVERED |
| R3 (MCP Client Core) | `tests/unit/UT1.4_MCPClient/test_mcp_client_jsonrpc.py` | `test_ut1_4_jsonrpc_tools_list_no_network` | COVERED |
| R3 (MCP Client Core) | `tests/integration/IT2.1_MCPProtocol/test_mcp_protocol.py` | `test_it2_1_mcp_protocol` | COVERED |
| R4 (MCP Transport Support) | `tests/unit/UT1.4_MCPClient/test_streamable_http_initialize.py` | UT1.4 streamable HTTP suite | COVERED |
| R4 (MCP Transport Support) | `tests/unit/UT1.4_MCPClient/test_legacy_sse_transport.py` | UT1.4 legacy SSE suite | COVERED |
| R4 (MCP Transport Support) | `tests/integration/IT2.10_LegacySSE/` | IT2.10 suite | COVERED |
| R5 (MCP Conformance Harness) | `tests/integration/IT2.2_MCPConformanceEverything_StreamableHTTP/` | IT2.2 suite | COVERED |
| R5 (MCP Conformance Harness) | `tests/integration/IT2.3_MCPConformanceTargets/` | IT2.3 suite | COVERED |
| R7 (Local Client API Server) | `tests/system/ST1.2_ClientAPIReadiness/` | ST1.2 suite | COVERED |
| R7 (Local Client API Server) | `tests/application/AT1.3_ClientAPIStreaming/test_client_api_streaming.py` | `test_client_api_streaming` (AT1.3 suite) | COVERED |
| R7.1 (Four-Server Runtime) | `tests/system/ST1.18_FourServerPattern/test_four_server_pattern.py` | `test_st1_18_four_server_health_and_proxy`, `test_st1_18_a2a_websocket_broadcasts_session_and_config_events` | COVERED |
| R7.2 (MCP File Transfer Proxy) | `tests/system/ST1.20_FileTransferProxy/test_file_transfer_proxy.py` | `test_st1_20_file_transfer_proxy_roundtrip_and_errors` | COVERED |
| R7.2 (MCP File Transfer Proxy) | `tests/integration/IT2.12_FileMCP/test_file_mcp_upload_download.py` | IT2.12 suite | COVERED |
| R7.3 (MCP Chat File Attachments) | `tests/application/AT_CHAT_FILE_ATTACHMENTS/test_chat_file_attachments.py` | `test_profile_file_intake_settings_roundtrip`, `test_profile_file_intake_defaults_when_absent`, `test_upload_by_value_attachment_metadata`, `test_upload_by_value_base64`, `test_upload_by_reference_source_url` | COVERED |
| R7.3 (MCP Chat Artifact Download Links) | `tests/application/AT_CHAT_FILE_ATTACHMENTS/test_chat_file_artifact_download_links.py` | `test_markdown_artifact_download_link`, `test_html_report_artifact_download`, `test_json_download_returns_base64`, `test_download_requires_authentication`, `test_upload_download_roundtrip_content_integrity` | COVERED |
| R-7 / R-16 Traceability Alias | `tests/system/ST1.13_MCPServerAdminRBAC/test_mcp_server_admin_rbac.py`, `tests/system/ST1.18_FourServerPattern/test_four_server_pattern.py` | Four-server admin/MCP runtime contract alias coverage for reconciled legacy IDs `R-7` and `R-16`. | COVERED |
| R9 (Session Persistence) | `tests/system/ST1.6_SessionPersistence/test_session_persistence.py` | `test_st1_6_session_persistence_and_context`, `test_st1_6_default_env_file` | COVERED |
| R10 (Server-Only Mode) | `tests/system/ST1.7_ServerOnlyCLI/` | ST1.7 suite | COVERED |
| R13 (Logging + Overrides) | `tests/system/ST_AuditLog/` | ST_AuditLog suite | COVERED |
| R13 (Logging + Overrides) | `tests/unit/UT_AuditLogFormat/` | UT_AuditLogFormat suite | COVERED |
| R15 (MCP Server Admin RBAC) | `tests/system/ST1.13_MCPServerAdminRBAC/test_mcp_server_admin_rbac.py` | `test_st1_13_mcp_server_admin_rbac_and_audit_logging` | COVERED |
| R16.8 (Accessibility) | `tests/application/AT_WEBUI_E2E/test_webui_e2e.py` | `test_t1_api_key_login`, `test_t2_user_crud_admin`, `test_t6_create_chat_session`, `test_t8_mcp_health`, `test_t10_settings` | COVERED |
| FR-P001 (No-Auth Mode) | `tests/unit/UT1.6_ClientAPIAuth/test_client_api_auth.py` | `test_ut1_6_api_key_auth`, `test_ut1_6_api_key_accepts_trusted_webui_admin_without_admin_key` | COVERED |
| R15 (W28A-727-R5 Flat WebUI Login: admin/read-write/read-only + read-only→403) | `tests/unit/UT1.47_FlatLoginRoles/test_flat_login_roles.py` | `test_flat_roles_are_exactly_three`, `test_admin_is_wildcard`, `test_read_write_has_baseline_plus_chat_use_perms`, `test_read_only_is_view_only_baseline`, `test_normalise_is_fail_closed`, `test_write_gate_path_classification`, `test_admin_login_reflects_admin_role`, `test_read_write_login_reflects_read_write_role`, `test_read_only_login_reflects_read_only_role`, `test_invalid_credentials_rejected`, `test_read_only_write_is_403_inline`, `test_read_only_get_is_not_gated`, `test_read_write_write_is_not_gated_by_readonly_rule` | COVERED |
| FR-P002 (OpenAPI Spec) | `tests/system/ST1.16_OpenAPISpec/test_openapi_spec.py` | `test_st1_16_openapi_spec_contract` | COVERED |
| NFR5 (Configuration) | `tests/unit/UT1.1_ConfigTests/test_config_manager.py` | `test_ut1_1_loads_default_yaml_and_env_override`, `test_ut1_1_precedence_os_env_over_env_file_config_and_default` | COVERED |
| R-DB-01 (DB access abstraction) | `tests/unit/UT1.8_DatabaseAbstraction/test_database_abstraction.py` | `test_ut_db_01_settings_bridge_and_engine_factory` | COVERED |
| R-DB-02 (Engine creation) | `tests/unit/UT1.8_DatabaseAbstraction/test_database_abstraction.py` | `test_ut_db_01_settings_bridge_and_engine_factory` | COVERED |
| R-DB-03 (Session management) | `tests/unit/UT1.8_DatabaseAbstraction/test_database_abstraction.py` | `test_ut_db_02_sync_session_manager_provides_working_session` | COVERED |
| R-DB-01..R-DB-10 (DB E2E) | `tests/application/AT1.22_DatabaseE2E/test_database_e2e.py` | AT1.22 suite | COVERED |
| R-DB-01..R-DB-10 (DB Startup) | `tests/integration/IT2.20_DatabaseStartup/test_database_startup.py` | IT2.20 suite | COVERED |
| CFG-01..CFG-04 / R7 (Config CRUD) | `tests/system/ST1.17_ConfigCrud/test_config_crud.py` | `test_st1_17_config_crud_live_server` | COVERED |
| CFG-05 (MCP tools for profiles) | `tests/unit/UT1.6_ClientAPIAuth/test_config_crud_routes.py` | UT1.6 config CRUD routes suite | COVERED |
| CFG-08..CFG-11 (User/Group/Key mgmt) | `tests/application/AT_WEBUI_E2E/test_webui_e2e.py` | `test_t2_user_crud_admin`, `test_t3_group_crud_admin`, `test_t4_api_key_crud_admin` | COVERED |
| CFG-13 (Admin-only CRUD) | `tests/system/ST1.13_MCPServerAdminRBAC/test_mcp_server_admin_rbac.py` | `test_st1_13_mcp_server_admin_rbac_and_audit_logging` | COVERED |
| R7 (Managed Jobs) | `tests/unit/UT1.10_JobsPackage/test_jobs_runtime.py` | UT1.10 suite | COVERED |
| R7 (Managed Jobs) | `tests/integration/IT2.23_JobsManagedMCP/test_jobs_managed_mcp.py` | IT2.23 suite | COVERED |
| FR-003 / R7 (Notification MCP) | `tests/integration/IT2.19_NotificationMCP/` | IT2.19 suite | COVERED |
| CFG-06 (A2A broadcast) | — | — | GAP |
| CFG-12 / R15 (Audit logging for CRUD) | `tests/unit/UT_AuditLogFormat/test_audit_log_format.py` + `tests/system/ST1.13_MCPServerAdminRBAC/test_mcp_server_admin_rbac.py` + `tests/system/ST_AuditLog/test_audit_routes.py` | `test_audit_event_has_all_au3_fields`, `test_audit_event_timestamp_format`, `test_audit_event_outcome_values`, `test_st1_13_mcp_server_admin_rbac_and_audit_logging`, `test_audit_positive_route_au3_identity_chain` (platform capability via `cloud_dog_logging.AuditLogger.log_crud` invoked at `src/cloud_dog_chat_client/servers/mcp_server.py:63`; `cloud_dog_logging.AuditEvent` emit at `src/cloud_dog_chat_client/api/config_admin.py:211` per CRUD action) | IMPLEMENTED |

## 2. Coverage map

Mandatory 10-column schema per PS-REQ-TEST-TRACE v1.0 §4.2. W28E-1801A retired the remaining orphan markers in the chat-client source tests and bound the Stream-A design rows to existing downstream test owners. This table records test design and ownership; execution evidence remains the responsibility of the relevant Stream-B/C lane unless the row is a static validator executed by W28E-1801A.

| Test ID | Tier | Use case | Requirement | Surface | Scenario | Variants | Env files | Known issue | Last run commit |
|---|---|---|---|---|---|---|---|---|---|
| QT-TRACE-REQ-COVERAGE | QT | Requirements traceability | `NF-005`, `FR-018` | internal | Required docs, requirement IDs, test IDs, delivery matrix, and no orphan tests remain aligned. | Static repo scan | `tests/env-QT` | W28E-1801A validator reruns required after docs edits. | W28E-1801A |
| QT-PLATFORM-PACKAGE | QT | Platform package adoption | `NF-002` | internal | Config/logging/API/IDAM/LLM/VDB/DB/jobs/storage use platform packages and package declarations remain aligned. | Static repo scan | `tests/env-QT` | Stream-B should rerun after dependency edits. | c7f4796 |
| QT-LOGGING-AUDIT | QT | Logging and audit compliance | `NF-003` | internal | Defaults and audit docs define integrity, rotation, retention, and audit-event coverage. | Static repo scan | `tests/env-QT` | Stream-B should rerun after logging config edits. | c7f4796 |
| QT-SECRETS-VAULT | QT | Confidentiality and Vault separation | `NF-004` | cli, internal | Source/default config/private env handling keeps secrets outside committed material and uses Vault expressions where required. | Static repo scan | `tests/env-QT` | W28E-1801A also runs repository confidentiality scrub. | W28E-1801A |
| QT-RULES-COMPLIANCE | QT | RULES.md compliance | `NF-006` | internal | No hardcoded URLs/credentials, direct external imports, IT/AT skips/mocks, missing headers, or unreviewed public functions. | Static repo scan | `tests/env-QT` | Stream-B should rerun after source edits. | c7f4796 |
| QT-MIGRATION-COMPLETE | QT | Platform migration completeness | `NF-007` | internal | Raw YAML config, raw FastAPI/auth replacements, and unmanaged `os.environ` config access are rejected outside approved helpers. | Static repo scan | `tests/env-QT` | Stream-B should rerun after migration edits. | c7f4796 |
| QT-SECURITY-HYGIENE | QT | Security and operator copy hygiene | `NF-008` | cli, internal | Secret logging, traversal, injection-risk patterns, unsafe domain-specific behaviour, and non-UK-English operator copy are checked. | Static repo scan | `tests/env-QT` | Stream-B should rerun after user-facing copy edits. | c7f4796 |
| UT-HARNESS-FLOW | UT | Harness scripted flow | `FR-006`, `FR-012` | cli, internal | Runtime creates flows, injects assistant/user turns, pauses for operator checkpoint, continues, validates response, and completes/fails flow state. | Pure runtime fixture | `tests/env-UT` | None recorded. | c7f4796 |
| ST-HARNESS-ROUTES | ST | Harness HTTP routes | `FR-009`, `FR-012`, `FR-013` | api | `/v1/sessions/{id}/inject`, `/inject-sequence`, `/v1/test-flows`, transcript readback, and `/ui/config` harness flag are exercised against a running service. | Local four-server | `tests/env-ST` | Stream-B should rerun after harness API changes. | c7f4796 |
| AT-HARNESS-A2A | AT | Harness A2A/session visibility | `FR-008`, `FR-012`, `FR-013` | api, a2a | Injected operator and harness turns appear in transcript and A2A message/session streams for the same target session. | WebSocket event fanout | `tests/env-AT` | Stream-C should archive event payload proof when rerun. | c7f4796 |
| ST-WEBUI-FLOW | ST | API-backed WebUI chat flow | `FR-001`, `FR-009`, `FR-014` | api, webui | `/ui`, `/runtime-config.js`, `/ui/config`, session create, message send, and transcript retrieval prove the server-side chat flow. | Local four-server | `tests/env-ST` | Does not replace CL-26 browser proof. | c7f4796 |
| AT-WEBUI-CHAT-SUBMIT-CL26 | AT | Browser chat submit | `FR-008`, `FR-014` | webui, api | Browser fills the chat composer, clicks Send, observes a POST to message endpoint, renders the prompt, and waits for assistant response content. Negative assertions prove blank submit is rejected and unknown session submit returns 404. | Playwright Chromium | `tests/env-AT`, `tests/env-AT-local-docker` | W28E-1801C Stream-C archived browser evidence plus CL-34 preprod sentinel smoke. | W28E-1801C |
| AT-WEBUI-SESSION-MODEL | AT | Profile/session/chat model | `FR-008`, `FR-016` | webui, api | Browser creates session, navigates session history, validates session table anchors, and checks selected session visibility. | Playwright Chromium | `tests/env-AT` | Stream-B/C must extend for CL-19..CL-22 and CL-31 redesign details. | c7f4796 |
| AT-WEBUI-CANONICAL-NAV | AT | Dashboard/navigation/external services | `FR-008`, `FR-015`, `FR-017` | webui, api, mcp, a2a | Browser login, dashboard/nav, External Services details, settings/LLM visibility, and console-error gate cover canonical UI contracts. | Playwright Chromium | `tests/env-AT` | Stream-B/C must extend for all CL-04..CL-18 and CL-25/27/28/30 details. | c7f4796 |
| T0-LIVE-SMOKE-CL34 | QT | Live route and sentinel seed smoke | `FR-010`, `FR-015`, `NF-001` | api, mcp, a2a, webui | Live route smoke proves chatclient0 health/login/runtime config and canonical MCP/A2A route reachability as the chat-client portion of the four-sentinel design. | Live preprod URL | `tests/env-W28A-751-live` | CL-34 aggregate requires Stream-C browser smoke across chatclient0, expertagent0, notificationagent0, and filemcpserver0. | c7f4796 |
| T1-T3-LIVE-IDAM | AT | Live IDAM cascade | `FR-007`, `CS-001`..`CS-013` | api, mcp, a2a, webui | Admin/read-write/read-only login, RBAC denial, API-key/group cascade, MCP tool visibility, and A2A event visibility. | Live preprod URL | `tests/env-W28A-751-live` | Stream-C should rerun after auth/RBAC changes. | c7f4796 |
| IT-MCP-CONFORMANCE | IT | MCP protocol and external service integration | `FR-011` | mcp, api, a2a | MCP initialize/tools/resources, streamable HTTP/SSE, example external MCP servers, notification MCP, file-MCP, and managed-job integration. | Local/integration endpoints | `tests/env-IT` | Stream-B should rerun after MCP config or transport edits. | c7f4796 |
