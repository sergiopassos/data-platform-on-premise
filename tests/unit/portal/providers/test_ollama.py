"""Tests for OllamaProvider — timeout fix and error wrapping."""
import pytest
from unittest.mock import MagicMock, patch

from portal.agent.providers.base import ProviderAPIError, ProviderTimeoutError
from portal.agent.providers.ollama import OllamaProvider


@pytest.mark.asyncio
async def test_ollama_happy_path(monkeypatch):
    provider = OllamaProvider(ollama_url="http://mock:11434")

    def fake_call_sync(prompt):
        return "dataContractSpecification: '0.9.3'"

    monkeypatch.setattr(provider, "_call_sync", fake_call_sync)

    result = await provider.generate_yaml("prompt")
    assert "0.9.3" in result


@pytest.mark.asyncio
async def test_ollama_timeout_raises_provider_timeout_error(monkeypatch):
    provider = OllamaProvider(ollama_url="http://mock:11434", timeout_s=1)

    def slow_sync(prompt):
        import time
        time.sleep(5)
        return "never returned"

    monkeypatch.setattr(provider, "_call_sync", slow_sync)

    with pytest.raises(ProviderTimeoutError) as exc_info:
        await provider.generate_yaml("prompt")

    assert "ollama" in str(exc_info.value).lower()
    assert "1 s" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ollama_http_error_wrapped(monkeypatch):
    import httpx
    provider = OllamaProvider(ollama_url="http://mock:11434")

    def bad_sync(prompt):
        response = MagicMock()
        response.status_code = 503
        raise httpx.HTTPStatusError("Service Unavailable", request=MagicMock(), response=response)

    monkeypatch.setattr(provider, "_call_sync", bad_sync)

    with pytest.raises(ProviderAPIError) as exc_info:
        await provider.generate_yaml("prompt")

    assert "503" in str(exc_info.value)


def test_ollama_name():
    assert OllamaProvider(ollama_url="http://mock:11434").name == "ollama"
