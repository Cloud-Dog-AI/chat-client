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

"""AT1.17 — Cross-project: Ukrainian News Search → Index → Briefing → Email.

Services exercised:
  - search-mcp-server MCP  (web search for Ukrainian war news)
  - index-retriever-mcp-server  (ingest_text, search, admin_collection_create)
  - LLM via chat-client API  (identify top sites, create briefing)
  - SMTP direct  (send briefing as HTML/MD/PDF/DOCX attachments)
  - imap-mcp-server  (confirm receipt of sent emails)

Workflow:
  1. Ask LLM to identify top 5 Ukrainian news sites.
  2. Search each site for news from past 72 hours via search MCP.
  3. Ingest all search results into index-retriever VDB collection.
  4. Search the VDB to confirm indexing.
  5. Ask LLM to create a detailed markdown briefing from indexed content.
  6. Generate HTML, PDF, and DOCX versions of the briefing.
  7. Email all 4 formats (MD body + HTML alt + PDF attachment + DOCX attachment).
  8. Wait for delivery, then confirm receipt via imap-mcp.
"""
from __future__ import annotations

import time
from typing import Any, Dict

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.cross_project import (
    create_session,
    ensure_local_docker_runtime,
    extract_tool_json,
    extract_tool_text,
    generate_docx_from_text,
    generate_pdf_from_text,
    llm_message,
    mcp_execute,
    mcp_tools_call,
    parse_json_list,
    require_cfg,
    smtp_send,
    utc_ts,
)
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_17_ukrainian_news_briefing(env_file):
    cfg = ConfigManager(env_file=env_file)
    ts = utc_ts()

    ensure_local_docker_runtime(cfg, "chat_tests.at1_17.search_mcp", label="AT1.17 search-mcp")
    ensure_local_docker_runtime(cfg, "chat_tests.at1_17.index_mcp", label="AT1.17 index-mcp")
    ensure_local_docker_runtime(cfg, "chat_tests.at1_17.imap_mcp", label="AT1.17 imap-mcp")

    # --- Service indices ---
    search_idx = int(require_cfg(cfg, "mcp.at1_17.search_server_index"))
    index_idx = int(require_cfg(cfg, "mcp.at1_17.index_server_index"))
    imap_idx = int(require_cfg(cfg, "mcp.at1_17.imap_server_index"))
    search_server = cfg.get("mcp.at1_17.search_server")
    index_server = cfg.get("mcp.at1_17.index_server")
    imap_server = cfg.get("mcp.at1_17.imap_server")
    if search_server is not None and not isinstance(search_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_17.search_server must be an object")
    if index_server is not None and not isinstance(index_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_17.index_server must be an object")
    if imap_server is not None and not isinstance(imap_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_17.imap_server must be an object")
    search_target_index = None if isinstance(search_server, dict) else search_idx
    index_target_index = None if isinstance(index_server, dict) else index_idx
    imap_target_index = None if isinstance(imap_server, dict) else imap_idx
    search_target_server = search_server if isinstance(search_server, dict) else None
    index_target_server = index_server if isinstance(index_server, dict) else None
    imap_target_server = imap_server if isinstance(imap_server, dict) else None
    search_init = bool(cfg.get("mcp.at1_17.require_initialize_search") or False)
    index_init = bool(cfg.get("mcp.at1_17.require_initialize_index") or False)
    imap_init = bool(cfg.get("mcp.at1_17.require_initialize_imap") or False)
    protocol_version = str(require_cfg(cfg, "mcp.defaults.protocol_version"))

    # --- Index-retriever config ---
    index_profile = str(require_cfg(cfg, "chat_tests.at1_17.index_profile"))
    collection_name = f"at1_17_ukraine_news_{ts}"

    # --- IMAP verification config ---
    imap_profile_id = str(require_cfg(cfg, "chat_tests.at1_17.imap_profile_id"))
    imap_inbox = str(cfg.get("chat_tests.at1_17.imap_inbox") or "INBOX")

    # --- SMTP config ---
    smtp_host = str(require_cfg(cfg, "chat_tests.at1_17.smtp_host"))
    smtp_port = int(require_cfg(cfg, "chat_tests.at1_17.smtp_port"))
    smtp_user = str(require_cfg(cfg, "chat_tests.at1_17.smtp_user"))
    smtp_pass = str(require_cfg(cfg, "chat_tests.at1_17.smtp_pass"))
    smtp_use_tls = bool(cfg.get("chat_tests.at1_17.smtp_use_tls") or True)
    email_from = str(require_cfg(cfg, "chat_tests.at1_17.email_from"))
    email_to = str(require_cfg(cfg, "chat_tests.at1_17.email_to"))
    smtp_imap_append_host = str(cfg.get("chat_tests.at1_17.smtp_imap_append.host") or "").strip()
    smtp_imap_append_cfg = None
    if smtp_imap_append_host:
        smtp_imap_append_cfg = {
            "host": smtp_imap_append_host,
            "port": int(cfg.get("chat_tests.at1_17.smtp_imap_append.port") or 143),
            "username": str(cfg.get("chat_tests.at1_17.smtp_imap_append.username") or ""),
            "password": str(cfg.get("chat_tests.at1_17.smtp_imap_append.password") or ""),
            "folder": str(cfg.get("chat_tests.at1_17.smtp_imap_append.folder") or imap_inbox),
            "use_starttls": str(
                cfg.get("chat_tests.at1_17.smtp_imap_append.use_starttls") or "true"
            ).strip().lower() in {"1", "true", "yes", "on"},
            "timeout_seconds": float(cfg.get("chat_tests.at1_17.smtp_imap_append.timeout_seconds") or 30),
        }

    # --- Search config ---
    search_tool_name = str(cfg.get("chat_tests.at1_17.search_tool_name") or "search")
    max_results_per_site = int(cfg.get("chat_tests.at1_17.max_results_per_site") or 5)

    receipt_wait_seconds = int(cfg.get("chat_tests.at1_17.receipt_wait_seconds") or 30)

    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        base_url = api_base_url(cfg)
        timeout = float(require_cfg(cfg, "client_api.request_timeout_seconds"))

        async with httpx.AsyncClient(
            base_url=base_url, headers=api_headers(cfg), timeout=timeout
        ) as client:
            session_id = await create_session(client, "at1.17")

            # --------------------------------------------------------
            # STEP 1: Ask LLM to identify top 5 Ukrainian news sites
            # --------------------------------------------------------
            sites_prompt = (
                "List exactly 5 of the most reliable English-language websites that provide "
                "up-to-date, accurate news coverage of the war in Ukraine. "
                "Return ONLY a JSON array of URLs, no commentary. Example: "
                '["https://www.example.com", "https://news.example.org"]'
            )
            sites_raw = await llm_message(client, session_id, sites_prompt)
            # Extract JSON array from LLM response
            import json, re
            match = re.search(r'\[.*?\]', sites_raw, re.DOTALL)
            assert match, f"CRITICAL ERROR: LLM did not return a JSON array of sites. Got: {sites_raw[:200]}"
            sites = json.loads(match.group(0))
            assert isinstance(sites, list) and len(sites) >= 3, (
                f"CRITICAL ERROR: Expected at least 3 sites, got {len(sites)}"
            )
            sites = [str(s).strip() for s in sites[:5]]
            print(f"[AT1.17] Top sites identified: {sites}")

            # --------------------------------------------------------
            # STEP 2: Search each site for Ukraine war news (past 72h)
            # --------------------------------------------------------
            all_search_texts: list[str] = []

            # Initialise search MCP
            await mcp_execute(
                client, session_id, search_target_index,
                steps=[{"method": "tools/list"}],
                require_initialize=search_init,
                protocol_version=protocol_version,
                server=search_target_server,
            )

            for site_url in sites:
                query = f"Ukraine war news site:{site_url}"
                try:
                    result = await mcp_tools_call(
                        client, session_id, search_target_index,
                        search_tool_name,
                        {"query": query, "max_results": max_results_per_site},
                        require_initialize=search_init,
                        protocol_version=protocol_version,
                        server=search_target_server,
                    )
                    text = extract_tool_text(result).strip()
                    if text:
                        all_search_texts.append(f"## Source: {site_url}\n\n{text}")
                        print(f"[AT1.17] Search '{site_url}': {len(text)} chars")
                except Exception as e:
                    print(f"[AT1.17] Search '{site_url}' failed: {e}")

            assert len(all_search_texts) > 0, "CRITICAL ERROR: All site searches returned empty"
            combined_search = "\n\n---\n\n".join(all_search_texts)
            print(f"[AT1.17] Total search content: {len(combined_search)} chars from {len(all_search_texts)} sites")

            # --------------------------------------------------------
            # STEP 3: Create VDB collection and ingest search results
            # --------------------------------------------------------
            await mcp_execute(
                client, session_id, index_target_index,
                steps=[{"method": "tools/list"}],
                require_initialize=index_init,
                protocol_version=protocol_version,
                server=index_target_server,
            )

            # Create collection
            await mcp_tools_call(
                client, session_id, index_target_index,
                "admin_collection_create",
                {"profile": index_profile, "collection": collection_name},
                require_initialize=index_init,
                protocol_version=protocol_version,
                server=index_target_server,
            )
            print(f"[AT1.17] Created VDB collection: {collection_name}")

            # Ingest each site's content separately for better granularity
            ingest_count = 0
            for i, search_text in enumerate(all_search_texts):
                source_label = sites[i] if i < len(sites) else f"site-{i}"
                await mcp_tools_call(
                    client, session_id, index_target_index,
                    "ingest_text",
                    {
                        "profile": index_profile,
                        "collection": collection_name,
                        "text": search_text,
                        "source": source_label,
                    },
                    require_initialize=index_init,
                    protocol_version=protocol_version,
                    server=index_target_server,
                )
                ingest_count += 1
            print(f"[AT1.17] Ingested {ingest_count} documents into {collection_name}")

            # --------------------------------------------------------
            # STEP 4: Search VDB to confirm indexing
            # --------------------------------------------------------
            vdb_search = await mcp_tools_call(
                client, session_id, index_target_index,
                "search",
                {
                    "profile": index_profile,
                    "collection": collection_name,
                    "query": "Ukraine war latest developments",
                    "top_k": 10,
                },
                require_initialize=index_init,
                protocol_version=protocol_version,
                server=index_target_server,
            )
            vdb_results_text = extract_tool_text(vdb_search)
            vdb_results_json = extract_tool_json(vdb_search)
            results_list = vdb_results_json.get("results") or []
            assert len(results_list) > 0 or len(vdb_results_text.strip()) > 0, (
                "CRITICAL ERROR: VDB search returned no results after ingestion"
            )
            print(f"[AT1.17] VDB search returned {len(results_list)} results")

            # --------------------------------------------------------
            # STEP 5: Ask LLM to create detailed markdown briefing
            # --------------------------------------------------------
            briefing_prompt = (
                "You are a senior intelligence analyst. Using the following indexed news content, "
                "create a detailed briefing document in Markdown format covering:\n"
                "1. Executive Summary\n"
                "2. Key Developments (past 72 hours)\n"
                "3. Military Situation Overview\n"
                "4. Political and Diplomatic Updates\n"
                "5. Humanitarian Impact\n"
                "6. Sources and Reliability Assessment\n\n"
                f"Indexed content:\n\n{vdb_results_text[:8000]}\n\n"
                f"Additional raw search data:\n\n{combined_search[:4000]}"
            )
            briefing_md = await llm_message(client, session_id, briefing_prompt)
            assert len(briefing_md.strip()) > 500, "CRITICAL ERROR: Briefing too short"
            print(f"[AT1.17] Briefing generated: {len(briefing_md)} chars")

            # --------------------------------------------------------
            # STEP 6: Generate HTML, PDF, DOCX from briefing
            # --------------------------------------------------------
            briefing_html = (
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<title>Ukraine War Briefing</title></head><body>"
                f"<h1>Ukraine War Intelligence Briefing</h1>"
                f"<pre>{briefing_md}</pre></body></html>"
            )
            briefing_pdf = generate_pdf_from_text(briefing_md, title="Ukraine War Briefing")
            briefing_docx = generate_docx_from_text(briefing_md)

            assert briefing_pdf[:5] == b"%PDF-", "CRITICAL ERROR: PDF generation failed"
            assert briefing_docx[:2] == b"PK", "CRITICAL ERROR: DOCX generation failed"
            print(f"[AT1.17] Generated: HTML={len(briefing_html)}B, PDF={len(briefing_pdf)}B, DOCX={len(briefing_docx)}B")

            # --------------------------------------------------------
            # STEP 7: Email briefing with all formats
            # --------------------------------------------------------
            subject = f"[AT1.17] Ukraine War Briefing {ts}"
            message_id = smtp_send(
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_user=smtp_user,
                smtp_pass=smtp_pass,
                smtp_use_tls=smtp_use_tls,
                from_addr=email_from,
                to_addr=email_to,
                subject=subject,
                body_text=briefing_md,
                body_html=briefing_html,
                attachments=[
                    {"filename": f"briefing_{ts}.pdf", "data": briefing_pdf, "mime_type": "application/pdf"},
                    {
                        "filename": f"briefing_{ts}.docx",
                        "data": briefing_docx,
                        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    },
                    {"filename": f"briefing_{ts}.md", "data": briefing_md.encode("utf-8"), "mime_type": "text/markdown"},
                ],
                imap_append_fallback=smtp_imap_append_cfg,
            )
            print(f"[AT1.17] Email sent: {subject} (Message-ID: {message_id})")

            # --------------------------------------------------------
            # STEP 8: Confirm receipt via imap-mcp
            # --------------------------------------------------------
            print(f"[AT1.17] Waiting {receipt_wait_seconds}s for email delivery...")
            time.sleep(receipt_wait_seconds)

            # Initialise imap-mcp
            await mcp_execute(
                client, session_id, imap_target_index,
                steps=[{"method": "tools/list"}],
                require_initialize=imap_init,
                protocol_version=protocol_version,
                server=imap_target_server,
            )

            # Search for our sent email
            receipt_result = await mcp_tools_call(
                client, session_id, imap_target_index,
                "mail_search",
                {
                    "profile_id": imap_profile_id,
                    "mode": "imap",
                    "query": "ALL",
                    "filters": {"folder": imap_inbox},
                },
                require_initialize=imap_init,
                protocol_version=protocol_version,
                server=imap_target_server,
            )
            receipt_text = extract_tool_text(receipt_result)
            receipt_json = extract_tool_json(receipt_result)

            # Verify we found the email
            found_messages = receipt_json.get("messages") or receipt_json.get("results") or []
            subject_hits = [
                msg for msg in found_messages
                if subject.lower() in str((msg or {}).get("subject") or "").lower()
            ]
            assert len(subject_hits) > 0 or subject.lower() in receipt_text.lower(), (
                f"CRITICAL ERROR: Sent email not found in {imap_inbox}. "
                f"Subject: {subject}. Delivery may be delayed."
            )
            print(f"[AT1.17] Receipt confirmed: found email with subject '{subject}'")

            # Verify attachments are present on the matched message when possible.
            target_messages = subject_hits or found_messages
            if target_messages:
                first_uid = str(
                    target_messages[0].get("uid") or target_messages[0].get("id") or ""
                ).strip()
                if first_uid:
                    att_result = await mcp_tools_call(
                        client, session_id, imap_target_index,
                        "mail_list_attachments",
                        {"profile_id": imap_profile_id, "uid": first_uid, "folder": imap_inbox},
                        require_initialize=imap_init,
                        protocol_version=protocol_version,
                        server=imap_target_server,
                    )
                    att_data = extract_tool_json(att_result)
                    attachments = att_data.get("attachments") or att_data.get("parts") or []
                    names = [
                        str((a or {}).get("filename") or (a or {}).get("name") or "").lower()
                        for a in attachments
                    ]
                    assert any(name.endswith(".pdf") or name.endswith(".docx") or name.endswith(".md") for name in names), (
                        "CRITICAL ERROR: Expected briefing attachments not found on received email"
                    )
                    print(f"[AT1.17] Attachments confirmed for UID {first_uid}: {names}")

            print(f"[AT1.17] PASS — Full pipeline: search→index→brief→email→receipt confirmed")

    finally:
        stop_api(cfg, env_file=env_file)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.smtp, pytest.mark.mcp, pytest.mark.docker, pytest.mark.heavy]

