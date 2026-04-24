"""Google Gemini provider using the ``google-generativeai`` SDK.

The SDK exposes a synchronous surface (``GenerativeModel.generate_content``);
we wrap the call in ``asyncio.to_thread`` plus ``asyncio.wait_for`` so the
Chainlit event loop is never blocked and has a hard wall-clock budget.

Configuration (env vars, all optional except the API key):
    GEMINI_API_KEY   : required; injected from the gemini-api-secret Secret
    GEMINI_MODEL     : model name, defaults to ``gemini-2.0-flash``
"""
from __future__ import annotations

import asyncio
import logging
import os

import google.generativeai as genai
from google.api_core import exceptions as google_exc

from .base import ProviderAPIError, ProviderTimeoutError

_LOG = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 30
_DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiProvider:
    """Google Gemini provider. Reads GEMINI_API_KEY from env at construction."""

    name = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_s: int = _DEFAULT_TIMEOUT_S,
    ) -> None:
        resolved_key = api_key or os.getenv("GEMINI_API_KEY")
        if not resolved_key:
            raise ProviderAPIError(self.name, "GEMINI_API_KEY is not set")
        genai.configure(api_key=resolved_key)
        self._model_name = model or os.getenv("GEMINI_MODEL", _DEFAULT_MODEL)
        self._model = genai.GenerativeModel(self._model_name)
        self._timeout_s = timeout_s

    async def generate_yaml(self, prompt: str) -> str:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._call_sync, prompt),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError as err:
            _LOG.warning("Gemini call exceeded %s s", self._timeout_s)
            raise ProviderTimeoutError(
                self.name, f"timed out after {self._timeout_s} s"
            ) from err
        except google_exc.PermissionDenied as err:
            raise ProviderAPIError(self.name, "invalid API key") from err
        except google_exc.GoogleAPIError as err:
            raise ProviderAPIError(self.name, str(err)) from err

    def _call_sync(self, prompt: str) -> str:
        response = self._model.generate_content(prompt)
        if not response or not getattr(response, "text", None):
            raise ProviderAPIError(self.name, "empty response")
        return response.text
