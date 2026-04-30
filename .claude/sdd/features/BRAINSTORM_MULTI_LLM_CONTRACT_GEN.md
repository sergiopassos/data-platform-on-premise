# BRAINSTORM: Multi-LLM Contract Generation with Per-Session Switching

> Exploratory session to clarify intent and approach before requirements capture

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | MULTI_LLM_CONTRACT_GEN |
| **Date** | 2026-04-23 |
| **Author** | brainstorm-agent |
| **Status** | Ready for Define |

---

## Initial Idea

**Raw Input:** Add multi-LLM support to the Chainlit data contract portal so users can switch between Ollama (local), Gemini (cloud), and potentially other providers. Fix the existing Ollama hang. Add Gemini integration.

**Context Gathered:**
- The portal lives in `portal/app.py` (Chainlit app) and `portal/agent/odcs_generator.py` (LLM call + fallback builder)
- `ODCSGenerator` is tightly coupled to Ollama via a direct `httpx.Client` call with a 600-second timeout — this is the hang source
- A rule-based `_build_fallback_contract()` already exists in `ODCSGenerator` and produces valid ODCS v3.1 YAML without any LLM
- Provider state is currently global (module-level `_generator` singleton); no per-session isolation exists
- Chainlit's `cl.user_session` is the natural per-session store
- The app runs inside Kubernetes (KIND cluster) but the contract generation path only needs Python SDK changes — no infra change required

**Technical Context Observed (for Define):**

| Aspect | Observation | Implication |
|--------|-------------|-------------|
| Likely Location | `portal/agent/` (new files), `portal/app.py` (wiring) | New `providers/` subpackage under `portal/agent/` |
| Relevant KB Domains | `genai`, `python`, `pydantic`, `prompt-engineering` | Protocol pattern, async error handling, structured extraction |
| IaC Patterns | No infra change needed for Gemini (SDK-only); Ollama already deployed | Gemini API key via new K8s secret + env var |

---

## Discovery Questions and Answers

| # | Question | Answer | Impact |
|---|----------|--------|--------|
| 1 | What is the primary motivation — cost reduction, quality improvement, or resilience? | Quality improvement (Gemini > Ollama 3b on CPU for structured YAML) and resilience (Ollama hangs) | LLM quality is a first-class concern; fallback must exist but LLM path should be preferred |
| 2 | Who switches providers — data producers using the portal, or platform admins? | Data producers (end users typing in Chainlit) | Switching must be self-service inside the chat UI, not a config file change |
| 3 | Should switching be global (all sessions) or per-session? | Per-session via Chainlit UI | State goes in `cl.user_session`; no shared mutable state between sessions |
| 4 | What happens when the selected LLM fails (bad key, timeout, hang)? | Show a clear error in the chat; let the user pick another provider or retry before generating anything | No silent fallback; user retains control; error message must name the provider and cause |

---

## Sample Data Inventory

| Type | Location | Count | Notes |
|------|----------|-------|-------|
| Existing LLM prompt | `portal/agent/odcs_generator.py` L31-46 | 1 | `_PROMPT_TEMPLATE` — reused verbatim across providers |
| Existing fallback output | `portal/agent/odcs_generator.py` L100-128 | 1 | `_build_fallback_contract()` — valid ODCS v3.1 dict, used as validation reference |
| Column schema input | `portal/agent/schema_inspector.py` | N | `ColumnInfo` dataclass — same input shape for all providers |
| Integration test | `tests/integration/test_pipeline_e2e.py` | 1 | Existing e2e test to stay green after refactor |

**How samples will be used:**
- `_PROMPT_TEMPLATE` is extracted to a shared module and passed into any provider unchanged
- `_build_fallback_contract()` output is used as a test fixture baseline for provider output validation
- `ColumnInfo` schema defines the provider interface input signature

---

## Approaches Explored

### Approach A: Protocol-based provider abstraction + Chainlit ChatSettings panel (Recommended)

**Description:** Define a `LLMProvider` Python `Protocol` in `portal/agent/providers/base.py` with a single method `generate(prompt: str) -> str`. Implement three providers: `OllamaProvider`, `GeminiProvider`, `FallbackProvider`. Wire them in `app.py` using `cl.user_session`. Expose provider switching via both Chainlit `ChatSettings` (gear icon dropdown) and `/llm <name>` slash command — both write to the same session key. Fix the Ollama hang by replacing the 600-second `httpx.Client` with `asyncio.wait_for(asyncio.to_thread(...), timeout=30)`. On any provider exception, send a Chainlit error message and set the session provider to `None`, blocking further generation until the user re-selects.

