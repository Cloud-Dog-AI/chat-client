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

# Verification script for chat-client LOGGING migration.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FAIL=0
PASS=0

check() {
    local gate="$1"
    local result="$2"
    if [ "$result" -eq 0 ]; then
        echo "  PASS  $gate"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  $gate"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== chat-client LOGGING Migration Verification ==="
echo ""

if grep -q '"cloud_dog_logging>=' "$PROJECT/pyproject.toml" 2>/dev/null; then
    check "QG-LOG-1 cloud_dog_logging declared in pyproject" 0
else
    check "QG-LOG-1 cloud_dog_logging declared in pyproject" 1
fi

COUNT=$(grep -RIn 'from cloud_dog_logging import get_logger' "$PROJECT/src/cloud_dog_chat_client" --include='*.py' 2>/dev/null | wc -l)
if [ "$COUNT" -ge 3 ]; then
    check "QG-LOG-2 cloud_dog_logging get_logger imports present (count=${COUNT})" 0
else
    check "QG-LOG-2 cloud_dog_logging get_logger imports present (count=${COUNT})" 1
fi

COUNT=$(grep -RIn 'logging\.getLogger\(' "$PROJECT/src/cloud_dog_chat_client" --include='*.py' 2>/dev/null | wc -l)
check "QG-LOG-3 no stdlib logging.getLogger usage in src (count=${COUNT})" "$COUNT"

COUNT=$(grep -RIn 'logging\.basicConfig\(' "$PROJECT/src/cloud_dog_chat_client" --include='*.py' 2>/dev/null | wc -l)
check "QG-LOG-4 no stdlib logging.basicConfig usage in src (count=${COUNT})" "$COUNT"

echo ""
echo "=== RESULTS: ${PASS} passed, ${FAIL} failed ==="
echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "VERDICT: ALL PASS — chat-client LOGGING migration verifier checks are green."
    exit 0
else
    echo "VERDICT: ${FAIL} gate(s) failed — review failures above."
    exit 1
fi
