#!/usr/bin/env bash
# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3)"
  fi
fi
choose_writable_dir() {
  local label="$1"
  shift
  local candidate probe
  for candidate in "$@"; do
    mkdir -p "$candidate" 2>/dev/null || true
    probe="$candidate/.write-test.$$"
    if touch "$probe" >/dev/null 2>&1; then
      rm -f "$probe"
      printf '%s' "$candidate"
      return 0
    fi
  done
  echo "ERROR: unable to find writable $label directory" >&2
  return 1
}

PID_DIR="$(choose_writable_dir "pid" \
  "$ROOT_DIR/.pids" \
  "$ROOT_DIR/working/.pids" \
  "${TMPDIR:-/tmp}/chat-client-pids")"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

ENV_FILE=""
ENV_FILE_REAL=""
ENV_FILE_ID=""
COMMAND=""
TARGET=""

usage() {
  echo "Usage: $0 --env <file> <start|stop|status> <api|web|mcp|a2a|all>" >&2
  exit 2
}

POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      ENV_FILE="${2:-}"
      shift 2
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done
set -- "${POSITIONAL[@]}"

COMMAND="${1:-}"
TARGET="${2:-}"
[[ -n "$ENV_FILE" && -n "$COMMAND" && -n "$TARGET" ]] || usage
[[ -f "$ENV_FILE" ]] || { echo "ERROR: env file does not exist: $ENV_FILE" >&2; exit 1; }
ENV_FILE_REAL="$("$PYTHON_BIN" -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$ENV_FILE")"
ENV_FILE_ID="$("$PYTHON_BIN" -c 'import hashlib, os, sys; print(hashlib.sha256(os.path.realpath(sys.argv[1]).encode()).hexdigest()[:12])' "$ENV_FILE")"

hydrate_plain_env_from_file() {
  eval "$({
    "$PYTHON_BIN" - "$ENV_FILE" <<'PY'
from __future__ import annotations

import shlex
import sys
from pathlib import Path


env_path = Path(sys.argv[1]).resolve()
for raw in env_path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    if line.startswith("export "):
        line = line[len("export "):].strip()
    key, value = line.split("=", 1)
    key = key.strip()
    if not key:
        continue
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    print(f"export {key}={shlex.quote(value)}")
PY
  } )"
}

hydrate_plain_env_from_file

resolve_config() {
  eval "$({
    PYTHONPATH="$ROOT_DIR/src" "$PYTHON_BIN" - "$ENV_FILE" <<'PY'
import shlex
import sys
from cloud_dog_chat_client.config import ConfigManager

cfg = ConfigManager(env_file=sys.argv[1])
start_timeout = cfg.get("client_api.start_timeout_seconds")
try:
    start_timeout = int(float(start_timeout if start_timeout is not None else 120))
except (TypeError, ValueError):
    start_timeout = 120
print(f"START_TIMEOUT_SECONDS={start_timeout}")
servers = [
    ("api", "api_server", 0, "start_api_server.py", "/health"),
    ("web", "web_server", 0, "start_web_server.py", "/health"),
    ("mcp", "mcp_server", 0, "start_mcp_server.py", "/health"),
    ("a2a", "a2a_server", 0, "start_a2a_server.py", "/health"),
]
for name, section, default_port, script, health in servers:
    host = str(cfg.get(f"{section}.host") or "0.0.0.0").strip() or "0.0.0.0"
    port = cfg.get(f"{section}.port")
    try:
        port = int(port if port is not None else default_port)
    except (TypeError, ValueError):
        port = int(default_port)
    enabled = cfg.get(f"{section}.enabled")
    enabled = not (str(enabled).strip().lower() in {"0", "false", "no", "off"})
    print(f"{name.upper()}_HOST={shlex.quote(host)}")
    print(f"{name.upper()}_PORT={port}")
    print(f"{name.upper()}_ENABLED={'true' if enabled else 'false'}")
    print(f"{name.upper()}_SCRIPT={shlex.quote(script)}")
    print(f"{name.upper()}_HEALTH_PATH={shlex.quote(health)}")
PY
  } )"
}

resolve_config

server_field() {
  local server="$1" field="$2"
  local prefix="${server^^}"
  case "$field" in
    host) eval "printf '%s' \"\${${prefix}_HOST}\"" ;;
    port) eval "printf '%s' \"\${${prefix}_PORT}\"" ;;
    enabled) eval "printf '%s' \"\${${prefix}_ENABLED}\"" ;;
    script) eval "printf '%s' \"\${${prefix}_SCRIPT}\"" ;;
    health_path) eval "printf '%s' \"\${${prefix}_HEALTH_PATH}\"" ;;
    *) return 1 ;;
  esac
}

pid_file() { printf '%s/%s-%s.pid' "$PID_DIR" "$1" "$ENV_FILE_ID"; }
log_file() { printf '%s/%s-%s.log' "$LOG_DIR" "$1" "$ENV_FILE_ID"; }

is_pid_running() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

listener_pids() {
  local server="$1" port
  port="$(server_field "$server" port)"
  lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

health_url() {
  local server="$1"
  local host port path
  host="$(server_field "$server" host)"
  port="$(server_field "$server" port)"
  path="$(server_field "$server" health_path)"
  [[ "$host" == "0.0.0.0" || "$host" == "::" || "$host" == "[::]" || -z "$host" ]] && host="127.0.0.1"
  printf 'http://%s:%s%s' "$host" "$port" "$path"
}

health_runtime_env() {
  local server="$1" payload
  payload="$(curl -fsS --max-time 2 "$(health_url "$server")" 2>/dev/null || true)"
  [[ -n "$payload" ]] || return 1
  printf '%s' "$payload" | "$PYTHON_BIN" -c '
import json
import os
import sys

try:
    payload = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)

runtime_env = ""
if isinstance(payload, dict):
    runtime_env = str(payload.get("env_file") or "").strip()
    if not runtime_env:
        runtime = payload.get("runtime")
        if isinstance(runtime, dict):
            runtime_env = str(runtime.get("env_file") or "").strip()

if runtime_env:
    print(os.path.realpath(runtime_env))
' || true
}

