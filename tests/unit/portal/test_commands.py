"""Tests for parse_llm_command and is_known_provider."""
import pytest

from portal.agent.commands import LLMCommand, is_known_provider, parse_llm_command


class TestParseLLMCommand:
    def test_known_provider_returns_command(self):
        result = parse_llm_command("/llm gemini")
        assert result == LLMCommand(provider_name="gemini")

    def test_normalises_to_lowercase(self):
        result = parse_llm_command("/llm  Ollama  ")
        assert result == LLMCommand(provider_name="ollama")

    def test_missing_arg_returns_none(self):
        assert parse_llm_command("/llm") is None

    def test_too_many_args_returns_none(self):
        assert parse_llm_command("/llm gemini extra") is None

    def test_non_command_returns_none(self):
        assert parse_llm_command("orders") is None

    def test_other_command_returns_none(self):
        assert parse_llm_command("/help") is None

    def test_prefix_of_llm_returns_none(self):
        assert parse_llm_command("/llmextra gemini") is None


class TestIsKnownProvider:
    def test_gemini_is_known(self):
        assert is_known_provider("gemini") is True

    def test_ollama_is_known(self):
        assert is_known_provider("ollama") is True

    def test_fallback_is_known(self):
        assert is_known_provider("fallback") is True

    def test_unknown_is_false(self):
        assert is_known_provider("openai") is False

    def test_case_insensitive(self):
        assert is_known_provider("GEMINI") is True
