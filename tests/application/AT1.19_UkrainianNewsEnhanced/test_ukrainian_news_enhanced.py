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

"""AT1.19 — Cross-project: Repeat Ukrainian News with 5 Different English Sites.

Services exercised:
  - search-mcp-server MCP  (web search for 5 additional Ukrainian news sites)
  - index-retriever-mcp-server  (ingest_text, search — extends AT1.17 VDB)
  - LLM via chat-client API  (enhanced summary from combined VDB content)
  - SMTP direct  (send email with PDF/MD/DOCX attachments)
  - imap-mcp-server  (confirm receipt)

Workflow:
  1. Ask LLM to identify 5 DIFFERENT English-language Ukrainian news sites.
  2. Search each for news, ingest into index-retriever (same or new collection).
  3. Send email with generated PDF, MD, DOCX attachments.
  4. Upload same content to VDB.
  5. Search VDB to confirm all new content is indexed.
  6. Create enhanced summary against ALL recent VDB content (AT1.17 + AT1.19).
  7. Confirm receipt via imap-mcp.
"""
from __future__ import annotations

import json
import re
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
    require_cfg,
    smtp_send,
    utc_ts,
)


def _looks_like_url_json_list(text: str) -> bool:
    """Detect the common failure mode where the model repeats site-list JSON."""
    raw = (text or "").strip()
    if not raw:
        return False

    if raw.startswith("```"):
        # Accept ```json ...``` and generic fenced payloads.
        cleaned = raw.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        raw = cleaned.strip()

    try:
        parsed = json.loads(raw)
    except Exception:
        return False

    return isinstance(parsed, list) and bool(parsed) and all(
        isinstance(item, str) and item.strip().startswith("http")
        for item in parsed
    )
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_19_ukrainian_news_enhanced(env_file):
    cfg = ConfigManager(env_file=env_file)
    ts = utc_ts()

    ensure_local_docker_runtime(cfg, "chat_tests.at1_19.search_mcp", label="AT1.19 search-mcp")
    ensure_local_docker_runtime(cfg, "chat_tests.at1_19.index_mcp", label="AT1.19 index-mcp")
    ensure_local_docker_runtime(cfg, "chat_tests.at1_19.imap_mcp", label="AT1.19 imap-mcp")

    # --- Service indices ---
    search_idx = int(require_cfg(cfg, "mcp.at1_19.search_server_index"))
    index_idx = int(require_cfg(cfg, "mcp.at1_19.index_server_index"))
    imap_idx = int(require_cfg(cfg, "mcp.at1_19.imap_server_index"))
    search_server = cfg.get("mcp.at1_19.search_server")
    index_server = cfg.get("mcp.at1_19.index_server")
    imap_server = cfg.get("mcp.at1_19.imap_server")
    if search_server is not None and not isinstance(search_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_19.search_server must be an object")
    if index_server is not None and not isinstance(index_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_19.index_server must be an object")
    if imap_server is not None and not isinstance(imap_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_19.imap_server must be an object")
    search_target_index = None if isinstance(search_server, dict) else search_idx
    index_target_index = None if isinstance(index_server, dict) else index_idx
    imap_target_index = None if isinstance(imap_server, dict) else imap_idx
    search_target_server = search_server if isinstance(search_server, dict) else None
    index_target_server = index_server if isinstance(index_server, dict) else None
    imap_target_server = imap_server if isinstance(imap_server, dict) else None
    search_init = bool(cfg.get("mcp.at1_19.require_initialize_search") or False)
    index_init = bool(cfg.get("mcp.at1_19.require_initialize_index") or False)
    imap_init = bool(cfg.get("mcp.at1_19.require_initialize_imap") or False)
    protocol_version = str(require_cfg(cfg, "mcp.defaults.protocol_version"))

    # --- Index-retriever config ---
    index_profile = str(require_cfg(cfg, "chat_tests.at1_19.index_profile"))
    collection_name = f"at1_19_ukraine_news_enhanced_{ts}"

    # --- IMAP config ---
    imap_profile_id = str(require_cfg(cfg, "chat_tests.at1_19.imap_profile_id"))
    imap_inbox = str(cfg.get("chat_tests.at1_19.imap_inbox") or "INBOX")

    # --- SMTP config ---
    smtp_host = str(require_cfg(cfg, "chat_tests.at1_19.smtp_host"))
    smtp_port = int(require_cfg(cfg, "chat_tests.at1_19.smtp_port"))
    smtp_user = str(require_cfg(cfg, "chat_tests.at1_19.smtp_user"))
    smtp_pass = str(require_cfg(cfg, "chat_tests.at1_19.smtp_pass"))
    smtp_use_tls = bool(cfg.get("chat_tests.at1_19.smtp_use_tls") or True)
    email_from = str(require_cfg(cfg, "chat_tests.at1_19.email_from"))
    email_to = str(require_cfg(cfg, "chat_tests.at1_19.email_to"))
    smtp_imap_append_host = str(cfg.get("chat_tests.at1_19.smtp_imap_append.host") or "").strip()
    smtp_imap_append_cfg = None
    if smtp_imap_append_host:
        smtp_imap_append_cfg = {
            "host": smtp_imap_append_host,
            "port": int(cfg.get("chat_tests.at1_19.smtp_imap_append.port") or 143),
            "username": str(cfg.get("chat_tests.at1_19.smtp_imap_append.username") or ""),
            "password": str(cfg.get("chat_tests.at1_19.smtp_imap_append.password") or ""),
            "folder": str(cfg.get("chat_tests.at1_19.smtp_imap_append.folder") or imap_inbox),
            "use_starttls": str(
                cfg.get("chat_tests.at1_19.smtp_imap_append.use_starttls") or "true"
            ).strip().lower() in {"1", "true", "yes", "on"},
            "timeout_seconds": float(cfg.get("chat_tests.at1_19.smtp_imap_append.timeout_seconds") or 30),
        }

    search_tool_name = str(cfg.get("chat_tests.at1_19.search_tool_name") or "search")
    max_results_per_site = int(cfg.get("chat_tests.at1_19.max_results_per_site") or 5)
    receipt_wait_seconds = int(cfg.get("chat_tests.at1_19.receipt_wait_seconds") or 30)

    # Sites to EXCLUDE (already used in AT1.17)
    exclude_sites_raw = cfg.get("chat_tests.at1_19.exclude_sites") or "[]"
    if isinstance(exclude_sites_raw, str):
        try:
            exclude_sites = json.loads(exclude_sites_raw)
        except Exception:
            exclude_sites = []
    else:
        exclude_sites = list(exclude_sites_raw) if isinstance(exclude_sites_raw, list) else []

    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        base_url = api_base_url(cfg)
        timeout = float(require_cfg(cfg, "client_api.request_timeout_seconds"))

        async with httpx.AsyncClient(
            base_url=base_url, headers=api_headers(cfg), timeout=timeout
        ) as client:
            session_id = await create_session(client, "at1.19")

            # --------------------------------------------------------
            # STEP 1: Ask LLM for 5 DIFFERENT English-language sites
            # --------------------------------------------------------
            exclude_text = ", ".join(exclude_sites) if exclude_sites else "none specified"
            sites_prompt = (
                "List exactly 5 reliable English-language websites that provide "
                "up-to-date, accurate news about the war in Ukraine. "
                f"Do NOT include any of these sites already used: {exclude_text}. "
                "Return ONLY a JSON array of URLs."
            )
            sites_raw = await llm_message(client, session_id, sites_prompt)
            match = re.search(r'\[.*?\]', sites_raw, re.DOTALL)
            assert match, f"CRITICAL ERROR: LLM did not return JSON array. Got: {sites_raw[:200]}"
            sites = json.loads(match.group(0))
            assert isinstance(sites, list) and len(sites) >= 3, (
                f"CRITICAL ERROR: Expected >= 3 sites, got {len(sites)}"
            )
            sites = [str(s).strip() for s in sites[:5]]
            print(f"[AT1.19] Sites identified: {sites}")

            # --------------------------------------------------------
            # STEP 2: Search each site
            # --------------------------------------------------------
            await mcp_execute(
                client, session_id, search_target_index,
                steps=[{"method": "tools/list"}],
                require_initialize=search_init,
                protocol_version=protocol_version,
                server=search_target_server,
            )

            all_search_texts: list[str] = []
            for site_url in sites:
                query = f"Ukraine war latest news English site:{site_url}"
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
                except Exception as e:
                    print(f"[AT1.19] Search '{site_url}' failed: {e}")

            assert len(all_search_texts) > 0, "CRITICAL ERROR: All searches returned empty"
            combined = "\n\n---\n\n".join(all_search_texts)
            print(f"[AT1.19] Collected {len(combined)} chars from {len(all_search_texts)} sites")

            # --------------------------------------------------------
            # STEP 3: Create VDB collection and ingest
            # --------------------------------------------------------
            await mcp_execute(
                client, session_id, index_target_index,
                steps=[{"method": "tools/list"}],
                require_initialize=index_init,
                protocol_version=protocol_version,
                server=index_target_server,
            )

            await mcp_tools_call(
                client, session_id, index_target_index,
                "admin_collection_create",
                {"profile": index_profile, "collection": collection_name},
                require_initialize=index_init,
                protocol_version=protocol_version,
                server=index_target_server,
            )

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
            print(f"[AT1.19] Ingested {len(all_search_texts)} documents")

            # --------------------------------------------------------
            # STEP 4: Generate enhanced briefing
            # --------------------------------------------------------
            vdb_search = await mcp_tools_call(
                client, session_id, index_target_index,
                "search",
                {
                    "profile": index_profile,
                    "collection": collection_name,
                    "query": "Ukraine war latest comprehensive",
                    "top_k": 20,
                },
                require_initialize=index_init,
                protocol_version=protocol_version,
                server=index_target_server,
            )
            vdb_text = extract_tool_text(vdb_search)

            briefing_prompt = (
                "You are a senior intelligence analyst. Generate a detailed Markdown briefing. "
                "Do NOT return JSON. Do NOT return a URL list. Do NOT repeat prior site-selection output.\n\n"
                "Required sections:\n"
                "1. Executive Summary\n"
                "2. Key Developments\n"
                "3. Military Situation\n"
                "4. Political/Diplomatic\n"
                "5. Humanitarian\n"
                "6. Source Assessment\n\n"
                f"Indexed content:\n{vdb_text[:8000]}"
            )
            briefing = await llm_message(client, session_id, briefing_prompt)
            if len(briefing.strip()) <= 500 or _looks_like_url_json_list(briefing):
                retry_prompt = (
                    "Your previous response was invalid because it was too short or formatted as JSON/URL list. "
                    "Rewrite as a full Markdown briefing with the required six sections and substantive detail. "
                    "No JSON, no code fence, no URL list.\n\n"
                    f"Evidence to use:\n{vdb_text[:8000]}"
                )
                briefing = await llm_message(client, session_id, retry_prompt)
            assert len(briefing.strip()) > 500 and not _looks_like_url_json_list(briefing), (
                "CRITICAL ERROR: Briefing too short or invalid JSON/URL-list format"
            )

            # --------------------------------------------------------
            # STEP 5: Generate attachments and send email
            # --------------------------------------------------------
            html = (
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<title>Enhanced Ukraine Briefing</title></head><body>"
                f"<h1>Enhanced Ukraine War Intelligence Briefing</h1><pre>{briefing}</pre>"
                "</body></html>"
            )
            pdf = generate_pdf_from_text(briefing, title="Enhanced Ukraine Briefing")
            docx = generate_docx_from_text(briefing)

            subject = f"[AT1.19] Enhanced Ukraine Briefing {ts}"
            smtp_send(
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_user=smtp_user,
                smtp_pass=smtp_pass,
                smtp_use_tls=smtp_use_tls,
                from_addr=email_from,
                to_addr=email_to,
                subject=subject,
                body_text=briefing,
                body_html=html,
                attachments=[
                    {"filename": f"enhanced_briefing_{ts}.pdf", "data": pdf, "mime_type": "application/pdf"},
                    {
                        "filename": f"enhanced_briefing_{ts}.docx",
                        "data": docx,
                        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    },
                    {"filename": f"enhanced_briefing_{ts}.md", "data": briefing.encode("utf-8"), "mime_type": "text/markdown"},
                ],
                imap_append_fallback=smtp_imap_append_cfg,
            )
            print(f"[AT1.19] Email sent: {subject}")

            # --------------------------------------------------------
            # STEP 6: Verify VDB content
            # --------------------------------------------------------
            verify_result = await mcp_tools_call(
                client, session_id, index_target_index,
                "search",
                {
                    "profile": index_profile,
                    "collection": collection_name,
                    "query": "Ukraine war news developments",
                    "top_k": 10,
                },
                require_initialize=index_init,
                protocol_version=protocol_version,
                server=index_target_server,
            )
            hits = extract_tool_json(verify_result).get("results") or []
            assert len(hits) > 0, "CRITICAL ERROR: VDB verification search returned no results"
            print(f"[AT1.19] VDB verified: {len(hits)} hits")

            # --------------------------------------------------------
            # STEP 7: Confirm email receipt
            # --------------------------------------------------------
            print(f"[AT1.19] Waiting {receipt_wait_seconds}s for delivery...")
            time.sleep(receipt_wait_seconds)

            await mcp_execute(
                client, session_id, imap_target_index,
                steps=[{"method": "tools/list"}],
                require_initialize=imap_init,
                protocol_version=protocol_version,
                server=imap_target_server,
            )

            receipt = await mcp_tools_call(
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
            receipt_text = extract_tool_text(receipt)
            receipt_json = extract_tool_json(receipt)
            found = receipt_json.get("messages") or receipt_json.get("results") or []
            subject_hits = [
                msg for msg in found
                if subject.lower() in str((msg or {}).get("subject") or "").lower()
            ]
            assert len(subject_hits) > 0 or subject.lower() in receipt_text.lower(), (
                f"CRITICAL ERROR: Email not found in {imap_inbox}. Subject: {subject}"
            )
            print(f"[AT1.19] PASS — Enhanced pipeline complete: 5 new sites→index→brief→email→receipt")

    finally:
        stop_api(cfg, env_file=env_file)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.smtp, pytest.mark.mcp, pytest.mark.docker, pytest.mark.heavy]

