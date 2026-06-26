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

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


class DockerRunnerError(RuntimeError):
    pass


def _run(cmd: list[str], *, timeout_seconds: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise DockerRunnerError(f"Docker command timed out: {' '.join(cmd)}") from e


def _maybe_build_known_fixture_image(image: str, *, timeout_seconds: float) -> bool:
    repo_root = Path(__file__).resolve().parents[2]
    everything_dir = repo_root / "third_party/modelcontextprotocol-servers/src/everything"
    git_dir = repo_root / "third_party/modelcontextprotocol-servers/src/git"
    fetch_dir = repo_root / "third_party/modelcontextprotocol-servers/src/fetch"
    time_dir = repo_root / "third_party/modelcontextprotocol-servers/src/time"

    def _ensure_python_fixture_wheelhouse(project_dir: Path) -> None:
        wheelhouse_dir = project_dir / ".wheelhouse"
        existing_wheels = list(wheelhouse_dir.glob("*.whl"))
        if existing_wheels:
            return
        wheelhouse_dir.mkdir(parents=True, exist_ok=True)
        build_timeout = max(timeout_seconds, 600.0)
        build = subprocess.run(
            ["python3", "-m", "pip", "wheel", "--wheel-dir", str(wheelhouse_dir), "."],
            cwd=str(project_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=build_timeout,
            check=False,
        )
        if build.returncode != 0:
            raise DockerRunnerError(
                "Known fixture wheelhouse build failed. "
                f"Project: {project_dir}. "
                "Command: python3 -m pip wheel --wheel-dir .wheelhouse .. "
                f"output={build.stdout.strip()}"
            )

    node_dist_fixtures = {
        "cloud-dog-mcp-everything:latest": everything_dir,
        "cloud-dog-mcp-filesystem:latest": repo_root
        / "third_party/modelcontextprotocol-servers/src/filesystem",
        "cloud-dog-mcp-memory:latest": repo_root
        / "third_party/modelcontextprotocol-servers/src/memory",
        "cloud-dog-mcp-sequentialthinking:latest": repo_root
        / "third_party/modelcontextprotocol-servers/src/sequentialthinking",
    }
    node_dist_dir = node_dist_fixtures.get(image)
    if node_dist_dir is not None:
        package = json.loads((node_dist_dir / "package.json").read_text(encoding="utf-8"))
        package.pop("scripts", None)
        package.pop("devDependencies", None)
        build_timeout = max(timeout_seconds, 600.0)
        with tempfile.TemporaryDirectory(prefix="chat-client-mcp-node-") as tmp:
            context = Path(tmp)
            (context / "package.json").write_text(
                json.dumps(package, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            shutil.copytree(node_dist_dir / "dist", context / "dist")
            install = subprocess.run(
                ["npm", "install", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"],
                cwd=str(context),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=build_timeout,
                check=False,
            )
            if install.returncode != 0:
                raise DockerRunnerError(
                    "Known fixture runtime dependency install failed. "
                    f"Image: {image}. "
                    "Command: npm install --omit=dev --ignore-scripts --no-audit --no-fund. "
                    f"output={install.stdout.strip()}"
                )
            (context / "Dockerfile").write_text(
                "\n".join(
                    [
                        "FROM node:22-alpine",
                        "WORKDIR /app",
                        "COPY package.json ./package.json",
                        "COPY node_modules ./node_modules",
                        "COPY dist ./dist",
                        'ENTRYPOINT ["node", "dist/index.js"]',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            build = subprocess.run(
                ["docker", "build", "--network=host", "-t", image, "."],
                cwd=str(context),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=build_timeout,
                check=False,
            )
            if build.returncode != 0:
                raise DockerRunnerError(
                    "Known fixture runtime image build failed. "
                    f"Image: {image}. "
                    f"Command: docker build -t {image} . "
                    f"output={build.stdout.strip()}"
                )
        return True
    if image == "cloud-dog-mcp-git:latest":
        _ensure_python_fixture_wheelhouse(git_dir)
    if image == "cloud-dog-mcp-fetch:latest":
        _ensure_python_fixture_wheelhouse(fetch_dir)
    if image == "cloud-dog-mcp-time:latest":
        _ensure_python_fixture_wheelhouse(time_dir)
    fixture_builds: dict[str, tuple[list[str], Path]] = {
        "mcp-streamable-test:latest": (
            ["bash", str(repo_root / "docker-build-example-mcp-server.sh"), image],
            repo_root,
        ),
        "cloud-dog-mcp-example-remote-server:latest": (
            ["bash", str(repo_root / "docker-build-example-mcp-server.sh"), image],
            repo_root,
        ),
        "cloud-dog-mcp-memory:latest": (
            [
                "docker",
                "build",
                "-t",
                image,
                "-f",
                str(
                    repo_root
                    / "third_party/modelcontextprotocol-servers/src/memory/Dockerfile"
                ),
                str(repo_root / "third_party/modelcontextprotocol-servers"),
            ],
            repo_root / "third_party/modelcontextprotocol-servers",
        ),
        "cloud-dog-mcp-everything:latest": (
            [
                "docker",
                "build",
                "-t",
                image,
                "-f",
                str(
                    everything_dir / "Dockerfile"
                ),
                str(repo_root / "third_party/modelcontextprotocol-servers"),
            ],
            repo_root / "third_party/modelcontextprotocol-servers",
        ),
        "cloud-dog-mcp-filesystem:latest": (
            [
                "docker",
                "build",
                "-t",
                image,
                "-f",
                str(
                    repo_root
                    / "third_party/modelcontextprotocol-servers/src/filesystem/Dockerfile"
                ),
                str(repo_root / "third_party/modelcontextprotocol-servers"),
            ],
            repo_root / "third_party/modelcontextprotocol-servers",
        ),
        "cloud-dog-mcp-fetch:latest": (
            [
                "docker",
                "build",
                "-t",
                image,
                "-f",
                str(repo_root / "third_party/modelcontextprotocol-servers/src/fetch/Dockerfile"),
                str(repo_root / "third_party/modelcontextprotocol-servers/src/fetch"),
            ],
            repo_root / "third_party/modelcontextprotocol-servers/src/fetch",
        ),
        "cloud-dog-mcp-git:latest": (
            [
                "docker",
                "build",
                "-t",
                image,
                "-f",
                str(git_dir / "Dockerfile"),
                str(git_dir),
            ],
            git_dir,
        ),
        "cloud-dog-mcp-time:latest": (
            [
                "docker",
                "build",
                "-t",
                image,
                "-f",
                str(time_dir / "Dockerfile"),
                str(time_dir),
            ],
            time_dir,
        ),
        "cloud-dog-mcp-sequentialthinking:latest": (
            [
                "docker",
                "build",
                "-t",
                image,
                "-f",
                str(
                    repo_root
                    / "third_party/modelcontextprotocol-servers/src/sequentialthinking/Dockerfile"
                ),
                str(repo_root / "third_party/modelcontextprotocol-servers"),
            ],
            repo_root / "third_party/modelcontextprotocol-servers",
        ),
    }
    build_spec = fixture_builds.get(image)
    if build_spec is None:
        return False

    cmd, workdir = build_spec
    if cmd[:2] == ["docker", "build"] and "--network" not in cmd:
        cmd = [cmd[0], cmd[1], "--network=host", *cmd[2:]]
    build_timeout = max(timeout_seconds, 600.0)
    result = subprocess.run(
        cmd,
        cwd=str(workdir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=build_timeout,
        check=False,
    )
    if result.returncode != 0:
        raise DockerRunnerError(
            "Known fixture image build failed. "
            f"Image: {image}. "
            f"Command: {' '.join(cmd)}. "
            f"output={result.stdout.strip()}"
        )
    return True


def ensure_image_present(image: str, *, timeout_seconds: float) -> None:
    p = _run(["docker", "image", "inspect", image], timeout_seconds=timeout_seconds)
    if p.returncode != 0:
        if _maybe_build_known_fixture_image(image, timeout_seconds=timeout_seconds):
            p = _run(["docker", "image", "inspect", image], timeout_seconds=timeout_seconds)
            if p.returncode == 0:
                return
        raise DockerRunnerError(
            "Prebuilt Docker image not found. "
            f"Image: {image}. "
            "Build it first (via ./docker-build.sh ...) or pull/load it into your local Docker daemon. "
            f"docker output: {p.stdout.strip()}"
        )


@dataclass
class DockerContainerSpec:
    image: str
    name_prefix: str
    network: str = "host"
    env: Optional[Dict[str, str]] = None
    args: Optional[list[str]] = None
    remove: bool = True


class DockerContainer:
    def __init__(self, spec: DockerContainerSpec):
        self.spec = spec
        self.name = f"{spec.name_prefix}-{uuid.uuid4().hex[:10]}"
        self._started = False

    def start(self, *, timeout_seconds: float) -> None:
        ensure_image_present(self.spec.image, timeout_seconds=timeout_seconds)

        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            self.name,
            "--network",
            self.spec.network,
        ]

        if self.spec.remove:
            cmd.insert(3, "--rm")

        if self.spec.env:
            for k, v in self.spec.env.items():
                key = str(k)
                value = str(v)
                # Config keys are normalized to lowercase by ConfigManager.
                # Export both forms so Dockerized services that expect
                # uppercase environment variables (e.g., PORT) still receive them.
                cmd.extend(["-e", f"{key}={value}"])
                upper_key = key.upper()
                if upper_key != key:
                    cmd.extend(["-e", f"{upper_key}={value}"])

        cmd.append(self.spec.image)

        run_args = list(self.spec.args or [])
        if self.spec.image == "cloud-dog-mcp-everything:latest" and not run_args:
            run_args = ["streamableHttp"]
        if run_args:
            cmd.extend(run_args)

        p = _run(cmd, timeout_seconds=timeout_seconds)
        if p.returncode != 0:
            raise DockerRunnerError(f"Failed to start container. cmd={' '.join(cmd)} output={p.stdout.strip()}")

        self._started = True

    def stop(self, *, timeout_seconds: float) -> None:
        if not self._started:
            return
        try:
            _run(["docker", "stop", self.name], timeout_seconds=timeout_seconds)
        except DockerRunnerError:
            _run(["docker", "rm", "-f", self.name], timeout_seconds=timeout_seconds)
        else:
            if not self.spec.remove:
                _run(["docker", "rm", "-f", self.name], timeout_seconds=timeout_seconds)
        self._started = False

    def wait_for_log_substring(
        self,
        needle: str,
        *,
        timeout_seconds: float,
        poll_seconds: float,
    ) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            p = _run(["docker", "logs", self.name], timeout_seconds=5.0)
            if p.returncode == 0 and needle in (p.stdout or ""):
                return
            time.sleep(poll_seconds)
        raise DockerRunnerError(f"Timed out waiting for container log substring: {needle}")

    def logs(self) -> str:
        p = _run(["docker", "logs", self.name], timeout_seconds=10.0)
        return p.stdout or ""
