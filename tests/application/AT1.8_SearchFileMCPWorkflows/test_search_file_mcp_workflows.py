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
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx
import pytest

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import api_base_url, api_headers, start_api, stop_api, wait_for_api
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


def _parse_json_list(value: Any, key: str) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as e:
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list") from e
        if not isinstance(parsed, list):
            raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list")
        return [str(item) for item in parsed]
    raise RuntimeError(f"CRITICAL ERROR: {key} must be a JSON list")


def _extract_tool_text(result: Dict[str, Any]) -> str:
    text = ""
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text += str(item.get("text") or "")
    return text


def _run_cmd(cmd: List[str], timeout_seconds: float) -> None:
    subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
        text=True,
    )


def _wait_health(url: str, timeout_seconds: float, poll_seconds: float) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            resp = httpx.get(url, timeout=poll_seconds)
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(poll_seconds)
    raise RuntimeError(f"CRITICAL ERROR: File MCP not ready at {url}")


def _start_file_mcp(cfg: ConfigManager) -> None:
    if bool(cfg.get("chat_tests.at1_8.file_mcp.use_external_runtime") or False):
        return
    control_script = str(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.control_script"))
    runtime_env = str(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.env_path"))
    runtime_config = str(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.config_path"))
    runtime_defaults = str(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.defaults_path"))
    runtime_pidfile = str(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.pidfile"))
    timeout_seconds = float(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.control_timeout_seconds"))
    health_url = str(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.health_url"))
    ready_timeout_seconds = float(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.ready_timeout_seconds"))
    poll_seconds = float(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.ready_poll_seconds"))

    _run_cmd(
        [
            "bash",
            control_script,
            "--env",
            runtime_env,
            "--config",
            runtime_config,
            "--defaults",
            runtime_defaults,
            "--pidfile",
            runtime_pidfile,
            "start",
            "mcp",
        ],
        timeout_seconds,
    )
    _wait_health(health_url, ready_timeout_seconds, poll_seconds)


def _stop_file_mcp(cfg: ConfigManager) -> None:
    if bool(cfg.get("chat_tests.at1_8.file_mcp.use_external_runtime") or False):
        return
    control_script = str(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.control_script"))
    runtime_env = str(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.env_path"))
    runtime_config = str(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.config_path"))
    runtime_defaults = str(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.defaults_path"))
    runtime_pidfile = str(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.pidfile"))
    timeout_seconds = float(_require_cfg(cfg, "chat_tests.at1_8.file_mcp.control_timeout_seconds"))
    _run_cmd(
        [
            "bash",
            control_script,
            "--env",
            runtime_env,
            "--config",
            runtime_config,
            "--defaults",
            runtime_defaults,
            "--pidfile",
            runtime_pidfile,
            "stop",
            "mcp",
        ],
        timeout_seconds,
    )


async def _mcp_tools_call(
    client: httpx.AsyncClient,
    session_id: str,
    server_index: int,
    name: str,
    arguments: Dict[str, Any],
    require_initialize: bool,
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
        raise RuntimeError(f"CRITICAL ERROR: tool '{name}' returned isError=true")
    return payload


async def _upload_file(
    client: httpx.AsyncClient,
    session_id: str,
    file_server_index: int,
    path: str,
    raw_bytes: bytes,
    require_initialize: bool,
) -> Dict[str, Any]:
    encoded = base64.b64encode(raw_bytes).decode("ascii")
    resp = await client.post(
        f"/sessions/{session_id}/mcp/files/upload",
        json={
            "server_index": file_server_index,
            "path": path,
            "content_base64": encoded,
            "overwrite": True,
            "require_initialize": require_initialize,
        },
    )
    assert resp.status_code == 200
    payload = resp.json() or {}
    if int(payload.get("bytes_written") or 0) <= 0:
        raise RuntimeError("CRITICAL ERROR: upload wrote zero bytes")
    return payload


async def _download_file(
    client: httpx.AsyncClient,
    session_id: str,
    file_server_index: int,
    path: str,
    require_initialize: bool,
) -> bytes:
    resp = await client.post(
        f"/sessions/{session_id}/mcp/files/download",
        json={"server_index": file_server_index, "path": path, "require_initialize": require_initialize},
    )
    assert resp.status_code == 200
    payload = resp.json() or {}
    encoded = str(payload.get("content_base64") or "")
    if not encoded:
        raise RuntimeError("CRITICAL ERROR: download response missing content_base64")
    return base64.b64decode(encoded)
@pytest.mark.AT
@pytest.mark.mcp
@pytest.mark.req("FR-008")


@pytest.mark.asyncio
async def test_at1_8_search_file_mcp_workflows(env_file):
    cfg = ConfigManager(env_file=env_file)
    curl_ollama_tags(cfg)
    _start_file_mcp(cfg)
    start_api(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)

        base_url = api_base_url(cfg)
        timeout_seconds = float(_require_cfg(cfg, "client_api.request_timeout_seconds"))

        search_server_index = int(_require_cfg(cfg, "mcp.at1_8.search_server_index"))
        file_server_index = int(_require_cfg(cfg, "mcp.at1_8.file_server_index"))
        search_require_initialize = bool(cfg.get("mcp.at1_8.require_initialize_search") or False)
        file_require_initialize = bool(cfg.get("mcp.at1_8.require_initialize_file") or False)
        protocol_version = str(_require_cfg(cfg, "mcp.defaults.protocol_version"))

        search_tool_name = str(_require_cfg(cfg, "mcp.at1_8.search_tool_name")).strip()
        search_args = _parse_json_obj(_require_cfg(cfg, "mcp.at1_8.search_args"), "mcp.at1_8.search_args")
        site_list = _parse_json_list(_require_cfg(cfg, "mcp.at1_8.site_list"), "mcp.at1_8.site_list")

        summary_prompt = str(_require_cfg(cfg, "chat_tests.at1_8.summary_prompt"))
        final_summary_prompt = str(_require_cfg(cfg, "chat_tests.at1_8.final_summary_prompt"))
        required_response_tokens = _parse_json_list(
            _require_cfg(cfg, "chat_tests.at1_8.required_response_tokens"),
            "chat_tests.at1_8.required_response_tokens",
        )

        file_root = str(_require_cfg(cfg, "mcp.at1_8.file_root")).rstrip("/")
        pdf_base64 = str(_require_cfg(cfg, "mcp.at1_8.pdf_base64"))
        search_content_args = _parse_json_obj(
            _require_cfg(cfg, "mcp.at1_8.search_content_args"), "mcp.at1_8.search_content_args"
        )
        search_paths_args = _parse_json_obj(
            _require_cfg(cfg, "mcp.at1_8.search_paths_args"), "mcp.at1_8.search_paths_args"
        )

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        summary_md_path = f"{file_root}/at1_8_summary_{ts}.md"
        summary_html_path = f"{file_root}/at1_8_summary_{ts}.html"
        summary_pdf_path = f"{file_root}/at1_8_summary_{ts}.pdf"
        sites_file_path = f"{file_root}/at1_8_sites_{ts}.txt"
        search_results_md_path = f"{file_root}/at1_8_search_results_{ts}.md"
        final_summary_md_path = f"{file_root}/at1_8_final_summary_{ts}.md"

        async with httpx.AsyncClient(base_url=base_url, headers=api_headers(cfg), timeout=timeout_seconds) as client:
            session_resp = await client.post("/sessions", json={"metadata": {"suite": "at1.8"}})
            assert session_resp.status_code == 200
            session_id = str(session_resp.json().get("session_id") or "")
            assert session_id

            search_exec_resp = await client.post(
                f"/sessions/{session_id}/mcp/execute",
                json={
                    "server_index": search_server_index,
                    "require_initialize": search_require_initialize,
                    "protocol_version": protocol_version,
                    "steps": [
                        {"method": "tools/list"},
                        {
                            "method": "tools/call",
                            "params": {"name": search_tool_name, "arguments": search_args},
                        },
                    ],
                },
            )
            assert search_exec_resp.status_code == 200
            search_exec_results = search_exec_resp.json().get("results") or []
            if len(search_exec_results) < 2:
                raise RuntimeError("CRITICAL ERROR: Search MCP execute returned insufficient steps")
            if not search_exec_results[0].get("ok"):
                raise RuntimeError("CRITICAL ERROR: Search MCP tools/list failed")
            if not search_exec_results[1].get("ok"):
                raise RuntimeError("CRITICAL ERROR: Search MCP tool call failed")

            file_tools_resp = await client.post(
                f"/sessions/{session_id}/mcp/tools/list",
                json={"server_index": file_server_index, "require_initialize": file_require_initialize},
            )
            assert file_tools_resp.status_code == 200

            search_result = search_exec_results[1].get("result") or {}
            search_text = _extract_tool_text(search_result)
            if not search_text.strip():
                raise RuntimeError("CRITICAL ERROR: Search MCP returned empty text")

            summary_resp = await client.post(
                f"/sessions/{session_id}/messages",
                json={"content": f"{summary_prompt}\n\nSearch results:\n{search_text}", "stream": False},
            )
            assert summary_resp.status_code == 200
            summary_text = str(summary_resp.json().get("content") or "")
            if not summary_text.strip():
                raise RuntimeError("CRITICAL ERROR: LLM summary response was empty")
            for token in required_response_tokens:
                if token and token not in summary_text:
                    raise RuntimeError(f"CRITICAL ERROR: summary missing expected token '{token}'")

            await _upload_file(
                client,
                session_id,
                file_server_index,
                summary_md_path,
                summary_text.encode("utf-8"),
                file_require_initialize,
            )

            html_text = (
                "<!doctype html><html><head><meta charset='utf-8'><title>AT1.8 Summary</title></head>"
                f"<body><h1>Summary</h1><pre>{summary_text}</pre></body></html>"
            )
            await _upload_file(
                client,
                session_id,
                file_server_index,
                summary_html_path,
                html_text.encode("utf-8"),
                file_require_initialize,
            )

            await _upload_file(
                client,
                session_id,
                file_server_index,
                summary_pdf_path,
                base64.b64decode(pdf_base64),
                file_require_initialize,
            )

            md_download = await _download_file(
                client, session_id, file_server_index, summary_md_path, file_require_initialize
            )
            if summary_text[:40].encode("utf-8") not in md_download:
                raise RuntimeError("CRITICAL ERROR: downloaded md content does not match summary")

            html_download = await _download_file(
                client, session_id, file_server_index, summary_html_path, file_require_initialize
            )
            if b"<html" not in html_download.lower():
                raise RuntimeError("CRITICAL ERROR: downloaded html file missing html marker")

            pdf_download = await _download_file(
                client, session_id, file_server_index, summary_pdf_path, file_require_initialize
            )
            if not pdf_download.startswith(b"%PDF"):
                raise RuntimeError("CRITICAL ERROR: downloaded pdf file missing PDF header")

            site_list_text = "\n".join(site_list) + "\n"
            await _upload_file(
                client,
                session_id,
                file_server_index,
                sites_file_path,
                site_list_text.encode("utf-8"),
                file_require_initialize,
            )

            read_sites = await _mcp_tools_call(
                client,
                session_id,
                file_server_index,
                "read_file",
                {"path": sites_file_path},
                file_require_initialize,
            )
            if site_list[0] not in _extract_tool_text(read_sites):
                raise RuntimeError("CRITICAL ERROR: site list file content mismatch")

            results_doc = (
                f"# Search Results ({ts})\n\n"
                f"Topic Query: {search_args.get('query')}\n\n"
                f"Sites:\n- " + "\n- ".join(site_list) + "\n\n"
                f"Raw Search Output:\n\n{search_text}\n"
            )
            await _upload_file(
                client,
                session_id,
                file_server_index,
                search_results_md_path,
                results_doc.encode("utf-8"),
                file_require_initialize,
            )

            search_paths_payload = dict(search_paths_args)
            search_paths_payload["query"] = ts
            paths_result = await _mcp_tools_call(
                client,
                session_id,
                file_server_index,
                "search_paths",
                search_paths_payload,
                file_require_initialize,
            )
            if ts not in _extract_tool_text(paths_result):
                raise RuntimeError("CRITICAL ERROR: search_paths did not find timestamped result file")

            search_content_payload = dict(search_content_args)
            search_content_payload["query"] = str(search_args.get("query") or "")
            content_result = await _mcp_tools_call(
                client,
                session_id,
                file_server_index,
                "search_content",
                search_content_payload,
                file_require_initialize,
            )
            content_text = _extract_tool_text(content_result)
            if not content_text.strip():
                raise RuntimeError("CRITICAL ERROR: search_content returned empty result")

            final_summary_resp = await client.post(
                f"/sessions/{session_id}/messages",
                json={"content": f"{final_summary_prompt}\n\nSearch content hits:\n{content_text}", "stream": False},
            )
            assert final_summary_resp.status_code == 200
            final_summary_text = str(final_summary_resp.json().get("content") or "")
            if not final_summary_text.strip():
                raise RuntimeError("CRITICAL ERROR: final LLM summary response was empty")

            await _upload_file(
                client,
                session_id,
                file_server_index,
                final_summary_md_path,
                final_summary_text.encode("utf-8"),
                file_require_initialize,
            )

            final_download = await _download_file(
                client,
                session_id,
                file_server_index,
                final_summary_md_path,
                file_require_initialize,
            )
            if not final_download.strip():
                raise RuntimeError("CRITICAL ERROR: final summary download is empty")
    finally:
        stop_api(cfg, env_file=env_file)
        _stop_file_mcp(cfg)

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.application, pytest.mark.llm, pytest.mark.mcp, pytest.mark.heavy]
