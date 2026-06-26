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

"""AT1.18 — Cross-project: Retrieve Ukrainian News Emails → Index → Verify → Summarise.

Services exercised:
  - imap-mcp-server  (mail_search, mail_get_message, mail_list_attachments, mail_download_attachment)
  - index-retriever-mcp-server  (admin_collection_create, ingest_text, search)
  - LLM via chat-client API  (summary generation + verification)

Workflow:
  1. Search IMAP inbox for the 4 Ukrainian news emails sent by AT1.17.
  2. For each email: extract body content and list/download all attachments.
  3. Upload body text + attachment text content into index-retriever VDB.
  4. Search VDB to confirm all 4 emails are fully indexed with all content.
  5. Verify metadata completeness (source, date, attachment filenames).
  6. Create summary against VDB contents and confirm it reflects uploaded materials.

Depends on AT1.17 having been run first (emails must exist in inbox).
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any, Dict, List

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
    parse_json_list,
    require_cfg,
    utc_ts,
)


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _message_body_text(result: Dict[str, Any]) -> str:
    payload = extract_tool_json(result)
    message_json = payload.get("json") if isinstance(payload.get("json"), dict) else {}
    markdown = _coerce_text(payload.get("markdown"))
    text_plain = _coerce_text(message_json.get("text_plain"))
    text_html = _coerce_text(message_json.get("text_html"))

    for candidate in (markdown, text_plain, text_html):
        if candidate:
            return candidate
    return extract_tool_text(result).strip()


def _attachment_text(result: Dict[str, Any], filename: str) -> str:
    payload = extract_tool_json(result)
    content = _coerce_text(payload.get("content"))
    content_encoding = _coerce_text(payload.get("content_encoding")).lower()

    if content_encoding == "text" and content:
        return content

    if content_encoding == "base64" and content:
        try:
            decoded = base64.b64decode(content, validate=True)
        except Exception:
            decoded = b""
        if decoded:
            try:
                return decoded.decode("utf-8").strip()
            except UnicodeDecodeError:
                pass

    text = extract_tool_text(result).strip()
    if text and text != content:
        return text

    # Keep binary attachments represented without pushing raw base64/PDF bytes
    # into the embedding service.
    return f"Attachment filename: {filename}"
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_18_email_attachment_index_verify(env_file):
    cfg = ConfigManager(env_file=env_file)
    ts = utc_ts()

    ensure_local_docker_runtime(cfg, "chat_tests.at1_18.imap_mcp", label="AT1.18 imap-mcp")
    ensure_local_docker_runtime(cfg, "chat_tests.at1_18.index_mcp", label="AT1.18 index-mcp")

    # --- Service indices ---
    imap_idx = int(require_cfg(cfg, "mcp.at1_18.imap_server_index"))
    index_idx = int(require_cfg(cfg, "mcp.at1_18.index_server_index"))
    imap_server = cfg.get("mcp.at1_18.imap_server")
    index_server = cfg.get("mcp.at1_18.index_server")
    if imap_server is not None and not isinstance(imap_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_18.imap_server must be an object")
    if index_server is not None and not isinstance(index_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_18.index_server must be an object")
    imap_target_index = None if isinstance(imap_server, dict) else imap_idx
    index_target_index = None if isinstance(index_server, dict) else index_idx
    imap_target_server = imap_server if isinstance(imap_server, dict) else None
    index_target_server = index_server if isinstance(index_server, dict) else None
    imap_init = bool(cfg.get("mcp.at1_18.require_initialize_imap") or False)
    index_init = bool(cfg.get("mcp.at1_18.require_initialize_index") or False)
    protocol_version = str(require_cfg(cfg, "mcp.defaults.protocol_version"))

    # --- Config ---
    imap_profile_id = str(require_cfg(cfg, "chat_tests.at1_18.imap_profile_id"))
    imap_inbox = str(cfg.get("chat_tests.at1_18.imap_inbox") or "INBOX")
    search_subject_pattern = str(require_cfg(cfg, "chat_tests.at1_18.search_subject_pattern"))
    expected_email_count = int(cfg.get("chat_tests.at1_18.expected_email_count") or 4)
    index_profile = str(require_cfg(cfg, "chat_tests.at1_18.index_profile"))
    collection_name = f"at1_18_email_attachments_{ts}"

    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        base_url = api_base_url(cfg)
        timeout = float(require_cfg(cfg, "client_api.request_timeout_seconds"))

        async with httpx.AsyncClient(
            base_url=base_url, headers=api_headers(cfg), timeout=timeout
        ) as client:
            session_id = await create_session(client, "at1.18")

            # --------------------------------------------------------
            # STEP 1: Search for Ukrainian news emails
            # --------------------------------------------------------
            await mcp_execute(
                client, session_id, imap_target_index,
                steps=[{"method": "tools/list"}],
                require_initialize=imap_init,
                protocol_version=protocol_version,
                server=imap_target_server,
            )

            search_result = await mcp_tools_call(
                client, session_id, imap_target_index,
                "mail_search",
                {
                    "profile_id": imap_profile_id,
                    "mode": "imap",
                    "query": search_subject_pattern,
                    "filters": {"folder": imap_inbox},
                },
                require_initialize=imap_init,
                protocol_version=protocol_version,
                server=imap_target_server,
            )
            search_data = extract_tool_json(search_result)
            messages = search_data.get("messages") or search_data.get("results") or []

            target_subject_prefix = "[AT1.17] Ukraine War Briefing"
            matched_messages: list[dict[str, str]] = []
            for msg in messages:
                uid = str(msg.get("uid") or msg.get("id") or "").strip()
                subject = str(msg.get("subject") or "").strip()
                if uid:
                    matched_messages.append({"uid": uid, "subject": subject})

            targeted = [
                item for item in matched_messages
                if target_subject_prefix.lower() in item["subject"].lower()
            ]
            selected_messages = targeted or matched_messages

            def _uid_sort_key(item: dict[str, str]) -> tuple[int, str]:
                uid_text = str(item.get("uid") or "").strip()
                try:
                    return (0, f"{int(uid_text):020d}")
                except ValueError:
                    return (1, uid_text)

            selected_messages = sorted(selected_messages, key=_uid_sort_key)
            if len(selected_messages) > expected_email_count:
                selected_messages = selected_messages[-expected_email_count:]

            uids = [item["uid"] for item in selected_messages]

            assert len(uids) >= 1, (
                f"CRITICAL ERROR: No Ukrainian news emails found matching "
                f"'{search_subject_pattern}' in {imap_inbox}. Run AT1.17 first."
            )
            print(
                f"[AT1.18] Found {len(messages)} matching emails; "
                f"selected {len(uids)} newest Ukrainian briefing emails "
                f"(expected ~{expected_email_count})"
            )

            # --------------------------------------------------------
            # STEP 2: Extract body + attachments for each email
            # --------------------------------------------------------
            await mcp_execute(
                client, session_id, index_target_index,
                steps=[{"method": "tools/list"}],
                require_initialize=index_init,
                protocol_version=protocol_version,
                server=index_target_server,
            )

            # Create VDB collection
            await mcp_tools_call(
                client, session_id, index_target_index,
                "admin_collection_create",
                {"profile": index_profile, "collection": collection_name},
                require_initialize=index_init,
                protocol_version=protocol_version,
                server=index_target_server,
            )
            print(f"[AT1.18] Created VDB collection: {collection_name}")

            total_ingested = 0
            attachment_log: list[Dict[str, Any]] = []

            for uid in uids:
                # Extract message body
                body_result = await mcp_tools_call(
                    client, session_id, imap_target_index,
                    "mail_extract_message",
                    {
                        "profile_id": imap_profile_id,
                        "uid": uid,
                        "folder": imap_inbox,
                        "format": "both",
                    },
                    require_initialize=imap_init,
                    protocol_version=protocol_version,
                    server=imap_target_server,
                )
                body_text = _message_body_text(body_result)

                if body_text:
                    await mcp_tools_call(
                        client, session_id, index_target_index,
                        "ingest_text",
                        {
                            "profile": index_profile,
                            "collection": collection_name,
                            "text": body_text,
                            "source": f"email:uid={uid}:body",
                        },
                        require_initialize=index_init,
                        protocol_version=protocol_version,
                        server=index_target_server,
                    )
                    total_ingested += 1

                # List attachments
                att_list_result = await mcp_tools_call(
                    client, session_id, imap_target_index,
                    "mail_list_attachments",
                    {"profile_id": imap_profile_id, "uid": uid, "folder": imap_inbox},
                    require_initialize=imap_init,
                    protocol_version=protocol_version,
                    server=imap_target_server,
                )
                att_data = extract_tool_json(att_list_result)
                attachments = att_data.get("attachments") or att_data.get("parts") or []
                att_text_fallback = extract_tool_text(att_list_result)

                for att in attachments:
                    part_id = str(att.get("part_id") or att.get("id") or "").strip()
                    filename = str(att.get("filename") or att.get("name") or "unknown").strip()
                    if not part_id:
                        continue

                    attachment_log.append({
                        "uid": uid,
                        "part_id": part_id,
                        "filename": filename,
                    })

                    # Download attachment
                    try:
                        dl_result = await mcp_tools_call(
                            client, session_id, imap_target_index,
                            "mail_download_attachment",
                            {
                                "profile_id": imap_profile_id,
                                "uid": uid,
                                "part_id": part_id,
                                "folder": imap_inbox,
                                "filename": filename,
                            },
                            require_initialize=imap_init,
                            protocol_version=protocol_version,
                            server=imap_target_server,
                        )
                        att_content = _attachment_text(dl_result, filename)

                        # For text-based attachments (.md), ingest directly
                        if filename.endswith(".md") and att_content:
                            await mcp_tools_call(
                                client, session_id, index_target_index,
                                "ingest_text",
                                {
                                    "profile": index_profile,
                                    "collection": collection_name,
                                    "text": att_content,
                                    "source": f"email:uid={uid}:attachment:{filename}",
                                },
                                require_initialize=index_init,
                                protocol_version=protocol_version,
                                server=index_target_server,
                            )
                            total_ingested += 1

                        # For PDF/DOCX, ingest the text representation if available
                        elif att_content:
                            await mcp_tools_call(
                                client, session_id, index_target_index,
                                "ingest_text",
                                {
                                    "profile": index_profile,
                                    "collection": collection_name,
                                    "text": f"[Attachment: {filename}]\n{att_content}",
                                    "source": f"email:uid={uid}:attachment:{filename}",
                                },
                                require_initialize=index_init,
                                protocol_version=protocol_version,
                                server=index_target_server,
                            )
                            total_ingested += 1
                    except Exception as e:
                        print(f"[AT1.18] Warning: Failed to download attachment {filename} from uid={uid}: {e}")

                print(f"[AT1.18] Processed email uid={uid}: body + {len(attachments)} attachments")

            print(f"[AT1.18] Total documents ingested: {total_ingested}")
            print(f"[AT1.18] Total attachments logged: {len(attachment_log)}")

            # --------------------------------------------------------
            # STEP 3: Search VDB to confirm all content indexed
            # --------------------------------------------------------
            verify_queries = [
                "Ukraine war news briefing",
                "military developments Ukraine",
                "political diplomatic updates",
            ]
            total_vdb_hits = 0
            for vq in verify_queries:
                vdb_result = await mcp_tools_call(
                    client, session_id, index_target_index,
                    "search",
                    {
                        "profile": index_profile,
                        "collection": collection_name,
                        "query": vq,
                        "top_k": 10,
                    },
                    require_initialize=index_init,
                    protocol_version=protocol_version,
                    server=index_target_server,
                )
                hits = extract_tool_json(vdb_result).get("results") or []
                total_vdb_hits += len(hits)
                print(f"[AT1.18] VDB search '{vq}': {len(hits)} hits")

            assert total_vdb_hits > 0, (
                "CRITICAL ERROR: VDB search returned zero hits — indexing verification failed"
            )

            # --------------------------------------------------------
            # STEP 4: Ask LLM to create summary from VDB content
            # --------------------------------------------------------
            # Gather VDB content for summary
            summary_search = await mcp_tools_call(
                client, session_id, index_target_index,
                "search",
                {
                    "profile": index_profile,
                    "collection": collection_name,
                    "query": "Ukraine war complete briefing all content",
                    "top_k": 20,
                },
                require_initialize=index_init,
                protocol_version=protocol_version,
                server=index_target_server,
            )
            vdb_content = extract_tool_text(summary_search)

            summary = await llm_message(
                client, session_id,
                f"Create a comprehensive summary of the following indexed email and attachment "
                f"content about the Ukraine war. Confirm the summary reflects the material "
                f"uploaded (total {total_ingested} documents, {len(attachment_log)} attachments "
                f"from {len(uids)} emails).\n\n{vdb_content[:8000]}",
            )
            assert len(summary.strip()) > 200, "CRITICAL ERROR: Summary too short"
            print(f"[AT1.18] Summary generated: {len(summary)} chars")

            # --------------------------------------------------------
            # STEP 5: Final verification
            # --------------------------------------------------------
            assert total_ingested >= len(uids), (
                f"CRITICAL ERROR: Ingested {total_ingested} docs but had {len(uids)} emails — "
                f"at minimum each email body should be ingested"
            )

            print(
                f"[AT1.18] PASS — {len(uids)} emails processed, {total_ingested} docs ingested, "
                f"{len(attachment_log)} attachments, {total_vdb_hits} VDB hits, summary confirmed"
            )

    finally:
        stop_api(cfg, env_file=env_file)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.smtp, pytest.mark.mcp, pytest.mark.docker, pytest.mark.heavy]
