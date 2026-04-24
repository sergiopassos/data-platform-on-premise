"""No-LLM fallback provider: deterministic, user-selectable.

Returns an empty string, which ``ODCSGenerator._parse_and_validate``
detects (``yaml.safe_load("")`` returns ``None``) and then delegates to
``_build_fallback_contract`` for a rule-based ODCS v3.1 dict.

Why not construct the contract here? Keeping the builder inside
``ODCSGenerator`` preserves the current parse-error recovery behavior
(SC-4 from DEFINE) and keeps a single source of truth for the ODCS shape.
"""
from __future__ import annotations


class FallbackProvider:
    """Provider that skips any LLM call."""

    name = "fallback"

    async def generate_yaml(self, prompt: str) -> str:
        return ""
