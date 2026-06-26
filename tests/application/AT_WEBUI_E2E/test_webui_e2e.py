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

"""W28A-408-D browser E2E coverage for the chat-client WebUI.

Description:
    Executes real browser flows against the local chat-client runtime using
    Playwright and the resolved `--env` profile. This covers the current WebUI
    surface honestly: present flows are asserted through DOM state, while
    missing/broken flows fail with explicit product-gap reasons.

Related requirements/tasks/architecture/tests:
    - Task: W28A-408-D
    - Tests: T1..T10 from AGENT-INSTRUCTION-W28A-408-D-CHAT-CLIENT-WEBUI-E2E.md
    - Existing system coverage: ST1.14_WebUIFlow

Recent change history:
    - 2026-03-26: Initial Playwright E2E coverage for W28A-408-D.
"""

from __future__ import annotations

import os
import json
import re
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

import httpx
import pytest
from playwright.sync_api import Browser, Page, Playwright, expect, sync_playwright

from cloud_dog_chat_client.config import ConfigManager
from tests.helpers.api_server import (
    api_base_url,
    api_headers,
    start_all,
    stop_all,
    wait_for_api,
    wait_for_base_url,
    web_base_url,
)

SCREENSHOT_DIR = Path("working/w28a-408-d-screenshots")
PLAYWRIGHT_EVIDENCE_DIR = Path(
    os.environ.get("CHAT_CLIENT_PLAYWRIGHT_EVIDENCE_DIR", "working/w28a-408-d-playwright")
)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Expose per-phase test reports to fixtures for screenshot capture."""

    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


@pytest.fixture(scope="module")
def runtime_cfg(env_file: str) -> Iterator[ConfigManager]:
    """Start the real four-server runtime and expose the resolved config."""

    cfg = ConfigManager(env_file=env_file)
    start_all(cfg, env_file=env_file)
    try:
        wait_for_api(cfg)
        wait_for_base_url(cfg, web_base_url(cfg))
        yield cfg
    finally:
        stop_all(cfg, env_file=env_file)


@pytest.fixture(scope="module")
def base_url(runtime_cfg: ConfigManager) -> str:
    """Return the live browser WebUI base URL."""

    return web_base_url(runtime_cfg)


@pytest.fixture(scope="module")
def auth_header_name() -> str:
    """Resolve the configured API-key header name."""

    return str(os.environ.get("CLOUD_DOG__CLIENT_API__API_KEY_HEADER") or "X-API-Key").strip()


@pytest.fixture(scope="module")
def api_key() -> str:
    """Resolve the browser login/API key from the selected env profile."""

    key = str(os.environ.get("CLOUD_DOG__CLIENT_API__API_KEY") or "").strip()
    if not key:
        pytest.fail("CRITICAL ERROR: client_api.api_key resolved empty from --env")
    return key


@pytest.fixture(scope="module")
def api_client(runtime_cfg: ConfigManager, auth_header_name: str, api_key: str) -> Iterator[httpx.Client]:
    """Provide an authenticated HTTP client for cleanup and setup helpers."""

    with httpx.Client(
        base_url=api_base_url(runtime_cfg),
        headers=api_headers(runtime_cfg) | {auth_header_name: api_key},
        timeout=60.0,
        follow_redirects=True,
    ) as client:
        yield client


@pytest.fixture(scope="module")
def playwright_instance() -> Iterator[Playwright]:
    """Start one Playwright instance for the module."""

    with sync_playwright() as playwright:
        yield playwright


@pytest.fixture(scope="module")
def browser(playwright_instance: Playwright) -> Iterator[Browser]:
    """Launch a real Chromium browser for the module."""

    browser = playwright_instance.chromium.launch(headless=True)
    try:
        yield browser
    finally:
        browser.close()


@pytest.fixture()
def page(request: pytest.FixtureRequest, browser: Browser) -> Iterator[Page]:
    """Provide an isolated page, collect page errors, and save a screenshot on failure.

    W28C-1715 §4a console-error-gate: registers page.on('pageerror') per PS-77 §1.8.
    All page errors are collected; the test asserts zero errors via the ``console_errors``
    fixture.  A screenshot is always captured on test failure.
    """

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    PLAYWRIGHT_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)
    context = browser.new_context(viewport={"width": 1440, "height": 1200})
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    console_events: list[dict[str, str]] = []
    network_failures: list[dict[str, str]] = []
    page_errors: list[str] = []
    page.on(
        "console",
        lambda msg: console_events.append(
            {"type": msg.type, "text": msg.text, "location": str(msg.location)}
        ),
    )
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    page.on(
        "requestfailed",
        lambda request: network_failures.append(
            {
                "url": request.url,
                "method": request.method,
                "failure": str(request.failure or ""),
            }
        ),
    )
    # Store error list on the page object so tests can access via fixture.
    page.__dict__["_page_errors"] = page_errors  # type: ignore[attr-defined]
    try:
        yield page
    finally:
        rep = getattr(request.node, "rep_call", None)
        if rep and rep.failed:
            target = SCREENSHOT_DIR / f"{request.node.name}.png"
            page.screenshot(path=str(target), full_page=True)
        page.screenshot(path=str(PLAYWRIGHT_EVIDENCE_DIR / f"{safe_name}-final.png"), full_page=True)
        context.tracing.stop(path=str(PLAYWRIGHT_EVIDENCE_DIR / f"{safe_name}-trace.zip"))
        (PLAYWRIGHT_EVIDENCE_DIR / f"{safe_name}-browser-events.json").write_text(
            json.dumps(
                {
                    "console": console_events,
                    "page_errors": page_errors,
                    "network_failures": network_failures,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        context.close()


@pytest.fixture()
def console_errors(page: "Page") -> list[str]:
    """Expose the page-error list collected by the ``page`` fixture.

    Tests that want a hard console-error gate should call::

        assert console_errors == [], f"Page JS errors: {console_errors}"

    This fixture is intentionally separate so callers can inspect errors
    before asserting, or selectively filter known-benign errors.
    """
    return page.__dict__.get("_page_errors", [])


def _login(page: Page, base_url: str, api_key: str) -> None:
    """Authenticate through the real browser login page."""

    page.goto(f"{base_url}/login", wait_until="domcontentloaded", timeout=120_000)
    dashboard_heading = page.get_by_role("heading", name=re.compile(r"^dashboard$", re.IGNORECASE))
    username = page.locator("#loginUsername")
    password = page.locator("#loginPassword")
    api_key_field = page.locator("#api-key")

    page.wait_for_timeout(1_500)
    if dashboard_heading.count():
        return

    if username.count():
        page.wait_for_timeout(500)
        page.fill("#loginUsername", "admin")
        page.fill("#loginPassword", "OrangeRiverTable")
    elif api_key_field.count():
        page.fill("#api-key", api_key)
    else:
        _fail(page, "t1-login-form-missing", "T1 FAIL: no recognised login form rendered on /login.")

    page.get_by_role("button", name="Sign in").click()
    page.wait_for_timeout(2_000)
    expect(page.get_by_role("link", name="Settings")).to_be_visible(timeout=30_000)
    current_path = urlparse(page.url).path.rstrip("/") or "/"
    if current_path not in {"/", "/chat", "/ui", "/dashboard"}:
        _fail(
            page,
            "t1-login-unexpected-destination",
            f"T1 FAIL: login succeeded but landed on unexpected path {current_path!r}.",
        )


def _body_text(page: Page) -> str:
    """Return the visible page body text for broad DOM assertions."""

    return page.locator("body").inner_text(timeout=30_000)


def _open_nav(page: Page, name: str) -> None:
    """Navigate through the SPA itself instead of direct deep-link reloads."""

    page.get_by_role("link", name=name).click()
    page.wait_for_timeout(1_500)


def _session_sidebar(page: Page):
    """Return the session sidebar container used by the SPA."""

    return page.locator("aside").first


def _fail(page: Page, slug: str, message: str) -> None:
    """Capture one screenshot and fail with an exact reason."""

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / f"{slug}.png"), full_page=True)
    pytest.fail(message)


def _extract_active_session_id(page: Page) -> str:
    """Parse the active session id from the visible chat page."""

    match = re.search(r"Active session:\s*([0-9a-f-]{36}|none)", _body_text(page), flags=re.IGNORECASE)
    if not match:
        _fail(page, "t6-t7-active-session-missing", "T6/T7 precondition failed: active session marker not visible in UI")
    return match.group(1)


def _create_session_via_api(client: httpx.Client, title: str) -> str:
    """Create one session for UI tests through the real API surface."""

    response = client.post("/sessions", json={"metadata": {"title": title}})
    assert response.status_code == 200, response.text
    payload = response.json()
    session_id = str(payload.get("session_id") or "").strip()
    assert session_id, payload
    return session_id


def _delete_session_via_api(client: httpx.Client, session_id: str) -> None:
    """Delete one session through the real API surface."""

    try:
        response = client.delete(f"/sessions/{session_id}")
    except httpx.ConnectError:
        return
    assert response.status_code in {200, 404}, response.text


def _list_session_ids_via_api(client: httpx.Client) -> set[str]:
    """Return the current session ids through the real API surface."""

    response = client.get("/sessions")
    assert response.status_code == 200, response.text
    rows = (response.json() or {}).get("sessions") or []
    return {str(row.get("session_id") or row.get("id") or "").strip() for row in rows if row}
@pytest.mark.AT
@pytest.mark.webui
@pytest.mark.req("FR-008")
@pytest.mark.req("FR-015")


def test_t1_api_key_login(page: Page, base_url: str, api_key: str) -> None:
    """T1 — API Key Login."""

    _login(page, base_url, api_key)
    body = _body_text(page)
    assert "Cloud-Dog : Chat client" in body
    assert "Dashboard" in body
    assert "Chat" in body
    assert "Sessions" in body
    assert "Settings" in body
    assert (urlparse(page.url).path.rstrip("/") or "/") in {"/", "/dashboard"}
@pytest.mark.AT
@pytest.mark.webui
@pytest.mark.req("FR-008")


def test_t2_user_crud_admin(page: Page, base_url: str, api_key: str) -> None:
    """T2 — User CRUD (admin)."""

    _login(page, base_url, api_key)
    if page.get_by_role("link", name="Users").count() == 0:
        _fail(page, "t2-user-crud-missing", "T2 FAIL: WebUI does not expose a Users/Admin page required for user CRUD.")
@pytest.mark.AT
@pytest.mark.webui
@pytest.mark.req("FR-008")


def test_t3_group_crud_admin(page: Page, base_url: str, api_key: str) -> None:
    """T3 — Group CRUD (admin)."""

    _login(page, base_url, api_key)
    if page.get_by_role("link", name="Groups").count() == 0:
        _fail(page, "t3-group-crud-missing", "T3 FAIL: WebUI does not expose a Groups page required for group CRUD.")
@pytest.mark.AT
@pytest.mark.webui
@pytest.mark.req("FR-008")


def test_t4_api_key_crud_admin(page: Page, base_url: str, api_key: str) -> None:
    """T4 — API Key CRUD (admin)."""

    _login(page, base_url, api_key)
    body = _body_text(page)
    if "Create API key" not in body and "Revoke API key" not in body and "API Keys" not in body:
        _fail(page, "t4-api-key-crud-missing", "T4 FAIL: WebUI does not expose API key CRUD controls; only masked key display is present.")
@pytest.mark.AT
@pytest.mark.webui
@pytest.mark.req("FR-008")


def test_t5_rbac_enforcement(page: Page, base_url: str, api_key: str) -> None:
    """T5 — RBAC Enforcement."""

    _login(page, base_url, api_key)
    if page.get_by_role("link", name="Users").count() == 0:
        _fail(page, "t5-rbac-missing", "T5 FAIL: RBAC browser flow cannot run because the WebUI lacks user/role management surfaces.")
@pytest.mark.AT
@pytest.mark.webui
@pytest.mark.req("FR-008")
@pytest.mark.req("FR-014")


def test_t6_create_chat_session(page: Page, base_url: str, api_key: str, api_client: httpx.Client) -> None:
    """T6 — Create Chat Session."""

    before_ids = _list_session_ids_via_api(api_client)
    session_id = ""
    _login(page, base_url, api_key)
    _open_nav(page, "Chat")
    page.get_by_role("button", name="New session", exact=True).click()
    page.wait_for_timeout(2_000)
    created_ids = _list_session_ids_via_api(api_client) - before_ids
    if not created_ids:
        _fail(page, "t6-session-not-created", "T6 FAIL: clicking New session did not create a backend session.")
    session_id = sorted(created_ids)[0]
    try:
        _open_nav(page, "Chat")
        message_box = page.get_by_placeholder(
            "Type your message. Press Enter to send, Shift+Enter for a new line."
        )
        token = f"W28A408_OK_{int(time.time())}"
        prompt = f"Reply with exactly {token}"
        message_box.fill(prompt)
        with page.expect_request(lambda request: request.method == "POST" and "/messages" in request.url):
            page.get_by_role("button", name="Send").click()
        expect(page.get_by_text(prompt)).to_be_visible(timeout=30_000)

        deadline = time.time() + 180
        while time.time() < deadline:
            if token in _body_text(page):
                break
            page.wait_for_timeout(5_000)
        else:
            _fail(page, "t6-chat-response-timeout", "T6 FAIL: assistant response token did not appear on screen within 180 seconds.")
    finally:
        if session_id and session_id.lower() != "none":
            _delete_session_via_api(api_client, session_id)
@pytest.mark.AT
@pytest.mark.webui
@pytest.mark.req("FR-008")
@pytest.mark.req("FR-016")


def test_t7_session_history(page: Page, base_url: str, api_key: str, api_client: httpx.Client) -> None:
    """T7 — Session History."""

    title = f"w28a408-history-{int(time.time())}"
    session_id = _create_session_via_api(api_client, title)
    try:
        _login(page, base_url, api_key)
        _open_nav(page, "Sessions")
        session_button = page.get_by_role("button", name=title, exact=True)
        expect(session_button).to_be_visible(timeout=30_000)
        session_button.click()
        page.wait_for_timeout(2_000)
        assert session_id in _body_text(page)
        _delete_session_via_api(api_client, session_id)
        session_id = ""
        page.reload()
        _open_nav(page, "Sessions")
        deadline = time.time() + 30
        while time.time() < deadline:
            text_count = page.get_by_text(title, exact=True).count()
            if text_count == 0:
                break
            page.wait_for_timeout(1_000)
        else:
            _fail(page, "t7-session-delete-persisted", "T7 FAIL: deleted session still appears in the session history view.")
    finally:
        if session_id:
            _delete_session_via_api(api_client, session_id)
@pytest.mark.AT
@pytest.mark.webui
@pytest.mark.req("FR-008")
@pytest.mark.req("FR-015")


def test_t8_mcp_health(page: Page, base_url: str, api_key: str) -> None:
    """T8 — MCP Health."""

    _login(page, base_url, api_key)
    _open_nav(page, "External Services")
    server_button = page.get_by_role("button", name="Details").first
    expect(server_button).to_be_visible(timeout=30_000)
    server_button.click()
    page.wait_for_timeout(1_500)
    body = _body_text(page)
    assert "Refresh health" in body
    assert "Base URL" in body
    assert "Transport" in body
    assert "Detailed health for the currently selected external service." in body
    assert "MCP path:" in body
    assert "Messages path:" in body
    if "Version:" not in body and "version" not in body.lower():
        _fail(page, "t8-mcp-version-missing", "T8 FAIL: MCP server metadata shows name/URL/health, but no version field is rendered in the WebUI.")
@pytest.mark.AT
@pytest.mark.webui
@pytest.mark.req("FR-008")


def test_t9_tools(page: Page, base_url: str, api_key: str) -> None:
    """T9 — Tools."""

    _login(page, base_url, api_key)
    _open_nav(page, "Tools")
    page.wait_for_timeout(3_000)
    body = _body_text(page)
    if "An internal error occurred" in body:
        _fail(page, "t9-tools-internal-error", "T9 FAIL: Tools page renders 'An internal error occurred' instead of a usable tool list.")
    if "No tools available for the current filter." in body:
        _fail(page, "t9-tools-empty", "T9 FAIL: Tools page loads but shows no usable MCP tools for the current session/filter.")
@pytest.mark.AT
@pytest.mark.webui
@pytest.mark.req("FR-008")
@pytest.mark.req("FR-017")


def test_t10_settings(page: Page, base_url: str, api_key: str) -> None:
    """T10 — Settings."""

    _login(page, base_url, api_key)
    _open_nav(page, "Settings")
    body = _body_text(page)
    assert "Operator preferences" in body
    assert "Default model" in body
    assert "Theme" in body
    assert "Service health" in body
    theme_select = page.get_by_role("combobox", name="Theme")
    original = theme_select.input_value()
    updated_value = "dark" if original != "dark" else "light"
    theme_select.select_option(updated_value)
    page.wait_for_timeout(1_500)
    page.goto(f"{base_url}/ui", wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_timeout(1_500)
    _open_nav(page, "Settings")
    updated = page.get_by_role("combobox", name="Theme").input_value()
    if updated != updated_value:
        _fail(page, "t10-setting-not-persisted", "T10 FAIL: theme setting did not persist after full /ui reload.")
    page.get_by_role("combobox", name="Theme").select_option(original)


@pytest.mark.AT
@pytest.mark.webui
@pytest.mark.req("FR-008")
def test_t11_console_error_gate(page: "Page", base_url: str, api_key: str, console_errors: list) -> None:
    """T11 — Hard console/page-error gate (W28C-1715 §4a console-error-gate).

    Loads the authenticated dashboard and asserts zero unhandled JavaScript page
    errors.  Errors are collected by the ``page`` fixture's ``pageerror`` handler
    (registered via ``page.on('pageerror', ...)`` per PS-77 §1.8).

    This test MUST be additive — it does not replace any existing T1..T10 assertions.
    """
    _login(page, base_url, api_key)
    # Allow any deferred JS to settle before reading the error list.
    page.wait_for_timeout(2_000)
    assert console_errors == [], (
        f"W28C-1715 console-error-gate FAIL: {len(console_errors)} unhandled page JS "
        f"error(s) detected after login:\n" + "\n".join(console_errors)
    )


@pytest.mark.AT
@pytest.mark.webui
@pytest.mark.req("FR-008")
@pytest.mark.req("FR-016")
def test_t12_cw_pattern_chat_session_table(page: "Page", base_url: str, api_key: str, console_errors: list) -> None:
    """T12 — Canonical PS-77 CW-pattern assertion for the chat session table.

    PS-77 requires that tabular data surfaces implement the canonical ``CW-T1``
    DataTable contract and that modal CRUD surfaces implement ``CW-F1``
    (EntityDialog). The chat-client WebUI now renders the Chat session list through
    the canonical ``DataTable`` from ``@cloud-dog/ui`` (ChatPage.tsx), so the served
    bundle carries the canonical ``data-testid="CW-T1"`` (DataTable root) and the
    Admin/API-key/Groups CRUD modals carry ``data-testid="CW-F1"`` (EntityDialog
    root). The prior CW-gap note is removed — the canonical testids are now present.

    This test asserts the canonical PS-77 ``CW-T1`` data-testid as the
    conformance-checked anchor (with the legacy ``chat-session-table`` /
    ``chat-new-session`` container/action testids retained as a regression guard).
    """
    _login(page, base_url, api_key)
    # Navigate to Chat to surface the DataTable-backed session table.
    _open_nav(page, "Chat")
    page.wait_for_timeout(1_500)
    # Canonical PS-77 anchor: assert the @cloud-dog/ui DataTable root (CW-T1) renders.
    expect(page.get_by_test_id("CW-T1").first).to_be_visible(timeout=15_000)
    # Legacy regression guard: the container + new-session action remain present.
    chat_session_table = page.locator('[data-testid="chat-session-table"]')
    expect(chat_session_table).to_be_visible(timeout=15_000)
    chat_new_session = page.locator('[data-testid="chat-new-session"]')
    expect(chat_new_session).to_be_visible(timeout=10_000)
    # Console-error gate: no JS errors during navigation.
    assert console_errors == [], (
        f"W28C-1715 CW-pattern-gate FAIL: {len(console_errors)} unhandled JS error(s) on Chat page:\n"
        + "\n".join(console_errors)
    )


@pytest.mark.AT
@pytest.mark.webui
@pytest.mark.req("FR-014")
def test_t13_cl26_chat_submit_positive_and_negative(
    page: "Page",
    base_url: str,
    api_key: str,
    api_client: httpx.Client,
    console_errors: list,
) -> None:
    """CL-26 — /chat submit has positive and negative browser proof.

    Positive proof:
      * browser creates a real session from /chat;
      * browser POSTs to /sessions/{id}/messages;
      * POST returns HTTP 200 with matching session id and assistant content;
      * transcript API contains both user and assistant events.

    Negative proof:
      * blank composer leaves Send disabled, so no empty UI submit can be fired;
      * same-origin browser fetch of blank content returns HTTP 400;
      * same-origin browser fetch for an unknown session returns HTTP 404.
    """

    before_ids = _list_session_ids_via_api(api_client)
    session_id = ""
    _login(page, base_url, api_key)
    _open_nav(page, "Chat")
    page.get_by_role("button", name="New session", exact=True).click()
    page.wait_for_timeout(2_000)
    created_ids = _list_session_ids_via_api(api_client) - before_ids
    if not created_ids:
        _fail(page, "t13-cl26-session-not-created", "CL-26 FAIL: New session did not create a backend session.")
    session_id = sorted(created_ids)[0]
    try:
        _open_nav(page, "Chat")
        message_box = page.get_by_placeholder(
            "Type your message. Press Enter to send, Shift+Enter for a new line."
        )
        send_button = page.get_by_role("button", name="Send")

        # Negative UI assertion: the browser composer refuses blank submits.
        message_box.fill("")
        expect(send_button).to_be_disabled(timeout=10_000)

        # Negative API assertions from the browser context, using the same
        # authenticated cookies/session as the SPA rather than a direct httpx call.
        negative_results = page.evaluate(
            """
            async ({ sessionId }) => {
              async function submit(path, content) {
                const response = await fetch(path, {
                  method: "POST",
                  credentials: "include",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ content, stream: false })
                });
                return { status: response.status, body: await response.text() };
              }
              return {
                blank: await submit(`/sessions/${sessionId}/messages`, ""),
                missing: await submit(`/sessions/00000000-0000-4000-8000-000000000026/messages`, "hello")
              };
            }
            """,
            {"sessionId": session_id},
        )
        assert negative_results["blank"]["status"] == 400, negative_results
        assert "content must be non-empty" in negative_results["blank"]["body"]
        assert negative_results["missing"]["status"] == 404, negative_results
        assert "Unknown session" in negative_results["missing"]["body"]

        # Positive browser assertion: /chat submits through the SPA and the
        # message endpoint returns a concrete assistant response.
        prompt = f"Reply with a short CL-26 browser-submit confirmation at {int(time.time())}."
        message_box.fill(prompt)
        with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and f"/sessions/{session_id}/messages" in response.url
            ),
            timeout=180_000,
        ) as message_response_info:
            send_button.click()
        message_response = message_response_info.value
        assert message_response.status == 200, message_response.text()
        message_path = urlparse(message_response.url).path
        assert message_path.endswith(
            (f"/sessions/{session_id}/messages", f"/sessions/{session_id}/messages/stream")
        ), message_response.url
        if message_path.endswith("/messages"):
            payload = message_response.json()
            assert payload["session_id"] == session_id
            assert isinstance(payload.get("content"), str) and payload["content"].strip()
        expect(page.get_by_text(prompt)).to_be_visible(timeout=30_000)

        assistant_content = ""
        deadline = time.time() + 180
        while time.time() < deadline:
            transcript = api_client.get(f"/sessions/{session_id}/transcript")
            assert transcript.status_code == 200, transcript.text
            events = transcript.json().get("events") or []
            assistant_events = [
                event
                for event in events
                if event.get("event_type") == "assistant_message"
                and str(event.get("data", {}).get("content") or "").strip()
            ]
            if assistant_events:
                assistant_content = str(
                    assistant_events[-1].get("data", {}).get("content") or ""
                ).strip()
                break
            page.wait_for_timeout(5_000)
        else:
            _fail(
                page,
                "t13-cl26-chat-response-timeout",
                "CL-26 FAIL: assistant response did not appear in transcript within 180 seconds.",
            )

        transcript = api_client.get(f"/sessions/{session_id}/transcript")
        assert transcript.status_code == 200, transcript.text
        events = transcript.json().get("events") or []
        assert any(
            event.get("event_type") == "user_message"
            and event.get("data", {}).get("content") == prompt
            for event in events
        ), events
        assert assistant_content
        assert assistant_content[: min(24, len(assistant_content))] in _body_text(page)
        assert console_errors == [], (
            f"CL-26 console-error-gate FAIL: {len(console_errors)} unhandled JS error(s):\n"
            + "\n".join(console_errors)
        )
    finally:
        if session_id:
            _delete_session_via_api(api_client, session_id)
