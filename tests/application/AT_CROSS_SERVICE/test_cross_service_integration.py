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

"""W28A-262 cross-service AT: chat profile orchestrates file-mcp and imap-mcp."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.cross_project import (
    create_session,
    ensure_local_docker_runtime,
    extract_tool_json,
    llm_message,
    mcp_execute,
    mcp_tools_call,
    require_cfg,
    utc_ts,
)


def _event_calls(events: List[Dict[str, Any]], *, server_index: int) -> List[str]:
    calls: List[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("event_type") or "") != "mcp_tool_call":
            continue
        data = event.get("data") or {}
        if not isinstance(data, dict):
            continue
        if int(data.get("server_index", -1)) != int(server_index):
            continue
        name = str(data.get("name") or "").strip()
        if name:
            calls.append(name)
    return calls


def _messages_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages = payload.get("messages")
    if messages is None:
        messages = payload.get("results")
    if not isinstance(messages, list):
        return []
    return [item for item in messages if isinstance(item, dict)]


def _subject_fragment(message: Dict[str, Any]) -> str:
    subject = str(message.get("subject") or "").strip()
    if not subject:
        raise RuntimeError("CRITICAL ERROR: imap search result missing subject")
    return subject[:64]


def _candidate_imap_folders(primary: str) -> list[str]:
    candidates: list[str] = []
    for folder in [primary, "INBOX.Fail2ban", "INBOX"]:
        cleaned = str(folder or "").strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
    return candidates
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
@pytest.mark.timeout(900)
async def test_cross_service_profile_file_and_email_access(env_file: str) -> None:
    cfg = ConfigManager(env_file=env_file)

    ensure_local_docker_runtime(cfg, "chat_tests.at1_23.file_mcp", label="W28A-262 file-mcp")
    ensure_local_docker_runtime(cfg, "chat_tests.at1_23.imap_mcp", label="W28A-262 imap-mcp")

    protocol_version = str(require_cfg(cfg, "mcp.defaults.protocol_version"))
    api_timeout = float(require_cfg(cfg, "client_api.request_timeout_seconds"))
    file_server = cfg.get("mcp.at1_23.file_server")
    imap_server = cfg.get("mcp.at1_23.imap_server")
    if not isinstance(file_server, dict) or not isinstance(imap_server, dict):
        raise RuntimeError("CRITICAL ERROR: AT1.23 file/imap server objects must be configured")

    require_init_file = bool(cfg.get("mcp.at1_23.require_initialize_file") or False)
    require_init_imap = bool(cfg.get("mcp.at1_23.require_initialize_imap") or False)
    imap_profile_id = str(cfg.get("chat_tests.at1_23.imap_profile_id") or "operations").strip()
    imap_folder = str(cfg.get("chat_tests.at1_23.imap_spam_folder") or "SPAM").strip()
    file_root = str(cfg.get("chat_tests.at1_23.file_root") or "").rstrip("/")
    if not file_root:
        raise RuntimeError("CRITICAL ERROR: chat_tests.at1_23.file_root is required")

    api_header = str(
        cfg.get("client_api.admin_api_key_header")
        or cfg.get("client_api.api_key_header")
        or "X-API-Key"
    ).strip()
    admin_key = str(require_cfg(cfg, "client_api.admin_api_key") or "").strip()
    if not admin_key:
        raise RuntimeError("CRITICAL ERROR: admin-capable API key is required for profile CRUD")
    admin_headers = {api_header: admin_key}

    ts = utc_ts()
    profile_id = f"w28a262-profile-{ts}"
    session_id = ""
    created_file = f"{file_root}/w28a262_{ts}/chat_access_probe.txt"
    created_dir = created_file.rsplit("/", 1)[0]

    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        async with httpx.AsyncClient(
            base_url=api_base_url(cfg),
            headers=api_headers(cfg),
            timeout=api_timeout,
        ) as client:
            created_profile = await client.post(
                "/v1/profiles",
                headers=admin_headers,
                json={
                    "profile_id": profile_id,
                    "name": f"W28A262 {ts}",
                    "description": "Cross-service file and email access profile",
                    "mcp_bindings": [file_server, imap_server],
                    "session_defaults": {"selected_mcp_server_indices": [0, 1]},
                    "access_control": {"roles": ["admin", "viewer"]},
                },
            )
            assert created_profile.status_code == 200, (
                f"CRITICAL ERROR: profile create failed: {created_profile.status_code} "
                f"{created_profile.text}"
            )

            session_id = await create_session(
                client,
                "w28a262",
                metadata={"profile_id": profile_id},
            )

            prefs = await client.put(
                f"/sessions/{session_id}/preferences",
                json={"selected_mcp_server_indices": [0, 1]},
            )
            assert prefs.status_code == 200, (
                f"CRITICAL ERROR: failed to set selected MCP servers: {prefs.status_code} "
                f"{prefs.text}"
            )

            file_tools = await mcp_execute(
                client,
                session_id,
                0,
                steps=[{"method": "tools/list"}],
                require_initialize=require_init_file,
                protocol_version=protocol_version,
            )
            assert file_tools and file_tools[0].get("ok"), "CRITICAL ERROR: file-mcp tools/list failed"

            imap_tools = await mcp_execute(
                client,
                session_id,
                1,
                steps=[{"method": "tools/list"}],
                require_initialize=require_init_imap,
                protocol_version=protocol_version,
            )
            assert imap_tools and imap_tools[0].get("ok"), "CRITICAL ERROR: imap-mcp tools/list failed"

            await mcp_tools_call(
                client,
                session_id,
                0,
                "create_dir",
                {"path": created_dir, "parents": True, "exist_ok": True},
                require_initialize=require_init_file,
                protocol_version=protocol_version,
            )
            await mcp_tools_call(
                client,
                session_id,
                0,
                "write_file",
                {
                    "path": created_file,
                    "content": f"W28A-262 file probe created at {datetime.now(timezone.utc).isoformat()}",
                    "overwrite": True,
                },
                require_initialize=require_init_file,
                protocol_version=protocol_version,
            )

            resolved_imap_folder = ""
            messages: list[dict[str, Any]] = []
            for candidate_folder in _candidate_imap_folders(imap_folder):
                search_result = await mcp_tools_call(
                    client,
                    session_id,
                    1,
                    "mail_search",
                    {
                        "profile_id": imap_profile_id,
                        "mode": "imap",
                        "query": "ALL",
                        "filters": {"folder": candidate_folder},
                        "limit": 10,
                    },
                    require_initialize=require_init_imap,
                    protocol_version=protocol_version,
                )
                messages = _messages_from_payload(extract_tool_json(search_result))
                if messages:
                    resolved_imap_folder = candidate_folder
                    break
            if not messages or not resolved_imap_folder:
                raise RuntimeError(
                    "BLOCKED: imap-mcp returned no messages for any candidate folder "
                    f"{_candidate_imap_folders(imap_folder)} with profile_id={imap_profile_id}"
                )
            expected_subjects = [
                _subject_fragment(message)
                for message in messages
                if str(message.get("subject") or "").strip()
            ]
            if not expected_subjects:
                raise RuntimeError("CRITICAL ERROR: imap search results contained no usable subjects")

            baseline_transcript = await client.get(f"/sessions/{session_id}/transcript")
            assert baseline_transcript.status_code == 200
            baseline_events = baseline_transcript.json().get("events") or []
            baseline_count = len(baseline_events) if isinstance(baseline_events, list) else 0

            file_prompt = (
                f"Use the configured file MCP service to list files in directory '{created_dir}'. "
                f"Return the exact filename '{created_file.rsplit('/', 1)[-1]}' if you can see it."
            )
            file_reply = await llm_message(client, session_id, file_prompt, stream=False)
            if created_file.rsplit("/", 1)[-1] not in file_reply:
                raise RuntimeError(
                    "CRITICAL ERROR: file listing reply did not include the expected filename. "
                    f"Reply was: {file_reply[:400]}"
                )

            transcript_after_file = await client.get(f"/sessions/{session_id}/transcript")
            assert transcript_after_file.status_code == 200
            file_events = transcript_after_file.json().get("events") or []
            new_file_events = file_events[baseline_count:] if isinstance(file_events, list) else []
            file_calls = _event_calls(new_file_events, server_index=0)
            if not file_calls:
                raise RuntimeError("CRITICAL ERROR: chat turn did not route through file-mcp")
            email_baseline_count = len(file_events) if isinstance(file_events, list) else baseline_count

            email_prompt = (
                f"Use the configured imap MCP service to check recent emails in folder '{resolved_imap_folder}' "
                f"with profile_id '{imap_profile_id}'. Mention one exact subject line from the results."
            )
            email_reply = await llm_message(client, session_id, email_prompt, stream=False)
            if not any(subject.lower() in email_reply.lower() for subject in expected_subjects):
                raise RuntimeError(
                    "CRITICAL ERROR: email reply did not include the expected subject fragment. "
                    f"Expected one of {expected_subjects[:5]}, reply was: {email_reply[:400]}"
                )

            transcript_after_email = await client.get(f"/sessions/{session_id}/transcript")
            assert transcript_after_email.status_code == 200
            email_events = transcript_after_email.json().get("events") or []
            new_email_events = email_events[email_baseline_count:] if isinstance(email_events, list) else []
            email_calls = _event_calls(new_email_events, server_index=1)
            if not email_calls:
                raise RuntimeError("CRITICAL ERROR: chat turn did not route through imap-mcp")
    finally:
        if session_id:
            try:
                async with httpx.AsyncClient(
                    base_url=api_base_url(cfg),
                    headers=api_headers(cfg),
                    timeout=api_timeout,
                ) as client:
                    try:
                        await mcp_tools_call(
                            client,
                            session_id,
                            0,
                            "delete_file",
                            {"path": created_file},
                            require_initialize=require_init_file,
                            protocol_version=protocol_version,
                        )
                    except Exception:
                        pass
                    try:
                        await mcp_tools_call(
                            client,
                            session_id,
                            0,
                            "delete_dir",
                            {"path": created_dir},
                            require_initialize=require_init_file,
                            protocol_version=protocol_version,
                        )
                    except Exception:
                        pass
                    await client.delete(f"/sessions/{session_id}")
                    await client.delete(f"/v1/profiles/{profile_id}", headers=admin_headers)
            except Exception:
                pass
        stop_api(cfg, env_file=env_file)
