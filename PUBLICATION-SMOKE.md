# Publication smoke test

External local-Docker smoke. Starts the published image with the checked-in
example env file and probes the local API, Web, MCP, and A2A surfaces.

```bash
set -euo pipefail
TAG="${TAG:-latest}"
NAME="${NAME:-chat-client-publication-smoke}"
SMOKE_ATTEMPTS="${SMOKE_ATTEMPTS:-120}"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$NAME" --network host \
  -e CHAT_CLIENT_ENV_FILE=/app/env \
  -v "$PWD/env-example:/app/env:ro" \
  "cloud-dog-chat-client:$TAG"

probe_url() {
  local url="$1" code attempt
  for attempt in $(seq 1 "$SMOKE_ATTEMPTS"); do
    code="$(curl -sS -o /dev/null -w '%{http_code}' -m 5 "$url" || true)"
    case "$code" in
      2*|3*|401|403|405) echo "PASS $url -> $code"; return 0 ;;
    esac
    sleep 1
  done
  echo "FAIL $url -> ${code:-000}" >&2
  docker logs "$NAME" >&2 || true
  return 1
}

probe_url http://127.0.0.1:8050/health
probe_url http://127.0.0.1:8051/
probe_url http://127.0.0.1:8052/mcp/health
probe_url http://127.0.0.1:8053/health
probe_url http://127.0.0.1:8053/.well-known/agent.json

echo "RESULT: PASS"
```

Expected result: all probes pass. HTTP redirects or auth-gated 401/403 responses
are acceptable because they prove that the published surface is running and
routing.
