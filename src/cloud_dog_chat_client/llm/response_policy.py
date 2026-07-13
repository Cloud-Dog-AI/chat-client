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

from dataclasses import dataclass
from typing import Optional, Tuple

from ..config import ConfigManager


@dataclass
class ResponsePolicy:
    enforce: bool
    envelope_tag: str
    format: str
    marker_key: str
    marker_value: str
    answer_key: str
    strip_for_user: bool
    show_thinking: bool
    display_answer_tag: str
    allow_header_only: bool
    retry_attempts: int
    retry_backoff_seconds: float


def _require_cfg(config: ConfigManager, key: str) -> str:
    """Internal helper to require configuration for this module."""
    value = config.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    value_str = str(value).strip()
    if not value_str:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value_str


def load_response_policy(config: ConfigManager) -> ResponsePolicy:
    """Load response policy for the current runtime context."""
    enforce = bool(config.get("llm.response.enforce") or False)
    if not enforce:
        return ResponsePolicy(
            enforce=False,
            envelope_tag="",
            format="",
            marker_key="",
            marker_value="",
            answer_key="",
            strip_for_user=False,
            show_thinking=False,
            display_answer_tag="",
            allow_header_only=False,
            retry_attempts=0,
            retry_backoff_seconds=0.0,
        )

    envelope_tag = _require_cfg(config, "llm.response.envelope_tag")
    response_format = _require_cfg(config, "llm.response.format")
    marker_key = _require_cfg(config, "llm.response.marker_key")
    marker_value = str(config.get("llm.response.marker_value") or "").strip()
    answer_key = _require_cfg(config, "llm.response.answer_key")
    strip_for_user = bool(config.get("llm.response.strip_for_user") or False)
    show_thinking = bool(config.get("llm.response.show_thinking") or False)
    display_answer_tag = str(
        config.get("llm.response.display_answer_tag") or ""
    ).strip()
    allow_header_only = bool(config.get("llm.response.allow_header_only") or False)
    retry_attempts = int(_require_cfg(config, "llm.response.retry_attempts"))
    retry_backoff_seconds = float(
        _require_cfg(config, "llm.response.retry_backoff_seconds")
    )

    if strip_for_user and not enforce:
        raise RuntimeError(
            "CRITICAL ERROR: llm.response.enforce must be true when strip_for_user is enabled"
        )

    return ResponsePolicy(
        enforce=True,
        envelope_tag=envelope_tag,
        format=response_format,
        marker_key=marker_key,
        marker_value=marker_value,
        answer_key=answer_key,
        strip_for_user=strip_for_user,
        show_thinking=show_thinking,
        display_answer_tag=display_answer_tag,
        allow_header_only=allow_header_only,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )


def build_response_instruction(policy: ResponsePolicy) -> str:
    """Build response instruction for the current runtime context."""
    lines = [
        f'Respond ONLY with a <{policy.envelope_tag} format="{policy.format}"> ... </{policy.envelope_tag}> envelope.',
        "Inside the envelope, include these lines in order:",
    ]
    if policy.marker_value:
        lines.append(f"{policy.marker_key}: {policy.marker_value}")
    else:
        lines.append(f"{policy.marker_key}: <value>")
    lines.append(f"{policy.answer_key}:")
    lines.append(
        "Then provide the answer content in markdown. Do not include anything outside the envelope."
    )
    if policy.allow_header_only:
        lines.append(
            "If you cannot use tags, output only the MARKER and ANSWER lines in plain text."
        )
    return "\n".join(lines)


def build_retry_instruction(policy: ResponsePolicy, last_error: str) -> str:
    """Build retry instruction for the current runtime context."""
    return (
        "Your previous response did not follow the required format.\n"
        f"Error: {last_error}\n\n" + build_response_instruction(policy)
    )


def strip_reasoning_tags(content: str) -> str:
    """Handle strip reasoning tags for the current runtime context."""
    if "<reasoning>" in content and "</reasoning>" in content:
        start = content.find("<reasoning>") + len("<reasoning>")
        end = content.find("</reasoning>", start)
        if end > start:
            return content[start:end]
    if "<think>" in content and "</think>" in content:
        close = content.find("</think>") + len("</think>")
        return content[close:]
    return content


def extract_thinking(content: str) -> str:
    """Handle extract thinking for the current runtime context."""
    if "<thinking>" in content and "</thinking>" in content:
        start = content.find("<thinking>") + len("<thinking>")
        end = content.find("</thinking>", start)
        if end > start:
            return content[start:end].strip()
    if "<think>" in content and "</think>" in content:
        start = content.find("<think>") + len("<think>")
        end = content.find("</think>", start)
        if end > start:
            return content[start:end].strip()
    return ""


