#!/usr/bin/env bash
set -euo pipefail

required=(VAULT_ADDR VAULT_TOKEN)
optional=(VAULT_MOUNT_POINT VAULT_CONFIG_PATH)

missing=0
for key in "${required[@]}"; do
  if [[ -z "${!key:-}" ]]; then
    echo "MISSING: $key"
    missing=1
  else
    echo "OK: $key"
  fi
done

for key in "${optional[@]}"; do
  if [[ -z "${!key:-}" ]]; then
    echo "WARN: $key is not set"
  else
    echo "OK: $key"
  fi
done

if [[ "$missing" -ne 0 ]]; then
  echo "Vault environment validation failed"
  exit 1
fi

echo "Vault environment validation passed"
