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

import asyncio
from typing import Callable, List, Optional

from ..config import ConfigManager
from ..llm import LLMService
from ..llm.protocols import ChatMessage
from ..prompts import (  # opt-in prompt-template resolution (W28B-319 / D5)
    PromptStore,
    default_prompt_store,
    resolve_request_system_prompt,
)
from ..session import SessionManager
from ..session.transcript import TranscriptEvent


def _build_messages(
    session_events: list[TranscriptEvent], system_prompt: Optional[str]
) -> List[ChatMessage]:
    """Internal helper to messages for this module."""
    messages: List[ChatMessage] = []

    if system_prompt:
        messages.append(ChatMessage(role="system", content=system_prompt))

    for e in session_events:
        if e.event_type == "context_loaded":
            content = str(e.data.get("content") or "")
            if content:
                messages.append(ChatMessage(role="system", content=content))

    for e in session_events:
        if e.event_type == "user_message":
            messages.append(
                ChatMessage(role="user", content=str(e.data.get("content") or ""))
            )
        elif e.event_type == "assistant_message":
            messages.append(
                ChatMessage(role="assistant", content=str(e.data.get("content") or ""))
            )

    return messages


async def _cli_system_prompt(
    config: ConfigManager, prompt_store: Optional[PromptStore]
) -> Optional[str]:
    """Resolve the CLI system prompt, opt-in to a template when configured.

    W28B-319 (D5): when ``llm.prompt_template`` is configured the system prompt
    is resolved+rendered from the prompt store (variables from
    ``llm.prompt_variables``). Otherwise behaviour is unchanged — the literal
    ``llm.system_prompt`` is used exactly as before.
    """
    template_name = str(config.get("llm.prompt_template") or "").strip()
    if template_name:
        variables = config.get("llm.prompt_variables")
        if not isinstance(variables, dict):
            variables = {}
        rendered = await resolve_request_system_prompt(
            prompt_store,
            prompt_template=template_name,
            prompt_variables=variables,
            prompt_version=config.get("llm.prompt_version"),
            strict=bool(config.get("llm.prompt_template_strict") or False),
        )
        if rendered is not None:
            return rendered

    system_prompt = config.get("llm.system_prompt")
    return str(system_prompt) if system_prompt else None


def _help_text(current_session_id: str, log_folder: str) -> str:
    """Internal helper to help text for this module."""
    return (
        "Cloud-Dog Chat Client\n"
        "Press ? for help. Commands:\n"
        "  ? | /help                  Show this help\n"
        "  /quit | /exit              Exit chat\n"
        "  /new                       Create and switch to a new session\n"
        "  /sessions                  List known sessions\n"
        "  /use <session_id>          Switch to an existing session\n"
        "  /mcp                       List configured MCP servers + selection\n"
        "  /mcp use 0,1               Set selected MCP servers for this session\n"
        "  /logs                      Show active session log path\n"
        "  /attach <path>             Attach context file\n"
        "  /write-context <path>      Write session context snapshot\n"
        "\n"
        f"Current session: {current_session_id}\n"
        f"Log folder: {log_folder}\n"
    )


def _parse_index_csv(raw: str) -> list[int]:
    """Internal helper to index csv for this module."""
    out: list[int] = []
    for part in str(raw or "").split(","):
        p = part.strip()
        if not p:
            continue
        try:
            idx = int(p)
        except ValueError:
            continue
        if idx < 0:
            continue
        if idx not in out:
            out.append(idx)
    return out


def _configured_mcp_servers(config: ConfigManager) -> list[dict]:
    """Internal helper to configured MCP servers for this module."""
    raw = config.get("mcp.servers") or []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "index": i,
                "name": str(item.get("name") or f"server-{i}"),
                "transport": str(item.get("transport") or ""),
                "base_url": str(item.get("base_url") or ""),
            }
        )
    return out


def _current_selected_mcp_indices(
    session_manager: SessionManager, session_id: str
) -> list[int]:
    """Internal helper to current selected MCP indices for this module."""
    session = session_manager.get_session(session_id)
    metadata = session.get("metadata") if isinstance(session, dict) else {}
    if not isinstance(metadata, dict):
        return []
    selected = metadata.get("selected_mcp_server_indices")
    if not isinstance(selected, list):
        return []
    out: list[int] = []
    for x in selected:
        try:
            idx = int(x)
        except (TypeError, ValueError):
            continue
        if idx >= 0 and idx not in out:
            out.append(idx)
    return out


