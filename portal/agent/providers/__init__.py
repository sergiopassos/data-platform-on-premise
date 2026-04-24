"""Provider registry: single place that instantiates concrete providers.

``portal/app.py`` calls ``build_from_name()``; it never imports
``*Provider`` classes directly, which keeps SDK imports out of the
Chainlit wiring layer.
"""
from __future__ import annotations

import os
from typing import Callable

from .base import (
    LLMProvider,
    ProviderAPIError,
    ProviderError,
    ProviderTimeoutError,
)
from .fallback import FallbackProvider
from .gemini import GeminiProvider
from .ollama import OllamaProvider

__all__ = [
    "LLMProvider",
    "ProviderError",
    "ProviderTimeoutError",
    "ProviderAPIError",
    "build_from_name",
    "KNOWN_PROVIDERS",
]

KNOWN_PROVIDERS: tuple[str, ...] = ("gemini", "ollama", "fallback")


def _build_ollama() -> LLMProvider:
    return OllamaProvider(
        ollama_url=os.getenv(
            "OLLAMA_URL", "http://ollama.portal.svc.cluster.local:11434"
        ),
        model=os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
        timeout_s=int(os.getenv("OLLAMA_TIMEOUT_S", "30")),
    )


def _build_gemini() -> LLMProvider:
    return GeminiProvider(
        timeout_s=int(os.getenv("GEMINI_TIMEOUT_S", "30")),
    )


def _build_fallback() -> LLMProvider:
    return FallbackProvider()


_BUILDERS: dict[str, Callable[[], LLMProvider]] = {
    "ollama": _build_ollama,
    "gemini": _build_gemini,
    "fallback": _build_fallback,
}


def build_from_name(name: str) -> LLMProvider:
    """Construct a provider by short name.

    Raises:
        ValueError: when ``name`` is not in ``KNOWN_PROVIDERS``.
        ProviderError: when the concrete provider fails to construct
            (e.g., missing ``GEMINI_API_KEY``). Caller must catch this.
    """
    key = name.strip().lower()
    if key not in _BUILDERS:
        raise ValueError(
            f"Unknown provider '{name}'. Known: {KNOWN_PROVIDERS}"
        )
    return _BUILDERS[key]()
