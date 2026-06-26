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

PASS=0
FAIL=0

check_cmd() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS: ${label}"
    PASS=$((PASS + 1))
  else
    echo "FAIL: ${label}"
    FAIL=$((FAIL + 1))
  fi
}

check_text() {
  local label="$1"
  local file="$2"
  local pattern="$3"
  if grep -qE -- "${pattern}" "${file}"; then
    echo "PASS: ${label}"
    PASS=$((PASS + 1))
  else
    echo "FAIL: ${label}"
    FAIL=$((FAIL + 1))
  fi
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

echo "=== chat-client Docker Containerization Verification ==="

check_cmd "Dockerfile exists" test -f Dockerfile.chat-client
check_cmd "docker-build.sh exists" test -f docker-build.sh
check_cmd "entrypoint exists" test -f scripts/docker-entrypoint.chat-client.sh
check_cmd "healthcheck exists" test -f scripts/healthcheck-chat-client.sh
check_cmd "compose exists" test -f docker-compose.chat-client.yml
check_cmd ".dockerignore exists" test -f .dockerignore
check_cmd "docker env example exists" test -f docker/chat-client.env.example
check_cmd "docker deployment doc exists" test -f docs/DOCKER_DEPLOYMENT.md

check_text "Dockerfile uses multi-stage build" Dockerfile.chat-client "FROM python:3\.11-slim AS builder"
check_text "Dockerfile uses BuildKit pip secret" Dockerfile.chat-client "--mount=type=secret,id=pip_conf"
check_text "Dockerfile uses non-root runtime" Dockerfile.chat-client "USER chat"
check_text "Dockerfile has healthcheck" Dockerfile.chat-client "HEALTHCHECK"
check_text "Dockerfile has entrypoint" Dockerfile.chat-client "ENTRYPOINT"

check_text "docker-build uses BuildKit" docker-build.sh "DOCKER_BUILDKIT=1 docker buildx build"
check_text "docker-build passes pip secret" docker-build.sh "--secret id=pip_conf"
check_text "docker-build resolves PyPI credentials" docker-build.sh "resolve_pypi_credentials"
check_text "docker-build prepares local wheelhouse" docker-build.sh "prepare_local_wheelhouse"

check_text "entrypoint sets up shell runtime" scripts/docker-entrypoint.chat-client.sh "setup_shell_runtime"
check_text "entrypoint supports api mode" scripts/docker-entrypoint.chat-client.sh "api\|server\|test-server\|all"

check_text "compose maps api port" docker-compose.chat-client.yml "CHAT_CLIENT_API_PORT:-8090}:8090"

check_text ".dockerignore excludes private folder" .dockerignore "^private/"
check_text ".dockerignore excludes working folder" .dockerignore "^working/"
check_text ".dockerignore excludes pip secret file" .dockerignore "^\.pip\.conf\.build$"

if command -v docker >/dev/null 2>&1; then
  check_cmd "Docker image exists locally" docker image inspect cloud-dog-chat-client:latest
else
  echo "WARN: docker command not available; skipping image presence check"
fi

echo "=========================================="
echo "PASS: ${PASS}  FAIL: ${FAIL}"
echo "=========================================="
if [[ ${FAIL} -eq 0 ]]; then
  echo "ALL PASS"
  exit 0
fi

echo "FAILURES DETECTED"
exit 1
