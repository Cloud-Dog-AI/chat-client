#!/bin/bash
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


set -e

CUSTOM_CA_CERT=/usr/local/share/ca-certificates/cloud-dog.net.ca.crt

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
APP_DIR="$ROOT_DIR/third_party/example-remote-server"
DOCKERFILE="$ROOT_DIR/third_party/example-remote-server.Dockerfile"
IMAGE="${1:-${IMAGE:-mcp-streamable-test:latest}}"
LOG_FILE="$ROOT_DIR/docker-build-example-mcp-server.log"
GENERIC_CA_CERT=custom-ca.crt

if [ ! -d "$APP_DIR" ]; then
  echo "ERROR: missing Example-MCP-Server directory: $APP_DIR"
  exit 1
fi

if [ ! -f "$DOCKERFILE" ]; then
  echo "ERROR: missing Dockerfile: $DOCKERFILE"
  exit 1
fi

cleanup() {
  rm -f "$APP_DIR/$GENERIC_CA_CERT" 2>/dev/null || true
}

trap cleanup EXIT

if [ ! -f "$CUSTOM_CA_CERT" ]; then
  echo "ERROR: custom CA certificate not found at $CUSTOM_CA_CERT" | tee "$LOG_FILE"
  echo "This build requires the Cloud-Dog CA to access upstream package registries." | tee -a "$LOG_FILE"
  exit 1
fi

cp "$CUSTOM_CA_CERT" "$APP_DIR/$GENERIC_CA_CERT"

echo "==========================================" | tee "$LOG_FILE"
echo "Docker Build Configuration" | tee -a "$LOG_FILE"
echo "==========================================" | tee -a "$LOG_FILE"
echo "Image:  $IMAGE" | tee -a "$LOG_FILE"
echo "Context: $APP_DIR" | tee -a "$LOG_FILE"
echo "Dockerfile: $DOCKERFILE" | tee -a "$LOG_FILE"
echo "CA Cert: $CUSTOM_CA_CERT" | tee -a "$LOG_FILE"
echo "==========================================" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

docker buildx build \
  --progress=plain \
  --network=host \
  --load \
  -f "$DOCKERFILE" \
  --build-arg CUSTOM_CA_CERT="$GENERIC_CA_CERT" \
  --build-arg HTTP_PROXY=${HTTP_PROXY} \
  --build-arg HTTPS_PROXY=${HTTPS_PROXY} \
  --build-arg NO_PROXY=${NO_PROXY} \
  --build-arg http_proxy=${http_proxy} \
  --build-arg https_proxy=${https_proxy} \
  --build-arg no_proxy=${no_proxy} \
  -t "$IMAGE" \
  "$APP_DIR" 2>&1 | tee -a "$LOG_FILE"

BUILD_STATUS=${PIPESTATUS[0]}

if [ $BUILD_STATUS -eq 0 ]; then
  echo "" | tee -a "$LOG_FILE"
  echo "==========================================" | tee -a "$LOG_FILE"
  echo "✓ Build completed successfully" | tee -a "$LOG_FILE"
  echo "==========================================" | tee -a "$LOG_FILE"
  echo "" | tee -a "$LOG_FILE"
  docker images | grep -E "mcp-streamable-test|cloud-dog-mcp-example-remote-server" || true
  exit 0
fi

echo "" | tee -a "$LOG_FILE"
echo "==========================================" | tee -a "$LOG_FILE"
echo "✗ Build failed" | tee -a "$LOG_FILE"
echo "==========================================" | tee -a "$LOG_FILE"
echo "Build log saved to: $LOG_FILE" | tee -a "$LOG_FILE"
exit 1
