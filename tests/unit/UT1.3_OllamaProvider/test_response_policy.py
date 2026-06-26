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

import pytest

from cloud_dog_chat_client.llm.response_policy import (
    ResponsePolicy,
    parse_response,
    validate_response,
)


def _policy() -> ResponsePolicy:
    return ResponsePolicy(
        enforce=True,
        envelope_tag="RESPONSE",
        format="markdown",
        marker_key="MARKER",
        marker_value="SQLAGENT_OK",
        answer_key="ANSWER",
        strip_for_user=False,
        show_thinking=False,
        display_answer_tag="",
        allow_header_only=True,
        retry_attempts=1,
        retry_backoff_seconds=0.5,
    )
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_3_validate_response_accepts_header_only_marker_plus_body():
    policy = _policy()

    ok, error = validate_response(
        "MARKER: SQLAGENT_OK\nIndonesia is stronger on this metric than Malaysia.",
        policy,
    )

    assert ok is True
    assert error is None
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_3_parse_response_accepts_header_only_marker_plus_body():
    policy = _policy()

    answer, thinking = parse_response(
        "MARKER: SQLAGENT_OK\nIndonesia is stronger on this metric than Malaysia.",
        policy,
    )

    assert answer == "Indonesia is stronger on this metric than Malaysia."
    assert thinking == ""
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


def test_ut1_3_validate_response_rejects_marker_without_answer_body():
    policy = _policy()

    ok, error = validate_response("MARKER: SQLAGENT_OK", policy)

    assert ok is False
    assert error == "missing ANSWER line"


# W28A-161 marker augmentation
_w28a_161_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_161_existing_pytestmark, list):
    _w28a_161_existing_pytestmark = [_w28a_161_existing_pytestmark]
pytestmark = _w28a_161_existing_pytestmark + [
    pytest.mark.unit,
    pytest.mark.llm,
    pytest.mark.fast,
]
