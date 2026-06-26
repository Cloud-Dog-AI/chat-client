# chat-client — External Build Guide

Self-contained instructions for an external builder to build and smoke-test the
`cloud-dog-chat-client` service from a fresh public clone, with no access to
Cloud-Dog internal infrastructure (no Vault, no internal PyPI, no internal
registry, no internal hostnames).

## What this service is

`chat-client` exposes the Cloud-Dog chat API, a Web UI, an MCP bridge, and an A2A
health surface. The Web UI ships as a **pre-built static bundle** under `ui/dist/`
— no Node.js / npm toolchain is required to build the image.

Default ports (from `defaults.yaml`, mirrored by `Dockerfile.public` `EXPOSE`):

| Surface | Port | Env override |
|---------|------|--------------|
| API     | 8050 | `CLOUD_DOG__API_SERVER__PORT` |
| Web UI  | 8051 | `CLOUD_DOG__WEB_SERVER__PORT` |
| MCP     | 8052 | `CLOUD_DOG__MCP_SERVER__PORT` |
| A2A     | 8053 | `CLOUD_DOG__A2A_SERVER__PORT` |

All configuration uses the `CLOUD_DOG__` env prefix with `__` as the nesting
delimiter (e.g. `CLOUD_DOG__LLM__MODEL`).

## Prerequisites

- **Linux / macOS / Windows (WSL2):** Docker 24+ with BuildKit enabled.
- Python 3.12 only if you intend to run the package outside Docker.
- Network access to a Python package index that serves the dependencies in
  `requirements.lock`. The default is public PyPI (`https://pypi.org/simple/`).

> **Cloud-Dog platform packages.** The `cloud-dog-*` dependencies
> (`cloud-dog-config`, `cloud-dog-logging`, `cloud-dog-api-kit`, `cloud-dog-idam`,
> `cloud-dog-llm`, `cloud-dog-agent`, `cloud-dog-cache`, `cloud-dog-db`,
> `cloud-dog-jobs`, `cloud-dog-storage`) must be available on the active index, or
> installed from their GitHub-mirrored source (`pip install` from
> `github.com/cloud-dog-ai/<pkg>`), before the image can build end-to-end. The
> publication pipeline supplies the exact published versions. **Never add
> `--extra-index-url`** to work around a missing package (PS-97 §3.3 / §4) — stop and
> report the gap instead.

## Docker build (recommended)

The public image is built with the `public` variant (the default):

```bash
# Default: public PyPI index, public CA trust store, no internal references.
./docker-build.sh latest --variant public
```

To point at a different boundary index (e.g. a Gitea public index) without baking
an internal host into the image, supply it at build time:

```bash
PYPI_URL=https://your-boundary-index.example.com/simple/ \
  ./docker-build.sh latest --variant public
```

For an isolated, non-pushing publication test build, add a tag suffix:

```bash
PUBLICATION_TAG_SUFFIX=github-test ./docker-build.sh latest --variant public
# image: cloud-dog-chat-client:latest-github-test (registry tag skipped)
```

The build:
- uses a single `--index-url` only (no `--extra-index-url`, no internal host);
- mounts package-index credentials (if any) as a BuildKit secret so they never
  enter image layers;
- copies the **pre-built** `ui/dist/` bundle — no npm install.

## Pure-source / package path (without Docker)

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip setuptools wheel
# Single index only. Replace the URL with your boundary index if needed.
.venv/bin/pip install --index-url https://pypi.org/simple/ -r requirements.lock
.venv/bin/pip install --index-url https://pypi.org/simple/ .
```

## Lockfile check

`requirements.lock` pins the full dependency set. Verify it covers every direct
dependency declared in `pyproject.toml`:

```bash
python3 -c "import tomllib,re; \
  d=tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; \
  L=open('requirements.lock').read().lower(); \
  miss=[x for x in d if re.split(r'[<>=!~ ]', x.replace('_','-').lower())[0] not in L]; \
  print('MISSING:', miss) if miss else print('LOCK OK: all direct deps present')"
```

Expected output: `LOCK OK: all direct deps present`.

There is **no npm lockfile** because there is no JavaScript build step in the
publishable tree — the UI is shipped pre-built (see `requirements-npm.EXCEPTION.md`).

## Smoke test

After building, run the smoke block in [PUBLICATION-SMOKE.md](PUBLICATION-SMOKE.md):

```bash
TAG=latest-github-test bash -c "$(sed -n '/^```bash$/,/^```$/p' PUBLICATION-SMOKE.md | sed '1d;$d')"
```

It starts the image with `docker-env.public.example`, then probes the API, Web,
MCP and A2A surfaces. Redirects and auth-gated `401/403` responses are accepted as
proof the surface is live.

## Returning evidence

Capture the following and return them to the requester as a tarball plus a
checksum:

```bash
mkdir -p evidence
./docker-build.sh latest --variant public 2>&1 | tee evidence/build.log
docker inspect --format '{{.Id}}' cloud-dog-chat-client:latest | tee evidence/image-digest.txt
TAG=latest bash ./run-smoke.sh 2>&1 | tee evidence/smoke.log   # or paste PUBLICATION-SMOKE.md block
tar czf chat-client-external-build-evidence.tar.gz evidence/
sha256sum chat-client-external-build-evidence.tar.gz | tee chat-client-external-build-evidence.sha256
```

Send `chat-client-external-build-evidence.tar.gz` and the `.sha256` file.

## Self-contained clone

See [EXTERNAL-CLONE-SELF-CONTAINED.md](EXTERNAL-CLONE-SELF-CONTAINED.md): a fresh
public clone of this repository is self-contained for the chat-client build — no
git submodules or sibling repositories are required.
