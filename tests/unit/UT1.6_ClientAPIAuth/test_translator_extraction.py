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
import pytest

from cloud_dog_chat_client.api.routes import _extract_translator_text
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")


def test_ut1_6_extract_translator_text_prefers_response_field():
    result = {
        "structuredContent": {
            "response": "Ez a magyar osszefoglalo.",
            "raw": {"debug": True},
        },
        "content": [{"type": "text", "text": "{\"raw\":\"json\"}"}],
    }
    assert _extract_translator_text(result) == "Ez a magyar osszefoglalo."
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")


def test_ut1_6_extract_translator_text_parses_json_text_payload():
    result = {
        "content": [
            {
                "type": "text",
                "text": '{"status":"ok","response":"A magyar hadero friss hirei roviden osszefoglalva."}',
            }
        ]
    }
    assert _extract_translator_text(result) == "A magyar hadero friss hirei roviden osszefoglalva."

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.pure, pytest.mark.fast]

