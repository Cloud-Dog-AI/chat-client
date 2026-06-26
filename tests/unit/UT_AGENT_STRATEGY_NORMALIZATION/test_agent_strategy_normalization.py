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

"""UT_AGENT_STRATEGY_NORMALIZATION — optional strategy metadata rules."""

from __future__ import annotations

import pytest

from cloud_dog_chat_client.agent.strategy import (
    SUPPORTED_AGENT_STRATEGIES,
    agent_strategy_for_profile_defaults,
    agent_strategy_for_session,
    normalize_agent_strategy,
    normalize_profile_session_defaults,
    normalize_session_metadata,
)
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_null_blank_strategy_defaults_simple(value):
    """Missing, null, and blank strategy values resolve to simple."""
    assert normalize_agent_strategy(value) == "simple"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.parametrize("strategy", SUPPORTED_AGENT_STRATEGIES)
def test_valid_strategy_values_normalize(strategy):
    """Every supported strategy persists in canonical lower-case form."""
    assert normalize_agent_strategy(strategy.upper()) == strategy
    assert agent_strategy_for_session({"agent_strategy": strategy}) == strategy
    assert agent_strategy_for_profile_defaults({"agent_strategy": strategy}) == strategy
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_invalid_explicit_strategy_rejected():
    """Invalid explicit values fail before persistence or dispatch."""
    with pytest.raises(ValueError, match="Unsupported agent_strategy"):
        normalize_agent_strategy("bespoke")
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_session_metadata_only_persists_default_when_requested():
    """Legacy metadata is not forced to carry a new required field."""
    assert normalize_session_metadata({}, persist_default=False) == {}
    assert normalize_session_metadata({}, persist_default=True)["agent_strategy"] == "simple"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_blank_profile_strategy_round_trips_as_simple():
    """A blank explicit profile strategy is normalised to simple."""
    defaults = normalize_profile_session_defaults({"agent_strategy": "  "})
    assert defaults["agent_strategy"] == "simple"

