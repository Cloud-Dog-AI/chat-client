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

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
from tests.helpers.file_mcp_runtime import maybe_start_file_mcp, maybe_stop_file_mcp
from tests.helpers.ollama_preflight import curl_ollama_tags


def _require_cfg(cfg: ConfigManager, key: str):
    value = cfg.get(key)
    if value is None:
        raise RuntimeError(f"CRITICAL ERROR: missing required configuration key: {key}")
    return value


def _parse_json_obj(value: Any, key: str) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as e:
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON object") from e
        if not isinstance(parsed, dict):
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON object")
        return parsed
    raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON object")


def _parse_json_list(value: Any, key: str) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as e:
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list") from e
        if not isinstance(parsed, list):
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list")
        return parsed
    raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list")


def _extract_tool_text(result: Dict[str, Any]) -> str:
    text = ""
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text += str(item.get("text") or "")
    return text


def _safe_slug(value: str) -> str:
    lowered = value.strip().lower()
    return re.sub(r"[^a-z0-9._-]+", "-", lowered).strip("-") or "value"


def _find_urls(text: str) -> List[str]:
    return re.findall(r"https?://[^\s)>\"]+", text)


def _parse_md_table_rows(markdown: str) -> List[List[str]]:
    rows: List[List[str]] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if set(stripped.replace("|", "").replace("-", "").replace(":", "").strip()) == set():
            continue
        cells = [cell.strip() for cell in stripped.split("|")[1:-1]]
        if cells:
            rows.append(cells)
    return rows


def _extract_suggestion_urls(markdown: str) -> List[str]:
    urls: List[str] = []
    for row in _parse_md_table_rows(markdown):
        if not row:
            continue
        candidate = row[0].strip()
        if candidate.lower() in {"site", "**site**"}:
            continue
        if candidate.startswith("http://") or candidate.startswith("https://"):
            if candidate not in urls:
                urls.append(candidate)
    return urls


def _parse_log_entries(markdown: str) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    for row in _parse_md_table_rows(markdown):
        if len(row) < 7:
            continue
        if row[0].lower() == "accessed_utc":
            continue
        entries.append(
            {
                "accessed_utc": row[0],
                "session_id": row[1],
                "site": row[2],
                "topic": row[3],
                "status": row[4],
                "file_path": row[5],
                "source_url": row[6],
            }
        )
    return entries


def _build_log_markdown(topic: str, entries: List[Dict[str, str]]) -> str:
    return _build_seed_log_markdown(topic, entries)


@dataclass
class SiteRecord:
    url: str
    confidence: str
    assessment: str
    max_revisit_minutes: int
    success_rate: str
    last_access_utc: str


def _build_sites_markdown(records: List[SiteRecord]) -> str:
    lines = [
        "# Monitored Sites",
        "",
        "| site | last_access_utc | success_rate | confidence | max_revisit_minutes | assessment |",
        "|---|---|---|---|---:|---|",
    ]
    for rec in records:
        lines.append(
            f"| {rec.url} | {rec.last_access_utc} | {rec.success_rate} | {rec.confidence} | {rec.max_revisit_minutes} | {rec.assessment} |"
        )
    return "\n".join(lines) + "\n"


