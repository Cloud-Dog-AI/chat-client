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

"""Normalise Chat-Client profile/session agent strategy metadata.

Related requirements: W28B-317 rows 3-6.
Related tests: UT_AGENT_STRATEGY_NORMALIZATION, UT_AGENT_SESSION_DEFAULTS.
"""

from __future__ import annotations

from typing import Any, Final

from cloud_dog_agent import AgentStrategy

SIMPLE_AGENT_STRATEGY: Final[str] = AgentStrategy.SIMPLE.value
LONGWORKFLOW_AGENT_STRATEGY: Final[str] = "longworkflow"
SUPPORTED_AGENT_STRATEGIES: Final[tuple[str, ...]] = (
    AgentStrategy.SIMPLE.value,
    AgentStrategy.REACT.value,
    AgentStrategy.CODEACT.value,
    AgentStrategy.SUBAGENT_ROUTER.value,
    AgentStrategy.RLM.value,
    AgentStrategy.REFLEXION.value,
    LONGWORKFLOW_AGENT_STRATEGY,
)


def normalize_agent_strategy(value: Any) -> str:
    """Return the canonical strategy name or raise for an explicit bad value.

    Missing, ``None``, and blank values resolve to ``simple``. Non-blank values
    must match the accepted cloud_dog_agent strategy names or the Chat-Client
    service-level ``longworkflow`` option.
    """
    if value is None:
        return SIMPLE_AGENT_STRATEGY
    candidate = str(value).strip().lower()
    if not candidate:
        return SIMPLE_AGENT_STRATEGY
    if candidate in SUPPORTED_AGENT_STRATEGIES:
        return candidate
    allowed = ", ".join(SUPPORTED_AGENT_STRATEGIES)
    raise ValueError(f"Unsupported agent_strategy '{candidate}'. Allowed values: {allowed}")


def normalize_session_metadata(
    metadata: dict[str, Any] | None,
    *,
    persist_default: bool = False,
) -> dict[str, Any]:
    """Validate and optionally persist the canonical session strategy value."""
    payload = dict(metadata or {})
    if "agent_strategy" in payload:
        payload["agent_strategy"] = normalize_agent_strategy(payload.get("agent_strategy"))
    elif persist_default:
        payload["agent_strategy"] = SIMPLE_AGENT_STRATEGY
    return payload


def normalize_profile_session_defaults(defaults: dict[str, Any] | None) -> dict[str, Any]:
    """Validate strategy metadata inside a profile's session defaults payload."""
    return normalize_session_metadata(defaults, persist_default=False)


def agent_strategy_for_session(metadata: dict[str, Any] | None) -> str:
    """Resolve the effective strategy for a session metadata dictionary."""
    if not isinstance(metadata, dict):
        return SIMPLE_AGENT_STRATEGY
    return normalize_agent_strategy(metadata.get("agent_strategy"))


def agent_strategy_for_profile_defaults(defaults: dict[str, Any] | None) -> str:
    """Resolve the effective strategy represented by profile session defaults."""
    if not isinstance(defaults, dict):
        return SIMPLE_AGENT_STRATEGY
    return normalize_agent_strategy(defaults.get("agent_strategy"))