**File layout:**
```
portal/
  agent/
    providers/
      __init__.py
      base.py          # LLMProvider Protocol
      ollama.py        # OllamaProvider
      gemini.py        # GeminiProvider (google-generativeai SDK)
      fallback.py      # FallbackProvider (wraps _build_fallback_contract)
    odcs_generator.py  # Refactored: prompt + parse logic only, no provider coupling
  app.py               # Chainlit wiring: ChatSettings + slash command + error handler
```

**Pros:**
- `app.py` depends only on the Protocol — never imports Ollama or Gemini SDKs directly
- `cl.user_session` gives true per-session isolation; concurrent users cannot affect each other
- ChatSettings gives a visual affordance discoverable by new users; slash command satisfies power users
- Ollama timeout fix is isolated to `OllamaProvider` and does not touch other providers
- Adding OpenAI or Anthropic later requires one new file — no changes to `app.py` or existing providers
- Error message names the provider and root cause before blocking further generation

**Cons:**
- Chainlit `ChatSettings` requires Chainlit >= 1.0 — version must be confirmed in `requirements.txt`
- Two UI surfaces (settings panel + slash command) need to stay consistent; a small helper function handles both writes to `cl.user_session`

**Why Recommended:** Matches the user's stated requirements exactly (per-session, visual UI, explicit error on failure). Directly fixes the Ollama hang as a first-class concern. The Protocol pattern from the `python` KB keeps the abstraction shallow — no abstract base classes, no metaclasses. Extending to more providers is a single file addition.

---

### Approach B: Slash-command only, env-var default

**Description:** No ChatSettings widget. Active provider defaults to `ODCS_LLM_PROVIDER` env var, overridden per-session by `/llm <name>` commands only. Same `LLMProvider` Protocol as Approach A.

**Pros:**
- Simpler UI surface — one mechanism only
- Env var gives operators cluster-level default control without code change

**Cons:**
- No visual affordance — discoverability depends entirely on the welcome message
- Chainlit's built-in settings panel is left unused, which feels inconsistent to users
- Current provider state is not visible without the user explicitly asking

---

### Approach C: LiteLLM unified gateway

**Description:** Replace all provider SDKs with `litellm.completion()`. Model string encodes provider (`"ollama/llama3.2:3b"`, `"gemini/gemini-2.0-flash"`). Per-session switching sets the model string in `cl.user_session`.

**Pros:**
- Single SDK call for all providers
- LiteLLM handles retries and token counting internally

**Cons:**
- Heavy transitive dependency for a two-provider use case (YAGNI)
- LiteLLM's Ollama async path has documented timeout issues that may reproduce the same hang
- Debugging failures is harder behind LiteLLM's abstraction layer
- Removed: see YAGNI section

---

## Selected Approach

| Attribute | Value |
|-----------|-------|
| **Chosen** | Approach A |
| **User Confirmation** | 2026-04-23 (implicit: user specified both ChatSettings/slash command and explicit error handling, matching Approach A exactly) |
| **Reasoning** | Per-session isolation, dual UI mechanism, explicit error surfacing, and isolated Ollama timeout fix all align with the user's four discovery answers |

---

## Key Decisions Made

| # | Decision | Rationale | Alternative Rejected |
|---|----------|-----------|----------------------|
| 1 | Use Python `Protocol` (structural typing) not ABC | Avoids forcing SDK wrappers to inherit from a project class; keeps provider files thin | Abstract base class — adds boilerplate with no benefit for three implementations |
| 2 | Store active provider instance in `cl.user_session` | True per-session isolation; Chainlit's documented pattern for session state | Module-level global — would cause cross-session contamination |
| 3 | Fix Ollama hang with `asyncio.wait_for(..., timeout=30)` not `httpx` timeout | The hang is an Ollama process stall, not a network timeout; `asyncio.wait_for` cancels the thread task and surfaces a clean `TimeoutError` | Increasing `httpx` timeout further — treats symptom not cause |
| 4 | On LLM failure, show error and block — do not auto-fallback | User explicitly chose option (b) during discovery; auto-fallback would silently hide provider failures | Silent fallback to rule-based builder — rejected by user in Q4 |
| 5 | Keep `_build_fallback_contract()` as an explicit `FallbackProvider` choice | User can consciously select it as a fast, deterministic option; keeps the path testable | Removing fallback entirely — reduces resilience |
| 6 | Reuse `_PROMPT_TEMPLATE` unchanged for all LLM providers | The prompt already produces clean ODCS v3.1 YAML; no per-provider prompt variation needed for MVP | Provider-specific prompt tuning — deferred (YAGNI) |

