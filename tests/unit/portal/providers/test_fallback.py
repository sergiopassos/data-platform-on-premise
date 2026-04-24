"""Tests for FallbackProvider."""
import pytest

from portal.agent.providers.fallback import FallbackProvider


@pytest.mark.asyncio
async def test_fallback_returns_empty_string():
    provider = FallbackProvider()
    result = await provider.generate_yaml("any prompt")
    assert result == ""


@pytest.mark.asyncio
async def test_fallback_name():
    assert FallbackProvider().name == "fallback"


@pytest.mark.asyncio
async def test_fallback_ignores_prompt_content():
    provider = FallbackProvider()
    result1 = await provider.generate_yaml("prompt A")
    result2 = await provider.generate_yaml("prompt B")
    assert result1 == result2 == ""
