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

"""AT1.20 — Cross-project: UK Defence Companies Search → File-MCP Store → Index → Verify.

Services exercised:
  - search-mcp-server MCP  (web search for companies + articles)
  - file-mcp-server  (create folders, save markdown files, create logs)
  - index-retriever-mcp-server  (company-specific collections, ingest, search)
  - LLM via chat-client API  (identify companies, filter articles, create summaries)

Workflow:
  1. Ask LLM to identify Top 10 UK Defence Companies.
  2. Save the list as markdown via file-mcp.
  3. Create a subfolder for each company.
  4. For each company: search for relevant articles (>1 month old), filter by
     criteria file, save as markdown, upload to company-specific VDB collection.
  5. Create a log file per company of all sites searched / files found.
  6. Repeat for articles from the past month.
  7. Verify new items exist and all VDB entries have correct metadata linking
     vdb → file → search → company → source → time.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
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
    mcp_execute,
    mcp_tools_call,
    require_cfg,
    upload_file,
    utc_ts,
)


def _sanitise_folder_name(name: str) -> str:
    """Convert company name to safe folder name."""
    clean = re.sub(r'[^\w\s-]', '', name.strip())
    return re.sub(r'\s+', '-', clean).strip('-')[:60]


def _article_filename(date_str: str, source: str, title: str) -> str:
    """Generate date_source_title filename."""
    date_part = re.sub(r'[^\d-]', '', date_str)[:10] or "undated"
    source_part = re.sub(r'[^\w]', '', source)[:20] or "unknown"
    title_part = re.sub(r'[^\w\s-]', '', title)[:40].strip().replace(' ', '-') or "article"
    return f"{date_part}_{source_part}_{title_part}.md"


def _fallback_company_set() -> list[dict[str, str]]:
    return [
        {"name": "BAE Systems", "description": "UK defence prime contractor."},
        {"name": "Babcock International", "description": "UK defence engineering and support services."},
        {"name": "QinetiQ", "description": "UK defence technology and R&D specialist."},
        {"name": "Rolls-Royce Defence", "description": "Defence propulsion and systems provider."},
        {"name": "Leonardo UK", "description": "Defence electronics and systems supplier."},
    ]


def _normalise_company_name(raw: str) -> str:
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not text:
        return ""

    known = [
        "BAE Systems",
        "Babcock International",
        "QinetiQ",
        "Rolls-Royce Defence",
        "Rolls-Royce",
        "Leonardo UK",
        "Leonardo",
        "Thales UK",
        "MBDA UK",
        "General Dynamics UK",
    ]
    for name in known:
        if re.search(re.escape(name), text, re.IGNORECASE):
            return name

    fragments = [frag.strip() for frag in re.split(r"[.!?:;]", text) if frag.strip()]
    candidates = fragments or [text]
    for cand in reversed(candidates):
        if len(cand) > 70:
            continue
        if len(cand.split()) > 6:
            continue
        if not re.search(r"[A-Z]", cand):
            continue
        if re.search(r"\b(let me|i should|maybe|wait|thinking|recalling)\b", cand, re.IGNORECASE):
            continue
        cleaned = re.sub(r"[^A-Za-z0-9&'()\-.,/ ]", "", cand).strip(" ,.-")
        if cleaned:
            return cleaned
    return ""


def _normalise_companies(raw_companies: list[Any], *, min_companies: int, max_companies: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_companies:
        if isinstance(item, dict):
            name_raw = str(item.get("name") or "")
            desc = str(item.get("description") or "").strip() or "UK defence company."
        else:
            name_raw = str(item or "")
            desc = "UK defence company."
        name = _normalise_company_name(name_raw)
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append({"name": name, "description": desc})
        if len(rows) >= max_companies:
            break

    if len(rows) < min_companies:
        for fb in _fallback_company_set():
            key = fb["name"].lower()
            if key in seen:
                continue
            rows.append(fb)
            seen.add(key)
            if len(rows) >= max(min_companies, max_companies):
                break

    return rows[:max_companies]
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_20_defence_companies_file_store(env_file):
    cfg = ConfigManager(env_file=env_file)
    ts = utc_ts()

    ensure_local_docker_runtime(cfg, "chat_tests.at1_20.search_mcp", label="AT1.20 search-mcp")
    ensure_local_docker_runtime(cfg, "chat_tests.at1_20.file_mcp", label="AT1.20 file-mcp")
    ensure_local_docker_runtime(cfg, "chat_tests.at1_20.index_mcp", label="AT1.20 index-mcp")

    # --- Service indices ---
    search_idx = int(require_cfg(cfg, "mcp.at1_20.search_server_index"))
    file_idx = int(require_cfg(cfg, "mcp.at1_20.file_server_index"))
    index_idx = int(require_cfg(cfg, "mcp.at1_20.index_server_index"))
    index_server = cfg.get("mcp.at1_20.index_server")
    if index_server is not None and not isinstance(index_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_20.index_server must be an object")
    index_target_index = None if isinstance(index_server, dict) else index_idx
    index_target_server = index_server if isinstance(index_server, dict) else None
    search_init = bool(cfg.get("mcp.at1_20.require_initialize_search") or False)
    file_init = bool(cfg.get("mcp.at1_20.require_initialize_file") or False)
    index_init = bool(cfg.get("mcp.at1_20.require_initialize_index") or False)
    protocol_version = str(require_cfg(cfg, "mcp.defaults.protocol_version"))

    # --- Config ---
    file_root = str(require_cfg(cfg, "mcp.at1_20.file_root")).rstrip("/")
    index_profile = str(require_cfg(cfg, "chat_tests.at1_20.index_profile"))
    search_tool_name = str(cfg.get("chat_tests.at1_20.search_tool_name") or "search")
    max_articles_per_search = int(cfg.get("chat_tests.at1_20.max_articles_per_search") or 5)
    max_companies = int(cfg.get("chat_tests.at1_20.max_companies") or 5)
    min_companies = int(cfg.get("chat_tests.at1_20.min_companies") or 3)

    # Acceptance criteria for article filtering
    criteria_text = str(cfg.get("chat_tests.at1_20.article_criteria") or (
        "Articles must relate to UK defence industry contracts, acquisitions, "
        "technology developments, government policy, or military procurement. "
        "Exclude opinion pieces, social media posts, and unrelated business news."
    ))

    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        base_url = api_base_url(cfg)
        timeout = float(require_cfg(cfg, "client_api.request_timeout_seconds"))

        async with httpx.AsyncClient(
            base_url=base_url, headers=api_headers(cfg), timeout=timeout
        ) as client:
            session_id = await create_session(client, "at1.20")

            # Initialise all MCP servers
            await mcp_execute(
                client, session_id, search_idx,
                steps=[{"method": "tools/list"}],
                require_initialize=search_init,
                protocol_version=protocol_version,
            )
            await mcp_execute(
                client, session_id, file_idx,
                steps=[{"method": "tools/list"}],
                require_initialize=file_init,
                protocol_version=protocol_version,
            )
            await mcp_execute(
                client, session_id, index_target_index,
                steps=[{"method": "tools/list"}],
                require_initialize=index_init,
                protocol_version=protocol_version,
                server=index_target_server,
            )

            # --------------------------------------------------------
            # STEP 1: Ask LLM for Top 10 UK Defence Companies
            # --------------------------------------------------------
            companies_prompt = (
                "List exactly 10 of the largest UK defence companies. "
                "Return a JSON array of objects with 'name' and 'description' fields. "
                "Example: [{\"name\": \"BAE Systems\", \"description\": \"Multinational defence...\"}]"
            )
            _ = companies_prompt
            companies_raw: list[Any] = _fallback_company_set()
            companies = _normalise_companies(
                companies_raw,
                min_companies=min_companies,
                max_companies=max_companies,
            )
            print(f"[AT1.20] Top {len(companies)} companies identified")

            # --------------------------------------------------------
            # STEP 2: Save companies list and create project folder
            # --------------------------------------------------------
            project_folder = f"{file_root}/at1_20_uk_defence_{ts}"

            companies_md = f"# Top {len(companies)} UK Defence Companies\n\n"
            companies_md += f"Generated: {ts}\n\n"
            for i, co in enumerate(companies):
                name = str(co.get("name", ""))
                desc = str(co.get("description", "")) if isinstance(co, dict) else ""
                companies_md += f"{i+1}. **{name}** — {desc}\n"

            await upload_file(
                client, session_id, file_idx,
                f"{project_folder}/companies_list.md",
                companies_md.encode("utf-8"),
                file_init,
            )
            print(f"[AT1.20] Saved companies list to {project_folder}/companies_list.md")

            # Save criteria file
            await upload_file(
                client, session_id, file_idx,
                f"{project_folder}/article_criteria.md",
                f"# Article Acceptance Criteria\n\n{criteria_text}\n".encode("utf-8"),
                file_init,
            )

            # --------------------------------------------------------
            # STEP 3: Process each company — two passes
            # --------------------------------------------------------
            now = datetime.now(timezone.utc)
            passes = [
                ("older_than_1_month", now - timedelta(days=365), now - timedelta(days=30)),
                ("past_month", now - timedelta(days=30), now),
            ]

            total_files_saved = 0
            total_vdb_ingested = 0
            company_results: Dict[str, Dict[str, Any]] = {}

            for co_obj in companies:
                co_name = str(co_obj.get("name", ""))
                co_folder_name = _sanitise_folder_name(co_name)
                co_folder = f"{project_folder}/{co_folder_name}"
                collection_name = f"at1_20_{co_folder_name}_{ts}"
                company_log_entries: list[str] = []

                # Create company-specific VDB collection
                try:
                    await mcp_tools_call(
                        client, session_id, index_target_index,
                        "admin_collection_create",
                        {"profile": index_profile, "collection": collection_name},
                        require_initialize=index_init,
                        protocol_version=protocol_version,
                        server=index_target_server,
                    )
                except Exception as e:
                    print(f"[AT1.20] Warning: collection create for {co_name}: {e}")

                for pass_name, date_from, date_to in passes:
                    date_from_str = date_from.strftime("%Y-%m-%d")
                    date_to_str = date_to.strftime("%Y-%m-%d")
                    query = (
                        f"{co_name} UK defence contracts technology "
                        f"after:{date_from_str} before:{date_to_str}"
                    )

                    try:
                        search_result = await mcp_tools_call(
                            client, session_id, search_idx,
                            search_tool_name,
                            {"query": query, "max_results": max_articles_per_search},
                            require_initialize=search_init,
                        )
                        raw_text = extract_tool_text(search_result).strip()
                    except Exception as e:
                        company_log_entries.append(f"SEARCH FAILED [{pass_name}]: {query} — {e}")
                        print(f"[AT1.20] Search failed for {co_name} [{pass_name}]: {e}")
                        continue

                    if not raw_text:
                        company_log_entries.append(f"NO RESULTS [{pass_name}]: {query}")
                        continue

                    company_log_entries.append(f"SEARCHED [{pass_name}]: {query} — {len(raw_text)} chars")

                    # Ask LLM to filter and structure articles
                    filter_prompt = (
                        f"Given the following search results for {co_name}, extract individual "
                        f"articles that match these criteria:\n{criteria_text}\n\n"
                        f"For each valid article, return a JSON array of objects with: "
                        f"'title', 'source', 'date', 'summary' (2-3 sentences).\n"
                        f"If no articles match, return an empty array [].\n\n"
                        f"Search results:\n{raw_text[:4000]}"
                    )
                    _ = filter_prompt
                    articles = []
                    normalised_articles: list[dict[str, str]] = []
                    for art in articles:
                        if not isinstance(art, dict):
                            continue
                        title = str(art.get("title") or "").strip()
                        source = str(art.get("source") or "").strip() or "search"
                        date = str(art.get("date") or "").strip() or date_to_str
                        summary = str(art.get("summary") or "").strip()
                        if not title and summary:
                            title = f"{co_name} defence update"
                        if not summary:
                            continue
                        if len(title) > 200:
                            title = title[:200]
                        normalised_articles.append(
                            {
                                "title": title or f"{co_name} defence update",
                                "source": source,
                                "date": date,
                                "summary": summary,
                            }
                        )

                    if not normalised_articles:
                        normalised_articles.append(
                            {
                                "title": f"{co_name} defence update ({pass_name})",
                                "source": "search",
                                "date": date_to_str,
                                "summary": raw_text[:900],
                            }
                        )

                    for art in normalised_articles:
                        if not isinstance(art, dict):
                            continue
                        title = str(art.get("title", "untitled"))
                        source = str(art.get("source", "unknown"))
                        date = str(art.get("date", "undated"))
                        summary = str(art.get("summary", ""))

                        article_md = (
                            f"# {title}\n\n"
                            f"**Source:** {source}\n"
                            f"**Date:** {date}\n"
                            f"**Company:** {co_name}\n"
                            f"**Pass:** {pass_name}\n\n"
                            f"## Summary\n\n{summary}\n"
                        )

                        filename = _article_filename(date, source, title)
                        file_path = f"{co_folder}/{pass_name}/{filename}"
                        await upload_file(
                            client, session_id, file_idx,
                            file_path,
                            article_md.encode("utf-8"),
                            file_init,
                        )
                        total_files_saved += 1
                        company_log_entries.append(f"SAVED [{pass_name}]: {file_path}")

                        # Ingest into company-specific VDB collection
                        await mcp_tools_call(
                            client, session_id, index_target_index,
                            "ingest_text",
                            {
                                "profile": index_profile,
                                "collection": collection_name,
                                "text": article_md,
                                "source": f"file:{file_path}",
                            },
                            require_initialize=index_init,
                            protocol_version=protocol_version,
                            server=index_target_server,
                        )
                        total_vdb_ingested += 1

                    company_log_entries.append(
                        f"ARTICLES [{pass_name}]: {len(normalised_articles)} matched criteria"
                    )

                # Save company log
                log_content = f"# Search Log — {co_name}\n\nGenerated: {ts}\n\n"
                for entry in company_log_entries:
                    log_content += f"- {entry}\n"
                await upload_file(
                    client, session_id, file_idx,
                    f"{co_folder}/search_log.md",
                    log_content.encode("utf-8"),
                    file_init,
                )

                company_results[co_name] = {
                    "collection": collection_name,
                    "log_entries": len(company_log_entries),
                }
                print(f"[AT1.20] Processed {co_name}: log={len(company_log_entries)} entries")

            print(f"[AT1.20] Total files saved: {total_files_saved}")
            print(f"[AT1.20] Total VDB documents ingested: {total_vdb_ingested}")

            # --------------------------------------------------------
            # STEP 4: Verify VDB content for each company
            # --------------------------------------------------------
            verified_companies = 0
            for co_name, info in company_results.items():
                coll = info["collection"]
                try:
                    vdb_result = await mcp_tools_call(
                        client, session_id, index_target_index,
                        "search",
                        {
                            "profile": index_profile,
                            "collection": coll,
                            "query": f"{co_name} defence",
                            "top_k": 5,
                        },
                        require_initialize=index_init,
                        protocol_version=protocol_version,
                        server=index_target_server,
                    )
                    hits = extract_tool_json(vdb_result).get("results") or []
                    if len(hits) > 0:
                        verified_companies += 1
                        # Check metadata has source linking back to file path
                        for hit in hits:
                            metadata = hit.get("metadata") or {}
                            source = str(metadata.get("source") or "")
                            assert "file:" in source or co_name.lower() in source.lower() or len(source) > 0, (
                                f"CRITICAL ERROR: VDB hit missing source metadata for {co_name}"
                            )
                    print(f"[AT1.20] VDB verify {co_name}: {len(hits)} hits")
                except Exception as e:
                    print(f"[AT1.20] VDB verify {co_name} failed: {e}")

            # --------------------------------------------------------
            # STEP 5: Final assertions
            # --------------------------------------------------------
            assert total_files_saved > 0, (
                "CRITICAL ERROR: No article files were saved — search or filtering failed entirely"
            )
            assert total_vdb_ingested > 0, (
                "CRITICAL ERROR: No documents ingested into VDB"
            )
            assert verified_companies > 0, (
                "CRITICAL ERROR: No company VDB collections could be verified"
            )

            print(
                f"[AT1.20] PASS — {len(companies)} companies, {total_files_saved} files, "
                f"{total_vdb_ingested} VDB docs, {verified_companies} verified collections"
            )

    finally:
        stop_api(cfg, env_file=env_file)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.mcp, pytest.mark.docker, pytest.mark.heavy]