def validate_response(
    content: str, policy: ResponsePolicy
) -> Tuple[bool, Optional[str]]:
    """Validate response for the current runtime context."""
    if not policy.enforce:
        return True, None

    text = strip_reasoning_tags(content).strip()
    if not text:
        return False, "empty response"

    tag = policy.envelope_tag
    open_idx = text.find(f"<{tag}")
    if open_idx < 0:
        if policy.allow_header_only:
            return _validate_header_only(text, policy)
        return False, f"missing <{tag}> envelope"
    open_end = text.find(">", open_idx)
    if open_end < 0:
        return False, f"unterminated <{tag}> tag"

    close_tag = f"</{tag}>"
    close_idx = text.rfind(close_tag)
    if close_idx < 0 or close_idx < open_end:
        return False, f"missing {close_tag}"

    open_tag = text[open_idx : open_end + 1]
    if f'format="{policy.format}"' not in open_tag:
        return False, f'missing format="{policy.format}" attribute in <{tag}>'

    inner = text[open_end + 1 : close_idx].strip()
    if not inner:
        return False, "empty envelope body"

    lines = [line.strip() for line in inner.splitlines() if line.strip()]
    if not lines:
        return False, "empty envelope lines"

    marker_prefix = f"{policy.marker_key}:"
    marker_line = next((line for line in lines if line.startswith(marker_prefix)), None)
    if not marker_line:
        return False, f"missing {policy.marker_key} line"
    if policy.marker_value:
        marker_value = marker_line[len(marker_prefix) :].strip()
        if marker_value != policy.marker_value:
            return False, f"marker value must be '{policy.marker_value}'"

    answer_prefix = f"{policy.answer_key}:"
    answer_idx = None
    for idx, line in enumerate(lines):
        if line.startswith(answer_prefix):
            answer_idx = idx
            break
    if answer_idx is None:
        return False, f"missing {policy.answer_key} line"

    answer_line = lines[answer_idx][len(answer_prefix) :].strip()
    if answer_line:
        return True, None

    if answer_idx == len(lines) - 1:
        return False, "answer content missing"
    return True, None


def _validate_header_only(
    text: str, policy: ResponsePolicy
) -> Tuple[bool, Optional[str]]:
    """Internal helper to header only for this module."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False, "empty header-only response"

    marker_prefix = f"{policy.marker_key}:"
    marker_line = next((line for line in lines if line.startswith(marker_prefix)), None)
    if not marker_line:
        return False, f"missing {policy.marker_key} line"
    if policy.marker_value:
        marker_value = marker_line[len(marker_prefix) :].strip()
        if marker_value != policy.marker_value:
            return False, f"marker value must be '{policy.marker_value}'"

    answer_prefix = f"{policy.answer_key}:"
    answer_line = next((line for line in lines if line.startswith(answer_prefix)), None)
    if not answer_line:
        marker_index = next(
            (idx for idx, line in enumerate(lines) if line.startswith(marker_prefix)),
            None,
        )
        if marker_index is not None and marker_index < len(lines) - 1:
            return True, None
        return False, f"missing {policy.answer_key} line"
    answer_value = answer_line[len(answer_prefix) :].strip()
    if answer_value:
        return True, None

    return True, None


def parse_response(content: str, policy: ResponsePolicy) -> Tuple[str, str]:
    """Parse response for the current runtime context."""
    text = strip_reasoning_tags(content).strip()
    if not text:
        raise RuntimeError("CRITICAL ERROR: empty response content")

    thinking = extract_thinking(content)
    tag = policy.envelope_tag
    open_idx = text.find(f"<{tag}")
    if open_idx >= 0:
        open_end = text.find(">", open_idx)
        if open_end < 0:
            raise RuntimeError("CRITICAL ERROR: unterminated response envelope")
        close_tag = f"</{tag}>"
        close_idx = text.rfind(close_tag)
        if close_idx < open_end:
            raise RuntimeError("CRITICAL ERROR: missing response envelope closing tag")
        body = text[open_end + 1 : close_idx].strip()
        answer = _extract_answer_from_lines(body.splitlines(), policy)
        return _strip_marker_from_answer(answer, policy), thinking

    if policy.allow_header_only:
        answer = _extract_answer_from_lines(text.splitlines(), policy)
        return _strip_marker_from_answer(answer, policy), thinking

    raise RuntimeError("CRITICAL ERROR: response envelope missing")


def _extract_answer_from_lines(lines: list[str], policy: ResponsePolicy) -> str:
    """Internal helper to extract answer from lines for this module."""
    stripped = [line.strip() for line in lines if line.strip()]
    if not stripped:
        raise RuntimeError("CRITICAL ERROR: empty response body")

    answer_prefix = f"{policy.answer_key}:"
    for idx, line in enumerate(stripped):
        if line.startswith(answer_prefix):
            remainder = line[len(answer_prefix) :].strip()
            if remainder:
                return remainder
            if idx + 1 < len(stripped):
                return "\n".join(stripped[idx + 1 :]).strip()
            return ""

    if policy.allow_header_only:
        marker_prefix = f"{policy.marker_key}:"
        for idx, line in enumerate(stripped):
            if line.startswith(marker_prefix) and idx + 1 < len(stripped):
                return "\n".join(stripped[idx + 1 :]).strip()

    raise RuntimeError(f"CRITICAL ERROR: missing {policy.answer_key} line")


def _strip_marker_from_answer(answer: str, policy: ResponsePolicy) -> str:
    """Internal helper to strip marker from answer for this module."""
    if not answer:
        return answer
    if policy.marker_value:
        answer = answer.replace(policy.marker_value, "")
    return answer.strip()


def format_user_response(content: str, policy: ResponsePolicy) -> str:
    """Format user response for the current runtime context."""
    if not policy.strip_for_user:
        return content

    answer, thinking = parse_response(content, policy)
    if policy.show_thinking:
        output = f"<thinking>{thinking}</thinking>"
        if policy.display_answer_tag:
            return f"{output}\n<{policy.display_answer_tag}>{answer}</{policy.display_answer_tag}>"
        return f"{output}\n{answer}"

    if policy.display_answer_tag:
        return f"<{policy.display_answer_tag}>{answer}</{policy.display_answer_tag}>"
    return answer
