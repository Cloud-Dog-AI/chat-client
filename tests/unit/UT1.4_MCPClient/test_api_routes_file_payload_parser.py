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

from cloud_dog_chat_client.api.routes import _decode_base64_byte_size, _extract_mcp_tool_payload
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_ut1_4_extract_mcp_tool_payload_from_structured_content():
    result = {"isError": False, "structuredContent": {"ok": True, "data": "YWJj"}}
    payload = _extract_mcp_tool_payload(result)
    assert payload["ok"] is True
    assert payload["data"] == "YWJj"
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_ut1_4_extract_mcp_tool_payload_from_text_json_content():
    result = {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": '{"ok": true, "path": "/tmp/a.txt", "bytes_written": 3}',
            }
        ],
    }
    payload = _extract_mcp_tool_payload(result)
    assert payload["ok"] is True
    assert payload["bytes_written"] == 3
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_ut1_4_extract_mcp_tool_payload_rejects_error_result():
    with pytest.raises(ValueError, match="isError=true"):
        _extract_mcp_tool_payload({"isError": True, "content": []})
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_ut1_4_decode_base64_byte_size_standard():
    assert _decode_base64_byte_size("YWJj", urlsafe=False) == 3
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_ut1_4_decode_base64_byte_size_urlsafe():
    assert _decode_base64_byte_size("c29tZS11cmxfc2FmZV9ieXRlcw", urlsafe=True) > 0
@pytest.mark.UT
@pytest.mark.mcp
@pytest.mark.req("FR-006")


def test_ut1_4_decode_base64_byte_size_invalid():
    with pytest.raises(ValueError, match="Base64 data must be a non-empty string"):
        _decode_base64_byte_size("", urlsafe=False)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.pure, pytest.mark.fast]

