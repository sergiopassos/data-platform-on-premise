"""Slash-command parsing for the portal chat input.

Supported commands:
    /llm <name>   -> switches the active provider for the current session
"""
from __future__ import annotations

from dataclasses import dataclass

from .providers import KNOWN_PROVIDERS


@dataclass(frozen=True)
class LLMCommand:
    """Parsed ``/llm <name>`` invocation."""

    provider_name: str


def parse_llm_command(message: str) -> LLMCommand | None:
    """Return an ``LLMCommand`` if ``message`` is a well-formed ``/llm`` invocation.

    Examples:
        "/llm gemini"         -> LLMCommand("gemini")
        "/llm  Ollama  "      -> LLMCommand("ollama")
        "/llm"                -> None   (missing arg)
        "/llm foo bar"        -> None   (too many args)
        "hello"               -> None   (not a command)
    """
    stripped = message.strip()
    if not stripped.startswith("/llm"):
        return None
    parts = stripped.split()
    if parts[0] != "/llm":
        return None
    if len(parts) != 2:
        return None
    return LLMCommand(provider_name=parts[1].lower())


def is_known_provider(name: str) -> bool:
    """True iff ``name`` (case-insensitive) is a known provider."""
    return name.lower() in KNOWN_PROVIDERS
