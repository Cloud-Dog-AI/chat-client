#!/usr/bin/env bash
# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
# Licensed under the Apache License, Version 2.0.
#
# W28C-1719 — Publish-BEFORE-Pin build guard (wrapper). FAIL CLOSED.
#
# Runs BEFORE `docker build` in a consumer's docker-build.sh. It resolves every
# internal (cloud-dog-*) shared-package pin from the SINGLE approved internal
# index in a clean cache-cold env; any unresolved internal pin exits non-zero and
# MUST abort the build (callers use `&&`, never `;` — W28C-1718 fail-closed).
#
# Index + credentials come from the SAME source the build uses:
#   PIP_INDEX_URL (single internal index; must NOT be a Gitea/GitHub host)
#   PYPI_USERNAME / PYPI_PASSWORD (from Vault dev.repository.pypi; never logged)
# or an already-generated PIP_CONFIG_FILE. Nothing secret is printed.
#
# Usage:  publish-before-pin-guard.sh [CONSUMER_DIR]   (default: .)
set -euo pipefail

CONSUMER_DIR="${1:-.}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD_PY="${HERE}/publish_before_pin_guard.py"
PYTHON_BIN="${GUARD_PYTHON:-$(command -v python3)}"
EXPECTED_HOST="${GUARD_INDEX_HOST:-pypi.cloud-dog.net}"

if [[ ! -f "${GUARD_PY}" ]]; then
  echo "publish-before-pin-guard: cannot find ${GUARD_PY}" >&2; exit 3
fi

CLEANUP_CONF=""
# Auto-detect the build's generated pip.conf so wiring is uniform across consumers
# (docker-build.sh writes .pip.conf.build in the repo root before the build).
if [[ -z "${PIP_CONFIG_FILE:-}" && -f "${CONSUMER_DIR}/.pip.conf.build" ]]; then
  export PIP_CONFIG_FILE="${CONSUMER_DIR}/.pip.conf.build"
fi
if [[ -z "${PIP_CONFIG_FILE:-}" ]]; then
  : "${PIP_INDEX_URL:?publish-before-pin-guard: set PIP_INDEX_URL to the single internal index (or PIP_CONFIG_FILE)}"
  HOST="$("${PYTHON_BIN}" -c "from urllib.parse import urlsplit;import sys;print(urlsplit(sys.argv[1]).hostname or '')" "${PIP_INDEX_URL}")"
  case "${HOST,,}" in
    *gitea*|*github*)
      echo "publish-before-pin-guard: forbidden index host '${HOST}' (Gitea/GitHub boundary; §0A.GH). Refusing." >&2
      exit 3 ;;
  esac
  TMPCONF="$(mktemp -t pbp-pip-conf.XXXXXX)"; CLEANUP_CONF="${TMPCONF}"
  chmod 600 "${TMPCONF}"
  if [[ -n "${PYPI_USERNAME:-}" && -n "${PYPI_PASSWORD:-}" ]]; then
    printf '[global]\nindex-url = https://%s:%s@%s\ntrusted-host = %s\n' \
      "${PYPI_USERNAME}" "${PYPI_PASSWORD}" "${PIP_INDEX_URL#https://}" "${HOST}" > "${TMPCONF}"
  else
    printf '[global]\nindex-url = %s\ntrusted-host = %s\n' "${PIP_INDEX_URL}" "${HOST}" > "${TMPCONF}"
  fi
  export PIP_CONFIG_FILE="${TMPCONF}"
fi

trap '[[ -n "${CLEANUP_CONF}" ]] && rm -f "${CLEANUP_CONF}"' EXIT

GUARD_PYTHON="${PYTHON_BIN}" GUARD_INDEX_HOST="${EXPECTED_HOST}" \
  "${PYTHON_BIN}" "${GUARD_PY}" "${CONSUMER_DIR}"
