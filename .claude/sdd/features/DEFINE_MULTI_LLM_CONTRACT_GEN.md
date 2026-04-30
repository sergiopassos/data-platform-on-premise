# DEFINE: Multi-LLM Contract Generation with Per-Session Switching

> Replace the single tightly-coupled Ollama call in the data contract portal with a Protocol-based LLM provider abstraction, fix the Ollama hang, add Google Gemini as a first-class provider, and expose per-session provider switching through both the Chainlit settings panel and a slash command.

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | MULTI_LLM_CONTRACT_GEN |
| **Date** | 2026-04-23 |
| **Author** | define-agent |
| **Status** | Ready for Design |
| **Clarity Score** | 15/15 |

---

## Problem Statement

The portal's ODCS v3.1 contract generation is locked to a single Ollama instance that hangs indefinitely on CPU inference (600-second `httpx` timeout, no cancellation); data producers have no way to switch to a faster provider without platform admin intervention, and no error is surfaced in the UI when generation stalls.

---

## Target Users

| User | Role | Pain Point |
|------|------|------------|
| Data producer | Portal end user (types in Chainlit) | Ollama hangs for 3–8 minutes or indefinitely; cannot switch to a faster LLM without asking an admin; receives no feedback when generation is stuck |
| Platform operator | Manages the KIND cluster | Cannot change the default provider without redeploying; Gemini API key has no injection path in the current config |

---

## Goals

What success looks like (prioritized):

| Priority | Goal |
|----------|------|
| **MUST** | Fix the Ollama hang by replacing the 600-second `httpx.Client` with `asyncio.wait_for(..., timeout=30)` |
| **MUST** | Add Google Gemini as a usable LLM provider via the `google-generativeai` SDK |
| **MUST** | Introduce a `LLMProvider` Python Protocol so `app.py` never imports provider SDKs directly |
| **MUST** | Store the active provider instance in `cl.user_session` to give true per-session isolation |
| **MUST** | When a provider fails, show a named error in the chat and block contract generation until the user explicitly re-selects a provider |
| **SHOULD** | Expose provider switching via a Chainlit `ChatSettings` gear-icon dropdown (requires Chainlit >= 1.0) |
| **SHOULD** | Expose provider switching via `/llm <name>` slash command as an alternative to the settings panel |
| **SHOULD** | Keep `FallbackProvider` (wrapping `_build_fallback_contract`) as an explicit user-selectable option, not a silent safety net |
| **COULD** | Accept `GEMINI_API_KEY` via environment variable so a future env-var default provider is possible without code changes |

**Priority Guide:**
- **MUST** = MVP fails without this
- **SHOULD** = Important, but workaround exists
- **COULD** = Nice-to-have, cut first if needed

---

## Success Criteria

Measurable outcomes:

- [ ] Typing `/llm gemini` in Chainlit switches the active provider for the current session only; other concurrent sessions are unaffected
- [ ] The Chainlit gear icon settings panel displays a provider dropdown; selecting a value writes to `cl.user_session` and is reflected immediately
- [ ] Both the slash command and the settings dropdown write to the same session key via a shared helper (no duplication of state logic)
- [ ] Ollama calls time out in at most 30 seconds and surface a chat message naming the provider (`Ollama`) and the error type (`TimeoutError`)
- [ ] Gemini generates a valid ODCS v3.1 YAML contract (passes the same `_parse_and_validate` logic) using the shared `_PROMPT_TEMPLATE` unchanged
- [ ] When any provider raises an exception, no contract YAML is saved and no Debezium connector activation is triggered; the session provider is set to `None`
- [ ] `FallbackProvider` is selectable by the user and produces a deterministic ODCS v3.1 dict without any LLM call
- [ ] The existing integration test `tests/integration/test_pipeline_e2e.py` passes without modification after the refactor
- [ ] No provider SDK (`httpx` for Ollama, `google-generativeai` for Gemini) is imported in `portal/app.py`

---

## Acceptance Tests

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AT-001 | Gemini generates a valid contract | Active provider is `gemini`, `GEMINI_API_KEY` is set | User requests contract for a PostgreSQL table | Chainlit displays valid ODCS v3.1 YAML; connector activation proceeds |
| AT-002 | Ollama timeout is surfaced | Active provider is `ollama`, Ollama process stalls | LLM call exceeds 30 seconds | Chat shows "Ollama timed out after 30 s"; contract generation is blocked; session provider becomes `None` |
| AT-003 | Bad Gemini API key shows named error | Active provider is `gemini`, `GEMINI_API_KEY` is invalid | User requests contract generation | Chat shows error naming "Gemini" and root cause; no contract saved; session provider becomes `None` |
| AT-004 | Slash command switches session only | Two concurrent sessions active | User in session A types `/llm gemini`; user in session B types `/llm ollama` | Session A uses Gemini; session B uses Ollama; no cross-session contamination |
| AT-005 | Settings panel switches provider | User opens gear icon settings panel, selects "Fallback" | User requests contract generation | Contract generated by `FallbackProvider` (rule-based, no LLM call); result is a valid ODCS v3.1 dict |
| AT-006 | Blocked generation after failure | Session provider is `None` after a previous failure | User requests contract generation without re-selecting a provider | Chat shows "No provider selected. Use /llm <name> or the settings panel to pick one."; no generation attempted |
| AT-007 | Existing e2e test stays green | Full platform stack running | `pytest tests/integration/test_pipeline_e2e.py` executed | All assertions pass; no test modification required |

---

## Out of Scope

Explicitly NOT included in this feature:

