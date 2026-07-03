---
doc-id: RELEASE-1.0RC01
project: chat-client
status: RELEASE_PROOF
updated: 2026-06-24
lane: W28E-1801C
---

# chat-client 1.0RC01 Release Notes

## Stream-C Status

W28E-1801C Stream-C closes the WebUI/E2E 1.0RC01 scope for chat-client. The authoritative release proof is recorded under `public release checklist/working/evidence/W28E-1801C/current/` and includes the pushed main proof, preprod deployment proof, four-sentinel browser smoke, and release tags.

## Changes

- Added Playwright coverage for CL-26 `/chat` submit through `tests/application/AT_WEBUI_E2E/test_webui_e2e.py::test_t13_cl26_chat_submit_positive_and_negative`.
- Proved blank browser composer submit stays disabled, blank same-origin submit returns 400, and unknown-session submit returns 404.
- Proved successful browser submit posts to the session message endpoint and yields a non-empty assistant transcript response.

## Evidence

- `public release checklist/working/evidence/W28E-1801C/current/local-playwright/at-webui-full-junit.xml`
- `public release checklist/working/evidence/W28E-1801C/current/07-local-docker-playwright-junit.xml`
- `public release checklist/working/evidence/W28E-1801C/current/10a-four-sentinel-browser-smoke.tsv`
- `public release checklist/working/evidence/W28E-1801C/current/final-evidence-validator.txt`