health_matches_expected_env() {
  local server="$1" runtime_env
  runtime_env="$(health_runtime_env "$server")"
  [[ -z "$runtime_env" || "$runtime_env" == "$ENV_FILE_REAL" ]]
}

stop_listener_pids() {
  local server="$1" pid
  for pid in $(listener_pids "$server"); do
    kill "$pid" 2>/dev/null || true
  done
  for _ in {1..6}; do
    local remaining=""
    remaining="$(listener_pids "$server")"
    if [[ -z "$remaining" ]]; then
      return 0
    fi
    sleep 0.5
  done
  for pid in $(listener_pids "$server"); do
    kill -9 "$pid" 2>/dev/null || true
  done
}

wait_for_health() {
  local server="$1" timeout="${2:-30}" deadline url
  url="$(health_url "$server")"
  deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

start_one() {
  local server="$1" pidfile logfile script enabled pid
  enabled="$(server_field "$server" enabled)"
  if [[ "$enabled" != "true" ]]; then
    echo "$server disabled"
    return 0
  fi
  pidfile="$(pid_file "$server")"
  logfile="$(log_file "$server")"
  script="$(server_field "$server" script)"

  if [[ -f "$pidfile" ]]; then
    pid="$(cat "$pidfile")"
    if is_pid_running "$pid"; then
      if curl -fsS --max-time 2 "$(health_url "$server")" >/dev/null 2>&1; then
        if health_matches_expected_env "$server"; then
          echo "$server already running (pid $pid)"
          return 0
        fi
        stop_listener_pids "$server"
      fi
      kill "$pid" 2>/dev/null || true
      for _ in {1..6}; do
        if is_pid_running "$pid"; then
          sleep 0.5
        else
          break
        fi
      done
      if is_pid_running "$pid"; then
        kill -9 "$pid" 2>/dev/null || true
      fi
    fi
    rm -f "$pidfile"
  fi

  if curl -fsS --max-time 1 "$(health_url "$server")" >/dev/null 2>&1; then
    if health_matches_expected_env "$server"; then
      echo "$server already running (untracked)"
      return 0
    fi
    stop_listener_pids "$server"
  fi

  (
    cd "$ROOT_DIR"
    export CLOUD_DOG__APP__ENV_FILE="$ENV_FILE"
    export CHAT_CLIENT_ENV_FILE="$ENV_FILE"
    export CLOUD_DOG__APP__PROJECT_ROOT="$ROOT_DIR"
    nohup "$PYTHON_BIN" "$ROOT_DIR/$script" >"$logfile" 2>&1 &
    echo $! >"$pidfile"
  )
  pid="$(cat "$pidfile")"
  local health_timeout
  health_timeout="${START_TIMEOUT_SECONDS:-45}"
  if [[ -z "$health_timeout" || "$health_timeout" -lt 45 ]]; then
    health_timeout=45
  fi
  if ! wait_for_health "$server" "$health_timeout"; then
    echo "ERROR: $server failed health check; see $logfile" >&2
    stop_one "$server" >/dev/null 2>&1 || true
    return 1
  fi
  echo "$server started (pid $pid)"
}

stop_one() {
  local server="$1" pidfile pid
  pidfile="$(pid_file "$server")"
  if [[ ! -f "$pidfile" ]]; then
    echo "$server not running"
    return 0
  fi
  pid="$(cat "$pidfile")"
  if is_pid_running "$pid"; then
    kill "$pid" 2>/dev/null || true
    for _ in {1..6}; do
      if is_pid_running "$pid"; then
        sleep 0.5
      else
        break
      fi
    done
    if is_pid_running "$pid"; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$pidfile"
  echo "$server stopped"
}

status_one() {
  local server="$1" pidfile pid
  pidfile="$(pid_file "$server")"
  if [[ -f "$pidfile" ]]; then
    pid="$(cat "$pidfile")"
    if is_pid_running "$pid"; then
      if curl -fsS --max-time 2 "$(health_url "$server")" >/dev/null 2>&1; then
        if ! health_matches_expected_env "$server"; then
          echo "$server wrong env (pid $pid)"
          return 1
        fi
        echo "$server running (pid $pid)"
        return 0
      fi
      echo "$server stale pidfile (pid $pid)"
      return 1
    fi
  fi
  if curl -fsS --max-time 1 "$(health_url "$server")" >/dev/null 2>&1; then
    if ! health_matches_expected_env "$server"; then
      echo "$server wrong env (untracked)"
      return 1
    fi
    echo "$server running (untracked)"
    return 0
  fi
  echo "$server not running"
  return 1
}

run_all() {
  local action="$1"
  case "$action" in
    start)
      start_one api
      start_one mcp
      start_one a2a
      start_one web
      ;;
    stop)
      stop_one web
      stop_one a2a
      stop_one mcp
      stop_one api
      ;;
    status)
      status_one api || true
      status_one web || true
      status_one mcp || true
      status_one a2a || true
      ;;
    *) usage ;;
  esac
}

case "$TARGET" in
  all) run_all "$COMMAND" ;;
  api|web|mcp|a2a)
    case "$COMMAND" in
      start) start_one "$TARGET" ;;
      stop) stop_one "$TARGET" ;;
      status) status_one "$TARGET" ;;
      *) usage ;;
    esac
    ;;
  *) usage ;;
esac