- Streaming (token-by-token) LLM output in Chainlit — contract YAML is short; async generator complexity not justified for MVP
- Per-message cost or token usage display — provider response metadata varies by SDK; deferred
- Automatic provider rotation or round-robin fallback — user explicitly chose explicit error + manual re-selection
- Model selection within a provider (e.g., `gemini-2.0-flash` vs `gemini-pro`) — one model per provider; model string can be an env var default
- LiteLLM unified gateway — two SDKs is tractable; LiteLLM is a heavy dependency for marginal gain at this scale
- Admin UI for org-wide provider defaults — single-user local-dev portal; no multi-user RBAC exists
- Provider-specific prompt templates — `_PROMPT_TEMPLATE` produces valid output for both providers; tuning deferred
- Any new Kubernetes Deployments or Services — Gemini is a pure SDK call; no new workloads required

---

## Constraints

| Type | Constraint | Impact |
|------|------------|--------|
| Technical | Gemini API key injected as Kubernetes Secret, surfaced as `GEMINI_API_KEY` env var — no hardcoded keys | Design must include a Secret manifest or reference an existing secret management pattern |
| Technical | `google-generativeai` Python SDK must be added to `portal/requirements.txt` (or `pyproject.toml`) | Dependency update required; Docker image rebuild needed |
| Technical | Chainlit version must be >= 1.0 to use `ChatSettings` | Must verify current pinned version in `portal/requirements.txt` before design proceeds |
| Technical | Refactor must not change the ODCS v3.1 output schema — `_build_fallback_contract()` output is the validation baseline | Parser and validation logic in `odcs_generator.py` stays untouched |
| Technical | `asyncio.wait_for(..., timeout=30)` must wrap `asyncio.to_thread(...)` — not an `httpx` timeout | The hang is an Ollama process stall, not a network timeout; this distinction is load-bearing |
| Operational | No new Kubernetes deployments required | Gemini is SDK-only; no cluster-level change except the new Secret |

---

## Technical Context

> Essential context for Design phase.

| Aspect | Value | Notes |
|--------|-------|-------|
| **Deployment Location** | `portal/agent/providers/` (new subpackage) + `portal/app.py` (wiring) | New files: `base.py`, `ollama.py`, `gemini.py`, `fallback.py`, `__init__.py`; `odcs_generator.py` refactored in place |
| **KB Domains** | `genai`, `python`, `pydantic`, `prompt-engineering`, `testing`, `on-premise-k8s` | `genai` for Protocol/chatbot pattern; `python` for Protocol + async patterns; `pydantic` for LLM output validation; `prompt-engineering` for prompt template reuse; `testing` for mocking providers in unit tests; `on-premise-k8s` for Kubernetes Secret pattern |
| **IaC Impact** | New K8s Secret for `GEMINI_API_KEY` | No new Deployments or Services; one new `Secret` manifest needed in the platform Helm values or a dedicated secret file |

**Why This Matters:**

- **Location** — The `providers/` subpackage keeps each provider file thin and prevents `app.py` from importing SDKs directly.
- **KB Domains** — `python` KB supplies the Protocol pattern; `genai` KB supplies the chatbot/session-state pattern; `testing` KB supplies mock-provider fixture patterns.
- **IaC Impact** — The Kubernetes Secret must exist before `GeminiProvider` is instantiated; Design phase must specify the secret name and key reference.

---

## Assumptions

| ID | Assumption | If Wrong, Impact | Validated? |
|----|------------|------------------|------------|
| A-001 | Chainlit version pinned in `portal/requirements.txt` is >= 1.0 | `ChatSettings` API may not be available; SHOULD goals would need a different UI mechanism | [ ] — verify before design |
| A-002 | The `google-generativeai` SDK supports async usage compatible with Chainlit's event loop | May need `asyncio.to_thread` wrapper for Gemini calls, matching the Ollama pattern | [ ] — verify SDK docs |
| A-003 | `_PROMPT_TEMPLATE` produces acceptable ODCS v3.1 YAML when sent to Gemini without modification | If Gemini output is poorly structured, a Gemini-specific post-processing step may be needed | [ ] — validate with a test call |
| A-004 | The existing integration test `tests/integration/test_pipeline_e2e.py` mocks the LLM call | If it makes a live Ollama call, it will break if Ollama is not running in CI; may need a `FallbackProvider` override | [ ] — inspect test setup |
| A-005 | Kubernetes Secret for `GEMINI_API_KEY` can be created manually or via the bootstrap script | If it must go through ArgoCD/Helm, a new secret template or sealed-secret pattern is needed | [ ] — check bootstrap approach |

**Note:** Validate A-001 and A-004 before starting Design phase. These two are highest-risk.

---

## Clarity Score Breakdown

| Element | Score (0-3) | Notes |
|---------|-------------|-------|
| Problem | 3/3 | One-sentence statement names the root cause (600-second httpx hang), the affected user (data producers), and the missing capability (provider switching) |
| Users | 3/3 | Two personas with role and specific, distinct pain points; switching motivations differ (UX vs ops) |
| Goals | 3/3 | All goals are MoSCoW-classified, actionable, and non-overlapping; MUST goals are individually necessary for MVP |
| Success | 3/3 | Nine testable criteria with specific values (30 s timeout, session isolation, zero connector activation on failure) |
| Scope | 3/3 | Seven explicit out-of-scope items with rationale; no ambiguous "maybe later" items |
| **Total** | **15/15** | |

---

## Open Questions

None — ready for Design.

All critical assumptions (A-001 through A-005) should be validated at the start of the Design phase, but none block the requirements themselves.

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-04-23 | define-agent | Initial version from BRAINSTORM_MULTI_LLM_CONTRACT_GEN.md |

---

## Next Step

**Ready for:** `/design .claude/sdd/features/DEFINE_MULTI_LLM_CONTRACT_GEN.md`
