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

"""Chat-client adoption seam for the shared ``cloud_dog_agent.prompts`` package.

W28B-319 (AGENTIC D5) — opt-in prompt-engineering adoption.

This module is the single integration point between the chat-client request
surface and the platform prompt-template store. It is deliberately *opt-in*:

* Callers that do not reference a template see byte-for-byte unchanged
  behaviour — :func:`resolve_request_system_prompt` returns ``None`` and the
  literal ``system_prompt`` (or ``llm.system_prompt`` default) is used exactly
  as before.
* Callers that supply a ``prompt_template`` reference get the template resolved
  and rendered through a :class:`~cloud_dog_agent.prompts.PromptStore`
  (default :class:`~cloud_dog_agent.prompts.InMemoryPromptStore`, injectable).

LLM execution is unaffected: this module only produces the *text* of a system
prompt; all model calls continue to flow through the chat-client ``LLMService``.

If the optional ``cloud-dog-agent`` dependency is not installed the import
degrades gracefully (``PROMPTS_AVAILABLE is False``) and any attempt to opt in
raises a clear runtime error instead of import-time failure for the whole API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

try:  # pragma: no cover - exercised by availability test
    from cloud_dog_agent.prompts import (
        InMemoryPromptStore,
        PromptStore,
        RenderError,
        RenderResult,
        TemplateNotFound,
    )

    PROMPTS_AVAILABLE = True
except Exception:  # pragma: no cover - optional-dependency fallback
    PROMPTS_AVAILABLE = False

    class TemplateNotFound(Exception):  # type: ignore[no-redef]
        """Fallback raised when prompt support is requested but unavailable."""

    class RenderError(Exception):  # type: ignore[no-redef]
        """Fallback render error when prompt support is unavailable."""

    class PromptStore:  # type: ignore[no-redef]
        """Placeholder protocol when ``cloud_dog_agent.prompts`` is absent."""

    InMemoryPromptStore = None  # type: ignore[assignment,misc]
    RenderResult = Any  # type: ignore[assignment,misc]

if TYPE_CHECKING:  # keep type names available without importing at runtime
    from cloud_dog_agent.prompts import PromptStore as PromptStore  # noqa: F401


class PromptResolutionError(RuntimeError):
    """Raised when an opt-in prompt template cannot be resolved/rendered."""


def default_prompt_store() -> "PromptStore":
    """Return a fresh in-memory prompt store.

    Used as the injectable default for the API router. A DB/cache-backed
    :class:`PromptStore` can be supplied instead without touching callers.
    """
    if not PROMPTS_AVAILABLE or InMemoryPromptStore is None:  # pragma: no cover
        raise PromptResolutionError(
            "prompt-template support requires the 'cloud-dog-agent' package"
        )
    return InMemoryPromptStore()


def _coerce_variables(variables: Any) -> dict[str, Any]:
    """Normalise a caller-supplied variable map into a plain ``dict``."""
    if variables is None:
        return {}
    if isinstance(variables, dict):
        return {str(k): v for k, v in variables.items()}
    raise PromptResolutionError("prompt_variables must be an object/mapping")


async def resolve_request_system_prompt(
    store: "Optional[PromptStore]",
    *,
    prompt_template: Optional[str],
    prompt_variables: Any = None,
    prompt_version: Optional[int] = None,
    strict: bool = False,
) -> Optional[str]:
    """Resolve+render a system prompt from a template, opt-in only.

    Returns ``None`` when the caller did not reference a template — the signal
    for the API to fall back to its existing literal-``system_prompt`` path,
    preserving byte-for-byte default behaviour.

    When ``prompt_template`` is supplied the named template is resolved through
    ``store`` (effective version, or the explicit ``prompt_version``) and
    rendered against ``prompt_variables``. The rendered text is returned.

    Raises :class:`PromptResolutionError` for opt-in misuse: support absent,
    unknown template, or (in ``strict`` mode) unfilled variables.
    """
    name = str(prompt_template or "").strip()
    if not name:
        return None  # caller did not opt in -> unchanged behaviour

    if not PROMPTS_AVAILABLE:
        raise PromptResolutionError(
            "prompt_template requested but 'cloud-dog-agent' is not installed"
        )
    if store is None:
        raise PromptResolutionError(
            "prompt_template requested but no PromptStore is configured"
        )

    variables = _coerce_variables(prompt_variables)
    try:
        result: RenderResult = await store.render(
            name,
            variables,
            version=prompt_version,
            strict=strict,
        )
    except TemplateNotFound as exc:
        raise PromptResolutionError(f"unknown prompt template: {name}") from exc
    except RenderError as exc:
        # strict-mode unfilled variables surface from the shared renderer.
        raise PromptResolutionError(
            f"prompt template '{name}' could not be rendered: {exc}"
        ) from exc

    if strict and not result.ok:
        raise PromptResolutionError(
            f"prompt template '{name}' has unfilled variables: "
            f"{', '.join(result.missing)}"
        )
    return result.text


__all__ = [
    "PROMPTS_AVAILABLE",
    "PromptResolutionError",
    "PromptStore",
    "TemplateNotFound",
    "default_prompt_store",
    "resolve_request_system_prompt",
]
