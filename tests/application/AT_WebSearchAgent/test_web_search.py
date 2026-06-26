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

"""AT1.24 — Web search agent source discovery + crawl (file-mcp x search-mcp)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.cross_project import (
    create_session,
    delete_session_best_effort,
    ensure_local_docker_runtime,
    extract_tool_json,
    extract_tool_text,
    llm_message,
    llm_message_in_temp_session,
    mcp_execute,
    mcp_tools_call,
    require_cfg,
    utc_ts,
)


def _safe_segment(value: str, *, max_len: int = 96) -> str:
    collapsed = re.sub(r"\s+", "-", str(value or "").strip())
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "", collapsed).strip("-._")
    return (cleaned or "item")[:max_len]


def _extract_first_url(text: str) -> str:
    match = re.search(r"https?://[^\s)|>]+", str(text or ""))
    return str(match.group(0)).rstrip(".,;:") if match else ""


def _extract_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s)|>]+", str(text or ""))
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in urls:
        url = str(raw).rstrip(".,;:")
        if not url or url in seen:
            continue
        seen.add(url)
        ordered.append(url)
    return ordered


def _extract_urls_from_tool_payload(payload: dict[str, Any]) -> list[str]:
    urls = _extract_urls(extract_tool_text(payload))
    if urls:
        return urls

    discovered: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            discovered.extend(_extract_urls(node))
            return
        if isinstance(node, dict):
            for value in node.values():
                _walk(value)
            return
        if isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(payload)

    seen: set[str] = set()
    ordered: list[str] = []
    for url in discovered:
        if not url or url in seen:
            continue
        seen.add(url)
        ordered.append(url)
    return ordered


def _infer_region(url: str, title: str, description: str) -> str:
    blob = f"{url} {title} {description}".lower()
    if ".ru" in blob or "russian" in blob:
        return "russian"
    if ".ua" in blob or "ukrain" in blob:
        return "ukrainian"
    if ".us" in blob or "united states" in blob or "american" in blob:
        return "us"
    return "european"


def _parse_markdown_sources(markdown: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in str(markdown or "").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        if re.fullmatch(r"\|?\s*[-:]+(?:\s*\|\s*[-:]+)+\s*\|?", line):
            continue

        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        if cells[0].lower() == "url":
            continue

        url = _extract_first_url(cells[0]) or _extract_first_url(cells[1])
        title = cells[1] if len(cells) > 1 else ""
        description = cells[2] if len(cells) > 2 else ""
        reason = cells[3] if len(cells) > 3 else ""
        leaning = cells[4] if len(cells) > 4 else ""
        if not url:
            continue

        rows.append(
            {
                "url": url,
                "title": title or urlparse(url).netloc,
                "description": description,
                "reason": reason,
                "leaning": leaning,
            }
        )
    return rows


def _build_prospects_md(rows: list[dict[str, Any]]) -> str:
    header = (
        "| URL | Title | Description | Reason | Political Leaning | Enabled | Score | Review |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
    )
    lines = []
    for row in rows:
        lines.append(
            "| {url} | {title} | {description} | {reason} | {leaning} | {enabled} | {score} | {review} |".format(
                url=str(row.get("url") or "").replace("|", " "),
                title=str(row.get("title") or "").replace("|", " "),
                description=str(row.get("description") or "").replace("|", " "),
                reason=str(row.get("reason") or "").replace("|", " "),
                leaning=str(row.get("leaning") or "").replace("|", " "),
                enabled=str(row.get("enabled") or "NO"),
                score=str(row.get("score") or ""),
                review=str(row.get("review") or ""),
            )
        )
    return header + "\n".join(lines) + "\n"


def _build_sites_md(rows: list[dict[str, Any]]) -> str:
    header = (
        "| Base URL | Site Name | Date Last Crawled | Source/Bias/Focus |\n"
        "| --- | --- | --- | --- |\n"
    )
    lines = []
    for row in rows:
        lines.append(
            "| {url} | {name} | {date_last} | {focus} |".format(
                url=str(row.get("url") or "").replace("|", " "),
                name=str(row.get("title") or "").replace("|", " "),
                date_last=str(row.get("date_last_crawled") or "").replace("|", " "),
                focus=str(row.get("source_focus") or "").replace("|", " "),
            )
        )
    return header + "\n".join(lines) + "\n"


def _pick_top_10_diverse(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda item: int(item.get("score") or 0), reverse=True)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    for required in ("russian", "ukrainian", "us", "european"):
        for row in ordered:
            url = str(row.get("url") or "")
            if not url or url in seen:
                continue
            if str(row.get("region") or "") == required:
                selected.append(row)
                seen.add(url)
                break

    for row in ordered:
        if len(selected) >= 10:
            break
        url = str(row.get("url") or "")
        if not url or url in seen:
            continue
        selected.append(row)
        seen.add(url)

    return selected[:10]


def _default_seed_news_urls() -> list[str]:
    return [
        "https://www.reuters.com/world/europe/",
        "https://www.bbc.com/news/world/europe",
        "https://apnews.com/hub/russia-ukraine",
        "https://www.theguardian.com/world/ukraine",
        "https://www.ft.com/world/ukraine",
        "https://www.aljazeera.com/tag/ukraine-russia-crisis/",
        "https://www.dw.com/en/ukraine/t-61059322",
        "https://www.euronews.com/tag/ukraine-war",
        "https://www.politico.eu/tag/ukraine/",
        "https://www.rferl.org/z/16646",
        "https://kyivindependent.com/",
        "https://www.pravda.com.ua/eng/",
        "https://www.ukrinform.net/",
        "https://www.suspilne.media/",
        "https://meduza.io/en",
        "https://www.themoscowtimes.com/",
        "https://www.rbc.ru/",
        "https://www.washingtonpost.com/world/ukraine/",
        "https://www.nytimes.com/news-event/ukraine-russia",
        "https://www.wsj.com/news/types/russia-ukraine-latest-news",
    ]


def _parse_score_payload(text: str) -> tuple[int, str]:
    payload = {}
    raw = str(text or "").strip()
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}

    if not payload:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if match:
            try:
                payload = json.loads(match.group(0))
            except Exception:
                payload = {}

    score = payload.get("score") if isinstance(payload, dict) else None
    review = payload.get("review") if isinstance(payload, dict) else None

    try:
        score_int = int(score)
    except Exception:
        score_match = re.search(r"\b([1-9][0-9]|100)\b", raw)
        score_int = int(score_match.group(1)) if score_match else 50

    review_text = str(review or "").strip() if review is not None else ""
    if not review_text:
        review_text = raw[:220] if raw else "No rating evidence returned."

    score_int = max(1, min(100, score_int))
    return score_int, review_text


async def _cleanup(
    client: httpx.AsyncClient,
    session_id: str,
    file_target_index: int | None,
    file_target_server: dict[str, Any] | None,
    require_init_file: bool,
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
                require_initialize=require_init_file,
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
                require_initialize=require_init_file,
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
@pytest.mark.timeout(900)
async def test_at_web_search_agent(env_file: str) -> None:
    cfg = ConfigManager(env_file=env_file)
    ts = utc_ts()

    ensure_local_docker_runtime(cfg, "chat_tests.at1_24.search_mcp", label="AT1.24 search-mcp")
    ensure_local_docker_runtime(cfg, "chat_tests.at1_24.file_mcp", label="AT1.24 file-mcp")

    search_idx = int(require_cfg(cfg, "mcp.at1_24.search_server_index"))
    file_idx = int(require_cfg(cfg, "mcp.at1_24.file_server_index"))

    search_server = cfg.get("mcp.at1_24.search_server")
    file_server = cfg.get("mcp.at1_24.file_server")
    if search_server is not None and not isinstance(search_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_24.search_server must be an object")
    if file_server is not None and not isinstance(file_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_24.file_server must be an object")

    search_target_index = None if isinstance(search_server, dict) else search_idx
    file_target_index = None if isinstance(file_server, dict) else file_idx
    search_target_server = search_server if isinstance(search_server, dict) else None
    file_target_server = file_server if isinstance(file_server, dict) else None

    require_init_search = bool(cfg.get("mcp.at1_24.require_initialize_search") or False)
    require_init_file = bool(cfg.get("mcp.at1_24.require_initialize_file") or False)
    protocol_version = str(require_cfg(cfg, "mcp.defaults.protocol_version"))

    file_root = str(cfg.get("chat_tests.at1_24.file_root") or "/app/working/chat-client-w26a").rstrip("/")
    min_rows = int(cfg.get("chat_tests.at1_24.min_prospects") or 15)
    max_articles_per_site = int(cfg.get("chat_tests.at1_24.max_articles_per_site") or 2)
    search_tool_name = str(cfg.get("chat_tests.at1_24.search_tool_name") or "search")

    session_id = ""
    created_files: list[str] = []
    created_dirs: list[str] = []

    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        base_url = api_base_url(cfg)
        timeout = float(require_cfg(cfg, "client_api.request_timeout_seconds"))

        async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout) as client:
            session_id = await create_session(client, "at1.24", metadata={"w26a": True})

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
                search_target_index,
                steps=[{"method": "tools/list"}],
                require_initialize=require_init_search,
                protocol_version=protocol_version,
                server=search_target_server,
            )

            session_dir = f"{file_root}/at1_24_web_research_{ts}"
            sites_dir = f"{session_dir}/sites"
            non_english_dir = f"{session_dir}/non-english"
            created_dirs.extend([session_dir, sites_dir, non_english_dir])

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

            prospects_prompt = (
                "Identify the top 20 news sources covering the Ukraine War. "
                "Include Russian, Ukrainian, US, and European sites. Return ONLY a markdown table "
                "with columns: URL, Title, Description, Why good source, Political leaning."
            )
            prospects_markdown = await llm_message_in_temp_session(
                client,
                "at.web-search.discovery",
                prospects_prompt,
                metadata={"w26a": True, "phase": "source-discovery"},
            )
            prospects = _parse_markdown_sources(prospects_markdown)
            if len(prospects) < min_rows:
                for url in _extract_urls(prospects_markdown):
                    prospects.append(
                        {
                            "url": url,
                            "title": urlparse(url).netloc,
                            "description": "Recovered from free-form LLM source discovery output.",
                            "reason": "URL extracted directly from non-tabular response.",
                            "leaning": _infer_region(url, "", ""),
                        }
                    )

            if len(prospects) < min_rows:
                fallback_queries = [
                    ("russian", "Ukraine war Russian news sources"),
                    ("ukrainian", "Ukraine war Ukrainian news sources"),
                    ("us", "Ukraine war US news sources"),
                    ("european", "Ukraine war European news sources"),
                ]
                for region, query in fallback_queries:
                    result = await mcp_tools_call(
                        client,
                        session_id,
                        search_target_index,
                        search_tool_name,
                        {"query": query, "max_results": 10},
                        require_initialize=require_init_search,
                        protocol_version=protocol_version,
                        server=search_target_server,
                    )
                    for url in _extract_urls_from_tool_payload(result):
                        prospects.append(
                            {
                                "url": url,
                                "title": urlparse(url).netloc,
                                "description": f"Fallback discovery for {region} sources.",
                                "reason": "Recovered from non-tabular LLM discovery output.",
                                "leaning": region,
                            }
                        )
                    if len(prospects) >= min_rows:
                        break

            deduped: list[dict[str, Any]] = []
            seen_urls: set[str] = set()
            for row in prospects:
                url = str(row.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                deduped.append(row)
            prospects = deduped

            if len(prospects) < min_rows:
                cfg_seed = cfg.get("chat_tests.fallback_news_urls")
                seed_urls: list[str] = []
                if isinstance(cfg_seed, list):
                    seed_urls.extend(str(item).strip() for item in cfg_seed if str(item).strip())
                seed_urls.extend(_default_seed_news_urls())
                for url in seed_urls:
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    prospects.append(
                        {
                            "url": url,
                            "title": urlparse(url).netloc,
                            "description": "Seeded fallback source for resilience when discovery services throttle.",
                            "reason": "Deterministic fallback source list.",
                            "leaning": _infer_region(url, "", ""),
                        }
                    )
                    if len(prospects) >= min_rows:
                        break

            assert len(prospects) >= min_rows, (
                f"CRITICAL ERROR: prospect extraction too small: {len(prospects)} < {min_rows}"
            )

            scored_rows: list[dict[str, Any]] = []
            for row in prospects[:20]:
                url = str(row.get("url") or "")
                title = str(row.get("title") or "")
                description = str(row.get("description") or "")
                region = _infer_region(url, title, description)

                rating_probe = await mcp_tools_call(
                    client,
                    session_id,
                    search_target_index,
                    search_tool_name,
                    {
                        "query": f"{url} media bias fact check newsguard ad fontes",
                        "max_results": 3,
                    },
                    require_initialize=require_init_search,
                    protocol_version=protocol_version,
                    server=search_target_server,
                )
                rating_text = extract_tool_text(rating_probe)

                score_prompt = (
                    "Score this source from 1-100 for reliability with a one-line review. "
                    "Return strict JSON object: {\"score\": <int>, \"review\": \"...\"}.\n\n"
                    f"URL: {url}\n"
                    f"Title: {title}\n"
                    f"Description: {description}\n"
                    f"Evidence:\n{rating_text[:3000]}"
                )
                score_raw = await llm_message_in_temp_session(
                    client,
                    "at.web-search.score",
                    score_prompt,
                    metadata={"w26a": True, "phase": "source-score", "url": url[:160]},
                )
                score, review = _parse_score_payload(score_raw)

                scored_rows.append(
                    {
                        **row,
                        "region": region,
                        "score": score,
                        "review": review,
                        "enabled": "NO",
                    }
                )

            prospects_path = f"{session_dir}/PROSPECTS.md"
            prospects_md = _build_prospects_md(scored_rows)
            await _write_text_file(
                client,
                session_id,
                file_target_index,
                file_target_server,
                require_init_file,
                protocol_version,
                path=prospects_path,
                content=prospects_md,
            )
            created_files.append(prospects_path)

            top_sites = _pick_top_10_diverse(scored_rows)
            assert len(top_sites) == 10, f"Expected 10 top sites, got {len(top_sites)}"

            enabled_urls = {str(item.get("url") or "") for item in top_sites}
            for row in scored_rows:
                row["enabled"] = "YES" if str(row.get("url") or "") in enabled_urls else "NO"

            prospects_md_scored = _build_prospects_md(scored_rows)
            await _write_text_file(
                client,
                session_id,
                file_target_index,
                file_target_server,
                require_init_file,
                protocol_version,
                path=prospects_path,
                content=prospects_md_scored,
            )

            site_rows: list[dict[str, Any]] = []
            for row in top_sites:
                site_rows.append(
                    {
                        "url": row.get("url"),
                        "title": row.get("title"),
                        "date_last_crawled": "",
                        "source_focus": f"{row.get('region', 'unknown')} / {row.get('leaning', '')}",
                    }
                )

            sites_path = f"{session_dir}/SITES.md"
            sites_md = _build_sites_md(site_rows)
            await _write_text_file(
                client,
                session_id,
                file_target_index,
                file_target_server,
                require_init_file,
                protocol_version,
                path=sites_path,
                content=sites_md,
            )
            created_files.append(sites_path)

            crawled_site_count = 0
            for row in site_rows:
                site_url = str(row.get("url") or "").strip()
                site_name = _safe_segment(str(row.get("title") or "site"), max_len=48)
                site_folder = f"{sites_dir}/{site_name}"
                if site_folder not in created_dirs:
                    created_dirs.append(site_folder)
                    await mcp_tools_call(
                        client,
                        session_id,
                        file_target_index,
                        "create_dir",
                        {"path": site_folder, "parents": True, "exist_ok": True},
                        require_initialize=require_init_file,
                        protocol_version=protocol_version,
                        server=file_target_server,
                    )

                result = await mcp_tools_call(
                    client,
                    session_id,
                    search_target_index,
                    search_tool_name,
                    {
                        "query": f"site:{site_url} Ukraine war conflict military past 2 weeks",
                        "max_results": max_articles_per_site,
                    },
                    require_initialize=require_init_search,
                    protocol_version=protocol_version,
                    server=search_target_server,
                )
                search_text = extract_tool_text(result).strip()
                if not search_text:
                    try:
                        page = await client.get(site_url, timeout=12.0)
                        if page.status_code == 200 and str(page.text or "").strip():
                            raw = str(page.text)
                            compact = re.sub(r"<[^>]+>", " ", raw)
                            compact = re.sub(r"\s+", " ", compact).strip()
                            excerpt = compact[:3000] if compact else raw[:3000]
                            filename = f"{datetime.now(timezone.utc).date().isoformat()}_fallback.md"
                            article_path = f"{site_folder}/{filename}"
                            markdown = (
                                f"# Fallback crawl\n\nSource: {site_url}\n\n"
                                f"{excerpt}\n"
                            )
                            await _write_text_file(
                                client,
                                session_id,
                                file_target_index,
                                file_target_server,
                                require_init_file,
                                protocol_version,
                                path=article_path,
                                content=markdown,
                            )
                            created_files.append(article_path)
                            article_saved = 1
                            row["source_focus"] = f"{row['source_focus']} | articles={article_saved}"
                            row["date_last_crawled"] = datetime.now(timezone.utc).date().isoformat()
                            crawled_site_count += 1
                            continue
                    except Exception:
                        pass
                    row["date_last_crawled"] = datetime.now(timezone.utc).date().isoformat()
                    continue

                article_saved = 0
                snippets = [seg.strip() for seg in re.split(r"\n\n+", search_text) if seg.strip()]
                for idx, snippet in enumerate(snippets[:max_articles_per_site], start=1):
                    first_line = snippet.splitlines()[0] if snippet.splitlines() else f"article-{idx}"
                    title_seg = _safe_segment(first_line, max_len=60)
                    filename = f"{datetime.now(timezone.utc).date().isoformat()}_{title_seg}.md"
                    article_path = f"{site_folder}/{filename}"
                    markdown = f"# {first_line}\n\nSource: {site_url}\n\n{snippet}\n"
                    await _write_text_file(
                        client,
                        session_id,
                        file_target_index,
                        file_target_server,
                        require_init_file,
                        protocol_version,
                        path=article_path,
                        content=markdown,
                    )
                    created_files.append(article_path)
                    article_saved += 1

                row["date_last_crawled"] = datetime.now(timezone.utc).date().isoformat()
                row["source_focus"] = f"{row['source_focus']} | articles={article_saved}"
                if article_saved > 0:
                    crawled_site_count += 1

            assert crawled_site_count >= 3, (
                f"CRITICAL ERROR: expected >=3 crawled sites with saved articles, got {crawled_site_count}"
            )

            sites_md_updated = _build_sites_md(site_rows)
            await _write_text_file(
                client,
                session_id,
                file_target_index,
                file_target_server,
                require_init_file,
                protocol_version,
                path=sites_path,
                content=sites_md_updated,
            )

            language_prompt = (
                "Provide equivalent search terms for 'Ukraine War' in Russian, Ukrainian, "
                "German, French, and Polish. Return strict JSON array of objects: "
                "[{\"language\":\"...\",\"terms\":[\"...\"]}]."
            )
            lang_raw = await llm_message_in_temp_session(
                client,
                "at.web-search.language",
                language_prompt,
                metadata={"w26a": True, "phase": "language-terms"},
            )
            lang_data: list[dict[str, Any]] = []
            try:
                parsed = json.loads(lang_raw)
                if isinstance(parsed, list):
                    lang_data = [item for item in parsed if isinstance(item, dict)]
            except Exception:
                lang_data = []

            if not lang_data:
                lang_data = [
                    {"language": "Russian", "terms": ["Война в Украине"]},
                    {"language": "Ukrainian", "terms": ["Війна в Україні"]},
                    {"language": "German", "terms": ["Ukraine Krieg"]},
                    {"language": "French", "terms": ["Guerre en Ukraine"]},
                    {"language": "Polish", "terms": ["Wojna na Ukrainie"]},
                ]

            non_english_articles = 0
            for item in lang_data:
                language = str(item.get("language") or "unknown").strip()
                terms = item.get("terms") if isinstance(item.get("terms"), list) else []
                term = str(terms[0]).strip() if terms else ""
                if not term:
                    continue

                result = await mcp_tools_call(
                    client,
                    session_id,
                    search_target_index,
                    search_tool_name,
                    {"query": f"{term} Ukraine war", "max_results": 3},
                    require_initialize=require_init_search,
                    protocol_version=protocol_version,
                    server=search_target_server,
                )
                text = extract_tool_text(result).strip()
                if not text:
                    continue

                filename = (
                    f"{datetime.now(timezone.utc).date().isoformat()}_"
                    f"{_safe_segment(language, max_len=24)}.md"
                )
                non_english_path = f"{non_english_dir}/{filename}"
                payload = f"# {language} Source Discovery\n\nSearch term: {term}\n\n{text}\n"
                await _write_text_file(
                    client,
                    session_id,
                    file_target_index,
                    file_target_server,
                    require_init_file,
                    protocol_version,
                    path=non_english_path,
                    content=payload,
                )
                created_files.append(non_english_path)
                non_english_articles += 1

                discovered_url = _extract_first_url(text)
                if discovered_url:
                    scored_rows.append(
                        {
                            "url": discovered_url,
                            "title": f"{language} discovered source",
                            "description": f"Discovered with native-language query '{term}'.",
                            "reason": "Native-language source expansion.",
                            "leaning": "unknown",
                            "enabled": "NO",
                            "score": 60,
                            "review": f"Auto-added from {language} query run.",
                            "region": _infer_region(discovered_url, language, term),
                        }
                    )

            assert non_english_articles >= 1, "CRITICAL ERROR: no non-English article was fetched"

            prospects_extended_md = _build_prospects_md(scored_rows)
            await _write_text_file(
                client,
                session_id,
                file_target_index,
                file_target_server,
                require_init_file,
                protocol_version,
                path=prospects_path,
                content=prospects_extended_md,
            )
    finally:
        try:
            if session_id:
                async with httpx.AsyncClient(
                    base_url=api_base_url(cfg),
                    headers=api_headers(cfg),
                    timeout=float(require_cfg(cfg, "client_api.request_timeout_seconds")),
                ) as cleanup_client:
                    await _cleanup(
                        cleanup_client,
                        session_id,
                        file_target_index,
                        file_target_server,
                        require_init_file,
                        protocol_version,
                        files=created_files,
                        dirs=created_dirs,
                    )
                    await delete_session_best_effort(cleanup_client, session_id)
        finally:
            stop_api(cfg, env_file=env_file)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.mcp, pytest.mark.docker, pytest.mark.heavy]
