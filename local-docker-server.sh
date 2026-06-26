#!/usr/bin/env bash
# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

usage() {
  cat <<USAGE
Usage:
  ./local-docker-server.sh --env <control-env-file> <start|stop|restart|status|ensure>

Control env keys:
  LOCAL_DOCKER_SOURCE_ENV=<runtime-env-file>
  TEST_API_BASE_URL=<optional explicit API base URL>
USAGE
}

abspath() {
  local p="$1"
  if [[ "$p" = /* ]]; then
    printf '%s\n' "$p"
  else
    printf '%s\n' "$SCRIPT_DIR/$p"
  fi
}

load_env_file() {
  local env_file="$1"
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    line="${line#export }"
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    key="$(printf '%s' "$key" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    value="${value#${value%%[![:space:]]*}}"
    value="${value%${value##*[![:space:]]}}"
    if [[ ( "$value" == '"'*'"' ) || ( "$value" == "'"*"'" ) ]]; then
      value="${value:1:${#value}-2}"
    fi
    export "${key}=${value}"
  done < "$env_file"
}

resolve_health_url() {
  if [[ -n "${TEST_API_HEALTH_URL:-}" ]]; then
    printf '%s\n' "$TEST_API_HEALTH_URL"
    return
  fi
  if [[ -n "${TEST_API_BASE_URL:-}" ]]; then
    printf '%s\n' "${TEST_API_BASE_URL%/}/health"
    return
  fi
  local host="${CLOUD_DOG__CLIENT_API__HOST:-127.0.0.1}"
  local port="${CLOUD_DOG__CLIENT_API__PORT:-8090}"
  printf '%s\n' "http://${host}:${port}/health"
}

if [[ $# -lt 3 || "$1" != "--env" ]]; then
  usage
  exit 2
fi

CONTROL_ENV="$(abspath "$2")"
ACTION="$3"
[[ -f "$CONTROL_ENV" ]] || { echo "CRITICAL ERROR: env file not found: $CONTROL_ENV" >&2; exit 2; }

load_env_file "$CONTROL_ENV"
if [[ -n "${LOCAL_DOCKER_SOURCE_ENV:-}" ]]; then
  RUNTIME_ENV="$(abspath "$LOCAL_DOCKER_SOURCE_ENV")"
  [[ -f "$RUNTIME_ENV" ]] || { echo "CRITICAL ERROR: runtime env not found: $RUNTIME_ENV" >&2; exit 2; }
  load_env_file "$RUNTIME_ENV"
else
  RUNTIME_ENV="$CONTROL_ENV"
fi

case "$ACTION" in
  start)
    bash ./server_control.sh --env "$RUNTIME_ENV" start api
    ;;
  stop)
    bash ./server_control.sh --env "$RUNTIME_ENV" stop api
    ;;
  restart)
    bash ./server_control.sh --env "$RUNTIME_ENV" stop api || true
    bash ./server_control.sh --env "$RUNTIME_ENV" start api
    ;;
  status)
    bash ./server_control.sh --env "$RUNTIME_ENV" status api
    ;;
  ensure)
    bash ./server_control.sh --env "$RUNTIME_ENV" start api
    HEALTH_URL="$(resolve_health_url)"
    for _ in $(seq 1 30); do
      if curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
        echo "ensure: healthy ($HEALTH_URL)"
        exit 0
      fi
      sleep 1
    done
    echo "CRITICAL ERROR: health check failed: $HEALTH_URL" >&2
    exit 3
    ;;
  *)
    usage
    exit 2
    ;;
esac
