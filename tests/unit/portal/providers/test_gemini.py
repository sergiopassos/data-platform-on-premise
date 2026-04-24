"""Tests for GeminiProvider — API key validation, timeout, error wrapping."""
import pytest
from unittest.mock import MagicMock, patch

from portal.agent.providers.base import ProviderAPIError, ProviderTimeoutError


def test_gemini_missing_api_key_raises_on_construction(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    from portal.agent.providers import gemini as gemini_mod
    with patch.object(gemini_mod.genai, "configure"):
        from portal.agent.providers.gemini import GeminiProvider
        with pytest.raises(ProviderAPIError) as exc_info:
            GeminiProvider(api_key=None)

    assert "GEMINI_API_KEY" in str(exc_info.value)


@pytest.mark.asyncio
async def test_gemini_happy_path(monkeypatch):
    from portal.agent.providers import gemini as gemini_mod

    fake_response = MagicMock()
    fake_response.text = "dataContractSpecification: '0.9.3'"

    with patch.object(gemini_mod.genai, "configure"), \
         patch.object(gemini_mod.genai, "GenerativeModel") as mock_model_cls:
        mock_model = MagicMock()
        mock_model.generate_content.return_value = fake_response
        mock_model_cls.return_value = mock_model

        from portal.agent.providers.gemini import GeminiProvider
        provider = GeminiProvider(api_key="test-key")

        result = await provider.generate_yaml("prompt")

    assert "0.9.3" in result


@pytest.mark.asyncio
async def test_gemini_timeout_raises_provider_timeout_error(monkeypatch):
    from portal.agent.providers import gemini as gemini_mod

    with patch.object(gemini_mod.genai, "configure"), \
         patch.object(gemini_mod.genai, "GenerativeModel") as mock_model_cls:
        mock_model = MagicMock()
        mock_model_cls.return_value = mock_model

        from portal.agent.providers.gemini import GeminiProvider
        provider = GeminiProvider(api_key="test-key", timeout_s=1)

        def slow_sync(prompt):
            import time
            time.sleep(5)
            return "never"

        monkeypatch.setattr(provider, "_call_sync", slow_sync)

        with pytest.raises(ProviderTimeoutError):
            await provider.generate_yaml("prompt")


def test_gemini_name(monkeypatch):
    from portal.agent.providers import gemini as gemini_mod
    with patch.object(gemini_mod.genai, "configure"), \
         patch.object(gemini_mod.genai, "GenerativeModel"):
        from portal.agent.providers.gemini import GeminiProvider
        assert GeminiProvider(api_key="key").name == "gemini"
