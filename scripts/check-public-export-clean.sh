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

hex_marker() {
  local hex="$1"
  local needle=""
  local pair
  local escaped
  while [ -n "$hex" ]; do
    pair="${hex:0:2}"
    hex="${hex:2}"
    printf -v escaped '\\x%s' "$pair"
    needle="${needle}$(printf '%b' "$escaped")"
  done
  check_marker "$needle"
}

hex_marker '2f6f70742f696163'
hex_marker '636c6f75642d646f672d61692d706c6174666f726d2d7374616e6461726473'
hex_marker '636c6f75642d646f672d7265706f'
hex_marker '656e762d7661756c74'
hex_marker '707970692e636c6f75642d646f672e6e6574'
hex_marker '67697465612e636c6f75642d646f672e6e6574'
hex_marker '72656769737472792e636c6f75642d646f672e6e6574'
hex_marker '7661756c74302e636c6f75642d646f672e6e6574'
hex_marker '2e6170702e767063302e636c6f75642d646f672e6e6574'
hex_marker '736572766572302e766965776465636b2e636f6d'
hex_marker '736572766572322e766965776465636b2e636f6d'

if [ "$fail" -eq 0 ]; then
  echo "PASS private/public boundary markers: 0"
  echo "PUBLIC_EXPORT_GUARD: PASS"
else
  echo "PUBLIC_EXPORT_GUARD: FAIL"
fi

exit "$fail"
