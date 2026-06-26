# Cloud-Dog Chat Client

`chat-client` exposes the Cloud-Dog chat API, Web UI, MCP bridge, and A2A health surface.

## Publication Quick Start

Prerequisites:

- Docker 24 or newer with BuildKit enabled
- Python 3.12 if you run the package locally
- Network access to a Python package index serving the dependencies in
  `requirements.lock` (default: public PyPI, `https://pypi.org/simple/`)

Build an isolated publication-test image (public variant is the default):

```bash
PUBLICATION_TAG_SUFFIX=github-test ./docker-build.sh latest --variant public
```

See [EXTERNAL-BUILD.md](EXTERNAL-BUILD.md) for the full external-builder guide.

Run the local smoke by executing the shell block in [PUBLICATION-SMOKE.md](PUBLICATION-SMOKE.md) with `TAG=latest-gitea-test`.

The smoke run uses [env-example](env-example) and probes:

- API: `8050`
- Web: `8051`
- MCP: `8052`
- A2A: `8053`

## Local Development

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip setuptools wheel
# Single index only (PS-97 §3.3). Override the URL for your boundary index.
.venv/bin/pip install --index-url https://pypi.org/simple/ -e ".[dev]"
```

Runtime configuration is loaded from the env file passed to `server_control.sh`, then from shell environment variables, then from `defaults.yaml`.

## Authentication

Every authenticated API/MCP/A2A endpoint requires the **`X-API-Key`** user
credential (header name configurable via `client_api.api_key_header`).

**Admin-scope operations** — create/update/delete of users, groups, roles,
api-keys, profiles, RBAC bindings, and MCP servers — are protected by a
**dual-key, defence-in-depth pairing**: they require **`X-API-Key` (user creds)
AND `X-Admin-Key` (admin scope)**. Presenting only one header returns
`401 UNAUTHENTICATED` with a message naming **both** required headers (not a bare
"Missing X-API-Key"). The two schemes are documented per-endpoint in the OpenAPI
schema (`/openapi.json` → `components.securitySchemes.ApiKeyAuth` /
`AdminKeyAuth`).

```bash
# User-scope read (single key):
curl -sS -k -H "X-API-Key: <user-key>" \
  https://chatclient0.cloud-dog.net/api/v1/users

# Admin-scope mutation (BOTH keys required):
curl -sS -k \
  -H "X-API-Key: <user-key>" \
  -H "X-Admin-Key: <admin-key>" \
  -X POST https://chatclient0.cloud-dog.net/api/v1/users \
  -d '{"username": "alice", "role": "user"}'

# Only one header -> 401 naming both:
curl -sS -k -H "X-Admin-Key: <admin-key>" \
  -X POST https://chatclient0.cloud-dog.net/api/v1/users -d '{}'
# {"ok":false,"errors":[{"code":"UNAUTHENTICATED",
#   "message":"This endpoint requires X-API-Key (user creds) AND X-Admin-Key (admin scope) headers"}]}
```

## Documentation

- [BUILD.md](BUILD.md)
- [PUBLICATION-SMOKE.md](PUBLICATION-SMOKE.md)
- [env-example](env-example)

## Licence

Apache-2.0 - Copyright (c) 2026 Cloud-Dog, Viewdeck Engineering Limited

## Security & Publication Notes

Authentication and authorisation use the platform IDAM credential/cert model; do not commit secrets.
This public source mirror excludes internal operations material; build artefacts (e.g. the UI bundle) are regenerated at build time.
