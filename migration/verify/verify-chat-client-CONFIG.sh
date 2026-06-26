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

# Verification script for chat-client CONFIG migration.

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

echo "=== chat-client CONFIG Migration Verification ==="
echo ""

if grep -q '"cloud_dog_config>=' "$PROJECT/pyproject.toml" 2>/dev/null; then
    check "QG-CFG-1 cloud_dog_config declared in pyproject" 0
else
    check "QG-CFG-1 cloud_dog_config declared in pyproject" 1
fi

if grep -q 'from cloud_dog_config import export_config, load_config' "$PROJECT/src/cloud_dog_chat_client/config/adapter.py" 2>/dev/null; then
    check "QG-CFG-2 adapter uses cloud_dog_config loader" 0
else
    check "QG-CFG-2 adapter uses cloud_dog_config loader" 1
fi

COUNT=$(grep -RInE 'yaml\.safe_load|yaml\.load\(|load_dotenv|dotenv\.load_dotenv|import hvac' "$PROJECT/src/cloud_dog_chat_client" --include='*.py' 2>/dev/null | wc -l)
check "QG-CFG-3 no bespoke config loaders in src (count=${COUNT})" "$COUNT"

COUNT=$(grep -RInE '\bos\.environ\b|\bos\.getenv\s*\(' "$PROJECT/src/cloud_dog_chat_client/database/db_config.py" --include='*.py' 2>/dev/null | wc -l)
check "QG-CFG-4 no direct os env bridge in db_config (count=${COUNT})" "$COUNT"

if [ -f "$PROJECT/defaults.yaml" ]; then
    check "QG-CFG-5 defaults.yaml present" 0
else
    check "QG-CFG-5 defaults.yaml present" 1
fi

echo ""
echo "=== RESULTS: ${PASS} passed, ${FAIL} failed ==="
echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "VERDICT: ALL PASS — chat-client CONFIG migration verifier checks are green."
    exit 0
else
    echo "VERDICT: ${FAIL} gate(s) failed — review failures above."
    exit 1
fi
