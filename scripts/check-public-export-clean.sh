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

literal_credential_scan() {
  local tmp_files
  tmp_files="$(mktemp)"
  git ls-files -z > "$tmp_files"
  python3 - "$tmp_files" <<'PYSCAN'
#!/usr/bin/env python3
import os
import re
import sys

file_list_path = sys.argv[1]
with open(file_list_path, 'rb') as fh:
    files = fh.read().split(b'\0')

secret_key_re = r'(?:password|passwd|secret|api[_.-]?key|apikey|token|auth[_.-]?token|access[_.-]?token|refresh[_.-]?token|bearer[_.-]?token|session[_.-]?token|vault[_.-]?token|admin[_.-]?token|client[_.-]?token)'
quoted_re = re.compile(r'(?P<key>["\']?[A-Za-z0-9_.-]*' + secret_key_re + r'[A-Za-z0-9_.-]*["\']?\s*[:=]\s*)(?P<quote>["\'])(?P<value>[^"\']{0,300})(?P=quote)', re.I)
unquoted_re = re.compile(r'\b(?P<key>[A-Za-z0-9_.-]*' + secret_key_re + r'[A-Za-z0-9_.-]*)\s*(?P<sep>[:=])\s*(?P<value>[^\s#;,}\]]+)', re.I)
allowed_exact = {
    'changeme', 'change_me', 'replace_me', 'redacted', 'placeholder', 'example',
    'dummy', 'test', 'none', 'null', 'false', 'true', '0', '1', 'x', 'xx', 'xxx',
    'password', 'secret', 'token', 'api', 'key', 'keys', 'api-key', 'api_key', 'apikey',
    'test-password', 'test-secret', 'test-token', 'test-api-key', 'dummy-password',
    'dummy-secret', 'dummy-token', 'example-password', 'example-secret', 'example-token',
    'admin', 'user', 'viewer', 'editor', 'writer', 'reader', 'public', 'local', 'dev',
    'development', 'x-api-key', 'authorization', 'bearer', 'basic'
}
non_secret_key_fragments = (
    'max_token', 'max_tokens', 'token_count', 'token_counts', 'token_limit',
    'token_limits', 'token_replacement', 'tokenizer', 'expected_tokens',
    'estimated_tokens', 'requested_max_tokens', 'token_estimate', 'chars_per_token',
    'token_overlap', 'token_budget', 'token_window', 'token_chunk', 'tokens_per',
    'token_per', 'api_key_id', 'apikey_id', 'profile_id', 'password_hash',
    'hashed_password', 'hide_password', 'password_required', 'password_min',
    'password_max', 'password_length', 'password_policy', 'password_requirement',
    'api_key_header', 'x-api-key', 'token_type', 'token_env_var', 'token_resolver',
    'api_key_manager', 'include_secrets', 'key_id', 'key_prefix', 'key_status',
    'api_key_status', 'password_path', 'secret_path', 'token_uri', 'default_token_uri',
    'password_provider', 'password_manager', 'api_key_item', 'api_key_obj',
    'api_key_verify_fn', 'api_key_prefix', 'secret_like', 'secret_log_re',
    'secret_key_re', 'pwd_context', '_active_env_secrets_files', 'api_key_name',
    'api_key.name',
)
non_secret_exact = {
    'apikey', 'apikeys', 'api_keys', 'apikeymanager', 'passwordmanager',
    'api-keys', 'api_keys.list', 'api_keys.create', 'api_keys.revoke',
    'admin_create_api_key', 'admin_revoke_api_key',
}


def key_name(key):
    k = key.strip()
    k = re.sub(r'\s*[:=]\s*$', '', k)
    k = k.strip('"\'')
    k = re.sub(r'\s*[:=]\s*$', '', k)
    k = k.strip('"\'')
    return k.lower()


def secret_key(key):
    k = key_name(key)
    if not k or '/' in k or '=' in k:
        return False
    if k in non_secret_exact:
        return False
    if any(frag in k for frag in non_secret_key_fragments):
        return False
    if 'token' in k and not re.search(r'(^|[_.-])(token|auth_token|access_token|refresh_token|bearer_token|session_token|vault_token|admin_token|client_token)($|[_.-])|(?:auth|access|refresh|bearer|session|vault|admin|client)token', k):
        return False
    return bool(re.search(secret_key_re, k, re.I))


def is_target(path):
    lower = path.lower()
    base = os.path.basename(lower)
    if '/ui/dist/' in lower or lower.startswith('ui/dist/'):
        return False
    if lower.endswith(('.yaml', '.yml', '.json', '.py', '.env', '.ini', '.cfg', '.toml')):
        return True
    return base.startswith(('.env', 'env', 'docker-env')) or base in {'defaults.yaml', 'config.yaml'}


def allowed(value, path):
    v = value.strip().strip('"\'')
    vl = v.lower()
    if v == '':
        return True
    if re.fullmatch(r'<[^>]+>', v):
        return True
    if re.fullmatch(r'\$\{[A-Za-z_][A-Za-z0-9_]*(?::[-?][^}]*)?\}', v):
        return True
    if re.fullmatch(r'\$[A-Za-z_][A-Za-z0-9_]*', v):
        return True
    if re.fullmatch(r'\*+', v) or re.fullmatch(r'x+', vl):
        return True
    if vl in allowed_exact:
        return True
    if vl.startswith(('placeholder_', 'dummy_', 'test_', 'example_', 'fake_')):
        return True
    if vl.startswith(('placeholder-', 'dummy-', 'test-', 'example-', 'fake-')):
        return True
    if '/test' in path.lower() or path.lower().startswith('test'):
        if re.fullmatch(r'[a-z0-9_.@:/+-]{1,32}', vl) and any(tok in vl for tok in ('test', 'dummy', 'example', 'fake', 'mock')):
            return True
    return False


def mask_line(line):
    line = quoted_re.sub(lambda m: f"{m.group('key')}{m.group('quote')}<masked>{m.group('quote')}" if secret_key(m.group('key')) else m.group(0), line)
    line = unquoted_re.sub(lambda m: f"{m.group('key')}{m.group('sep')}<masked>" if secret_key(m.group('key')) else m.group(0), line)
    return line


for raw in files:
    if not raw:
        continue
    path = raw.decode('utf-8', 'replace')
    if not is_target(path):
        continue
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            for lineno, line in enumerate(fh, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith('#'):
                    continue
                findings = []
                for m in quoted_re.finditer(line):
                    if secret_key(m.group('key')) and not allowed(m.group('value'), path):
                        findings.append(key_name(m.group('key')))
                if not path.lower().endswith('.py') and '${' not in line:
                    for m in unquoted_re.finditer(line):
                        if '"' in m.group('value') or "'" in m.group('value'):
                            continue
                        if secret_key(m.group('key')) and not allowed(m.group('value'), path):
                            findings.append(key_name(m.group('key')))
                for key in sorted(set(findings)):
                    print('\t'.join([path, str(lineno), key, 'REAL_OR_REAL_SHAPED_LITERAL_CREDENTIAL', mask_line(line.rstrip('\n'))]))
    except OSError:
        continue
PYSCAN
  local rc=$?
  rm -f "$tmp_files"
  return "$rc"
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "FAIL literal credential assignments: python3 missing"
  fail=1
else
  if literal_matches="$(literal_credential_scan)"; then
    if [ -n "$literal_matches" ]; then
      echo "FAIL literal credential assignments found:"
      printf '%s
' "$literal_matches"
      fail=1
    else
      echo "PASS literal credential assignments: 0"
    fi
  else
    echo "FAIL literal credential assignments: scanner execution failed"
    fail=1
  fi
fi

if [ "$fail" -eq 0 ]; then
  echo "PASS private/public boundary markers: 0"
  echo "PUBLIC_EXPORT_GUARD: PASS"
else
  echo "PUBLIC_EXPORT_GUARD: FAIL"
fi

exit "$fail"
