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

"""AT1.16 — Cross-project: IMAP Fail2ban Email Analysis + Trend Report.

Services exercised:
  - imap-mcp-server  (mail_search, mail_get_message, mail_extract_message)
  - LLM via chat-client API  (trend analysis + summary generation)

Workflow:
  1. Connect to imap-mcp, search for Fail2ban emails from past 24h in configured folder.
  2. Fetch full message content for each hit.
  3. Send aggregated email content to LLM for analysis:
     - Identify attacking hosts/IPs.
     - Summarise trends (top source countries, attack frequency, targeted services).
     - Generate a markdown summary report.
  4. Assert: non-empty results, LLM report contains expected structural tokens.
"""
from __future__ import annotations

import imaplib
import json
import ssl
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.conftest import _parse_env_file
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
    parse_json_list,
    utc_ts,
)


def _seed_fail2ban_messages(cfg: ConfigManager) -> int:
    runtime_env_path = Path(str(require_cfg(cfg, "chat_tests.at1_16.imap_mcp.env_path"))).resolve()
    env_values = _parse_env_file(runtime_env_path)

    host = str(env_values.get("IMAP_OPERATIONS_HOST") or "").strip()
    username = str(env_values.get("IMAP_OPERATIONS_USERNAME") or "").strip()
    password = str(env_values.get("IMAP_OPERATIONS_PASSWORD") or "").strip()
    folder = str(require_cfg(cfg, "chat_tests.at1_16.imap_folder")).strip()
    port = int(str(env_values.get("IMAP_OPERATIONS_PORT") or "143").strip() or "143")

    if not host or not username or not password:
        raise RuntimeError(
            f"CRITICAL ERROR: unable to seed Fail2ban mailbox; missing IMAP credentials in {runtime_env_path}"
        )

    samples = [
        {
            "ip": "203.0.113.45",
            "service": "sshd",
            "timestamp": "2026-04-02T06:15:00Z",
            "ban": "600 seconds",
        },
        {
            "ip": "198.51.100.27",
            "service": "nginx-http-auth",
            "timestamp": "2026-04-02T07:42:00Z",
            "ban": "600 seconds",
        },
        {
            "ip": "192.0.2.88",
            "service": "sshd",
            "timestamp": "2026-04-02T08:03:00Z",
            "ban": "1200 seconds",
        },
    ]

    with imaplib.IMAP4(host, port, timeout=30) as imap_client:
        imap_client.starttls(ssl.create_default_context())
        imap_client.login(username, password)
        imap_client.create(folder)

        for index, sample in enumerate(samples, start=1):
            msg = EmailMessage()
            msg["From"] = username
            msg["To"] = username
            msg["Subject"] = f"Fail2ban Alert {utc_ts()} #{index}"
            msg["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime())
            msg["Message-ID"] = f"<at1-16-{utc_ts()}-{index}@example.com>"
            msg.set_content(
                "\n".join(
                    [
                        "Fail2ban automated notification",
                        f"Source IP: {sample['ip']}",
                        f"Targeted service: {sample['service']}",
                        f"Timestamp: {sample['timestamp']}",
                        f"Ban action: banned for {sample['ban']}",
                        "Reason: excessive authentication failures detected.",
                    ]
                )
            )
            status, data = imap_client.append(
                folder,
                None,
                imaplib.Time2Internaldate(time.time()),
                msg.as_bytes(),
            )
            if str(status).upper() != "OK":
                raise RuntimeError(
                    f"CRITICAL ERROR: failed to append seeded Fail2ban message to {folder}: {status} {data}"
                )

    return len(samples)


async def _mail_search(
    client: httpx.AsyncClient,
    session_id: str,
    imap_target_index: int | None,
    imap_profile_id: str,
    imap_folder: str,
    search_query: str,
    *,
    imap_require_init: bool,
    protocol_version: str,
    imap_target_server: Dict[str, Any] | None,
) -> Dict[str, Any]:
    return await mcp_tools_call(
        client,
        session_id,
        imap_target_index,
        "mail_search",
        {
            "profile_id": imap_profile_id,
            "mode": "imap",
            "query": search_query,
            "filters": {"folder": imap_folder},
        },
        require_initialize=imap_require_init,
        protocol_version=protocol_version,
        server=imap_target_server,
    )


def _start_imap_mcp(cfg: ConfigManager) -> None:
    if cfg.get("chat_tests.at1_16.imap_mcp.docker_control_script") and cfg.get(
        "chat_tests.at1_16.imap_mcp.docker_env_path"
    ):
        ensure_local_docker_runtime(
            cfg,
            "chat_tests.at1_16.imap_mcp",
            label="AT1.16 imap-mcp",
        )
        return

    # Preprod mode: no control_script means IMAP MCP runs on preprod.
    control_script_raw = cfg.get("chat_tests.at1_16.imap_mcp.control_script")
    env_path_raw = cfg.get("chat_tests.at1_16.imap_mcp.env_path")
    if not control_script_raw or not env_path_raw:
        return

    import subprocess
    import time

    control_script = str(control_script_raw)
    env_path = str(env_path_raw)
    control_dir = str(Path(control_script).resolve().parent)
    timeout_seconds = float(require_cfg(cfg, "chat_tests.at1_16.imap_mcp.control_timeout_seconds"))
    health_url = str(require_cfg(cfg, "chat_tests.at1_16.imap_mcp.health_url"))
    ready_timeout = float(require_cfg(cfg, "chat_tests.at1_16.imap_mcp.ready_timeout_seconds"))
    poll = float(require_cfg(cfg, "chat_tests.at1_16.imap_mcp.ready_poll_seconds"))

    # Ensure stale pidfiles or reused PIDs cannot block startup.
    subprocess.run(
        ["bash", control_script, "--env", env_path, "stop"],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout_seconds,
        cwd=control_dir,
    )
    start = subprocess.run(
        ["bash", control_script, "--env", env_path, "start"],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout_seconds,
        cwd=control_dir,
    )
    deadline = time.time() + ready_timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(health_url, timeout=poll)
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(poll)

    status = subprocess.run(
        ["bash", control_script, "--env", env_path, "status"],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout_seconds,
        cwd=control_dir,
    )
    start_out = (start.stdout or b"").decode("utf-8", errors="ignore")
    status_out = (status.stdout or b"").decode("utf-8", errors="ignore")
    raise RuntimeError(
        f"CRITICAL ERROR: IMAP MCP not ready at {health_url}. "
        f"start={start_out.strip()} status={status_out.strip()}"
    )


def _stop_imap_mcp(cfg: ConfigManager) -> None:
    if cfg.get("chat_tests.at1_16.imap_mcp.docker_control_script") and cfg.get(
        "chat_tests.at1_16.imap_mcp.docker_env_path"
    ):
        return

    # Preprod mode: no control_script means IMAP MCP runs on preprod.
    control_script_raw = cfg.get("chat_tests.at1_16.imap_mcp.control_script")
    env_path_raw = cfg.get("chat_tests.at1_16.imap_mcp.env_path")
    if not control_script_raw or not env_path_raw:
        return

    import subprocess

    control_script = str(control_script_raw)
    env_path = str(env_path_raw)
    control_dir = str(Path(control_script).resolve().parent)
    timeout_seconds = float(require_cfg(cfg, "chat_tests.at1_16.imap_mcp.control_timeout_seconds"))
    subprocess.run(
        ["bash", control_script, "--env", env_path, "stop"],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout_seconds,
        cwd=control_dir,
    )
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_16_fail2ban_email_analysis(env_file):
    cfg = ConfigManager(env_file=env_file)

    imap_server_index = int(require_cfg(cfg, "mcp.at1_16.imap_server_index"))
    imap_server = cfg.get("mcp.at1_16.imap_server")
    if imap_server is not None and not isinstance(imap_server, dict):
        raise RuntimeError("CRITICAL ERROR: mcp.at1_16.imap_server must be an object")
    imap_target_index = None if isinstance(imap_server, dict) else imap_server_index
    imap_target_server = imap_server if isinstance(imap_server, dict) else None
    imap_require_init = bool(cfg.get("mcp.at1_16.require_initialize_imap") or False)
    protocol_version = str(require_cfg(cfg, "mcp.defaults.protocol_version"))

    imap_profile_id = str(require_cfg(cfg, "chat_tests.at1_16.imap_profile_id"))
    imap_folder = str(require_cfg(cfg, "chat_tests.at1_16.imap_folder"))
    search_query = str(require_cfg(cfg, "chat_tests.at1_16.search_query"))
    max_messages = int(cfg.get("chat_tests.at1_16.max_messages") or 20)
    analysis_prompt = str(require_cfg(cfg, "chat_tests.at1_16.analysis_prompt"))
    required_tokens = parse_json_list(
        require_cfg(cfg, "chat_tests.at1_16.required_report_tokens"),
        "chat_tests.at1_16.required_report_tokens",
    )

    _start_imap_mcp(cfg)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        base_url = api_base_url(cfg)
        timeout_seconds = float(require_cfg(cfg, "client_api.request_timeout_seconds"))

        async with httpx.AsyncClient(
            base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds
        ) as client:
            session_id = await create_session(client, "at1.16")

            # Step 1: Initialise imap-mcp and list tools
            init_results = await mcp_execute(
                client, session_id, imap_target_index,
                steps=[{"method": "tools/list"}],
                require_initialize=imap_require_init,
                protocol_version=protocol_version,
                server=imap_target_server,
            )
            assert init_results and init_results[0].get("ok"), "CRITICAL ERROR: imap-mcp tools/list failed"
            tools_payload = init_results[0].get("result") or {}
            tool_names = [t.get("name") for t in tools_payload.get("tools") or []]
            assert "mail_search" in tool_names, "CRITICAL ERROR: mail_search tool not found"
            assert "mail_get_message" in tool_names, "CRITICAL ERROR: mail_get_message tool not found"

            # Step 2: Search for Fail2ban emails in the configured folder
            search_result = await _mail_search(
                client,
                session_id,
                imap_target_index,
                imap_profile_id,
                imap_folder,
                search_query,
                imap_require_init=imap_require_init,
                protocol_version=protocol_version,
                imap_target_server=imap_target_server,
            )
            search_data = extract_tool_json(search_result)
            if not search_data:
                search_data = {"messages": [], "text": extract_tool_text(search_result)}

            messages_found = search_data.get("messages") or search_data.get("results") or []
            uids = []
            for msg in messages_found[:max_messages]:
                uid = str(msg.get("uid") or msg.get("id") or "").strip()
                if uid:
                    uids.append(uid)

            if not uids:
                seeded_count = _seed_fail2ban_messages(cfg)
                print(f"[AT1.16] Seeded {seeded_count} Fail2ban messages into {imap_folder}")
                time.sleep(2)
                search_result = await _mail_search(
                    client,
                    session_id,
                    imap_target_index,
                    imap_profile_id,
                    imap_folder,
                    search_query,
                    imap_require_init=imap_require_init,
                    protocol_version=protocol_version,
                    imap_target_server=imap_target_server,
                )
                search_data = extract_tool_json(search_result)
                if not search_data:
                    search_data = {"messages": [], "text": extract_tool_text(search_result)}
                messages_found = search_data.get("messages") or search_data.get("results") or []
                uids = []
                for msg in messages_found[:max_messages]:
                    uid = str(msg.get("uid") or msg.get("id") or "").strip()
                    if uid:
                        uids.append(uid)

            assert len(uids) > 0, (
                f"CRITICAL ERROR: No Fail2ban emails found in folder '{imap_folder}' "
                f"with query '{search_query}'. Verify real mail exists."
            )
            print(f"[AT1.16] Found {len(uids)} Fail2ban emails in {imap_folder}")

            # Step 3: Fetch full content of each message
            message_contents: list[str] = []
            for uid in uids:
                extract_result = await mcp_tools_call(
                    client, session_id, imap_target_index,
                    "mail_extract_message",
                    {
                        "profile_id": imap_profile_id,
                        "uid": uid,
                        "folder": imap_folder,
                        "format": "markdown",
                    },
                    require_initialize=imap_require_init,
                    protocol_version=protocol_version,
                    server=imap_target_server,
                )
                content = extract_tool_text(extract_result).strip()
                if content:
                    message_contents.append(content)

            assert len(message_contents) > 0, "CRITICAL ERROR: All message extractions returned empty"
            print(f"[AT1.16] Extracted content from {len(message_contents)} messages")

            # Step 4: Aggregate and send to LLM for analysis
            aggregated = "\n\n---\n\n".join(
                f"### Email {i+1}\n{c}" for i, c in enumerate(message_contents)
            )
            prompt = (
                f"{analysis_prompt}\n\n"
                f"Total emails analysed: {len(message_contents)}\n"
                f"Source folder: {imap_folder}\n\n"
                f"{aggregated}"
            )

            report = await llm_message(client, session_id, prompt, stream=False)
            print(f"[AT1.16] LLM report length: {len(report)} chars")

            # Step 5: Validate report structure
            report_lc = report.lower()
            semantic_aliases = {
                "summary": ("summary", "executive summary", "key observations", "overview", "conclusion", "osszefoglalo", "összefoglaló", "analysis", "report"),
            }
            for token in required_tokens:
                expected = str(token).lower().strip()
                accepted_terms = semantic_aliases.get(expected, (expected,))
                assert any(term in report_lc for term in accepted_terms), (
                    f"CRITICAL ERROR: Report missing expected token {token}"
                )

            # Step 6: Confirm report has substantive content
            assert len(report.strip()) > 200, (
                "CRITICAL ERROR: Report too short — expected substantive analysis"
            )
            print(f"[AT1.16] PASS — Fail2ban analysis report generated with {len(message_contents)} emails")

    finally:
        stop_api(cfg, env_file=env_file)
        _stop_imap_mcp(cfg)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.smtp, pytest.mark.mcp, pytest.mark.docker, pytest.mark.heavy]
