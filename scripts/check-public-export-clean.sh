#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

fail=0
uri_re='[A-Za-z][A-Za-z0-9+.-]*://[^/[:space:]<>$]+:[^/@[:space:]<>$]+@'

mask_uri_userinfo() {
  perl -pe 's{([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s:<>&\$]+:[^/@\s<>&\$]+@}{$1<user>:<redacted>@}g'
}

if matches="$(git grep -n -I -E "$uri_re" -- . ':(exclude).git' 2>/dev/null)"; then
  echo "FAIL unsafe URI userinfo found:"
  printf '%s\n' "$matches" | mask_uri_userinfo
  fail=1
else
  echo "PASS unsafe URI userinfo: 0"
fi

check_marker() {
  local needle="$1"
  if matches="$(git grep -n -I -F -e "$needle" -- . ':(exclude).git' 2>/dev/null)"; then
    echo "FAIL private marker found: $needle"
    printf '%s\n' "$matches"
    fail=1
  fi
}

marker() {
  local needle=""
  local part
  for part in "$@"; do
    needle="${needle}${part}"
  done
  check_marker "$needle"
}

marker '/opt' '/iac'
marker 'cloud-dog-ai-platform' '-standards'
marker 'cloud-dog' '-repo'
marker 'env' '-vault'
marker 'pypi.' 'cloud-dog' '.net'
marker 'gitea.' 'cloud-dog' '.net'
marker 'registry.' 'cloud-dog' '.net'
marker 'vault0.' 'cloud-dog' '.net'
marker '.app.vpc0.' 'cloud-dog' '.net'
marker 'server0.' 'viewdeck' '.com'
marker 'server2.' 'viewdeck' '.com'

if [ "$fail" -eq 0 ]; then
  echo "PASS private/public boundary markers: 0"
  echo "PUBLIC_EXPORT_GUARD: PASS"
else
  echo "PUBLIC_EXPORT_GUARD: FAIL"
fi

exit "$fail"
