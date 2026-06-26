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

"""AT1.23 — Cross-project SPAM analysis (file-mcp x imap-mcp).

Workflow summary:
- Query older/recent SPAM windows from imap-mcp via chat-client MCP routing.
- Persist extracted messages as markdown into file-mcp folders.
- Generate older/recent summaries and a trend comparison with LLM.
- Ensure all artefacts are cleaned in finally blocks.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.cross_project import (
    create_session,
    ensure_local_docker_runtime,
    extract_tool_json,
    extract_tool_text,
    llm_message,
    mcp_execute,
    mcp_tools_call,
    require_cfg,
    utc_ts,
)


def _safe_segment(value: str, *, max_len: int = 96) -> str:
    collapsed = re.sub(r"\s+", "-", str(value or "").strip())
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "", collapsed).strip("-._")
    return (cleaned or "item")[:max_len]


def _imap_query(days_back: int, *, older: bool) -> str:
    anchor = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%d-%b-%Y")
    return f"BEFORE {anchor}" if older else f"SINCE {anchor}"


def _messages_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages = payload.get("messages")
    if messages is None:
        messages = payload.get("results")
    if not isinstance(messages, list):
        return []
    return [item for item in messages if isinstance(item, dict)]


async def _fetch_extract_markdown(
    client: httpx.AsyncClient,
    session_id: str,
    imap_target_index: int | None,
    imap_target_server: dict[str, Any] | None,
    require_initialize: bool,
    protocol_version: str,
    *,
    profile_id: str,
    folder: str,
    uid: str,
) -> str:
    extracted = await mcp_tools_call(
        client,
        session_id,
        imap_target_index,
        "mail_extract_message",
        {
            "profile_id": profile_id,
            "uid": uid,
            "folder": folder,
            "format": "markdown",
        },
        require_initialize=require_initialize,
        protocol_version=protocol_version,
        server=imap_target_server,
    )
    text = extract_tool_text(extracted).strip()
    if text:
        return text

    fetched = await mcp_tools_call(
        client,
        session_id,
        imap_target_index,
        "mail_get_message",
        {
            "profile_id": profile_id,
            "uid": uid,
            "folder": folder,
        },
        require_initialize=require_initialize,
        protocol_version=protocol_version,
        server=imap_target_server,
    )
    fallback = extract_tool_json(fetched)
    raw_eml = str(fallback.get("raw_eml") or "").strip()
    if raw_eml:
        return raw_eml
    return extract_tool_text(fetched).strip()


async def _delete_file_tree(
    client: httpx.AsyncClient,
    session_id: str,
    file_target_index: int | None,
    file_target_server: dict[str, Any] | None,
    require_initialize: bool,
    protocol_version: str,
    *,
    files: list[str],
    dirs: list[str],
) -> None:
    for path in reversed(files):
        try:
            await mcp_tools_call(
                client,
                session_id,
                file_target_index,
                "delete_file",
                {"path": path},
                require_initialize=require_initialize,
                protocol_version=protocol_version,
                server=file_target_server,
            )
        except Exception:
            continue

    for path in reversed(dirs):
        try:
            await mcp_tools_call(
                client,
                session_id,
                file_target_index,
                "delete_dir",
                {"path": path},
                require_initialize=require_initialize,
                protocol_version=protocol_version,
                server=file_target_server,
            )
        except Exception:
            continue


async def _write_text_file(
    client: httpx.AsyncClient,
    session_id: str,
    file_target_index: int | None,
    file_target_server: dict[str, Any] | None,
    require_initialize: bool,
    protocol_version: str,
    *,
    path: str,
    content: str,
) -> None:
    await mcp_tools_call(
        client,
        session_id,
        file_target_index,
        "write_file",
        {"path": path, "content": content, "overwrite": True},
        require_initialize=require_initialize,
        protocol_version=protocol_version,
        server=file_target_server,
    )
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
@pytest.mark.timeout(1200)
async def test_at1_23_cross_project_spam_analysis(env_file: str) -> None:
    cfg = ConfigManager(env_file=env_file)
    ts = utc_ts()

    ensure_local_docker_runtime(cfg, "chat_tests.at1_23.file_mcp", label="AT1.23 file-mcp")
    ensure_local_docker_runtime(cfg, "chat_tests.at1_23.imap_mcp", label="AT1.23 imap-mcp")

    file_idx = int(require_cfg(cfg, "mcp.at1_23.file_server_index"))
    imap_idx = int(require_cfg(cfg, "mcp.at1_23.imap_server_index"))

    file_server = cfg.get("mcp.at1_23.file_server")
    imap_server = cfg.get("mcp.at1_23.imap_server")
    if file_server is not None and not isinstance(file_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_23.file_server must be an object")
    if imap_server is not None and not isinstance(imap_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_23.imap_server must be an object")

    file_target_index = None if isinstance(file_server, dict) else file_idx
    imap_target_index = None if isinstance(imap_server, dict) else imap_idx
    file_target_server = file_server if isinstance(file_server, dict) else None
    imap_target_server = imap_server if isinstance(imap_server, dict) else None

    require_init_file = bool(cfg.get("mcp.at1_23.require_initialize_file") or False)
    require_init_imap = bool(cfg.get("mcp.at1_23.require_initialize_imap") or False)
    protocol_version = str(require_cfg(cfg, "mcp.defaults.protocol_version"))

    file_root = str(cfg.get("chat_tests.at1_23.file_root") or "/app/working/chat-client-w26a").rstrip("/")
    imap_profile_id = str(cfg.get("chat_tests.at1_23.imap_profile_id") or "operations")
    spam_folder = str(cfg.get("chat_tests.at1_23.imap_spam_folder") or "SPAM")
    max_emails = int(cfg.get("chat_tests.at1_23.max_emails_per_batch") or 50)
    days_back = int(cfg.get("chat_tests.at1_23.days_back") or 7)

    session_id = ""
    analysis_session_ids: list[str] = []
    created_files: list[str] = []
    created_dirs: list[str] = []

    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        base_url = api_base_url(cfg)
        timeout = float(require_cfg(cfg, "client_api.request_timeout_seconds"))

        async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout) as client:
            session_id = await create_session(client, "at1.23", metadata={"w26a": True})

            await mcp_execute(
                client,
                session_id,
                file_target_index,
                steps=[{"method": "tools/list"}],
                require_initialize=require_init_file,
                protocol_version=protocol_version,
                server=file_target_server,
            )
            await mcp_execute(
                client,
                session_id,
                imap_target_index,
                steps=[{"method": "tools/list"}],
                require_initialize=require_init_imap,
                protocol_version=protocol_version,
                server=imap_target_server,
            )

            parent_dir = f"{file_root}/at1_23_spam_analysis_{ts}"
            older_dir = f"{parent_dir}/older-week"
            recent_dir = f"{parent_dir}/recent-week"
            created_dirs.extend([parent_dir, older_dir, recent_dir])

            for path in created_dirs:
                await mcp_tools_call(
                    client,
                    session_id,
                    file_target_index,
                    "create_dir",
                    {"path": path, "parents": True, "exist_ok": True},
                    require_initialize=require_init_file,
                    protocol_version=protocol_version,
                    server=file_target_server,
                )

            older_query = _imap_query(days_back, older=True)
            recent_query = _imap_query(days_back, older=False)

            older_search = await mcp_tools_call(
                client,
                session_id,
                imap_target_index,
                "mail_search",
                {
                    "profile_id": imap_profile_id,
                    "mode": "imap",
                    "query": older_query,
                    "filters": {"folder": spam_folder},
                    "limit": max_emails,
                },
                require_initialize=require_init_imap,
                protocol_version=protocol_version,
                server=imap_target_server,
            )
            recent_search = await mcp_tools_call(
                client,
                session_id,
                imap_target_index,
                "mail_search",
                {
                    "profile_id": imap_profile_id,
                    "mode": "imap",
                    "query": recent_query,
                    "filters": {"folder": spam_folder},
                    "limit": max_emails,
                },
                require_initialize=require_init_imap,
                protocol_version=protocol_version,
                server=imap_target_server,
            )

            older_messages = _messages_from_payload(extract_tool_json(older_search))[:max_emails]
            recent_messages = _messages_from_payload(extract_tool_json(recent_search))[:max_emails]

            async def _persist_batch(
                label: str,
                target_dir: str,
                messages: list[dict[str, Any]],
            ) -> tuple[int, str]:
                saved = 0
                chunks: list[str] = []
                for idx, message in enumerate(messages):
                    uid = str(message.get("uid") or message.get("id") or "").strip()
                    if not uid:
                        continue

                    subject = str(message.get("subject") or f"{label}-{idx+1}").strip()
                    msg_date = str(message.get("date") or message.get("internal_date") or "undated")

                    markdown = await _fetch_extract_markdown(
                        client,
                        session_id,
                        imap_target_index,
                        imap_target_server,
                        require_init_imap,
                        protocol_version,
                        profile_id=imap_profile_id,
                        folder=spam_folder,
                        uid=uid,
                    )
                    if not markdown.strip():
                        continue

                    filename = f"{_safe_segment(msg_date, max_len=20)}_{_safe_segment(subject, max_len=64)}.md"
                    if not filename.endswith(".md"):
                        filename = f"{filename}.md"
                    path = f"{target_dir}/{filename}"

                    body = (
                        f"# {subject}\n\n"
                        f"- UID: {uid}\n"
                        f"- Date: {msg_date}\n"
                        f"- Folder: {spam_folder}\n\n"
                        f"{markdown}\n"
                    )
                    await _write_text_file(
                        client,
                        session_id,
                        file_target_index,
                        file_target_server,
                        require_init_file,
                        protocol_version,
                        path=path,
                        content=body,
                    )
                    created_files.append(path)
                    chunks.append(body[:6000])
                    saved += 1

                return saved, "\n\n---\n\n".join(chunks)

            older_saved, older_corpus = await _persist_batch("older", older_dir, older_messages)
            recent_saved, recent_corpus = await _persist_batch("recent", recent_dir, recent_messages)

            if older_messages:
                assert older_saved > 0, "CRITICAL ERROR: older-week messages existed but none were persisted"
            if recent_messages:
                assert recent_saved > 0, "CRITICAL ERROR: recent-week messages existed but none were persisted"

            older_summary_session_id = await create_session(client, "at1.23-older-summary")
            analysis_session_ids.append(older_summary_session_id)
            older_summary = await llm_message(
                client,
                older_summary_session_id,
                (
                    "Summarise older SPAM emails in markdown with headings for top senders, "
                    "themes, and phishing patterns.\n\n"
                    f"{older_corpus[:12000]}"
                ),
            )
            older_summary_path = f"{parent_dir}/older-week-summary.md"
            await _write_text_file(
                client,
                session_id,
                file_target_index,
                file_target_server,
                require_init_file,
                protocol_version,
                path=older_summary_path,
                content=older_summary,
            )
            created_files.append(older_summary_path)

            recent_summary_session_id = await create_session(client, "at1.23-recent-summary")
            analysis_session_ids.append(recent_summary_session_id)
            recent_summary = await llm_message(
                client,
                recent_summary_session_id,
                (
                    "Summarise recent SPAM emails in markdown with headings for top senders, "
                    "themes, and phishing patterns.\n\n"
                    f"{recent_corpus[:12000]}"
                ),
            )
            recent_summary_path = f"{parent_dir}/recent-week-summary.md"
            await _write_text_file(
                client,
                session_id,
                file_target_index,
                file_target_server,
                require_init_file,
                protocol_version,
                path=recent_summary_path,
                content=recent_summary,
            )
            created_files.append(recent_summary_path)

            trend_session_id = await create_session(client, "at1.23-trend-analysis")
            analysis_session_ids.append(trend_session_id)
            trend_analysis = await llm_message(
                client,
                trend_session_id,
                (
                    "Compare older-week and recent-week SPAM summaries. Return markdown with headings "
                    "and bullet points describing trend/comparison insights.\n\n"
                    f"## Older\n{older_summary[:6000]}\n\n"
                    f"## Recent\n{recent_summary[:6000]}"
                ),
            )
            trend_path = f"{parent_dir}/trend-analysis.md"
            await _write_text_file(
                client,
                session_id,
                file_target_index,
                file_target_server,
                require_init_file,
                protocol_version,
                path=trend_path,
                content=trend_analysis,
            )
            created_files.append(trend_path)

            trend_lc = trend_analysis.lower()
            assert len(trend_analysis.strip()) > 200, "CRITICAL ERROR: trend analysis is too short"
            assert ("trend" in trend_lc) or ("comparison" in trend_lc)
            assert "#" in trend_analysis or "- " in trend_analysis

            trend_read = await mcp_tools_call(
                client,
                session_id,
                file_target_index,
                "read_file",
                {"path": trend_path},
                require_initialize=require_init_file,
                protocol_version=protocol_version,
                server=file_target_server,
            )
            trend_payload = extract_tool_json(trend_read)
            trend_text = str(trend_payload.get("content") or trend_payload.get("text") or "").strip()
            if not trend_text:
                trend_text = extract_tool_text(trend_read).strip()
            assert trend_text, "CRITICAL ERROR: trend-analysis.md was not readable after write"
    finally:
        try:
            if session_id:
                try:
                    async with httpx.AsyncClient(
                        base_url=api_base_url(cfg),
                        headers=api_headers(cfg),
                        timeout=float(require_cfg(cfg, "client_api.request_timeout_seconds")),
                    ) as cleanup_client:
                        await _delete_file_tree(
                            cleanup_client,
                            session_id,
                            file_target_index,
                            file_target_server,
                            require_init_file,
                            protocol_version,
                            files=created_files,
                            dirs=created_dirs,
                        )
                        await cleanup_client.delete(f"/sessions/{session_id}")
                        for analysis_session_id in analysis_session_ids:
                            await cleanup_client.delete(f"/sessions/{analysis_session_id}")
                except httpx.HTTPError:
                    pass
        finally:
            stop_api(cfg, env_file=env_file)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.smtp, pytest.mark.mcp, pytest.mark.docker, pytest.mark.heavy]
