"""W28A-233: Unit tests for IMAP-MCP intent routing in prompt-assist."""
import importlib.util
import sys
from pathlib import Path

import pytest

# Import the routing functions directly to avoid full app init/Vault dependency.
_routes_path = Path(__file__).resolve().parents[2] / "src" / "cloud_dog_chat_client" / "api" / "routes.py"

# We need 're' available since routes.py uses it at module level
import re  # noqa: F401

# Load just the target functions via exec to avoid heavy app imports
from typing import Any, Dict, Optional
_ns = {"re": re, "Dict": Dict, "Optional": Optional, "Any": Any, "__name__": "_test_stub"}
_src = _routes_path.read_text()
# Extract only the IMAP helper functions (they are self-contained)
_funcs = []
for fname in ("_extract_prompt_value", "_build_imap_tool_call", "_extract_imap_subject",
              "_extract_imap_text", "_extract_imap_message_id", "_extract_imap_date_since"):
    start = _src.find(f"\ndef {fname}(")
    if start < 0:
        start = _src.find(f"def {fname}(")
    if start < 0:
        continue
    # Find next function def at same indent
    next_def = _src.find("\ndef ", start + 1)
    if next_def < 0:
        next_def = len(_src)
    _funcs.append(_src[start:next_def])

exec("\n".join(_funcs), _ns)
_build_imap_tool_call = _ns["_build_imap_tool_call"]
_extract_imap_subject = _ns["_extract_imap_subject"]
_extract_imap_text = _ns["_extract_imap_text"]
_extract_imap_message_id = _ns["_extract_imap_message_id"]
_extract_imap_date_since = _ns["_extract_imap_date_since"]

IMAP_TOOLS = {
    "mail_search": "mail_search",
    "mail_get_message": "mail_get_message",
    "mail_extract_message": "mail_extract_message",
    "mail_headlines": "mail_headlines",
    "mail_list_attachments": "mail_list_attachments",
    "mail_search_since_last": "mail_search_since_last",
}


class TestExtractImapSubject:
    @pytest.mark.UT
    @pytest.mark.req("CS-012")  # W28C-1711-R3.5 binding
    @pytest.mark.req("CS-011")  # W28C-1711-R3.5 binding
    @pytest.mark.req("CS-008")  # W28C-1711-R3.5 binding
    @pytest.mark.req("CS-003")  # W28C-1711-R3.5 binding
    @pytest.mark.cli
    @pytest.mark.req("CS-001")
    def test_fail2ban_keyword(self):
        assert _extract_imap_subject("Search email for fail2ban alerts") == "fail2ban"
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("CS-005")

    def test_subject_containing(self):
        assert _extract_imap_subject('messages with subject containing fail2ban') == "fail2ban"
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("CS-006")

    def test_no_subject(self):
        assert _extract_imap_subject("just a general email query") == ""


class TestExtractImapText:
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")
    def test_ukraine(self):
        assert _extract_imap_text("emails containing ukraine or kyiv") == "ukraine"
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")

    def test_kyiv(self):
        assert _extract_imap_text("search for messages about kyiv") == "kyiv"
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")

    def test_no_text(self):
        assert _extract_imap_text("list my recent emails") == ""


class TestExtractImapMessageId:
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")
    def test_message_id(self):
        result = _extract_imap_message_id('Get message with Message-ID "abc@example.com"')
        assert result == "<abc@example.com>"
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")

    def test_no_message_id(self):
        assert _extract_imap_message_id("just get the latest email") == ""


class TestExtractImapDateSince:
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")
    def test_last_24h(self):
        result = _extract_imap_date_since("emails from the last 24 hours")
        assert result  # Non-empty date string
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")

    def test_last_90_days(self):
        result = _extract_imap_date_since("messages from the last 90 days")
        assert result
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")

    def test_last_7_days(self):
        result = _extract_imap_date_since("emails from the last 7 days")
        assert result
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")

    def test_no_date(self):
        assert _extract_imap_date_since("search for emails") == ""


class TestBuildImapToolCall:
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")
    def test_u1_fail2ban_triage(self):
        result = _build_imap_tool_call(
            IMAP_TOOLS,
            "Search my email for messages with subject containing fail2ban from the last 24 hours"
        )
        assert result is not None
        tool_name, args = result
        assert tool_name == "mail_search"
        assert "SUBJECT" in args.get("query", "")
        assert "fail2ban" in args.get("query", "")
        assert "ALL" not in args.get("query", "")
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")

    def test_u2_ukraine_scan(self):
        result = _build_imap_tool_call(
            IMAP_TOOLS,
            "Search my email for messages containing ukraine or kyiv from the last 90 days"
        )
        assert result is not None
        tool_name, args = result
        assert tool_name == "mail_search"
        assert "TEXT" in args.get("query", "")
        assert "ALL" not in args.get("query", "")
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")

    def test_u3_structured_extract(self):
        result = _build_imap_tool_call(
            IMAP_TOOLS,
            'Get the full message with Message-ID "test@example.com" and extract structured data'
        )
        assert result is not None
        tool_name, args = result
        assert tool_name == "mail_get_message"
        assert args.get("message_id") == "<test@example.com>"
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")

    def test_u4_unseen(self):
        result = _build_imap_tool_call(
            IMAP_TOOLS,
            "Search for unseen emails from the last 7 days"
        )
        assert result is not None
        tool_name, args = result
        assert tool_name == "mail_search"
        assert "UNSEEN" in args.get("query", "")
        assert "ALL" not in args.get("query", "")
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")

    def test_u5_since_last(self):
        result = _build_imap_tool_call(
            IMAP_TOOLS,
            "Search for fail2ban emails using the since-last-check mode to see what is new"
        )
        assert result is not None
        tool_name, args = result
        assert tool_name == "mail_search_since_last"
        assert "SUBJECT" in args.get("query", "")
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")

    def test_fallback_generic(self):
        result = _build_imap_tool_call(IMAP_TOOLS, "show me my emails")
        assert result is not None
        tool_name, args = result
        assert tool_name == "mail_search"
        assert args.get("query") == "ALL"
    @pytest.mark.UT
    @pytest.mark.cli
    @pytest.mark.req("FR-006")

    def test_no_imap_tools(self):
        result = _build_imap_tool_call({"search": "search"}, "search my email")
        assert result is None
