# Test Scope Map — chat-client

W28E-1801A Stream-A status: this file is the active scope map for requirements/test-design ownership. It maps service source areas to bound pytest suites and identifies central test packs consumed by reference only.

## Source to test mapping

| Source module | QT | UT | ST | IT | AT |
|--------------|----|----|----|----|-----|
| `src/cloud_dog_chat_client/api/routes.py` | `tests/quality/QT*` | `UT1.6_ClientAPIAuth` | `ST1.2_ClientAPIReadiness`, `ST1.3_ClientAPILLM` | `IT2.11_SQLAgentMCP` | `AT1.1_ChatClientConversation`, `AT1.3_ClientAPIStreaming` |
| `src/cloud_dog_chat_client/llm/service.py` | `tests/quality/QT*` | `UT1.3_OllamaProvider` | `ST1.1_OllamaReadiness`, `ST1.4_OllamaModels` | `IT2.9_SearchMCP` | `AT1.7_SQLAgentThinking`, `AT1.17_UkrainianNewsBriefing` |
| `src/cloud_dog_chat_client/mcp/client.py` | — | `UT1.4_MCPClient`, `UT1.5_MCPConformance` | `ST1.11_FileMCPRuntimeReadiness`, `ST1.13_MCPServerAdminRBAC` | `IT2.13_FileMCPToolCoverage`, `IT2.18_ExpertAgentHungarianMCP` | `AT1.2_ChatClientMCP`, `AT1.11_FileMCPPathOps` |
| `src/cloud_dog_chat_client/cli/app.py` | `tests/quality/QT*` | `UT1.7_CLIInteractive` | `ST1.5_CLIChat`, `ST1.7_ServerOnlyCLI` | `tests/integration/IT2.5_ExampleMCPServer` | `AT1.4_CLIChat`, `AT1.27_PeriodicResearchMonitor` |
| `src/cloud_dog_chat_client/session/session_manager.py` | — | `UT1.2_SessionLogging` | `ST1.6_SessionPersistence` | `IT2.11_SQLAgentMCP` | `AT1.9_SearchFileResumeRecovery`, `AT1.23_CrossProjectSpamAnalysis` |
| `src/cloud_dog_chat_client/database/store.py` | `tests/quality/QT*` | `UT1.8_DatabaseAbstraction` | `ST1.15_DatabaseAbstraction` | `tests/integration/IT2.11_SQLAgentMCP` | `AT1.22_DatabaseE2E` |
| `src/cloud_dog_chat_client/mcp/transports/streamable_http.py` | — | `UT1.4_MCPClient`, `UT1.5_MCPConformance` | `ST1.12_ContainerHostNetworkReadiness` | `IT2.9_SearchMCP` | `AT1.5_SearchMCPMultiStage`, `AT1.24_WebSearchAgentSourceDiscovery` |
| `src/cloud_dog_chat_client/llm/response_policy.py` | `tests/quality/QT*` | `UT1.9_DeliveryArtifacts` | `ST1.8_ResponseFormatting`, `ST1.9_ResponseFormattingRaw`, `ST1.10_ResponseFormattingDefault` | `tests/integration/IT2.9_SearchMCP` | `AT1.10_FileArtifactsDeterminism`, `AT1.15_HungarianTranslatorHardFailure` |
| Package declarations / WebUI non-LLM standards | `tests/quality/QT_COMPLIANCE/test_qt_package_adoption.py`, `tests/quality/QT_PACKAGE_COMPLIANCE/test_package_compliance.py` | — | — | — | UI monorepo `apps/chat-client/tests/e2e/non-llm-standards.spec.ts` |

## PS-REQ-TEST-TRACE Source Glob Map

