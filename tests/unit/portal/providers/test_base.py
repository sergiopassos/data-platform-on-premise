"""Verify that all providers satisfy the LLMProvider Protocol at runtime."""

from portal.agent.providers.base import LLMProvider
from portal.agent.providers.fallback import FallbackProvider
from portal.agent.providers.ollama import OllamaProvider


class TestProtocolConformance:
    def test_fallback_satisfies_protocol(self):
        assert isinstance(FallbackProvider(), LLMProvider)

    def test_ollama_satisfies_protocol(self):
        provider = OllamaProvider(ollama_url="http://mock:11434")
        assert isinstance(provider, LLMProvider)

    def test_unknown_class_does_not_satisfy_protocol(self):
        class NotAProvider:
            pass

        assert not isinstance(NotAProvider(), LLMProvider)

    def test_protocol_requires_generate_yaml(self):
        class MissingMethod:
            name = "missing"

        assert not isinstance(MissingMethod(), LLMProvider)
