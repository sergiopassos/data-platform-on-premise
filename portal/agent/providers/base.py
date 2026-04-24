"""LLMProvider Protocol and shared exception types.

All provider implementations live in sibling modules. portal/app.py must
never import a provider implementation directly: it uses build_from_name()
from the package __init__.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class ProviderError(Exception):
    """Base for all provider-level failures surfaced to the user.

    The provider_name attribute is used by the UI layer to render a
    named error (e.g., "Falha em gemini (ProviderAPIError): ...").
    Error messages MUST NOT include secret values such as API keys.
    """

    def __init__(self, provider_name: str, message: str) -> None:
        super().__init__(f"{provider_name}: {message}")
        self.provider_name = provider_name


class ProviderTimeoutError(ProviderError):
    """Raised when a provider exceeds its wall-clock budget."""


class ProviderAPIError(ProviderError):
    """Raised on remote-service errors (HTTP 4xx/5xx, invalid API key, etc.)."""


@runtime_checkable
class LLMProvider(Protocol):
    """Structural interface for ODCS-generating LLM providers.

    Any class that exposes a string ``name`` attribute and an async
    ``generate_yaml(prompt)`` method satisfies this protocol: no
    inheritance required.

    Implementations MUST:
        - Wrap any blocking SDK call in ``asyncio.to_thread(...)``.
        - Wrap the full call with ``asyncio.wait_for(..., timeout=...)``.
        - Translate SDK-specific exceptions into ``ProviderError`` subclasses.
    """

    name: str

    async def generate_yaml(self, prompt: str) -> str:
        """Return raw YAML string for the given prompt."""
        ...