| Source glob / design source | Requirement rows | Test IDs / suites | Tier(s) | Surfaces | Notes |
|---|---|---|---|---|---|
| `src/cloud_dog_chat_client/api/**/*.py` | `FR-006`, `FR-009`, `FR-012`, `FR-013`, `FR-014`, `CS-001`..`CS-013` | `ST-HARNESS-ROUTES`, `ST-WEBUI-FLOW`, `T1-T3-LIVE-IDAM`, `UT1.6_ClientAPIAuth` | UT, ST, AT | api, webui | API auth, sessions, harness injection, message send, transcript, RBAC denials. |
| `src/cloud_dog_chat_client/test_harness/**/*.py` | `FR-012`, `FR-013` | `UT-HARNESS-FLOW`, `ST-HARNESS-ROUTES`, `AT-HARNESS-A2A` | UT, ST, AT | api, a2a, internal | Jun26 supplement owner for injected prompts/responses and pause/continue flows. |
| `src/cloud_dog_chat_client/servers/**/*.py` | `FR-001`, `FR-002`, `FR-010`, `FR-015`, `NF-001` | `ST1.18_FourServerPattern`, `T0-LIVE-SMOKE-CL34`, `AT-WEBUI-CANONICAL-NAV` | ST, QT, AT | api, mcp, a2a, webui | Four-server routing, login/runtime config, canonical console routes. |
| `src/cloud_dog_chat_client/mcp/**/*.py` | `FR-002`, `FR-011`, `FR-015` | `IT-MCP-CONFORMANCE`, `ST1.13_MCPServerAdminRBAC`, `AT-WEBUI-CANONICAL-NAV` | ST, IT, AT | mcp, api, webui | MCP transport/admin/tooling and External Services visibility. |
| `src/cloud_dog_chat_client/a2a/**/*.py`, `src/cloud_dog_chat_client/servers/a2a*.py` | `FR-002`, `FR-007`, `FR-013`, `FR-015` | `AT-HARNESS-A2A`, `T1-T3-LIVE-IDAM`, `IT-MCP-CONFORMANCE` | IT, AT | a2a, api | Session/message event fanout and live IDAM event visibility. |
| `src/cloud_dog_chat_client/session/**/*.py` | `FR-006`, `FR-013`, `FR-016`, `FR-017` | `UT1.2_SessionLogging`, `ST1.6_SessionPersistence`, `AT-WEBUI-SESSION-MODEL` | UT, ST, AT | api, webui, internal | Session persistence, transcript state, switching, and chat history. |
| `src/cloud_dog_chat_client/llm/**/*.py` | `FR-003`, `FR-014`, `FR-017` | `UT1.3_OllamaProvider`, `ST-WEBUI-FLOW`, `AT-WEBUI-CHAT-SUBMIT-CL26` | UT, ST, AT | api, webui, internal | Provider config, response policy, chat submit, error/retry/model-test design. |
| `docs/**/*.md`, `tests/**/*.py`, `pyproject.toml`, `defaults.yaml` | `NF-002`..`NF-008`, `FR-018` | `QT-TRACE-REQ-COVERAGE`, `QT-PLATFORM-PACKAGE`, `QT-LOGGING-AUDIT`, `QT-SECRETS-VAULT`, `QT-RULES-COMPLIANCE`, `QT-MIGRATION-COMPLETE`, `QT-SECURITY-HYGIENE` | QT | internal, cli | Static quality and traceability controls. |
| GarysWorkingNotes CL-26 | `FR-014` | `AT-WEBUI-CHAT-SUBMIT-CL26` | AT | webui, api | Stream-C browser proof driver; no curl-only substitute. |
| GarysWorkingNotes CL-34 | `NF-001` | `T0-LIVE-SMOKE-CL34` and Stream-C aggregate browser smoke | QT, AT | webui, api, mcp, a2a | Four-sentinel browser smoke driver across chatclient0, expertagent0, notificationagent0, filemcpserver0. |

## Central Test Pack References

| Pack | Consumed rows | Local materialization | Copy policy |
|---|---|---|---|
| `TP-COMMON` | `NF-002`..`NF-008`, `CS-001`..`CS-013` | Referenced by `tests/fixtures/TEST-PACK-REFERENCE.md` | Central zip/preview is not copied into this repo. |
| `TP-INTEGRATION-EXAMPLES` | `FR-011`, `FR-012`, `FR-013`, `FR-018` | Referenced by `tests/fixtures/TEST-PACK-REFERENCE.md` | Central zip/preview is not copied into this repo. |

## W28A-118D non-LLM traceability

| Scope item | Evidence |
|---|---|
| Package drift (`cloud_dog_llm`, jobs, storage) | `pyproject.toml`, `requirements.txt`, `README.md`, `docs/ARCHITECTURE.md`, and `tests/quality/QT_COMPLIANCE/test_qt_package_adoption.py` |
| Settings/config display | UI monorepo `apps/chat-client/tests/e2e/non-llm-standards.spec.ts` |
| MCP/A2A console and tool metadata rendering | UI monorepo `apps/chat-client/tests/e2e/non-llm-standards.spec.ts` |
| File-transfer/proxy surfaces | UI monorepo `apps/chat-client/tests/e2e/non-llm-standards.spec.ts` |
| Docs/about/profile metadata and empty/error states | UI monorepo `apps/chat-client/tests/e2e/non-llm-standards.spec.ts` |
| Deferred LLM flows | Generated answers, chat response quality, Ragflow, translation, summarisation, and model-backed chat flows remain out of scope. |

## Scoped run examples

If you changed `src/cloud_dog_chat_client/mcp/client.py`, run:

```bash
pytest tests/unit/UT1.4*/ tests/unit/UT1.5*/ tests/system/ST1.11*/ tests/system/ST1.13*/ tests/integration/IT2.13*/ tests/integration/IT2.18*/ tests/application/AT1.2*/ tests/application/AT1.11*/ -v
```

If you changed `src/cloud_dog_chat_client/llm/service.py`, run:

```bash
pytest tests/unit/UT1.3*/ tests/system/ST1.1*/ tests/system/ST1.4*/ tests/integration/IT2.9*/ tests/application/AT1.7*/ tests/application/AT1.17*/ -v
```

If you changed `src/cloud_dog_chat_client/api/routes.py`, run:

```bash
pytest tests/unit/UT1.6*/ tests/system/ST1.2*/ tests/system/ST1.3*/ tests/integration/IT2.11*/ tests/application/AT1.1*/ tests/application/AT1.3*/ -v
```