async def run_chat_loop(
    *,
    config: ConfigManager,
    session_manager: SessionManager,
    session_id: str,
    stream_override: Optional[bool],
    write_out: Callable[[str], None],
    write_out_flush: Callable[[], None],
    read_in: Callable[[], str],
    prompt_store: Optional[PromptStore] = None,
) -> None:
    """Handle run chat loop for the current runtime context."""
    llm = LLMService(config)
    current_session_id = session_id
    # W28B-319 (D5): only stand up a prompt store when a template is configured,
    # so the unchanged default CLI path needs no prompt dependency.
    if prompt_store is None and str(config.get("llm.prompt_template") or "").strip():
        prompt_store = default_prompt_store()

    write_out(f"[info] Press ? for help. Current session: {current_session_id}\n")
    write_out_flush()

    while True:
        user_text = read_in().strip()
        if not user_text:
            continue

        if user_text in ("?", "/help"):
            write_out(
                _help_text(
                    current_session_id=current_session_id,
                    log_folder=str(session_manager.log_folder),
                )
            )
            write_out_flush()
            continue

        if user_text in ("/quit", "/exit"):
            session_manager.append_event(
                current_session_id, TranscriptEvent(event_type="session_ended", data={})
            )
            return

        if user_text == "/new":
            current_session_id = session_manager.create_session(metadata={})
            write_out(f"[session] created and switched to {current_session_id}\n")
            write_out_flush()
            continue

        if user_text == "/sessions":
            sessions = session_manager.list_sessions()
            if not sessions:
                write_out("[sessions] none\n")
            else:
                write_out("[sessions]\n")
                for s in sessions:
                    sid = str(s.get("id") or "")
                    mark = "*" if sid == current_session_id else " "
                    write_out(f" {mark} {sid}  {str(s.get('created_at') or '')}\n")
            write_out_flush()
            continue

        if user_text.startswith("/use "):
            target = user_text[len("/use ") :].strip()
            if not target:
                write_out("[error] usage: /use <session_id>\n")
                write_out_flush()
                continue
            try:
                session_manager.load_session(target)
                current_session_id = target
                write_out(f"[session] switched to {current_session_id}\n")
            except Exception as e:
                write_out(f"[error] unable to switch session: {e}\n")
            write_out_flush()
            continue

        if user_text == "/logs":
            session = session_manager.get_session(current_session_id)
            write_out(f"[logs] {session.get('log_path')}\n")
            write_out_flush()
            continue

        if user_text == "/mcp":
            servers = _configured_mcp_servers(config)
            selected = _current_selected_mcp_indices(
                session_manager, current_session_id
            )
            write_out("[mcp] configured servers\n")
            if not servers:
                write_out(" (none)\n")
            else:
                for s in servers:
                    idx = int(s["index"])
                    mark = "*" if idx in selected else " "
                    write_out(
                        f" {mark} {idx}: {s['name']} [{s['transport']}] {s['base_url']}\n"
                    )
            write_out(f"[mcp] selected for session {current_session_id}: {selected}\n")
            write_out_flush()
            continue

        if user_text.startswith("/mcp use "):
            raw = user_text[len("/mcp use ") :].strip()
            selected = _parse_index_csv(raw)
            servers = _configured_mcp_servers(config)
            max_idx = len(servers) - 1
            if max_idx >= 0:
                selected = [i for i in selected if i <= max_idx]
            else:
                selected = []
            session_manager.update_session_metadata(
                current_session_id,
                {"selected_mcp_server_indices": selected},
            )
            write_out(f"[mcp] selected for session {current_session_id}: {selected}\n")
            write_out_flush()
            continue

        if user_text.startswith("/attach "):
            path = user_text[len("/attach ") :].strip()
            session_manager.load_context_file(current_session_id, path)
            write_out(f"[attached] {path}\n")
            write_out_flush()
            continue

        if user_text.startswith("/write-context "):
            out_path = user_text[len("/write-context ") :].strip()
            session_manager.write_context_snapshot(current_session_id, out_path)
            write_out(f"[context written] {out_path}\n")
            write_out_flush()
            continue

        session_manager.append_event(
            current_session_id,
            TranscriptEvent(event_type="user_message", data={"content": user_text}),
        )

        session = session_manager.get_session(current_session_id)
        system_prompt = await _cli_system_prompt(config, prompt_store)
        messages = _build_messages(session["events"], system_prompt)

        stream_enabled = bool(config.get("llm.stream"))
        if stream_override is not None:
            stream_enabled = bool(stream_override)

        try:
            if stream_enabled:
                assistant_text = ""
                async for chunk in llm.stream(messages):
                    if chunk.content_delta:
                        assistant_text += chunk.content_delta
                        write_out(chunk.content_delta)
                        write_out_flush()
                        session_manager.append_event(
                            current_session_id,
                            TranscriptEvent(
                                event_type="assistant_stream_chunk",
                                data={"content_delta": chunk.content_delta},
                            ),
                        )
                write_out("\n")
                write_out_flush()
                session_manager.append_event(
                    current_session_id,
                    TranscriptEvent(
                        event_type="assistant_message", data={"content": assistant_text}
                    ),
                )
            else:
                result = await llm.complete(messages)
                write_out(result.content)
                write_out("\n")
                write_out_flush()
                session_manager.append_event(
                    current_session_id,
                    TranscriptEvent(
                        event_type="assistant_message", data={"content": result.content}
                    ),
                )
        except Exception as e:
            error_text = f"[error] LLM request failed: {e}"
            write_out(error_text)
            write_out("\n")
            write_out_flush()
            session_manager.append_event(
                current_session_id,
                TranscriptEvent(event_type="assistant_error", data={"message": str(e)}),
            )

        await asyncio.sleep(0)