---

## Features Removed (YAGNI)

| Feature Suggested | Reason Removed | Can Add Later? |
|-------------------|----------------|----------------|
| Streaming token-by-token output in Chainlit | Contract YAML is short (~50 lines); latency improvement is marginal; adds async generator complexity to all providers | Yes |
| Per-message cost and token usage display | Useful but not needed for MVP; provider response metadata varies by SDK | Yes |
| Automatic provider rotation on failure (round-robin) | User explicitly chose explicit error + manual re-selection (Q4 answer b); auto-rotation contradicts that decision | Yes, as an opt-in setting |
| Model selection within a provider (e.g., gemini-2.0-flash vs gemini-pro) | One model per provider is sufficient for MVP; model string can be an env var default | Yes |
| LiteLLM unified gateway (Approach C) | Two provider SDKs is tractable; LiteLLM is a heavy dep for marginal gain at this scale | Yes, if providers > 4 |
| Admin UI to set org-wide default provider | This is a single-user local-dev portal; no multi-user RBAC exists | Yes |
| Provider-specific prompt templates | `_PROMPT_TEMPLATE` produces valid output for both Ollama and Gemini; tuning deferred | Yes |

---

## Incremental Validations

| Section | Presented | User Feedback | Adjusted? |
|---------|-----------|---------------|-----------|
| Switching scope (global vs per-session) | Q3 | Per-session via Chainlit UI | Yes — moved state to `cl.user_session` |
| Error handling strategy | Q4 | Show error, let user pick or retry | Yes — removed auto-fallback; added session block until re-selection |
| Approach options | This message | Approach A selected (implicit from requirements match) | No further changes needed |

---

## Suggested Requirements for /define

### Problem Statement (Draft)
The portal's contract generation is locked to a single Ollama instance that frequently hangs on CPU inference; users need to switch to faster cloud LLMs (starting with Gemini) per-session without restarting the portal, and must see explicit errors when a provider fails.

### Target Users (Draft)

| User | Pain Point |
|------|------------|
| Data producer (portal end user) | Ollama hangs for 3-8 minutes or indefinitely; no way to switch to a faster provider without asking a platform admin |
| Platform operator | Cannot change the default provider without redeploying; Gemini API key has nowhere to go in the current config |

### Success Criteria (Draft)
- [ ] `/llm gemini` typed in Chainlit switches the active provider for the current session only
- [ ] The Chainlit gear icon settings panel shows a provider dropdown that reflects and updates the active provider
- [ ] Ollama calls time out in at most 30 seconds and surface a named error message in the chat
- [ ] Gemini generates a valid ODCS v3.1 YAML contract using the same `_PROMPT_TEMPLATE`
- [ ] When a provider fails, no contract is saved and no Debezium connector is activated until the user retries with a working provider
- [ ] The `FallbackProvider` (rule-based) remains selectable as an explicit option, not a silent safety net
- [ ] Existing integration tests pass without modification after the refactor

### Constraints Identified
- Gemini API key must be injected as a Kubernetes Secret and surfaced as `GEMINI_API_KEY` env var — no hardcoded keys
- The `google-generativeai` Python SDK must be added to `requirements.txt` (or `pyproject.toml`)
- Chainlit version must be >= 1.0 to use `ChatSettings` — verify in current `requirements.txt`
- The refactor must not change the ODCS v3.1 output schema — `_build_fallback_contract()` output is the validation baseline
- No new Kubernetes deployments required — Gemini is a pure SDK call to Google's API

### Out of Scope (Confirmed)
- Streaming (token-by-token) LLM output in Chainlit
- Per-message cost or token usage display
- Automatic provider rotation / round-robin fallback
- Model selection within a provider
- LiteLLM gateway
- Admin UI for org-wide provider defaults
- Provider-specific prompt templates

---

## Session Summary

| Metric | Value |
|--------|-------|
| Questions Asked | 4 |
| Approaches Explored | 3 |
| Features Removed (YAGNI) | 7 |
| Validations Completed | 3 |
| Duration | Single session |

---

## Next Step

**Ready for:** `/define .claude/sdd/features/BRAINSTORM_MULTI_LLM_CONTRACT_GEN.md`
