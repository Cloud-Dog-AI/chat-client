#!/usr/bin/env bash
# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
# Licensed under the Apache License, Version 2.0

# chat-client — Docker Build Script (PS-91 / PS-97 v1.1 §1.1.3)
# Uses BuildKit secret mount for optional package-index credentials; credentials never enter image layers.
#
# Variant selector (PS-97 v1.1 §1.1.3):
#   --variant public  (default) builds Dockerfile.public for publication.
#                     Default package index is public PyPI (https://pypi.org/simple/).
#   --variant dev     builds Dockerfile.chat-client (the internal/dev variant). The
#                     internal package index must be supplied via the PYPI_URL env override.
#
# Usage:
#   docker-build.sh [VERSION] [--variant public|dev]
#
# Env overrides: PYPI_URL, PYPI_USERNAME, PYPI_PASSWORD, CUSTOM_CA_CERT, CORPORATE_CA_CERT, REGISTRY.
# Never uses --extra-index-url (PS-97 §3.3, §4 anti-pattern); a single index-url only.
set -euo pipefail

require_main_or_release_branch() {
  local branch
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  case "${branch}" in
    main|release/*)
      return 0
      ;;
  esac

  echo "ERROR: docker-build.sh refuses to build/push from non-main branch. Got '${branch:-unknown}'; checkout main or release/*." >&2
  exit 1
}

require_main_or_release_branch

# ── Argument parsing ────────────────────────────────────────────
VARIANT="${PUBLICATION_BUILD_VARIANT:-public}"
POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --variant)
      VARIANT="${2:-public}"
      shift 2
      ;;
    --variant=*)
      VARIANT="${1#*=}"
      shift
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done
set -- ${POSITIONAL[@]+"${POSITIONAL[@]}"}

case "${VARIANT}" in
  public)
    DOCKERFILE="Dockerfile.public"
    DEFAULT_PYPI_URL="https://pypi.org/simple/"
    ;;
  dev)
    DOCKERFILE="Dockerfile.chat-client"
    # The internal index is supplied by the build environment via PYPI_URL.
    DEFAULT_PYPI_URL=""
    ;;
  *)
    echo "ERROR: --variant must be 'public' or 'dev' (got: ${VARIANT})" >&2
    exit 2
    ;;
esac

if [[ ! -f "${DOCKERFILE}" ]]; then
  echo "ERROR: ${DOCKERFILE} not found (variant=${VARIANT})" >&2
  exit 2
fi

VERSION="${1:-latest}"
CONTAINER="chat-client"
FOLDER="cloud-dog"
REGISTRY="${REGISTRY:-}"
PIP_CONF=".pip.conf.build"
CA_BUNDLE_FILE=".ca-bundle.build"

PUBLICATION_TAG_SUFFIX="${PUBLICATION_TAG_SUFFIX:-}"
if [[ -n "${PUBLICATION_TAG_SUFFIX}" ]]; then
  if [[ ! "${PUBLICATION_TAG_SUFFIX}" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]; then
    echo "ERROR: PUBLICATION_TAG_SUFFIX must match ^[a-z0-9]([a-z0-9-]*[a-z0-9])?\$ (got: '${PUBLICATION_TAG_SUFFIX}')" >&2
    exit 2
  fi
  case "${PUBLICATION_TAG_SUFFIX}" in
    latest|dev|prod|release|stable)
      echo "ERROR: PUBLICATION_TAG_SUFFIX '${PUBLICATION_TAG_SUFFIX}' is reserved" >&2
      exit 2
      ;;
  esac
  EFFECTIVE_TAG="${VERSION}-${PUBLICATION_TAG_SUFFIX}"
  echo "Publication test build: tag suffix '-${PUBLICATION_TAG_SUFFIX}' (registry tag will be skipped)."
else
  EFFECTIVE_TAG="${VERSION}"
fi

CUSTOM_CA_CERT="${CUSTOM_CA_CERT:-}"
CORPORATE_CA_CERT="${CORPORATE_CA_CERT:-/usr/local/share/ca-certificates/cloud-dog.net.ca.crt}"

echo "=========================================="
echo "Docker Build: cloud-dog-${CONTAINER}:${EFFECTIVE_TAG} (variant=${VARIANT}, dockerfile=${DOCKERFILE})"
echo "=========================================="

# ── PyPI Configuration ───────────────────────────────────────────
# Single index only — never --extra-index-url (PS-97 §3.3 / §4).
PYPI_URL="${PYPI_URL:-${DEFAULT_PYPI_URL}}"
PYPI_USERNAME="${PYPI_USERNAME:-}"
PYPI_PASSWORD="${PYPI_PASSWORD:-}"

if [[ "${VARIANT}" == "dev" && -z "${PYPI_URL}" ]]; then
  echo "ERROR: --variant dev requires PYPI_URL to be set (internal index)." >&2
  exit 2
fi

# Generate pip.conf only when a non-default index or credentials are supplied.
# For the public default (pypi.org) the Dockerfile's PIP_INDEX_URL ARG is sufficient
# and no pip.conf secret is mounted.
USE_PIP_CONF=0
if [[ -n "${PYPI_USERNAME}" ]] && [[ -n "${PYPI_PASSWORD}" ]] && [[ -n "${PYPI_URL}" ]]; then
  USE_PIP_CONF=1
  python3 - "${PYPI_URL}" "${PYPI_USERNAME}" "${PYPI_PASSWORD}" "${PIP_CONF}" <<'PY'
from pathlib import Path
from urllib.parse import quote, urlsplit
import sys

url, username, password, pip_conf = sys.argv[1:5]
parts = urlsplit(url)
scheme = parts.scheme or "https"
host_path = (parts.netloc or parts.path.split("/", 1)[0]) + (
    "/" + parts.path.split("/", 1)[1] if not parts.netloc and "/" in parts.path else parts.path
)
host_path = host_path.lstrip("/")
trusted_host = urlsplit(url).hostname or host_path.split("/", 1)[0]
auth = quote(username, safe="") + ":" + quote(password, safe="") + "@"
index_url = scheme + "://" + auth + host_path
Path(pip_conf).write_text(
    "[global]\n"
    f"index-url = {index_url}\n"
    f"trusted-host = {trusted_host}\n"
    "               files.pythonhosted.org\n",
    encoding="utf-8",
)
PY
  chmod 600 "${PIP_CONF}"
  echo "pip.conf generated with authenticated single-index access (strict-single-index, PS-97 §3.5)."
elif [[ -n "${PYPI_URL}" && "${PYPI_URL}" != "https://pypi.org/simple/" ]]; then
  USE_PIP_CONF=1
  TRUSTED_HOST="$(python3 -c "from urllib.parse import urlsplit; print(urlsplit('${PYPI_URL}').hostname or '')")"
  cat > "${PIP_CONF}" << EOF
[global]
index-url = ${PYPI_URL}
trusted-host = ${TRUSTED_HOST}
               files.pythonhosted.org
EOF
  chmod 600 "${PIP_CONF}"
  echo "pip.conf generated with anonymous single-index access (${PYPI_URL})."
else
  echo "Using default public PyPI index (${PYPI_URL:-https://pypi.org/simple/}); no pip.conf secret mounted."
fi

# ── CA Certificate (dev variant only; public uses system trust store) ─────
rm -f "${CA_BUNDLE_FILE}"
touch "${CA_BUNDLE_FILE}"
if [[ "${VARIANT}" == "dev" ]]; then
  for cert in "${CUSTOM_CA_CERT}" "${CORPORATE_CA_CERT}"; do
    if [[ -n "${cert}" && -f "${cert}" ]]; then
      cat "${cert}" >> "${CA_BUNDLE_FILE}"
      echo "" >> "${CA_BUNDLE_FILE}"
    fi
  done
  # Dockerfile.chat-client COPYs custom-ca.crt (may be empty)
  cp "${CA_BUNDLE_FILE}" custom-ca.crt 2>/dev/null || touch custom-ca.crt
fi
chmod 600 "${CA_BUNDLE_FILE}"

# ── Build ────────────────────────────────────────────────────────
BUILD_ARGS=(
  --build-arg HTTP_PROXY="${HTTP_PROXY:-}"
  --build-arg HTTPS_PROXY="${HTTPS_PROXY:-}"
  --build-arg NO_PROXY="${NO_PROXY:-}"
  --build-arg http_proxy="${http_proxy:-}"
  --build-arg https_proxy="${https_proxy:-}"
  --build-arg no_proxy="${no_proxy:-}"
)
if [[ "${VARIANT}" == "public" && "${USE_PIP_CONF}" -eq 0 && -n "${PYPI_URL}" ]]; then
  BUILD_ARGS+=( --build-arg PIP_INDEX_URL="${PYPI_URL}" )
fi

SECRET_ARGS=()
if [[ "${USE_PIP_CONF}" -eq 1 ]]; then
  SECRET_ARGS+=( --secret id=pip_conf,src="${PIP_CONF}" )
fi
if [[ "${VARIANT}" == "dev" ]]; then
  SECRET_ARGS+=( --secret id=ca_bundle,src="${CA_BUNDLE_FILE}" )
fi

# ── W28C-1719 publish-before-pin guard (fail-closed; blocks build on unpublished internal pin) ──
_PBP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
"${_PBP_DIR}/scripts/publish-before-pin-guard.sh" "${_PBP_DIR}" || exit $?

_PBP_REV="$(git -C "$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)" rev-parse HEAD 2>/dev/null || echo unknown)"
# W28E-1863 fix-wave-d (WSC-014): propagate build identity to the image so the
# Dockerfile can stamp OCI labels + runtime ENV for build_identity() / /version.
SOURCE_COMMIT="${_PBP_REV}"
SOURCE_BRANCH="$(git -C "${_PBP_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
BUILD_ARGS+=(
  --build-arg SOURCE_COMMIT="${SOURCE_COMMIT}"
  --build-arg SOURCE_BRANCH="${SOURCE_BRANCH}"
  --build-arg BUILD_DATE="${BUILD_DATE}"
)
DOCKER_BUILDKIT=1 docker buildx build \
  --label "org.opencontainers.image.revision=${_PBP_REV}" \
  --progress=plain \
  --network=host \
  --load \
  -f "${DOCKERFILE}" \
  ${SECRET_ARGS[@]+"${SECRET_ARGS[@]}"} \
  "${BUILD_ARGS[@]}" \
  -t "cloud-dog-${CONTAINER}:${EFFECTIVE_TAG}" \
  . 2>&1 | tee docker-build.log

BUILD_STATUS=${PIPESTATUS[0]}

if [[ ${BUILD_STATUS} -eq 0 ]]; then
  echo ""
  echo "=========================================="
  echo "Build completed successfully (variant=${VARIANT})"
  echo "=========================================="
  docker images "cloud-dog-${CONTAINER}:${EFFECTIVE_TAG}" --format "{{.Repository}}:{{.Tag}} {{.Size}}"
  if [[ "${VARIANT}" == "dev" && -n "${REGISTRY}" && -z "${PUBLICATION_TAG_SUFFIX}" ]]; then
    docker tag "cloud-dog-${CONTAINER}:${EFFECTIVE_TAG}" \
      "${REGISTRY}/${FOLDER}/${CONTAINER}:${EFFECTIVE_TAG}"
    echo "Tagged: ${REGISTRY}/${FOLDER}/${CONTAINER}:${EFFECTIVE_TAG}"
  elif [[ -n "${PUBLICATION_TAG_SUFFIX}" ]]; then
    echo "Registry tag skipped for publication suffix '${PUBLICATION_TAG_SUFFIX}'."
  else
    echo "Public variant built; internal registry tag skipped (PS-97 §1.1.3 closed-loop)."
  fi
else
  echo "Build FAILED — see docker-build.log"
fi

# ── Cleanup secrets ──────────────────────────────────────────────
rm -f "${PIP_CONF}" "${CA_BUNDLE_FILE}" custom-ca.crt
exit ${BUILD_STATUS}
