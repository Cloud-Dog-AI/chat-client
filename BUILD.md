# Build Instructions

## Project
`chat-client` - interactive chat and session management service with optional MCP integrations.

## Prerequisites
- Python 3.10+
- Docker 24+
- pip

## Development Setup
```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e ".[dev]"
```

If the cloud-dog platform packages are served from a specific package index, point
pip at that single index (no `--extra-index-url`; PS-97 §3.3 / §4):
```bash
PYPI_URL=https://pypi.org/simple/   # or your boundary index
.venv/bin/pip install --index-url "$PYPI_URL" -e ".[dev]"
```

## Local Configuration
```bash
cat > .env.local <<'ENV'
CLOUD_DOG__API_SERVER__PORT=8050
CLOUD_DOG__WEB_SERVER__PORT=8051
CLOUD_DOG__MCP_SERVER__PORT=8052
CLOUD_DOG__A2A_SERVER__PORT=8053
CLOUD_DOG__DB__URL=sqlite:///./database/chat-client.db
ENV
```

## Run Locally
```bash
./server_control.sh --env ./.env.local start all
./server_control.sh --env ./.env.local status all
./server_control.sh --env ./.env.local stop all
```

## Run Tests
```bash
.venv/bin/python -m pytest tests/quality --env ./.env.test -v
.venv/bin/python -m pytest tests/unit --env ./.env.test -v
.venv/bin/python -m pytest tests/system --env ./.env.test -v
.venv/bin/python -m pytest tests/integration --env ./.env.test -v
.venv/bin/python -m pytest tests/application --env ./.env.test -v
```

## Build
### Python Package
```bash
.venv/bin/python -m pip install build
.venv/bin/python -m build
```

### Docker Container
Build the public chat-client image (public variant is the default):
```bash
PUBLICATION_TAG_SUFFIX=publication-test ./docker-build.sh latest --variant public
```

Build with an explicit single package index (no `--extra-index-url`):
```bash
PYPI_URL=https://pypi.org/simple/ \
PUBLICATION_TAG_SUFFIX=publication-test ./docker-build.sh latest --variant public
```

The internal/dev variant (`--variant dev`) builds `Dockerfile.chat-client` and
requires `PYPI_URL` to be set to the internal index; it is not part of the public
publication path. See [EXTERNAL-BUILD.md](EXTERNAL-BUILD.md).

## Docker Push
```bash
docker tag cloud-dog-chat-client:latest registry.example.com/team/chat-client:latest
docker push registry.example.com/team/chat-client:latest
```

## Configuration
The runtime loads the env file passed to `server_control.sh`, then applies any higher-priority shell environment variables, and finally falls back to `defaults.yaml`.

## Local Secrets
Put local-only values in the env file passed to `server_control.sh` or mounted into Docker. Do not commit real credentials.
