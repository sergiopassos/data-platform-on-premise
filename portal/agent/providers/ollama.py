"""Ollama provider with hard wall-clock timeout.

Fixes the 600-second blocking ``httpx.Client`` call that hung the Chainlit
UI. The approach is:

    asyncio.wait_for(
        asyncio.to_thread(_call_sync, prompt),
        timeout=30,
    )

This gives a hard wall-clock cancellation of the coroutine. The background
thread may outlive the timeout (Ollama will finish on its own inside the
cluster); that is acceptable in local-dev and we log a warning.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .base import ProviderAPIError, ProviderTimeoutError

_LOG = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 30
_HTTPX_CONNECT_TIMEOUT_S = 10
_HTTPX_READ_TIMEOUT_S = 60  # safety net; asyncio.wait_for fires before this


class OllamaProvider:
    """Ollama HTTP provider that never blocks the UI for more than ``timeout_s``."""

    name = "ollama"

    def __init__(
        self,
        ollama_url: str,
        model: str = "llama3.2:3b",
        timeout_s: int = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._url = ollama_url.rstrip("/")
        self._model = model
        self._timeout_s = timeout_s

    async def generate_yaml(self, prompt: str) -> str:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._call_sync, prompt),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError as err:
            _LOG.warning(
                "Ollama call exceeded %s s; coroutine cancelled", self._timeout_s
            )
            raise ProviderTimeoutError(
                self.name, f"timed out after {self._timeout_s} s"
            ) from err
        except httpx.HTTPStatusError as err:
            raise ProviderAPIError(
                self.name, f"HTTP {err.response.status_code}"
            ) from err
        except httpx.HTTPError as err:
            raise ProviderAPIError(self.name, str(err)) from err

    def _call_sync(self, prompt: str) -> str:
        timeout = httpx.Timeout(
            connect=_HTTPX_CONNECT_TIMEOUT_S,
            read=_HTTPX_READ_TIMEOUT_S,
            write=_HTTPX_READ_TIMEOUT_S,
            pool=_HTTPX_CONNECT_TIMEOUT_S,
        )
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{self._url}/api/generate",
                json={"model": self._model, "prompt": prompt, "stream": False},
            )
            resp.raise_for_status()
            return resp.json()["response"]
