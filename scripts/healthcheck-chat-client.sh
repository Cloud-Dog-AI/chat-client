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

HOST="${CHAT_CLIENT_HEALTH_HOST:-127.0.0.1}"
PORT="${CLOUD_DOG__API_SERVER__PORT:-${CHAT_CLIENT_API_PORT:-0}}"
PATH_PART="${CHAT_CLIENT_HEALTH_PATH:-/health}"

# W28A-SEC-R18 hardening: probe with python's stdlib urllib instead of curl so the
# runtime image does not need the curl package (removes curl/libcurl CVE surface).
exec python3 -c 'import sys,urllib.request; urllib.request.urlopen(sys.argv[1], timeout=5).read()' \
  "http://${HOST}:${PORT}${PATH_PART}"
