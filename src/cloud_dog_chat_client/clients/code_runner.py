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

"""Outbound A2A client for the code-runner service (``code.execute`` skill).

W28I-1218 (consumer side). chat-client invokes the code-runner producer A2A
endpoint to run user/admin-supplied code:

    POST {base_url}/a2a/tasks
    headers: {api_key_header}: <api key>, content-type: application/json
    body: {"task_id": "<id>", "skill_id": "code.execute",
           "input": {"code": "<code>", "language": "python"|"node"}}
    -> {"status": "completed"|"failed", "result": {"stdout", "stderr",
        "exit_code", "duration_ms", ...}}

This reuses the platform outbound pattern already used by chat-client (httpx
``AsyncClient`` + ``ConfigManager``-driven config + ``X-API-Key`` header +
``X-Correlation-Id`` propagation), mirroring the expert-assist call in
``api/routes.py``. No bespoke HTTP machinery and no new dependency is added.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx

from ..config import ConfigManager

# Canonical producer contract constants (code-runner side).
SKILL_ID = "code.execute"
TASKS_PATH = "/a2a/tasks"
CORRELATION_ID_HEADER = "X-Correlation-Id"
SUPPORTED_LANGUAGES = ("python", "node")

# Config key prefix for the consumer-side code-runner client.
_CONFIG_PREFIX = "code_runner"


class CodeRunnerError(RuntimeError):
    """Raised when the code-runner A2A call cannot be completed.

    This covers transport failures, non-2xx HTTP responses, and malformed
    response bodies. A code-runner *task* that reports ``status == "failed"``
    (i.e. the code ran but exited non-zero / errored) is **not** an error here:
    it is surfaced as a populated :class:`CodeRunnerResult` so callers can read
    ``stdout`` / ``stderr`` / ``exit_code``.
    """


@dataclass
class CodeRunnerConfig:
    """Resolved configuration for the code-runner outbound client."""

    base_url: str
    api_key: str
    api_key_header: str = "X-API-Key"
    tasks_path: str = TASKS_PATH
    timeout_seconds: float = 60.0
    verify_tls: bool = True
    correlation_id_header: str = CORRELATION_ID_HEADER

    def endpoint(self) -> str:
        """Return the fully-qualified ``/a2a/tasks`` endpoint URL."""
        return f"{self.base_url.rstrip('/')}/{self.tasks_path.lstrip('/')}"


@dataclass
class CodeRunnerResult:
    """Outcome of a ``code.execute`` task.

    ``status`` is the producer-reported task status (``"completed"`` or
    ``"failed"``). The execution detail (stdout/stderr/exit_code/duration_ms)
    lives in ``result``; convenience accessors are provided for the common
    fields.
    """

    status: str
    result: Dict[str, Any] = field(default_factory=dict)
    task_id: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def completed(self) -> bool:
        return str(self.status or "").strip().lower() == "completed"

    @property
    def stdout(self) -> str:
        return str(self.result.get("stdout") or "")

    @property
    def stderr(self) -> str:
        return str(self.result.get("stderr") or "")

    @property
    def exit_code(self) -> Optional[int]:
        value = self.result.get("exit_code")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @property
    def duration_ms(self) -> Optional[int]:
        value = self.result.get("duration_ms")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "completed": self.completed,
            "task_id": self.task_id,
            "result": self.result,
        }


def code_runner_config_from_manager(config: ConfigManager) -> CodeRunnerConfig:
    """Build a :class:`CodeRunnerConfig` from the service ``ConfigManager``.

    Reads (defaults in ``defaults.yaml`` under ``code_runner:``; env overrides
    via ``CLOUD_DOG__CODE_RUNNER__*``):

    - ``code_runner.base_url``           — code-runner service base URL.
    - ``code_runner.api_key``            — code-runner API key (secret; never
      hardcoded — supplied via env / mounted secret).
    - ``code_runner.api_key_header``     — header carrying the API key.
    - ``code_runner.tasks_path``         — A2A tasks path (default ``/a2a/tasks``).
    - ``code_runner.timeout_seconds``    — request timeout.
    - ``code_runner.verify_tls``         — TLS verification toggle.
    - ``code_runner.correlation_id_header`` — header used to propagate the
      chat-client correlation id to code-runner.

    Raises :class:`CodeRunnerError` when the base URL or API key are not
    configured, so a misconfiguration surfaces as a clear, actionable error
    rather than a silent no-op.
    """
    base_url = str(config.get(f"{_CONFIG_PREFIX}.base_url") or "").strip()
    api_key = str(config.get(f"{_CONFIG_PREFIX}.api_key") or "").strip()
    api_key_header = (
        str(config.get(f"{_CONFIG_PREFIX}.api_key_header") or "").strip()
        or "X-API-Key"
    )
    tasks_path = str(config.get(f"{_CONFIG_PREFIX}.tasks_path") or "").strip() or TASKS_PATH
    correlation_id_header = (
        str(config.get(f"{_CONFIG_PREFIX}.correlation_id_header") or "").strip()
        or CORRELATION_ID_HEADER
    )

    raw_timeout = config.get(f"{_CONFIG_PREFIX}.timeout_seconds")
    try:
        timeout_seconds = float(raw_timeout) if raw_timeout not in (None, "") else 60.0
    except (TypeError, ValueError):
        timeout_seconds = 60.0

    raw_verify = config.get(f"{_CONFIG_PREFIX}.verify_tls")
    verify_tls = True if raw_verify is None else bool(raw_verify)

    if not base_url:
        raise CodeRunnerError(
            "code-runner is not configured: missing required configuration key "
            "code_runner.base_url (set CLOUD_DOG__CODE_RUNNER__BASE_URL)"
        )
    if not api_key:
        raise CodeRunnerError(
            "code-runner is not configured: missing required configuration key "
            "code_runner.api_key (set CLOUD_DOG__CODE_RUNNER__API_KEY)"
        )

    return CodeRunnerConfig(
        base_url=base_url,
        api_key=api_key,
        api_key_header=api_key_header,
        tasks_path=tasks_path,
        timeout_seconds=timeout_seconds,
        verify_tls=verify_tls,
        correlation_id_header=correlation_id_header,
    )


class CodeRunnerClient:
    """Async client that submits ``code.execute`` tasks to code-runner over A2A.

    The optional ``client`` argument injects a pre-built ``httpx.AsyncClient``
    (e.g. wired to an ``httpx.MockTransport`` in unit tests) so the request
    shape can be asserted without a live network call — the same injection
    pattern used by ``cloud_dog_chat_client.mcp.client.MCPClient``.
    """

    def __init__(
        self,
        config: CodeRunnerConfig,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._config = config
        self._client = client

    @classmethod
    def from_config_manager(
        cls,
        config: ConfigManager,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ) -> "CodeRunnerClient":
        """Construct a client from the service ``ConfigManager``."""
        return cls(code_runner_config_from_manager(config), client=client)

    @property
    def config(self) -> CodeRunnerConfig:
        return self._config

    @staticmethod
    def _normalise_language(language: str) -> str:
        lang = str(language or "").strip().lower()
        if lang not in SUPPORTED_LANGUAGES:
            raise CodeRunnerError(
                f"unsupported language '{language}'; expected one of "
                f"{', '.join(SUPPORTED_LANGUAGES)}"
            )
        return lang

    def build_payload(
        self,
        *,
        code: str,
        language: str = "python",
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build the A2A task payload for the ``code.execute`` skill."""
        if not str(code or "").strip():
            raise CodeRunnerError("code must be a non-empty string")
        lang = self._normalise_language(language)
        return {
            "task_id": str(task_id or "").strip() or secrets.token_hex(16),
            "skill_id": SKILL_ID,
            "input": {"code": str(code), "language": lang},
        }

    def build_headers(self, correlation_id: Optional[str] = None) -> Dict[str, str]:
        """Build outbound headers: API key, content-type, correlation id.

        The chat-client correlation id is propagated to code-runner so the
        producer's audit log can be linked back to the originating chat-client
        request.
        """
        headers: Dict[str, str] = {
            self._config.api_key_header: self._config.api_key,
            "content-type": "application/json",
        }
        corr = str(correlation_id or "").strip()
        if corr:
            headers[self._config.correlation_id_header] = corr
        return headers

    async def execute(
        self,
        *,
        code: str,
        language: str = "python",
        correlation_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> CodeRunnerResult:
        """Submit a ``code.execute`` task and return the parsed result.

        Raises :class:`CodeRunnerError` on transport / HTTP / decode failures.
        A task whose producer status is ``"failed"`` is returned as a normal
        :class:`CodeRunnerResult` (it carries stdout/stderr/exit_code).
        """
        payload = self.build_payload(code=code, language=language, task_id=task_id)
        headers = self.build_headers(correlation_id)
        endpoint = self._config.endpoint()

        try:
            if self._client is not None:
                response = await self._client.post(
                    endpoint, json=payload, headers=headers
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self._config.timeout_seconds,
                    verify=self._config.verify_tls,
                ) as client:
                    response = await client.post(
                        endpoint, json=payload, headers=headers
                    )
        except httpx.HTTPError as exc:
            raise CodeRunnerError(
                f"code-runner request failed: {exc}"
            ) from exc

        if response.status_code != 200:
            raise CodeRunnerError(
                f"code-runner returned HTTP {response.status_code}: "
                f"{response.text[:240]}"
            )

        try:
            body = response.json() if response.text.strip() else {}
        except ValueError as exc:
            raise CodeRunnerError(
                f"code-runner returned a non-JSON response: {exc}"
            ) from exc
        if not isinstance(body, dict):
            raise CodeRunnerError("code-runner returned a non-object response body")

        result = body.get("result")
        if not isinstance(result, dict):
            result = {}

        return CodeRunnerResult(
            status=str(body.get("status") or "").strip(),
            result=result,
            task_id=str(body.get("task_id") or payload["task_id"]),
            raw=body,
        )
