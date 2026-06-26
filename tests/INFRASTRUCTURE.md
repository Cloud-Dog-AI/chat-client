# Test Infrastructure Requirements

This document defines the real infrastructure expected by each test tier.
All runs must pass `--env <file>` and use real services (no stubs/mocks) for IT/AT.

## UT (unit)

- Scope: isolated unit contracts.
- Required infrastructure: none.
- Notes:
- still uses config loading and env overlays.
- no external network dependency should be required by assertions.

## ST (system)

- Scope: chat-client runtime behaviours (API/CLI/UI/system flows).
- Required infrastructure:
- local chat-client API started by test fixtures (via `server_control.sh`).
- remote LLM endpoint configured in env (`https://llm.example.com`).
- Docker daemon available for container readiness scenarios (for `ST1.12`).

## IT (integration)

- Scope: protocol-level and cross-service integration.
- Required infrastructure:
- local chat-client API fixture runtime.
- Docker daemon for MCP conformance containers (example servers, legacy SSE targets).
- reachable MCP services configured per env profile:
- example MCP targets (local docker-hosted).
- search/sql/file/expert MCP endpoints as configured in `tests/env-IT`.
- auth dependencies as configured by each profile (including OAuth-compatible flows where required).

## AT (application)

- Scope: end-to-end user workflows and multi-step orchestration.
- Required infrastructure:
- local chat-client API fixture runtime.
- real LLM endpoint (`https://llm.example.com`) with configured model profiles.
- reachable MCP dependencies from `tests/env-AT`:
- search MCP.
- sql-agent MCP.
- file MCP runtime.
- expert-agent Hungarian translator MCP.
- long-call tolerance for slow MCP/LLM paths (timeouts configured via env).

## Standard Startup Pattern

Use env-vault first, then execute a single suite at a time:

```bash
set -a; source /opt/iac/Development/cloud-dog-ai/env-vault; set +a
python3 -m pytest tests/<tier>/ --env tests/env-<TIER> -v --tb=short
```

## Dependency Failure Policy

- UT/ST failures due missing local runtime setup should be fixed in fixtures/config.
- IT/AT missing external dependency must fail explicitly with root cause.
- Do not hide missing infrastructure with `skip` workarounds.
