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

from pathlib import Path

import pytest
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_7_delivery_artifacts_exist(env_file):
    repo_root = Path(__file__).resolve().parents[3]

    dockerfile = repo_root / "Dockerfile.chat-client"
    install_script = repo_root / "scripts" / "install-chat-client.sh"
    run_script = repo_root / "scripts" / "run-chat-client.sh"

    if not dockerfile.exists():
        pytest.fail("CRITICAL ERROR: Dockerfile.chat-client is missing")
    if not install_script.exists():
        pytest.fail("CRITICAL ERROR: install-chat-client.sh is missing")
    if not run_script.exists():
        pytest.fail("CRITICAL ERROR: run-chat-client.sh is missing")

    if not dockerfile.read_text(encoding="utf-8").strip():
        pytest.fail("CRITICAL ERROR: Dockerfile.chat-client is empty")

    for path in (install_script, run_script):
        first_line = path.read_text(encoding="utf-8").splitlines()[:1]
        if not first_line or not first_line[0].startswith("#!/"):
            pytest.fail(f"CRITICAL ERROR: {path.name} missing shebang")
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_7_delivery_artifacts_docker_ps91_alignment(env_file):
    repo_root = Path(__file__).resolve().parents[3]

    dockerfile = (repo_root / "Dockerfile.chat-client").read_text(encoding="utf-8")
    build_script = (repo_root / "docker-build.sh").read_text(encoding="utf-8")
    entrypoint = (repo_root / "scripts" / "docker-entrypoint.chat-client.sh").read_text(encoding="utf-8")
    dockerignore = (repo_root / ".dockerignore").read_text(encoding="utf-8")
    compose = (repo_root / "docker-compose.chat-client.yml").read_text(encoding="utf-8")

    required_dockerfile_tokens = [
        "# syntax=docker/dockerfile:1",
        "FROM python:3.12-slim AS builder",
        "--mount=type=secret,id=pip_conf,target=/etc/pip.conf",
        "HEALTHCHECK",
        "ENTRYPOINT",
        "USER chat",
    ]
    for token in required_dockerfile_tokens:
        assert token in dockerfile, f"CRITICAL ERROR: Dockerfile missing token: {token}"

    required_build_tokens = [
        "DOCKER_BUILDKIT=1 docker buildx build",
        "--secret id=pip_conf",
        # W28A-727-R5: the W28A-861-R3 PS-97 v1.1 rewrite replaced the old
        # `resolve_pypi_credentials` helper with direct PYPI_USERNAME/PYPI_PASSWORD
        # → pip.conf → BuildKit secret plumbing (matches all sibling services).
        # Assert the credential plumbing still exists, aligned to the shipped artifact.
        "PYPI_PASSWORD",
        ".pip.conf.build",
    ]
    for token in required_build_tokens:
        assert token in build_script, f"CRITICAL ERROR: docker-build.sh missing token: {token}"

    required_entrypoint_tokens = [
        "setup_shell_runtime",
        "print_banner",
        "CHAT_CLIENT_MODE",
    ]
    for token in required_entrypoint_tokens:
        assert token in entrypoint, f"CRITICAL ERROR: docker entrypoint missing token: {token}"

    required_dockerignore_tokens = [
        ".pip.conf.build",
        "private/",
        "working/",
    ]
    for token in required_dockerignore_tokens:
        assert token in dockerignore, f"CRITICAL ERROR: .dockerignore missing token: {token}"

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.docker, pytest.mark.fast]
