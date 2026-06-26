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

"""UT_PROMPT_TEMPLATE_OPTIN — W28B-319 (AGENTIC D5) prompt-engineering adoption.

Proves the chat-client opt-in prompt-template resolution:

* ``UT_PROMPT_RESOLVE_OPTIN``     — a ``prompt_template`` reference resolves and
  renders through the shared PromptStore and becomes the system message.
* ``UT_PROMPT_DEFAULT_UNCHANGED`` — with no template the request behaves
  byte-for-byte as before (literal ``system_prompt`` / configured default), and
  the LLM call still flows through the chat-client ``LLMService``.
* ``UT_PROMPT_RENDER_VARS``       — variables are substituted and an explicit
  pinned ``prompt_version`` is honoured.

The fake ``LLMService`` captures the exact ``messages`` list handed to the LLM,
so assertions are made against the real system prompt the model would receive —
no stub of the thing under test, no fabricated output.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import cloud_dog_chat_client.api.routes as routes_module
from cloud_dog_chat_client.api.routes import SendMessageRequest, build_router
from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.llm.protocols import ChatCompletionResult
from cloud_dog_chat_client.llm.response_policy import ResponsePolicy
from cloud_dog_chat_client.prompts import (
    PROMPTS_AVAILABLE,
    PromptResolutionError,
    default_prompt_store,
    resolve_request_system_prompt,
)
from cloud_dog_chat_client.session import SessionManager

pytestmark = pytest.mark.asyncio


def _disabled_policy() -> ResponsePolicy:
    return ResponsePolicy(
        enforce=False,
        envelope_tag="",
        format="",
        marker_key="",
        marker_value="",
        answer_key="",
        strip_for_user=False,
        show_thinking=False,
        display_answer_tag="",
        allow_header_only=False,
        retry_attempts=0,
        retry_backoff_seconds=0.0,
    )


def _route_endpoint(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"endpoint not found: {method} {path}")


class _CapturingLLMService:
    """Fake LLMService that records the exact messages passed to ``complete``."""

    captured: list = []

    def __init__(self, _cfg, **_kwargs):
        self.response_policy = _disabled_policy()

    async def complete(self, messages):
        type(self).captured = list(messages)
        return ChatCompletionResult(content="prompt-optin unit response", raw={})


def _install_fake_llm(monkeypatch):
    _CapturingLLMService.captured = []
    monkeypatch.setattr(routes_module, "LLMService", _CapturingLLMService)


async def _seed_store():
    store = default_prompt_store()
    await store.create_template(
        "support_agent",
        "You are a {persona} support agent for {product}. Be {tone}.",
        description="Support persona",
        tags=["support"],
        created_by="ut",
    )
    return store


def _system_messages(messages) -> list[str]:
    return [str(m.content) for m in messages if str(m.role) == "system"]


# --------------------------------------------------------------------------- #
# UT_PROMPT_RESOLVE_OPTIN
# --------------------------------------------------------------------------- #
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")
@pytest.mark.skipif(not PROMPTS_AVAILABLE, reason="cloud-dog-agent not installed")
async def test_ut_prompt_resolve_optin(env_file, monkeypatch):
    """A prompt_template reference is resolved+rendered into the system prompt."""
    cfg = ConfigManager(env_file=env_file)
    sessions = SessionManager("./logs")
    store = await _seed_store()
    router = build_router(config=cfg, sessions=sessions, prompt_store=store)
    _install_fake_llm(monkeypatch)

    send_message = _route_endpoint(router, "/sessions/{session_id}/messages", "POST")
    session_id = sessions.create_session(metadata={})

    resp = await send_message(
        session_id,
        SendMessageRequest(
            content="hello",
            stream=False,
            prompt_template="support_agent",
            prompt_variables={"persona": "friendly", "product": "Cloud-Dog", "tone": "concise"},
        ),
    )

    assert resp.content == "prompt-optin unit response"
    system_msgs = _system_messages(_CapturingLLMService.captured)
    rendered = "You are a friendly support agent for Cloud-Dog. Be concise."
    assert rendered in system_msgs
    # The unresolved template body must NOT have leaked through.
    assert "{persona}" not in " ".join(system_msgs)
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.skipif(not PROMPTS_AVAILABLE, reason="cloud-dog-agent not installed")
async def test_ut_prompt_resolve_optin_unknown_template_is_400(env_file, monkeypatch):
    """An unknown prompt_template is a clear client error, never a silent skip."""
    cfg = ConfigManager(env_file=env_file)
    sessions = SessionManager("./logs")
    router = build_router(config=cfg, sessions=sessions, prompt_store=default_prompt_store())
    _install_fake_llm(monkeypatch)

    send_message = _route_endpoint(router, "/sessions/{session_id}/messages", "POST")
    session_id = sessions.create_session(metadata={})

    with pytest.raises(HTTPException) as err:
        await send_message(
            session_id,
            SendMessageRequest(content="hi", stream=False, prompt_template="does_not_exist"),
        )
    assert err.value.status_code == 400
    assert "does_not_exist" in str(err.value.detail)


# --------------------------------------------------------------------------- #
# UT_PROMPT_DEFAULT_UNCHANGED
# --------------------------------------------------------------------------- #
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")
async def test_ut_prompt_default_unchanged_literal_system_prompt(env_file, monkeypatch):
    """With no template, an explicit system_prompt is used verbatim (unchanged)."""
    cfg = ConfigManager(env_file=env_file)
    sessions = SessionManager("./logs")
    router = build_router(config=cfg, sessions=sessions)
    _install_fake_llm(monkeypatch)

    send_message = _route_endpoint(router, "/sessions/{session_id}/messages", "POST")
    session_id = sessions.create_session(metadata={})

    literal = "You are the legacy literal prompt."
    resp = await send_message(
        session_id,
        SendMessageRequest(content="hello", stream=False, system_prompt=literal),
    )

    assert resp.content == "prompt-optin unit response"
    system_msgs = _system_messages(_CapturingLLMService.captured)
    assert literal in system_msgs
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


async def test_ut_prompt_default_unchanged_matches_no_prompt_feature(env_file, monkeypatch):
    """Default-path messages are identical whether or not prompts is wired in.

    Builds the message list for a request with no template through a router that
    has a prompt store and one that does not — they must be byte-for-byte equal,
    proving the opt-in path adds nothing on the default path.
    """
    cfg = ConfigManager(env_file=env_file)
    sessions = SessionManager("./logs")
    _install_fake_llm(monkeypatch)

    router_with_store = build_router(
        config=cfg, sessions=sessions, prompt_store=default_prompt_store()
    )
    router_without_store = build_router(config=cfg, sessions=sessions)

    send_with = _route_endpoint(router_with_store, "/sessions/{session_id}/messages", "POST")
    send_without = _route_endpoint(router_without_store, "/sessions/{session_id}/messages", "POST")

    literal = "Plain default prompt."

    sid_a = sessions.create_session(metadata={})
    await send_with(sid_a, SendMessageRequest(content="ping", stream=False, system_prompt=literal))
    msgs_with = [(str(m.role), str(m.content)) for m in _CapturingLLMService.captured]

    sid_b = sessions.create_session(metadata={})
    await send_without(sid_b, SendMessageRequest(content="ping", stream=False, system_prompt=literal))
    msgs_without = [(str(m.role), str(m.content)) for m in _CapturingLLMService.captured]

    assert msgs_with == msgs_without
    assert ("system", literal) in msgs_with
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


async def test_ut_prompt_default_resolver_returns_none_without_template():
    """The resolver returns None when no template is referenced (the fallback signal)."""
    store = default_prompt_store() if PROMPTS_AVAILABLE else None
    assert await resolve_request_system_prompt(store, prompt_template=None) is None
    assert await resolve_request_system_prompt(store, prompt_template="   ") is None


# --------------------------------------------------------------------------- #
# UT_PROMPT_RENDER_VARS
# --------------------------------------------------------------------------- #
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")
@pytest.mark.skipif(not PROMPTS_AVAILABLE, reason="cloud-dog-agent not installed")
async def test_ut_prompt_render_vars_substitutes_and_honours_pinned_version(env_file):
    """Variables render correctly and an explicit prompt_version is honoured."""
    store = default_prompt_store()
    await store.create_template("v_demo", "v1: hi {name}", created_by="ut")
    await store.add_version("v_demo", "v2: hello {name}!", note="reworded")

    # Effective (latest) version renders v2.
    latest = await resolve_request_system_prompt(
        store, prompt_template="v_demo", prompt_variables={"name": "Ada"}
    )
    assert latest == "v2: hello Ada!"

    # Explicit version pin renders v1.
    pinned = await resolve_request_system_prompt(
        store, prompt_template="v_demo", prompt_variables={"name": "Ada"}, prompt_version=1
    )
    assert pinned == "v1: hi Ada"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


@pytest.mark.skipif(not PROMPTS_AVAILABLE, reason="cloud-dog-agent not installed")
async def test_ut_prompt_render_vars_strict_missing_variable_errors():
    """Strict rendering reports unfilled variables as a resolution error."""
    store = default_prompt_store()
    await store.create_template("strict_demo", "Hello {first} {last}", created_by="ut")

    # Non-strict leaves the unfilled placeholder intact and still renders.
    soft = await resolve_request_system_prompt(
        store, prompt_template="strict_demo", prompt_variables={"first": "Ada"}
    )
    assert soft == "Hello Ada {last}"

    with pytest.raises(PromptResolutionError) as err:
        await resolve_request_system_prompt(
            store,
            prompt_template="strict_demo",
            prompt_variables={"first": "Ada"},
            strict=True,
        )
    assert "last" in str(err.value)


# --------------------------------------------------------------------------- #
# CLI opt-in
# --------------------------------------------------------------------------- #
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")
@pytest.mark.skipif(not PROMPTS_AVAILABLE, reason="cloud-dog-agent not installed")
async def test_ut_prompt_cli_optin_renders_template(env_file, monkeypatch):
    """CLI resolves a configured llm.prompt_template; otherwise unchanged."""
    from cloud_dog_chat_client.cli import interactive

    cfg = ConfigManager(env_file=env_file)
    store = default_prompt_store()
    await store.create_template("cli_tpl", "CLI persona: {role}", created_by="ut")

    # Opt-in via config: template configured -> rendered text returned.
    monkeypatch.setattr(cfg, "get", _patched_get(cfg, {
        "llm.prompt_template": "cli_tpl",
        "llm.prompt_variables": {"role": "analyst"},
    }))
    rendered = await interactive._cli_system_prompt(cfg, store)
    assert rendered == "CLI persona: analyst"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-006")


async def test_ut_prompt_cli_default_unchanged(env_file, monkeypatch):
    """CLI without a configured template falls back to llm.system_prompt verbatim."""
    from cloud_dog_chat_client.cli import interactive

    cfg = ConfigManager(env_file=env_file)
    monkeypatch.setattr(cfg, "get", _patched_get(cfg, {
        "llm.prompt_template": None,
        "llm.system_prompt": "Legacy CLI prompt",
    }))
    result = await interactive._cli_system_prompt(cfg, None)
    assert result == "Legacy CLI prompt"


def _patched_get(cfg, overrides):
    """Return a config.get replacement that honours specific overrides."""
    original = cfg.get

    def _get(key, default=None):
        if key in overrides:
            return overrides[key]
        return original(key, default)

    return _get


# Keep asyncio import meaningful for static analysers / future async helpers.
assert asyncio is not None
