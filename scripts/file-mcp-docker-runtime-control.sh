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
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
UPSTREAM_FILE_MCP_ROOT="${PROJECT_ROOT}/../file-mcp-server"

ENV_PATH=""
ACTION=""
CONFIG_PATH=""
DEFAULTS_PATH=""
PIDFILE=""

wait_for_health() {
  local url="$1"
  local timeout_seconds="${2:-30}"
  local start_ts
  start_ts="$(date +%s)"
  while true; do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    if (( "$(date +%s)" - start_ts >= timeout_seconds )); then
      echo "CRITICAL ERROR: file-mcp runtime not ready at ${url}" >&2
      return 1
    fi
    sleep 1
  done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      ENV_PATH="${2:-}"
      shift 2
      ;;
    --config)
      CONFIG_PATH="${2:-}"
      shift 2
      ;;
    --defaults)
      DEFAULTS_PATH="${2:-}"
      shift 2
      ;;
    --pidfile)
      PIDFILE="${2:-}"
      shift 2
      ;;
    --profile)
      shift 2
      ;;
    start)
      ACTION="start"
      shift
      ;;
    stop|status|restart|ensure)
      ACTION="$1"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${ENV_PATH}" ]]; then
  echo "ERROR: --env is required" >&2
  exit 2
fi

if [[ -z "${ACTION}" ]]; then
  echo "ERROR: lifecycle action is required" >&2
  exit 2
fi

if [[ ! -f "${ENV_PATH}" ]]; then
  echo "ERROR: env file not found: ${ENV_PATH}" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "${ENV_PATH}"
set +a

CONFIG_PATH="${CONFIG_PATH:-${PROJECT_ROOT}/working/file-mcp-runtime/config.yaml}"
DEFAULTS_PATH="${DEFAULTS_PATH:-${PROJECT_ROOT}/working/file-mcp-runtime/defaults.yaml}"
PIDFILE="${PIDFILE:-${PROJECT_ROOT}/working/file-mcp-runtime/file-mcp-server.pid}"
HEALTH_HOST="${FILE_MCP_HTTP_HOST:-127.0.0.1}"
HEALTH_PORT="${FILE_MCP_HTTP_PORT:-8062}"
HEALTH_PATH="${FILE_MCP_HTTP_HEALTH_PATH:-/health}"
HEALTH_URL="http://${HEALTH_HOST}:${HEALTH_PORT}${HEALTH_PATH}"
READY_TIMEOUT_SECONDS="${FILE_MCP_READY_TIMEOUT_SECONDS:-60}"

upstream_control() {
  bash "${UPSTREAM_FILE_MCP_ROOT}/server_control.sh" \
    --env "${ENV_PATH}" \
    --config "${CONFIG_PATH}" \
    --defaults "${DEFAULTS_PATH}" \
    --pidfile "${PIDFILE}" \
    "$@"
}

case "${ACTION}" in
  start|ensure)
    if [[ "${ACTION}" == "ensure" ]] && curl -fsS --max-time 2 "${HEALTH_URL}" >/dev/null 2>&1; then
      exit 0
    fi
    upstream_control stop mcp >/dev/null 2>&1 || true
    upstream_control start mcp
    wait_for_health "${HEALTH_URL}" "${READY_TIMEOUT_SECONDS}"
    ;;
  stop)
    upstream_control stop mcp
    ;;
  status)
    upstream_control status mcp
    ;;
  restart)
    upstream_control stop mcp >/dev/null 2>&1 || true
    upstream_control start mcp
    wait_for_health "${HEALTH_URL}" "${READY_TIMEOUT_SECONDS}"
    ;;
  *)
    echo "Unsupported action: ${ACTION}" >&2
    exit 2
    ;;
esac
