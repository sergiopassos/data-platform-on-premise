"""Per-session LLM provider accessors backed by ``cl.user_session``.

Both the ``/llm`` slash command and the ``ChatSettings`` gear-icon
dropdown write through this single helper so there is no drift between
the two code paths (SC-3 from DEFINE).
"""
from __future__ import annotations

import chainlit as cl

from .providers import LLMProvider, build_from_name

SESSION_KEY_PROVIDER = "llm_provider"


def get_provider() -> LLMProvider | None:
    """Return the active provider for the current session, or None."""
    provider = cl.user_session.get(SESSION_KEY_PROVIDER)
    if provider is None:
        return None
    return provider


def set_provider_by_name(name: str) -> LLMProvider:
    """Build a provider by name and store it in the current session.

    Raises:
        ValueError: unknown provider name.
        ProviderError: construction failure (e.g., missing API key).
    """
    provider = build_from_name(name)
    cl.user_session.set(SESSION_KEY_PROVIDER, provider)
    return provider


def clear_provider() -> None:
    """Remove the active provider from the current session.

    Used after a provider raises an error so the next message forces
    the user to explicitly re-select (AT-006 from DEFINE).
    """
    cl.user_session.set(SESSION_KEY_PROVIDER, None)