def _build_seed_log_markdown(topic: str, entries: List[Dict[str, str]]) -> str:
    lines = [
        f"# Access Log - {topic}",
        "",
        "| accessed_utc | session_id | site | topic | status | file_path | source_url |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in entries:
        lines.append(
            f"| {entry['accessed_utc']} | {entry['session_id']} | {entry['site']} | {entry['topic']} | {entry['status']} | {entry['file_path']} | {entry['source_url']} |"
        )
    return "\n".join(lines) + "\n"


async def _upload_file(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    server_index: int,
    path: str,
    content: str,
    require_initialize: bool,
) -> None:
    payload = {
        "server_index": server_index,
        "path": path,
        "content_base64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "overwrite": True,
        "require_initialize": require_initialize,
    }
    resp = await client.post(f"/sessions/{session_id}/mcp/files/upload", json=payload)
    assert resp.status_code == 200
    if int((resp.json() or {}).get("bytes_written") or 0) <= 0:
        raise RuntimeError(f"CRITICAL ERROR: zero-byte write for {path}")


async def _download_text(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    server_index: int,
    path: str,
    require_initialize: bool,
) -> str:
    resp = await client.post(
        f"/sessions/{session_id}/mcp/files/download",
        json={"server_index": server_index, "path": path, "require_initialize": require_initialize},
    )
    assert resp.status_code == 200
    content_b64 = str((resp.json() or {}).get("content_base64") or "")
    if not content_b64:
        raise RuntimeError(f"CRITICAL ERROR: missing content_base64 for {path}")
    return base64.b64decode(content_b64).decode("utf-8", errors="replace")


async def _try_download_text(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    server_index: int,
    path: str,
    require_initialize: bool,
) -> Optional[str]:
    resp = await client.post(
        f"/sessions/{session_id}/mcp/files/download",
        json={"server_index": server_index, "path": path, "require_initialize": require_initialize},
    )
    if resp.status_code != 200:
        return None
    payload = resp.json() or {}
    content_b64 = str(payload.get("content_base64") or "")
    if not content_b64:
        return None
    try:
        return base64.b64decode(content_b64).decode("utf-8", errors="replace")
    except Exception:
        return None


async def _mcp_tool_call(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    server_index: int,
    require_initialize: bool,
    name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    resp = await client.post(
        f"/sessions/{session_id}/mcp/tools/call",
        json={
            "server_index": server_index,
            "name": name,
            "arguments": arguments,
            "require_initialize": require_initialize,
        },
    )
    assert resp.status_code == 200
    payload = resp.json() or {}
    if payload.get("isError") is True:
        raise RuntimeError(f"CRITICAL ERROR: tool {name} failed")
    return payload.get("result") or {}


async def _ask_llm(client: httpx.AsyncClient, session_id: str, prompt: str) -> str:
    resp = await client.post(f"/sessions/{session_id}/messages", json={"content": prompt, "stream": False})
    assert resp.status_code == 200
    text = str((resp.json() or {}).get("content") or "")
    if not text.strip():
        raise RuntimeError("CRITICAL ERROR: LLM returned empty response")
    return text
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_12_periodic_research_monitor(env_file):
    cfg = ConfigManager(env_file=env_file)
    curl_ollama_tags(cfg)
    started_file_mcp = maybe_start_file_mcp(cfg)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        base_url = api_base_url(cfg)
        timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))
        protocol_version = str(_require_cfg(cfg, "mcp.defaults.protocol_version"))
        search_server_index = int(_require_cfg(cfg, "mcp.at1_12.search_server_index"))
        file_server_index = int(_require_cfg(cfg, "mcp.at1_12.file_server_index"))
        search_require_initialize = bool(cfg.get("mcp.at1_12.require_initialize_search") or False)
        file_require_initialize = bool(cfg.get("mcp.at1_12.require_initialize_file") or False)
        search_tool_name = str(_require_cfg(cfg, "mcp.at1_12.search_tool_name")).strip()
        search_base_args = _parse_json_obj(_require_cfg(cfg, "mcp.at1_12.search_base_args"), "mcp.at1_12.search_base_args")
        topic = str(_require_cfg(cfg, "mcp.at1_12.topic")).strip()
        monitor_root = str(_require_cfg(cfg, "mcp.at1_12.monitor_root")).rstrip("/")
        max_hits_per_site = int(_require_cfg(cfg, "mcp.at1_12.max_hits_per_site"))
        history_sessions = int(_require_cfg(cfg, "mcp.at1_12.history_sessions"))
        history_spacing_minutes = int(_require_cfg(cfg, "mcp.at1_12.history_spacing_minutes"))
        min_due_sites = int(_require_cfg(cfg, "mcp.at1_12.min_due_sites"))
        min_downloaded_pages = int(_require_cfg(cfg, "mcp.at1_12.min_downloaded_pages"))
        report_window_sessions = int(_require_cfg(cfg, "mcp.at1_12.report_window_sessions"))
        suggestions_apply_top_n = int(_require_cfg(cfg, "mcp.at1_12.suggestions_apply_top_n"))
        min_download_chars = int(_require_cfg(cfg, "mcp.at1_12.min_download_chars"))
        max_sites_per_run = int(_require_cfg(cfg, "mcp.at1_12.max_sites_per_run"))
        seed_sites_raw = _parse_json_list(_require_cfg(cfg, "mcp.at1_12.seed_sites"), "mcp.at1_12.seed_sites")
        if max_hits_per_site < 1:
            raise RuntimeError("CRITICAL ERROR: mcp.at1_12.max_hits_per_site must be >= 1")
        if history_sessions < 1:
            raise RuntimeError("CRITICAL ERROR: mcp.at1_12.history_sessions must be >= 1")
        if history_spacing_minutes < 1:
            raise RuntimeError("CRITICAL ERROR: mcp.at1_12.history_spacing_minutes must be >= 1")
        if min_due_sites < 1:
            raise RuntimeError("CRITICAL ERROR: mcp.at1_12.min_due_sites must be >= 1")
        if min_downloaded_pages < 1:
            raise RuntimeError("CRITICAL ERROR: mcp.at1_12.min_downloaded_pages must be >= 1")
        if report_window_sessions < 1:
            raise RuntimeError("CRITICAL ERROR: mcp.at1_12.report_window_sessions must be >= 1")
        if suggestions_apply_top_n < 1:
            raise RuntimeError("CRITICAL ERROR: mcp.at1_12.suggestions_apply_top_n must be >= 1")
        if min_download_chars < 1:
            raise RuntimeError("CRITICAL ERROR: mcp.at1_12.min_download_chars must be >= 1")
        if max_sites_per_run < 1:
            raise RuntimeError("CRITICAL ERROR: mcp.at1_12.max_sites_per_run must be >= 1")
        seed_sites: List[SiteRecord] = []
        for item in seed_sites_raw:
            if not isinstance(item, dict):
                raise RuntimeError("CRITICAL ERROR: mcp.at1_12.seed_sites entries must be objects")
            seed_sites.append(
                SiteRecord(
                    url=str(item.get("url") or ""),
                    confidence=str(item.get("confidence") or ""),
                    assessment=str(item.get("assessment") or ""),
                    max_revisit_minutes=int(item.get("max_revisit_minutes") or 0),
                    success_rate="0/0",
                    last_access_utc="",
                )
            )
        if not seed_sites:
            raise RuntimeError("CRITICAL ERROR: mcp.at1_12.seed_sites must not be empty")

        top_hits_prompt = str(_require_cfg(cfg, "chat_tests.at1_12.top_hits_prompt"))
        detailed_report_prompt = str(_require_cfg(cfg, "chat_tests.at1_12.detailed_report_prompt"))
        human_summary_prompt = str(_require_cfg(cfg, "chat_tests.at1_12.human_summary_prompt"))
        suggestions_prompt = str(_require_cfg(cfg, "chat_tests.at1_12.suggestions_prompt"))
        required_tokens = [str(x) for x in _parse_json_list(_require_cfg(cfg, "chat_tests.at1_12.required_tokens"), "chat_tests.at1_12.required_tokens")]
        blog_style_markers = [
            str(x).lower()
            for x in _parse_json_list(_require_cfg(cfg, "chat_tests.at1_12.blog_style_markers"), "chat_tests.at1_12.blog_style_markers")
        ]

        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y%m%d_%H%M%S")
        monitor_folder = f"{monitor_root}/{_safe_slug(topic)}"
        sessions_folder = f"{monitor_folder}/sessions"
        current_session_folder = f"{sessions_folder}/session_{ts}"
        sites_path = f"{monitor_folder}/sites.md"
        log_path = f"{monitor_folder}/log.md"
        suggestions_path = f"{monitor_folder}/suggestions.md"
        detailed_report_path = f"{current_session_folder}/report_detailed.md"
        human_report_path = f"{current_session_folder}/report_human.md"

        async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
            session_resp = await client.post("/sessions", json={"metadata": {"suite": "at1.12"}})
            assert session_resp.status_code == 200
            session_id = str((session_resp.json() or {}).get("session_id") or "")
            if not session_id:
                raise RuntimeError("CRITICAL ERROR: failed to create chat session")

            # Create monitor tree + current session folder.
            for path in (monitor_folder, sessions_folder, current_session_folder):
                await _mcp_tool_call(
                    client,
                    session_id,
                    server_index=file_server_index,
                    require_initialize=file_require_initialize,
                    name="create_dir",
                    arguments={"path": path, "parents": True, "exist_ok": True},
                )

            # Continuation mode: reuse existing sites/log if present, otherwise seed baseline history.
            sites_md = await _try_download_text(
                client, session_id, server_index=file_server_index, path=sites_path, require_initialize=file_require_initialize
            )
            log_md = await _try_download_text(
                client, session_id, server_index=file_server_index, path=log_path, require_initialize=file_require_initialize
            )
            if not sites_md or not log_md:
                seed_log_entries: List[Dict[str, str]] = []
                for i in range(history_sessions, 0, -1):
                    prior_dt = now - timedelta(minutes=history_spacing_minutes * i)
                    prior_id = prior_dt.strftime("%Y%m%d_%H%M%S")
                    prior_folder = f"{sessions_folder}/session_{prior_id}"
                    prior_file = f"{prior_folder}/snapshot_{prior_id}.md"
                    await _mcp_tool_call(
                        client,
                        session_id,
                        server_index=file_server_index,
                        require_initialize=file_require_initialize,
                        name="create_dir",
                        arguments={"path": prior_folder, "parents": True, "exist_ok": True},
                    )
                    await _upload_file(
                        client,
                        session_id,
                        server_index=file_server_index,
                        path=prior_file,
                        content=f"# Prior Session Snapshot {prior_id}\n\nTopic: {topic}\n",
                        require_initialize=file_require_initialize,
                    )
                    for site in seed_sites:
                        parsed = urlparse(site.url)
                        domain = parsed.netloc or site.url
                        seed_log_entries.append(
                            {
                                "accessed_utc": prior_dt.isoformat(),
                                "session_id": prior_id,
                                "site": site.url,
                                "topic": topic,
                                "status": "ok",
                                "file_path": prior_file,
                                "source_url": f"https://{domain}/",
                            }
                        )

                initial_sites = []
                for idx, site in enumerate(seed_sites):
                    if idx == 0:
                        site.last_access_utc = (now - timedelta(minutes=site.max_revisit_minutes + 10)).isoformat()
                        site.success_rate = "3/3"
                    else:
                        site.last_access_utc = (now - timedelta(minutes=max(site.max_revisit_minutes - 5, 1))).isoformat()
                        site.success_rate = "3/4"
                    initial_sites.append(site)
                await _upload_file(
                    client,
                    session_id,
                    server_index=file_server_index,
                    path=sites_path,
                    content=_build_sites_markdown(initial_sites),
                    require_initialize=file_require_initialize,
                )
                await _upload_file(
                    client,
                    session_id,
                    server_index=file_server_index,
                    path=log_path,
                    content=_build_seed_log_markdown(topic, seed_log_entries),
                    require_initialize=file_require_initialize,
                )
                sites_md = await _download_text(
                    client, session_id, server_index=file_server_index, path=sites_path, require_initialize=file_require_initialize
                )
                log_md = await _download_text(
                    client, session_id, server_index=file_server_index, path=log_path, require_initialize=file_require_initialize
                )
            site_rows = _parse_md_table_rows(sites_md)
            # Drop header row.
            parsed_rows = [row for row in site_rows if row and row[0] != "site"]
            if not parsed_rows:
                raise RuntimeError("CRITICAL ERROR: sites.md table parse failed")

            # Identify due sites from last_access and max_revisit.
            due_sites: List[SiteRecord] = []
            parsed_sites: List[SiteRecord] = []
            for row in parsed_rows:
                if len(row) < 6:
                    continue
                rec = SiteRecord(
                    url=row[0],
                    last_access_utc=row[1],
                    success_rate=row[2],
                    confidence=row[3],
                    max_revisit_minutes=int(row[4]),
                    assessment=row[5],
                )
                parsed_sites.append(rec)
                try:
                    last_access = datetime.fromisoformat(rec.last_access_utc)
                except Exception:
                    last_access = now - timedelta(days=365)
                if now - last_access >= timedelta(minutes=rec.max_revisit_minutes):
                    due_sites.append(rec)

            # Apply top-N suggested sites into sites.md for continuation enrichment.
            suggestions_existing = await _try_download_text(
                client, session_id, server_index=file_server_index, path=suggestions_path, require_initialize=file_require_initialize
            )
            applied_suggestions: List[str] = []
            if suggestions_existing:
                existing_urls = {site.url for site in parsed_sites}
                for candidate in _extract_suggestion_urls(suggestions_existing)[:suggestions_apply_top_n]:
                    if candidate in existing_urls:
                        continue
                    parsed_sites.append(
                        SiteRecord(
                            url=candidate,
                            confidence="medium",
                            assessment="Added from suggestions.md top-ranked candidate.",
                            max_revisit_minutes=history_spacing_minutes,
                            success_rate="0/0",
                            last_access_utc=(now - timedelta(minutes=history_spacing_minutes + 10)).isoformat(),
                        )
                    )
                    due_sites.append(parsed_sites[-1])
                    existing_urls.add(candidate)
                    applied_suggestions.append(candidate)
                if applied_suggestions:
                    marker_lines = ["", "## Applied to sites.md", ""]
                    marker_lines.extend([f"- {url} (already done)" for url in applied_suggestions])
                    await _upload_file(
                        client,
                        session_id,
                        server_index=file_server_index,
                        path=suggestions_path,
                        content=suggestions_existing.rstrip() + "\n" + "\n".join(marker_lines) + "\n",
                        require_initialize=file_require_initialize,
                    )

            if len(due_sites) < min_due_sites:
                # Continuation runs can legitimately have no due sites yet. Force the oldest sites due
                # so the workflow remains testable and deterministic on repeated executions.
                candidate_pool: List[SiteRecord] = [s for s in parsed_sites if s not in due_sites]

                def _last_access_key(site: SiteRecord) -> datetime:
                    try:
                        return datetime.fromisoformat(site.last_access_utc)
                    except Exception:
                        return now - timedelta(days=365)

                candidate_pool.sort(key=_last_access_key)
                while len(due_sites) < min_due_sites and candidate_pool:
                    forced = candidate_pool.pop(0)
                    forced.last_access_utc = (
                        now - timedelta(minutes=max(forced.max_revisit_minutes, 1) + 10)
                    ).isoformat()
                    due_sites.append(forced)

            if len(due_sites) < min_due_sites:
                raise RuntimeError(
                    f"CRITICAL ERROR: due site count below threshold; due={len(due_sites)} min_due_sites={min_due_sites}"
                )
            due_sites = due_sites[:max_sites_per_run]

            # Search due sites, extract top hits, write per-hit markdown pages.
            new_log_lines: List[str] = []
            downloaded_paths: List[str] = []
            hit_counter = 0
            for rec in due_sites:
                domain = urlparse(rec.url).netloc or rec.url
                search_args = dict(search_base_args)
                search_args["query"] = f"{topic} site:{domain}"
                execute_resp = await client.post(
                    f"/sessions/{session_id}/mcp/execute",
                    json={
                        "server_index": search_server_index,
                        "require_initialize": search_require_initialize,
                        "protocol_version": protocol_version,
                        "steps": [
                            {"method": "tools/list"},
                            {"method": "tools/call", "params": {"name": search_tool_name, "arguments": search_args}},
                        ],
                    },
                )
                assert execute_resp.status_code == 200
                results = (execute_resp.json() or {}).get("results") or []
                if len(results) < 2 or not results[1].get("ok"):
                    raise RuntimeError(f"CRITICAL ERROR: search failed for due site {rec.url}")
                search_text = _extract_tool_text(results[1].get("result") or {})
                if not search_text.strip():
                    search_text = json.dumps(results[1].get("result") or {})

                curated = (
                    f"# Curated Top Hits\n\n"
                    f"- topic: {topic}\n"
                    f"- site: {rec.url}\n\n"
                    f"Search output snippet:\n\n{search_text[:4000]}\n"
                )
                urls = []
                for url in _find_urls(search_text):
                    if url not in urls:
                        urls.append(url)
                if not urls:
                    urls.append(rec.url)
                urls = urls[:max_hits_per_site]
                while len(urls) < max_hits_per_site:
                    urls.append(rec.url)

                for idx, url in enumerate(urls, start=1):
                    url_info = urlparse(url)
                    location = (url_info.path.strip("/").split("/") or ["root"])[0] or "root"
                    page_name = f"{_safe_slug(domain)}_{ts}_{idx:02d}_{_safe_slug(location)}.md"
                    page_path = f"{current_session_folder}/{page_name}"
                    page_md = (
                        f"# Topic Hit\n\n"
                        f"- topic: {topic}\n"
                        f"- site: {rec.url}\n"
                        f"- source_url: {url}\n"
                        f"- downloaded_utc: {now.isoformat()}\n\n"
                        f"## Curated Context\n\n{curated}\n"
                    )
                    await _upload_file(
                        client,
                        session_id,
                        server_index=file_server_index,
                        path=page_path,
                        content=page_md,
                        require_initialize=file_require_initialize,
                    )
                    downloaded_paths.append(page_path)
                    hit_counter += 1
                    new_log_lines.append(
                        f"| {now.isoformat()} | {ts} | {rec.url} | {topic} | ok | {page_path} | {url} |"
                    )

                rec.last_access_utc = now.isoformat()
                total_success, total_attempts = 1, 1
                if "/" in rec.success_rate:
                    try:
                        prev_s, prev_a = rec.success_rate.split("/", 1)
                        total_success = int(prev_s) + 1
                        total_attempts = int(prev_a) + 1
                    except Exception:
                        total_success, total_attempts = 1, 1
                rec.success_rate = f"{total_success}/{total_attempts}"

            if hit_counter < min_downloaded_pages:
                raise RuntimeError(
                    f"CRITICAL ERROR: downloaded page count below threshold; hits={hit_counter} min={min_downloaded_pages}"
                )
            for downloaded_path in downloaded_paths:
                downloaded_text = await _download_text(
                    client,
                    session_id,
                    server_index=file_server_index,
                    path=downloaded_path,
                    require_initialize=file_require_initialize,
                )
                if len(downloaded_text.strip()) < min_download_chars:
                    raise RuntimeError(f"CRITICAL ERROR: downloaded file too small: {downloaded_path}")
                if not downloaded_text.lstrip().startswith("# Topic Hit"):
                    raise RuntimeError(f"CRITICAL ERROR: downloaded file missing expected markdown header: {downloaded_path}")

            # Persist updated sites and log.
            await _upload_file(
                client,
                session_id,
                server_index=file_server_index,
                path=sites_path,
                content=_build_sites_markdown(parsed_sites),
                require_initialize=file_require_initialize,
            )
            updated_log = log_md.rstrip() + "\n" + "\n".join(new_log_lines) + "\n"
            await _upload_file(
                client,
                session_id,
                server_index=file_server_index,
                path=log_path,
                content=updated_log,
                require_initialize=file_require_initialize,
            )

            # Step 2: generate detailed + human summary with links, scoped to last M sessions.
            log_entries = _parse_log_entries(updated_log)
            ordered_entries = sorted(log_entries, key=lambda item: item["accessed_utc"])
            recent_session_ids: List[str] = []
            for entry in reversed(ordered_entries):
                sid = entry["session_id"]
                if sid not in recent_session_ids:
                    recent_session_ids.append(sid)
                if len(recent_session_ids) >= report_window_sessions:
                    break
            recent_session_ids = list(reversed(recent_session_ids))
            window_entries = [entry for entry in ordered_entries if entry["session_id"] in set(recent_session_ids)]
            window_log_markdown = _build_log_markdown(topic, window_entries)

            recent_paths_payload = await _mcp_tool_call(
                client,
                session_id,
                server_index=file_server_index,
                require_initialize=file_require_initialize,
                name="search_paths",
                arguments={"query": "session_", "max_depth": 8},
            )
            recent_paths_text = _extract_tool_text(recent_paths_payload)
            detailed_report = await _ask_llm(
                client,
                session_id,
                (
                    f"{detailed_report_prompt}\n\nTopic: {topic}\nCurrent session: {ts}\n\n"
                    f"Report window sessions: {report_window_sessions}\n"
                    f"Included session IDs: {', '.join(recent_session_ids)}\n\n"
                    f"Downloaded files this session:\n" + "\n".join(downloaded_paths) + "\n\n"
                    "Recent session paths:\n"
                    f"{recent_paths_text}\n\n"
                    "Scoped access logs:\n"
                    f"{window_log_markdown}\n"
                ),
            )
            human_summary = await _ask_llm(
                client,
                session_id,
                (
                    f"{human_summary_prompt}\n\nTopic: {topic}\n\n"
                    f"Report window sessions: {report_window_sessions}\n"
                    f"Detailed report:\n{detailed_report}\n\n"
                    "Include links from logs to justify changes."
                ),
            )
            if "http" not in human_summary.lower():
                support_links = []
                for entry in window_entries:
                    url = str(entry.get("source_url") or "").strip()
                    if url.startswith("http") and url not in support_links:
                        support_links.append(url)
                if support_links:
                    human_summary = (
                        human_summary.rstrip()
                        + "\n\n## Supporting Links\n\n"
                        + "\n".join(f"- {url}" for url in support_links[:10])
                        + "\n"
                    )

            detailed_envelope = (
                f"# Detailed Change Report\n\n"
                f"- report_window_sessions: {report_window_sessions}\n"
                f"- included_session_ids: {', '.join(recent_session_ids)}\n\n"
                f"{detailed_report}"
            )
            await _upload_file(
                client,
                session_id,
                server_index=file_server_index,
                path=detailed_report_path,
                content=detailed_envelope,
                require_initialize=file_require_initialize,
            )
            await _upload_file(
                client,
                session_id,
                server_index=file_server_index,
                path=human_report_path,
                content=human_summary,
                require_initialize=file_require_initialize,
            )

            # Step 3: update suggestions.md.
            sites_latest = await _download_text(
                client, session_id, server_index=file_server_index, path=sites_path, require_initialize=file_require_initialize
            )
            suggestions = await _ask_llm(
                client,
                session_id,
                (
                    f"{suggestions_prompt}\n\nTopic: {topic}\n\nCurrent monitored sites:\n{sites_latest}\n\n"
                    "Return markdown table with columns: site, relevance, accuracy, rationale."
                ),
            )
            combined_suggestions = suggestions
            if suggestions_existing:
                combined_suggestions = suggestions_existing.rstrip() + "\n\n---\n\n## Latest Suggestions Run\n\n" + suggestions
            if applied_suggestions:
                applied_block = "\n\n## Applied to sites.md\n\n" + "\n".join(
                    [f"- {url} (already done)" for url in applied_suggestions]
                )
                combined_suggestions = combined_suggestions.rstrip() + applied_block + "\n"
            await _upload_file(
                client,
                session_id,
                server_index=file_server_index,
                path=suggestions_path,
                content=combined_suggestions,
                require_initialize=file_require_initialize,
            )

            # Final validation reads.
            detailed_text = await _download_text(
                client, session_id, server_index=file_server_index, path=detailed_report_path, require_initialize=file_require_initialize
            )
            human_text = await _download_text(
                client, session_id, server_index=file_server_index, path=human_report_path, require_initialize=file_require_initialize
            )
            suggestions_text = await _download_text(
                client, session_id, server_index=file_server_index, path=suggestions_path, require_initialize=file_require_initialize
            )
            final_log = await _download_text(
                client, session_id, server_index=file_server_index, path=log_path, require_initialize=file_require_initialize
            )
            detailed_l = detailed_text.lower()
            human_l = human_text.lower()
            suggestions_l = suggestions_text.lower()
            for token in required_tokens:
                token_l = token.lower().strip()
                if token_l and token_l not in human_l and token_l not in detailed_l and token_l not in suggestions_l:
                    raise RuntimeError(f"CRITICAL ERROR: required token missing in reports/suggestions: {token}")
            if ts not in final_log:
                raise RuntimeError("CRITICAL ERROR: log.md missing current session entries")
            if "http" not in human_text:
                raise RuntimeError("CRITICAL ERROR: human summary missing supporting links")
            if f"report_window_sessions: {report_window_sessions}" not in detailed_l:
                raise RuntimeError("CRITICAL ERROR: detailed report missing report-window metadata")
            if "included_session_ids:" not in detailed_l:
                raise RuntimeError("CRITICAL ERROR: detailed report missing included-session metadata")
            if not any(marker in human_l for marker in blog_style_markers):
                raise RuntimeError("CRITICAL ERROR: human summary does not match blog-style markers")
            latest_sites_md = await _download_text(
                client, session_id, server_index=file_server_index, path=sites_path, require_initialize=file_require_initialize
            )
            for applied_url in applied_suggestions:
                if applied_url not in latest_sites_md:
                    raise RuntimeError(f"CRITICAL ERROR: applied suggestion missing from sites.md: {applied_url}")
            if applied_suggestions and "already done" not in suggestions_l:
                raise RuntimeError("CRITICAL ERROR: suggestions.md missing 'already done' applied markers")
    finally:
        stop_api(cfg, env_file=env_file)
        if started_file_mcp:
            maybe_stop_file_mcp(cfg)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.llm, pytest.mark.mcp, pytest.mark.heavy]

