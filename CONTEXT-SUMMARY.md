# chat-client Context Summary

Date: 2026-05-08

## Completed Workstream

Workstream: `W28A-101a-chat-client-PYTHON-312-RERUN-2026-05-08`

Instruction file:

`/opt/iac/Development/cloud-dog-ai/cloud-dog-ai-platform-standards/working/instructions/W28A-101a-chat-client-PYTHON-312-RERUN-2026-05-08.md`

Scope was `chat-client` only. The Python 3.12.13 verification is complete.

## Final Evidence

All evidence logs are under `chat-client/working/`.

- PS-100 guard: `w28a-101-ps100-python-runtime-guard.log`, Python `3.12.13`, `asyncio_cross_thread=pass`, `runtime_guard=pass`.
- UT: `w28a-101-ut.log`, `98 passed, 14 warnings`.
- ST: `w28a-101-st.log`, `43 passed, 17 warnings`.
- IT: `w28a-101-it.log`, `23 passed, 8 warnings`.
- AT: `w28a-101-at.log`, `47 passed, 9 warnings`.
- QT: `w28a-101-qt.log`, `57 passed`.
- Package build: `w28a-101-build.log`, sdist and wheel built.
- Docker build: `w28a-101-docker-build.log`, `cloud-dog-chat-client:latest` built successfully.
- Server smoke: `w28a-101-server-smoke.log`, API/Web/MCP/A2A started via `server_control.sh`, all returned HTTP `200 ok`, then all stopped.
- `git diff --check`: `w28a-101-diff-check.log`, clean.
- Active Python 3.10/3.11 grep: `w28a-101-stale-python-grep.log`, no matches.
- `--trusted-host pypi.org` regression grep: `w28a-101-trusted-host-grep.log`, no matches.
- `py_compile`: `w28a-101-py-compile.log`, clean.

## Fixes Applied

- `server_control.sh` now uses the repo `.venv` interpreter where present and hydrates env-file Vault placeholders before loading `ConfigManager`, matching the required Python 3.12 runtime path.
- Test env resolution handles Vault root payloads stored as a JSON string without printing or persisting secret values.
- Delivery-artifact expectations now match the Python 3.12 Docker base image.
- CLI system tests use `sys.executable` rather than a bare `python3`.
- QT doc/source findings were closed for traceability wording, hardcoded loopback detection, and docstring coverage.
- Dev dependencies include Playwright because the application suite requires it.

## Operational Notes

- The IMAP preprod/local container `imapmcpserver0.app.vpc0.cloud-dog.net` was unhealthy during AT validation. It was restarted, then `/mcp/health`, JSON-RPC `/mcp` `tools/list`, and `/mcp/tools` all returned HTTP 200. This was an infrastructure repair, not a test bypass.
- SQL-agent and notification-agent workstreams were not touched.
- The untracked `.venv.ps100-backup-20260508T113636Z/` directory remains untracked and was not committed.

## W28A-861-R3 Publication Prep (2026-06-07)

Workstream: `W28A-861-R3-PUBLICATION-PREP-EXTERNAL-BUILD-LEAKAGE-HARDENING`, scope chat-client only.

Added/changed:
- `Dockerfile.public` — public-boundary build variant; default index public PyPI via `PIP_INDEX_URL` build ARG; single `--index-url` only (no `--extra-index-url`, PS-97 §3.3/§4); no internal host, no private CA, no SSH server; ships the pre-built `ui/dist/` static bundle (no npm).
- `docker-env.public.example` — public env example; ports 8050/8051/8052/8053 (API/Web/MCP/A2A) derived from `defaults.yaml`; `CLOUD_DOG__` prefix; `<your-...-here>` placeholders.
- `docker-build.sh` — `--variant public|dev`; public default = pypi.org, dev builds `Dockerfile.chat-client` against internal index supplied via `PYPI_URL`; removed `--extra-index-url` in favour of single `index-url`.
- `requirements.lock` — platform-package floor pins + full third-party transitive closure resolved from pypi.org; consistency check passes (all 20 direct deps present).
- `requirements-npm.EXCEPTION.md` — no npm lockfile: the UI is shipped pre-built; no JS build step in the publishable tree.
- `EXTERNAL-BUILD.md` + `EXTERNAL-CLONE-SELF-CONTAINED.md` — external-builder guide + §7 submodule audit.
- Leakage scrub: README.md/BUILD.md de-internalised; stale `ui/dist.bak.*` removed; `.publish-exclude` extended to deny internal docs/build-variant/env-profiles/SSH-key material.

§7 submodule verdict: a fresh public clone is SELF-CONTAINED for chat-client. The three `.gitmodules` entries are public github.com repos (modelcontextprotocol/servers, example-remote-server, danny-avila/Example-MCP-Server), are uninitialised in a fresh clone, are excluded by `.dockerignore`, and are referenced by no build file. Closes W28A-580.

Honest build attempts (server2, DOCKER_HOST=tcp://server2.viewdeck.com:2375):
- public variant against pypi.org: builds correctly through the multi-stage image; pip resolves via single pypi.org index then fails at `cloud_dog_config>=0.3.1` ("No matching distribution") because the cloud-dog platform packages are not yet on public PyPI (known boundary dependency / publication-pipeline pre-publish step). Not a defect in this lane.
- dev variant against pypi.cloud-dog.net: pip prompts for credentials (401) — internal index requires Vault-backed PYPI creds which were not scavenged (RULES §9.2). Consistent with R2's credentialed build.

Cross-repo finding (NOT changed here — different repo): `cdci/configs/publish-manifests/chat-client.yml` `public_dirs` still lists `Dockerfile.chat-client` + `pip.conf.docker` and does NOT list `Dockerfile.public`/`docker-env.public.example`. The manifest must be updated by the cdci owner (W28A-879) to ship the public variant and drop the internal one. The repo-local `.publish-exclude` already denies `Dockerfile.chat-client`/`pip.conf.docker`/`scripts/validate-vault.sh` so they will not leak even under the current manifest.
