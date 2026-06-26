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
from typing import Any, Dict, List

from ..config import ConfigManager


def _parse_json(value: str) -> Any:
    """Internal helper to json for this module."""
    try:
        return json.loads(value)
    except Exception:
        return None


def _truncate(text: str, max_chars: int) -> str:
    """Internal helper to truncate for this module."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _field_list(cfg_value: Any) -> List[str]:
    """Internal helper to field list for this module."""
    if isinstance(cfg_value, list):
        return [str(item) for item in cfg_value if str(item)]
    if isinstance(cfg_value, str):
        try:
            parsed = json.loads(cfg_value)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item)]
    return []


def _reduce_results_list(
    results: List[Dict[str, Any]],
    *,
    max_items: int,
    max_field_chars: int,
    fields: List[str],
) -> List[str]:
    """Internal helper to reduce results list for this module."""
    lines: List[str] = []
    for idx, item in enumerate(results[:max_items]):
        title = str(item.get("title") or item.get("name") or f"result_{idx + 1}")
        url = str(item.get("url") or item.get("link") or "")
        line = f"- {title}"
        if url:
            line += f" ({url})"
        lines.append(line)

        for field in fields:
            if field in ("title", "url", "name", "link"):
                continue
            value = item.get(field)
            if value is None:
                continue
            value_str = _truncate(str(value), max_field_chars)
            lines.append(f"  {field}: {value_str}")
    return lines


def reduce_tool_output(
    text: str,
    *,
    format_hint: str,
    output_format: str,
    max_items: int,
    max_chars: int,
    max_field_chars: int,
    fields: List[str],
) -> str:
    """Handle reduce tool output for the current runtime context."""
    raw = str(text or "").strip()
    if not raw:
        return ""

    hint = (format_hint or "auto").lower().strip()
    if hint in ("json", "auto"):
        parsed = _parse_json(raw)
        if isinstance(parsed, dict):
            lines: List[str] = []
            query = parsed.get("query")
            if query:
                lines.append(f"Query: {query}")
            count = parsed.get("number_of_results")
            if count is not None:
                lines.append(f"Number of results: {count}")
            results = parsed.get("results")
            if isinstance(results, list):
                lines.extend(
                    _reduce_results_list(
                        results,
                        max_items=max_items,
                        max_field_chars=max_field_chars,
                        fields=fields,
                    )
                )
            else:
                json_blob = json.dumps(parsed, ensure_ascii=True, indent=2)
                lines.append(_truncate(json_blob, max_chars))
            output = "\n".join(lines).strip()
            return _truncate(output, max_chars)
        if isinstance(parsed, list):
            results = [item for item in parsed if isinstance(item, dict)]
            lines = _reduce_results_list(
                results,
                max_items=max_items,
                max_field_chars=max_field_chars,
                fields=fields,
            )
            output = "\n".join(lines).strip()
            return _truncate(output, max_chars)

    if output_format.lower().strip() == "markdown":
        return _truncate(raw, max_chars)

    return _truncate(raw, max_chars)


def format_tool_output(text: str, config: ConfigManager, server_index: int = 0) -> str:
    """Format tool output for the current runtime context."""
    servers = config.get("mcp.servers", [])
    format_hint = ""
    if isinstance(servers, list) and 0 <= server_index < len(servers):
        server = servers[server_index]
        if isinstance(server, dict):
            format_hint = str(server.get("output_format") or "").strip()

    output_format = str(config.get("mcp.output.format") or "").strip()
    max_items = int(config.get("mcp.output.max_items"))
    max_chars = int(config.get("mcp.output.max_chars"))
    max_field_chars = int(config.get("mcp.output.max_field_chars"))
    fields = _field_list(config.get("mcp.output.fields"))

    return reduce_tool_output(
        text,
        format_hint=format_hint,
        output_format=output_format,
        max_items=max_items,
        max_chars=max_chars,
        max_field_chars=max_field_chars,
        fields=fields,
    )
