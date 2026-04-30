# DESIGN: Multi-LLM Contract Generation with Per-Session Switching

> Technical design for replacing the single tightly-coupled Ollama call in the portal with a Protocol-based LLM provider abstraction, fixing the Ollama hang, adding Google Gemini, and exposing per-session provider switching via `ChatSettings` and a `/llm` slash command.

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | MULTI_LLM_CONTRACT_GEN |
| **Date** | 2026-04-23 |
| **Author** | design-agent |
| **DEFINE** | [DEFINE_MULTI_LLM_CONTRACT_GEN.md](./DEFINE_MULTI_LLM_CONTRACT_GEN.md) |
| **Status** | Ready for Build |

---

## Assumption Validation (from DEFINE)

| ID | Assumption | Status | Evidence |
|----|------------|--------|----------|
| A-001 | Chainlit >= 1.0 (supports `ChatSettings`) | Validated | `portal/requirements.txt` pins `chainlit==2.0.4` |
| A-002 | `google-generativeai` SDK has a sync surface | Validated | SDK exposes sync `GenerativeModel.generate_content`; we wrap with `asyncio.to_thread` matching Ollama path |
| A-003 | `_PROMPT_TEMPLATE` works for Gemini unchanged | Assumed | Preserved unchanged; Gemini will be exercised during AT-001 |
| A-004 | Integration test mocks the LLM call | Partially | `tests/integration/test_pipeline_e2e.py` never exercises the portal's LLM; it hits Kafka Connect / Trino only. Safe to leave untouched (SC-8) |
| A-005 | Kubernetes Secret can be created via bootstrap script | Validated | `cluster/bootstrap.sh` section "8. postgres-source-secret" is the pattern; append a new section for `gemini-api-secret` in the `portal` namespace |

---

## Architecture Overview

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                          Chainlit Portal (Python)                             │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│   ┌─────────────────┐     @cl.on_chat_start / @cl.on_settings_update         │
│   │  portal/app.py  │─────────────────────────────┐                          │
│   │   (UI wiring)   │  @cl.on_message (dispatch)  │                          │
│   └────────┬────────┘                             │                          │
│            │                                       │                          │
│            │ set/get provider via                  │                          │
│            ▼ cl.user_session                       ▼                          │
│   ┌─────────────────────┐          ┌───────────────────────────┐             │
│   │ agent/session.py    │          │  agent/commands.py        │             │
│   │ get/set_provider()  │◄─────────│  parse /llm <name>        │             │
│   │ build_from_name()   │          │  parse ChatSettings value │             │
│   └──────────┬──────────┘          └───────────────────────────┘             │
│              │  build_from_name(name) -> LLMProvider                          │
│              ▼                                                                │
│   ┌──────────────────────────────────────────────────────────────────┐       │
│   │  agent/providers/  (new subpackage — SDKs isolated here)         │       │
│   │                                                                   │       │
│   │  ┌────────────┐  ┌─────────────┐  ┌──────────────┐  ┌─────────┐ │       │
│   │  │  base.py   │  │  ollama.py  │  │  gemini.py   │  │fallback │ │       │
│   │  │ LLMProvider│  │ OllamaProv. │  │ GeminiProv.  │  │Provider │ │       │
│   │  │ (Protocol) │  │  httpx +    │  │ google-gen   │  │ no LLM  │ │       │
│   │  │ ProviderErr│  │ asyncio     │  │ SDK          │  │ rule    │ │       │
│   │  │            │  │ .wait_for 30│  │ .to_thread   │  │ based   │ │       │
│   │  └────────────┘  └─────────────┘  └──────────────┘  └─────────┘ │       │
│   └──────────────────────────────┬───────────────────────────────────┘       │
│                                  │ generate_yaml(prompt) -> str              │
│                                  ▼                                            │
│   ┌──────────────────────────────────────────────────────────────┐           │
│   │  agent/odcs_generator.py  (refactored — provider-agnostic)   │           │
│   │                                                               │           │
│   │   ODCSGenerator(provider: LLMProvider)                       │           │
│   │   ├── generate(table, cols) -> dict                          │           │
│   │   ├── _build_prompt(...)  (shared _PROMPT_TEMPLATE)          │           │
│   │   ├── _parse_and_validate(raw_yaml)  (unchanged)             │           │
│   │   └── _build_fallback_contract(...)  (unchanged)             │           │
│   └──────────────────────────────────────────────────────────────┘           │
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
          ┌──────────────────────────────────────────────────┐
          │              External Integrations                │
          ├──────────────────────────────────────────────────┤
          │  Ollama HTTP  (portal.svc.cluster.local:11434)   │
          │  Gemini API   (generativelanguage.googleapis.com)│
          │  K8s Secret   (gemini-api-secret in ns portal)   │
          └──────────────────────────────────────────────────┘
```

---

## Components

| Component | Purpose | Technology |
|-----------|---------|------------|
| `agent/providers/base.py` | `LLMProvider` Protocol + `ProviderError` exception hierarchy + provider registry | Python 3.11 `typing.Protocol`, `runtime_checkable` |
| `agent/providers/ollama.py` | Ollama provider: `httpx` + `asyncio.wait_for(asyncio.to_thread(...), timeout=30)` | `httpx` 0.27 |
| `agent/providers/gemini.py` | Gemini provider: `google.generativeai.GenerativeModel.generate_content` wrapped in `asyncio.to_thread` + `asyncio.wait_for` | `google-generativeai >= 0.8` |
| `agent/providers/fallback.py` | Deterministic ODCS v3.1 dict builder; no network call | stdlib only |
| `agent/providers/__init__.py` | Exposes `LLMProvider`, `ProviderError`, `build_from_name`, `KNOWN_PROVIDERS` | Python package |
| `agent/session.py` | `cl.user_session` accessors: `get_provider()`, `set_provider()`, `clear_provider()`; session key constant `SESSION_KEY_PROVIDER` | Chainlit 2.0.4 |
| `agent/commands.py` | Parses `/llm <name>` slash command; shared helper used by both `on_message` and `on_settings_update` | stdlib |
| `agent/odcs_generator.py` | Orchestrates prompt build, provider call, parse/validate; provider-agnostic (takes `LLMProvider` at call time, not init time) | `pyyaml`, dep-injected provider |
| `portal/app.py` | Chainlit wiring: `@cl.on_chat_start` sends `ChatSettings`; `@cl.on_settings_update` updates session; `@cl.on_message` dispatches `/llm` or contract generation | Chainlit 2.0.4 |
| `helm/chainlit/values.yaml` | Adds `GEMINI_API_KEY`, `GEMINI_MODEL`, `DEFAULT_LLM_PROVIDER` env entries | Helm |
| `manifests/platform/gemini-api-secret.yaml` | K8s Secret manifest template (actual secret created via bootstrap script with env var) | Kubernetes |
| `cluster/bootstrap.sh` | Creates `gemini-api-secret` in `portal` namespace from `$GEMINI_API_KEY` | Bash |

---

## Key Decisions

### Decision 1: Protocol (not ABC) for the provider interface

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** DEFINE requires a provider abstraction so `app.py` never imports SDKs directly. Two options: `abc.ABC` subclassing or `typing.Protocol` structural typing.

**Choice:** `typing.Protocol` with `@runtime_checkable` in `agent/providers/base.py`.

**Rationale:**
- Providers are cleanly decoupled: a provider class does not need to import `LLMProvider` to satisfy it; tests can use a plain `MagicMock` and pass `isinstance(mock, LLMProvider)` at runtime.
- Matches `.claude/kb/python` clean-architecture guidance (interfaces in application layer; adapters depend on them without inheritance).
- Async-compatible: methods declared `async` in the Protocol are checked structurally.

**Alternatives Rejected:**
1. `abc.ABC` — Rejected because it forces inheritance which couples providers to our codebase; harder to mock without concrete subclasses.
2. Callable (`Callable[[str], Awaitable[str]]`) — Rejected because we need per-provider `name` attribute for error messages (AT-002, AT-003) and a named `close()` hook for future cleanup.

**Consequences:**
- `isinstance()` checks work only at runtime; Python < 3.12 has no static type check for Protocol conformance by default — mypy/pyright handle it.
- All providers must implement `name: str` and `async def generate_yaml(prompt: str) -> str`.

---

### Decision 2: Fix the Ollama hang with `asyncio.wait_for(asyncio.to_thread(...), 30)`, not an `httpx` timeout

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** The current code uses `httpx.Client(timeout=600)`. The hang is an Ollama process stall on CPU inference, not a network timeout; `httpx` never fires because bytes are still trickling.

**Choice:** Keep a short `httpx` connect/read timeout (60 s) as a safety net, but wrap the whole synchronous call with `asyncio.wait_for(asyncio.to_thread(call_sync), timeout=30)`.

**Rationale:**
- DEFINE MUST goal is explicit: "Fix the Ollama hang by replacing the 600-second `httpx.Client` with `asyncio.wait_for(..., timeout=30)`".
- Constraint in DEFINE: "`asyncio.wait_for(..., timeout=30)` must wrap `asyncio.to_thread(...)` — not an `httpx` timeout".
- When `wait_for` fires, it cancels the task; the thread continues running but the coroutine raises `asyncio.TimeoutError` immediately — the UI unblocks.

**Alternatives Rejected:**
1. Keep `httpx.Client(timeout=30)` only — Rejected because network timeouts don't fire when the server is streaming slow bytes.
2. Use `httpx.AsyncClient` natively — Rejected: Ollama sync endpoint is fine; the Ollama-side slowness is the bug, not the client API choice, and adding AsyncClient doesn't give us a hard wall-clock cancellation of the coroutine.

**Consequences:**
- The background thread may outlive the timeout; acceptable because Ollama runs in-cluster and will finish or die on its own. We log this at WARNING.
- Hard 30 s SLA from the user's perspective.

---

### Decision 3: Session state in `cl.user_session`, shared helper for both command and settings

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** DEFINE requires both `/llm` command and `ChatSettings` dropdown to write to the same place (SC-3). Duplicating session logic across two event handlers risks drift.

**Choice:** Single helper module `agent/session.py` exposing `get_provider()`, `set_provider_by_name(name)`, `clear_provider()`. Both `@cl.on_message` (when message starts with `/llm`) and `@cl.on_settings_update` call `set_provider_by_name()`.

**Rationale:**
- Single source of truth; SC-3 is mechanically enforced because there's only one writer.
- `cl.user_session` is per-websocket-session by design, guaranteeing AT-004 isolation.
- Builder `build_from_name()` in `providers/__init__.py` is the only place provider classes are instantiated, keeping `app.py` SDK-free.

**Alternatives Rejected:**
1. Global module-level dict keyed by session id — Rejected: re-implements `cl.user_session` poorly; risks memory leaks on disconnect.
2. Separate code paths in command vs settings — Rejected: duplication; violates SC-3.

**Consequences:**
- A session that has never selected a provider has `None`; generation is blocked until user selects (AT-006). On `on_chat_start` we optionally pre-populate from `DEFAULT_LLM_PROVIDER` env var (COULD goal).

---

### Decision 4: `odcs_generator.ODCSGenerator` takes the provider at `generate()` call time, not at construction

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** Current code constructs `ODCSGenerator(ollama_url=..., model=...)` at module import in `app.py`. With per-session providers, the generator cannot bind to one provider at startup.

**Choice:** Refactor `ODCSGenerator` so that `__init__` takes no provider, and `generate(table, columns, *, provider: LLMProvider)` receives the provider per call.

**Rationale:**
- Provider lifetime is per-session; generator is stateless → can be a module-level singleton reused across all sessions.
- Keeps `_parse_and_validate` and `_build_fallback_contract` unchanged — SC-4/SC-5 from DEFINE (preserve output schema).

**Alternatives Rejected:**
1. Construct a new `ODCSGenerator` per session — Rejected: unnecessary object churn; generator has no session state.
2. Pass the provider via `cl.user_session` read inside the generator — Rejected: couples the generator to Chainlit; breaks unit-test isolation.

**Consequences:**
- Existing unit test in `tests/unit/portal/test_odcs_generator.py` needs update: mocked `httpx.Client` must become mocked provider. Refactor is bounded; patterns shown in Code Patterns section.

---

### Decision 5: `FallbackProvider` is a first-class user-selectable provider, not a silent safety net

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** Current code silently falls back to `_build_fallback_contract()` if YAML parse fails. DEFINE SHOULD goal is to make fallback explicit.

**Choice:** Keep `_build_fallback_contract()` as a private method for parse-error recovery (preserving current behavior). Additionally expose `FallbackProvider` which calls the same helper without any LLM; selectable from the settings dropdown as `"fallback"`.

**Rationale:**
- AT-005 requires user-selectable deterministic generation.
- SC-4 requires preserving existing parse-error fallback; users who chose Gemini/Ollama and got malformed YAML still get a valid contract (current behavior).

**Alternatives Rejected:**
1. Remove automatic parse-error fallback — Rejected: breaks SC-4 and existing `test_invalid_yaml_falls_back_to_built_contract` test.

**Consequences:**
- Two code paths to the same builder function; acceptable because they serve different purposes (user-selected vs parse-error recovery).

---

### Decision 6: On provider failure, set session provider to `None` and block

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-23 |

**Context:** DEFINE: "When a provider fails, show a named error in the chat and block contract generation until the user explicitly re-selects a provider" (AT-002, AT-003, AT-006).

**Choice:** `app.py` wraps the `generate()` call in `try/except ProviderError`. On exception: send error message naming `provider.name` and `type(err).__name__`, call `clear_provider()`, return early. Subsequent messages check `get_provider() is None` and prompt re-selection.

**Rationale:**
- Explicit over implicit — matches DEFINE's "no silent fallback" principle.
- Prevents cascading failures where a broken Gemini API key silently retries on every message.

**Alternatives Rejected:**
1. Auto-retry with backoff — Rejected: out of scope (DEFINE out-of-scope bullet).
2. Auto-swap to next available provider — Rejected: user wants explicit control (AT-003 rationale).

**Consequences:**
- User friction on failure (must re-select). Acceptable because failures are rare and this is a single-user local-dev portal.

---

## File Manifest

| # | File | Action | Purpose | Agent | Dependencies |
|---|------|--------|---------|-------|--------------|
| 1 | `portal/agent/providers/__init__.py` | Create | Export `LLMProvider`, `ProviderError`, `build_from_name`, `KNOWN_PROVIDERS` | @python-developer | 2, 3, 4, 5 |
| 2 | `portal/agent/providers/base.py` | Create | `LLMProvider` Protocol + `ProviderError`, `ProviderTimeoutError`, `ProviderAPIError` exceptions | @python-developer | None |
| 3 | `portal/agent/providers/ollama.py` | Create | `OllamaProvider` with `httpx` + `asyncio.wait_for(asyncio.to_thread(...), 30)` | @llm-specialist | 2 |
| 4 | `portal/agent/providers/gemini.py` | Create | `GeminiProvider` using `google.generativeai`, wrapped in `asyncio.to_thread` + `asyncio.wait_for` | @llm-specialist | 2 |
| 5 | `portal/agent/providers/fallback.py` | Create | `FallbackProvider` — no-LLM deterministic ODCS dict generator | @python-developer | 2 |
| 6 | `portal/agent/session.py` | Create | `cl.user_session` accessors: `get_provider()`, `set_provider_by_name()`, `clear_provider()` | @python-developer | 1 |
| 7 | `portal/agent/commands.py` | Create | Slash-command parser for `/llm <name>`; shared by message and settings handlers | @python-developer | 1, 6 |
| 8 | `portal/agent/odcs_generator.py` | Modify | Remove `httpx`/`ollama_url`/`model` init args; take `provider: LLMProvider` at `generate()` call; keep parse/validate untouched | @python-developer | 1 |
| 9 | `portal/app.py` | Modify | Add `ChatSettings` in `on_chat_start`; add `@cl.on_settings_update`; dispatch `/llm` in `on_message`; surface `ProviderError` as named chat error; block when session provider is `None` | @python-developer | 1, 6, 7, 8 |
| 10 | `portal/requirements.txt` | Modify | Add `google-generativeai==0.8.3` | @python-developer | None |
| 11 | `helm/chainlit/values.yaml` | Modify | Add `GEMINI_API_KEY` (from Secret), `GEMINI_MODEL` (literal), `DEFAULT_LLM_PROVIDER` (literal) env entries | @on-premise-k8s-specialist | 13 |
| 12 | `manifests/platform/gemini-api-secret.yaml` | Create | Template/documentation stub for the `gemini-api-secret` Secret (actual value from env via bootstrap) | @on-premise-k8s-specialist | None |
| 13 | `cluster/bootstrap.sh` | Modify | Add section "8b. gemini-api-secret for Chainlit portal" creating the Secret from `$GEMINI_API_KEY` env var | @on-premise-k8s-specialist | 12 |
| 14 | `tests/unit/portal/providers/__init__.py` | Create | Package marker | @test-generator | None |
| 15 | `tests/unit/portal/providers/test_base.py` | Create | Test Protocol conformance of all three providers (structural) | @test-generator | 2, 3, 4, 5 |
| 16 | `tests/unit/portal/providers/test_ollama.py` | Create | Test `asyncio.wait_for` timeout behavior, error wrapping, happy path | @test-generator | 3 |
| 17 | `tests/unit/portal/providers/test_gemini.py` | Create | Test Gemini SDK call mocking, API-key-missing error, timeout behavior | @test-generator | 4 |
| 18 | `tests/unit/portal/providers/test_fallback.py` | Create | Test deterministic output matches `_build_fallback_contract` shape | @test-generator | 5 |
| 19 | `tests/unit/portal/test_session.py` | Create | Test `get/set/clear_provider` round-trip with fake `cl.user_session` | @test-generator | 6 |
| 20 | `tests/unit/portal/test_commands.py` | Create | Test `/llm <name>` parser: known names, unknown name, malformed command, non-command message | @test-generator | 7 |
| 21 | `tests/unit/portal/test_odcs_generator.py` | Modify | Replace `@patch("...httpx.Client")` with fake `LLMProvider` implementations | @test-generator | 8 |

**Total Files:** 21 (14 create, 7 modify)

---

## Agent Assignment Rationale

> Agents discovered from `.claude/agents/` — Build phase invokes matched specialists.

| Agent | Files Assigned | Why This Agent |
|-------|----------------|----------------|
| @python-developer | 1, 2, 5, 6, 7, 8, 9, 10 | Core Python code: Protocol definitions, session helpers, command parsers, Chainlit wiring, `requirements.txt`. KB domains match: `python`, `pydantic`, `testing`. |
| @llm-specialist | 3, 4 | Provider implementations that call LLM SDKs directly (Ollama HTTP, `google-generativeai`). KB domains match: `prompt-engineering`, `genai`. Both files include the timeout-wrapping pattern which is LLM-infrastructure territory. |
| @on-premise-k8s-specialist | 11, 12, 13 | Helm values, Kubernetes Secret manifest, and bootstrap script changes. KB domains match: `on-premise-k8s`. Already owns `postgres-source-secret` pattern. |
| @test-generator | 14, 15, 16, 17, 18, 19, 20, 21 | All test files (unit + test refactor). KB domains match: `testing`, `data-quality`. |

**Agent Discovery:**
- Scanned: `.claude/agents/**/*.md` (23 agents total)
- Matched by: KB domain overlap with DEFINE, path patterns (`providers/` → llm-specialist for SDK files; `python-developer` for orchestration), file type (`.yaml` + K8s → on-premise-k8s-specialist), purpose keywords (`test_*.py` → test-generator).

---

## Code Patterns

### Pattern 1: `LLMProvider` Protocol + exception hierarchy

```python
# portal/agent/providers/base.py
"""LLMProvider Protocol and shared exception types.

All provider implementations live in sibling modules. portal/app.py must
never import a provider implementation directly — it uses build_from_name()
from the package __init__.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class ProviderError(Exception):
    """Base for all provider-level failures surfaced to the user."""

    def __init__(self, provider_name: str, message: str) -> None:
        super().__init__(f"{provider_name}: {message}")
        self.provider_name = provider_name


class ProviderTimeoutError(ProviderError):
    """Raised when a provider exceeds its wall-clock budget."""


class ProviderAPIError(ProviderError):
    """Raised on remote-service errors (HTTP 4xx/5xx, invalid API key, etc.)."""


@runtime_checkable
class LLMProvider(Protocol):
    """Structural interface for ODCS-generating LLM providers.

    Any class that exposes a string `name` attribute and an async
    `generate_yaml(prompt)` method satisfies this protocol. No inheritance
    required.
    """

    name: str

    async def generate_yaml(self, prompt: str) -> str:
        """Return raw YAML string for the given prompt.

        Implementations MUST:
          - Wrap any blocking SDK call in `asyncio.to_thread(...)`.
          - Wrap the full call with `asyncio.wait_for(..., timeout=...)`.
          - Translate SDK-specific exceptions into `ProviderError` subclasses.
        """
        ...
```

### Pattern 2: `OllamaProvider` refactor with `asyncio.wait_for`

```python
# portal/agent/providers/ollama.py
"""Ollama provider — fixes the 600 s hang via asyncio.wait_for wrapper."""
from __future__ import annotations

import asyncio
import logging

import httpx

from .base import LLMProvider, ProviderAPIError, ProviderTimeoutError

_LOG = logging.getLogger(__name__)
_DEFAULT_TIMEOUT_S = 30
_HTTPX_CONNECT_TIMEOUT_S = 10
_HTTPX_READ_TIMEOUT_S = 60  # safety net; wait_for fires before this


class OllamaProvider:
    """Ollama HTTP provider with hard wall-clock timeout."""

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
            # Note: background thread may continue; Ollama will finish on its own
            _LOG.warning("Ollama call exceeded %s s; coroutine cancelled", self._timeout_s)
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
```

### Pattern 3: `GeminiProvider`

```python
# portal/agent/providers/gemini.py
"""Google Gemini provider using google-generativeai SDK."""
from __future__ import annotations

import asyncio
import logging
import os

import google.generativeai as genai
from google.api_core import exceptions as google_exc

from .base import LLMProvider, ProviderAPIError, ProviderTimeoutError

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
        # Defensive: Gemini returns .text when there is a single candidate
        if not response or not getattr(response, "text", None):
            raise ProviderAPIError(self.name, "empty response")
        return response.text
```

### Pattern 4: `FallbackProvider`

```python
# portal/agent/providers/fallback.py
"""No-LLM fallback provider — deterministic, user-selectable."""
from __future__ import annotations

import json

from .base import LLMProvider


class FallbackProvider:
    """Returns a YAML stub that the generator's _parse_and_validate
    will intercept and rebuild via _build_fallback_contract. The empty
    string signals ODCSGenerator to take the fallback branch.

    Note: we purposely return a string that yaml.safe_load parses to None,
    triggering the existing fallback path. This keeps generator logic
    unchanged.
    """

    name = "fallback"

    async def generate_yaml(self, prompt: str) -> str:
        return ""  # triggers _build_fallback_contract in _parse_and_validate
```

### Pattern 5: Provider registry + factory

```python
# portal/agent/providers/__init__.py
"""Provider registry — single place that instantiates concrete providers.

portal/app.py calls build_from_name(); it never imports *Provider classes
directly, keeping SDK imports out of the Chainlit wiring layer.
"""
from __future__ import annotations

import os
from typing import Callable

from .base import LLMProvider, ProviderAPIError, ProviderError, ProviderTimeoutError
from .fallback import FallbackProvider
from .gemini import GeminiProvider
from .ollama import OllamaProvider

__all__ = [
    "LLMProvider",
    "ProviderError",
    "ProviderTimeoutError",
    "ProviderAPIError",
    "build_from_name",
    "KNOWN_PROVIDERS",
]

KNOWN_PROVIDERS: tuple[str, ...] = ("ollama", "gemini", "fallback")


def _build_ollama() -> LLMProvider:
    return OllamaProvider(
        ollama_url=os.getenv("OLLAMA_URL", "http://ollama.portal.svc.cluster.local:11434"),
        model=os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
    )


def _build_gemini() -> LLMProvider:
    return GeminiProvider()  # reads GEMINI_API_KEY / GEMINI_MODEL from env


def _build_fallback() -> LLMProvider:
    return FallbackProvider()


_BUILDERS: dict[str, Callable[[], LLMProvider]] = {
    "ollama": _build_ollama,
    "gemini": _build_gemini,
    "fallback": _build_fallback,
}


def build_from_name(name: str) -> LLMProvider:
    """Construct a provider by short name. Raises ValueError on unknown name.

    Provider-construction errors (e.g., missing GEMINI_API_KEY) propagate
    as ProviderError and must be caught by the caller.
    """
    key = name.strip().lower()
    if key not in _BUILDERS:
        raise ValueError(f"Unknown provider '{name}'. Known: {KNOWN_PROVIDERS}")
    return _BUILDERS[key]()
```

### Pattern 6: Session helper

```python
# portal/agent/session.py
"""Per-session LLM provider accessors backed by cl.user_session."""
from __future__ import annotations

import chainlit as cl

from .providers import LLMProvider, build_from_name

SESSION_KEY_PROVIDER = "llm_provider"


def get_provider() -> LLMProvider | None:
    provider = cl.user_session.get(SESSION_KEY_PROVIDER)
    if provider is None:
        return None
    assert isinstance(provider, LLMProvider), "corrupt session state"
    return provider


def set_provider_by_name(name: str) -> LLMProvider:
    """Build and store. Raises ValueError for unknown name or ProviderError
    on construction failure (e.g., missing GEMINI_API_KEY)."""
    provider = build_from_name(name)
    cl.user_session.set(SESSION_KEY_PROVIDER, provider)
    return provider


def clear_provider() -> None:
    cl.user_session.set(SESSION_KEY_PROVIDER, None)
```

### Pattern 7: Slash-command parser

```python
# portal/agent/commands.py
"""Slash-command parsing for the portal chat input."""
from __future__ import annotations

from dataclasses import dataclass

from .providers import KNOWN_PROVIDERS


@dataclass(frozen=True)
class LLMCommand:
    """Parsed /llm <name> invocation."""

    provider_name: str


def parse_llm_command(message: str) -> LLMCommand | None:
    """Return LLMCommand if message is a well-formed /llm invocation, else None.

    Examples:
      "/llm gemini"         -> LLMCommand("gemini")
      "/llm  Ollama  "      -> LLMCommand("ollama")
      "/llm"                -> None  (missing arg)
      "/llm foo bar"        -> None  (too many args)
      "hello"               -> None  (not a command)
    """
    stripped = message.strip()
    if not stripped.startswith("/llm"):
        return None
    parts = stripped.split()
    if len(parts) != 2:
        return None
    return LLMCommand(provider_name=parts[1].lower())


def is_known_provider(name: str) -> bool:
    return name.lower() in KNOWN_PROVIDERS
```

### Pattern 8: `ChatSettings` + `/llm` integration in `app.py`

```python
# portal/app.py (excerpt — additions and modifications only)
import asyncio
import os
from pathlib import Path

import chainlit as cl
from chainlit.input_widget import Select

from agent.commands import is_known_provider, parse_llm_command
from agent.odcs_generator import ODCSGenerator
from agent.providers import KNOWN_PROVIDERS, ProviderError
from agent.session import clear_provider, get_provider, set_provider_by_name

_generator = ODCSGenerator()  # no provider at construction — injected per call
_DEFAULT_PROVIDER = os.getenv("DEFAULT_LLM_PROVIDER", "ollama")


@cl.on_chat_start
async def on_start() -> None:
    await cl.ChatSettings(
        [
            Select(
                id="llm_provider",
                label="LLM Provider",
                values=list(KNOWN_PROVIDERS),
                initial_index=list(KNOWN_PROVIDERS).index(_DEFAULT_PROVIDER)
                if _DEFAULT_PROVIDER in KNOWN_PROVIDERS
                else 0,
            ),
        ]
    ).send()

    # Pre-populate the session with the default provider so users don't have
    # to select on first use (COULD goal from DEFINE).
    try:
        set_provider_by_name(_DEFAULT_PROVIDER)
    except (ValueError, ProviderError) as err:
        await cl.Message(
            content=f"Aviso: não foi possível carregar o provider padrão: {err}"
        ).send()

    await cl.Message(
        content=(
            "Bem-vindo ao portal de ingestão de dados.\n\n"
            f"Provider ativo: `{_DEFAULT_PROVIDER}`. Use `/llm <nome>` ou o "
            f"ícone de engrenagem para trocar. Providers: {', '.join(KNOWN_PROVIDERS)}.\n\n"
            "Digite o nome de uma tabela PostgreSQL para iniciar o CDC."
        )
    ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict) -> None:
    name = settings.get("llm_provider")
    if not name:
        return
    try:
        set_provider_by_name(name)
        await cl.Message(content=f"Provider ativo: `{name}`.").send()
    except (ValueError, ProviderError) as err:
        clear_provider()
        await cl.Message(content=f"Falha ao carregar `{name}`: {err}").send()


@cl.on_message
async def handle_message(message: cl.Message) -> None:
    # 1. /llm slash command short-circuit
    cmd = parse_llm_command(message.content)
    if cmd is not None:
        if not is_known_provider(cmd.provider_name):
            await cl.Message(
                content=(
                    f"Provider desconhecido: `{cmd.provider_name}`. "
                    f"Opções: {', '.join(KNOWN_PROVIDERS)}."
                )
            ).send()
            return
        try:
            set_provider_by_name(cmd.provider_name)
            await cl.Message(content=f"Provider ativo: `{cmd.provider_name}`.").send()
        except ProviderError as err:
            clear_provider()
            await cl.Message(content=f"Falha ao carregar `{cmd.provider_name}`: {err}").send()
        return

    # 2. No provider? Block and tell the user to choose.
    provider = get_provider()
    if provider is None:
        await cl.Message(
            content=(
                "Nenhum provider selecionado. Use `/llm <nome>` ou o ícone de "
                f"engrenagem. Opções: {', '.join(KNOWN_PROVIDERS)}."
            )
        ).send()
        return

    # 3. Happy path: validate table, introspect, generate.
    # ... (table_exists / introspect code stays unchanged) ...

    await cl.Message(content=f"Gerando contrato via `{provider.name}`...").send()
    try:
        contract = await _generator.generate(table_name, columns, provider=provider)
    except ProviderError as err:
        clear_provider()
        await cl.Message(
            content=(
                f"Falha em `{err.provider_name}` ({type(err).__name__}): {err}. "
                "Selecione outro provider para continuar."
            )
        ).send()
        return

    # ... (write contract, activate connector — unchanged) ...
```

### Pattern 9: Refactored `ODCSGenerator.generate`

```python
# portal/agent/odcs_generator.py (refactored — shape only, _parse_and_validate
# and _build_fallback_contract kept verbatim from current file)
"""Provider-agnostic ODCS v3.1 contract generator."""
from __future__ import annotations

import json
from typing import Any

import yaml

from .providers import LLMProvider
from .schema_inspector import ColumnInfo

_PROMPT_TEMPLATE = """..."""  # unchanged from current file


class ODCSGenerator:
    """Stateless generator. Provider is injected per generate() call."""

    async def generate(
        self,
        table_name: str,
        columns: list[ColumnInfo],
        *,
        provider: LLMProvider,
    ) -> dict[str, Any]:
        prompt = self._build_prompt(table_name, columns)
        raw_yaml = await provider.generate_yaml(prompt)
        return self._parse_and_validate(raw_yaml, table_name, columns)

    def _build_prompt(self, table_name: str, columns: list[ColumnInfo]) -> str:
        columns_json = json.dumps(
            [
                {
                    "name": c.name,
                    "pg_type": c.data_type,
                    "nullable": c.is_nullable,
                    "primary_key": c.is_primary_key,
                }
                for c in columns
            ],
            indent=2,
        )
        return _PROMPT_TEMPLATE.format(table_name=table_name, columns_json=columns_json)

    # _parse_and_validate and _build_fallback_contract remain UNCHANGED.
```

### Pattern 10: Requirements bump

```text
# portal/requirements.txt
chainlit==2.0.4
psycopg[binary]==3.1.18
httpx==0.27.0
pyyaml==6.0.1
boto3==1.34.0
google-generativeai==0.8.3
```

---

## Kubernetes Changes

### New Secret: `gemini-api-secret` in namespace `portal`

**Template manifest** (committed for documentation; real secret is created by bootstrap):

```yaml
# manifests/platform/gemini-api-secret.yaml
# This file documents the shape of the Secret. The actual value is injected
# by cluster/bootstrap.sh from the $GEMINI_API_KEY environment variable so
# the key never lands in Git. See bootstrap.sh section "8b".
apiVersion: v1
kind: Secret
metadata:
  name: gemini-api-secret
  namespace: portal
type: Opaque
stringData:
  api-key: "REPLACED_BY_BOOTSTRAP"
```

### Bootstrap script addition (`cluster/bootstrap.sh`)

```bash
# ── 8b. gemini-api-secret for Chainlit portal ────────────────────────────────
log "Creating gemini-api-secret in portal namespace..."
if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  log "WARNING: GEMINI_API_KEY env var is empty — creating placeholder secret."
  log "         Gemini provider will fail until the secret is updated."
fi
kubectl create secret generic gemini-api-secret \
  -n portal \
  --from-literal=api-key="${GEMINI_API_KEY:-placeholder-replace-me}" \
  --dry-run=client -o yaml \
  | kubectl apply -f -
```

### Helm values update (`helm/chainlit/values.yaml`)

```yaml
env:
  # ... existing entries unchanged ...
  - name: DEFAULT_LLM_PROVIDER
    value: "ollama"
  - name: GEMINI_MODEL
    value: "gemini-2.0-flash"
  - name: GEMINI_API_KEY
    valueFrom:
      secretKeyRef:
        name: gemini-api-secret
        key: api-key
```

No new `Deployment`, `Service`, `PVC`, `Ingress`, or `ArgoCD Application` is required — Gemini is a pure outbound SDK call.

---

## Data Flow

```text
1. User opens Chainlit session
   │   @cl.on_chat_start: send ChatSettings(Select llm_provider);
   │   set_provider_by_name(DEFAULT_LLM_PROVIDER)
   ▼
2. User types  "/llm gemini"   OR   picks "gemini" in settings
   │   parse_llm_command / on_settings_update
   │   → set_provider_by_name("gemini")
   │   → GeminiProvider() reads GEMINI_API_KEY env var → stored in user_session
   ▼
3. User types  "orders"
   │   @cl.on_message: parse_llm_command returns None
   │   → get_provider() returns GeminiProvider (session)
   │   → inspector.introspect("orders") → list[ColumnInfo]
   ▼
4. Generate
   │   await _generator.generate(table, cols, provider=GeminiProvider)
   │   ├── _build_prompt(...)
   │   ├── provider.generate_yaml(prompt)
   │   │    └── asyncio.wait_for(asyncio.to_thread(sync_call), timeout=30)
   │   └── _parse_and_validate(raw_yaml)
   ▼
5. Success path: write YAML to /contracts and MinIO; activate connector (UNCHANGED)
   Failure path: ProviderError caught → clear_provider() → named error in chat
```

---

## Integration Points

| External System | Integration Type | Authentication |
|-----------------|-----------------|----------------|
| Ollama (in-cluster) | HTTP `POST /api/generate` via `httpx` | None (cluster-internal) |
| Google Gemini | `google-generativeai` Python SDK | `GEMINI_API_KEY` env var → K8s Secret |
| Chainlit front-end | Built-in `@cl.on_settings_update`, `cl.user_session`, `cl.ChatSettings` | None (browser session) |
| Kubernetes Secret API | `kubectl create secret` via bootstrap; Deployment consumes via `secretKeyRef` | kubeconfig (bootstrap time) |
| MinIO (S3) | `boto3.client("s3")` — unchanged | Existing MinIO access keys |
| Kafka Connect | `httpx.post` via `ConnectorActivator` — unchanged | None (cluster-internal) |

---

## Testing Strategy

| Test Type | Scope | Files | Tools | Coverage Goal |
|-----------|-------|-------|-------|---------------|
| Unit — Protocol | All 3 providers satisfy `LLMProvider` runtime check | `tests/unit/portal/providers/test_base.py` | pytest, `isinstance` | 100% of providers |
| Unit — Ollama | Timeout path fires at 30 s; HTTP-error wrapping; happy path | `tests/unit/portal/providers/test_ollama.py` | pytest-asyncio, unittest.mock, `httpx.MockTransport` | Branches: timeout, 4xx, 5xx, happy |
| Unit — Gemini | Missing API key raises `ProviderAPIError`; timeout path; SDK error wrapping | `tests/unit/portal/providers/test_gemini.py` | pytest-asyncio, monkeypatch on `genai.GenerativeModel` | Branches: missing key, invalid key, timeout, happy |
| Unit — Fallback | Returns empty string; generator falls back to deterministic builder | `tests/unit/portal/providers/test_fallback.py` | pytest-asyncio | Happy only |
| Unit — Session | get/set/clear round-trip; isolation across fake session dicts | `tests/unit/portal/test_session.py` | pytest, monkeypatch on `cl.user_session` | 100% |
| Unit — Commands | Known / unknown / malformed / non-command | `tests/unit/portal/test_commands.py` | pytest | All 4 branches |
| Unit — Generator refactor | Provider is injected per call; `_parse_and_validate` behavior unchanged | `tests/unit/portal/test_odcs_generator.py` | pytest-asyncio, fake provider | Replaces existing `httpx` mock tests |
| Integration — Pipeline e2e | Full KIND stack still green (AT-007, SC-8) | `tests/integration/test_pipeline_e2e.py` | pytest, live KIND | Unchanged file; runs untouched |
| Manual — UI | Gear icon dropdown switches provider; `/llm` command does the same; named errors appear | Chainlit UI | Browser | AT-001 through AT-006 |

### Fake-provider fixture (used by `test_odcs_generator.py`)

```python
# Factory pattern from .claude/kb/testing/patterns/fixture-factories.md
class _FakeProvider:
    name = "fake"

    def __init__(self, yaml_payload: str = "", *, raise_exc: Exception | None = None) -> None:
        self._payload = yaml_payload
        self._raise = raise_exc

    async def generate_yaml(self, prompt: str) -> str:
        if self._raise:
            raise self._raise
        return self._payload


@pytest.fixture
def fake_provider_factory():
    return _FakeProvider
```

### Ollama timeout test skeleton

```python
import asyncio
import pytest
from portal.agent.providers.ollama import OllamaProvider
from portal.agent.providers.base import ProviderTimeoutError


@pytest.mark.asyncio
async def test_ollama_wait_for_timeout(monkeypatch):
    provider = OllamaProvider(ollama_url="http://mock:11434", timeout_s=1)

    def slow_sync(prompt):
        import time; time.sleep(5)  # simulate stalled Ollama
        return "never returned"

    monkeypatch.setattr(provider, "_call_sync", slow_sync)

    with pytest.raises(ProviderTimeoutError) as exc_info:
        await provider.generate_yaml("prompt")
    assert "ollama" in str(exc_info.value).lower()
    assert "1 s" in str(exc_info.value)
```

---

## Error Handling

| Error Type | Handling Strategy | Retry? |
|------------|-------------------|--------|
| `asyncio.TimeoutError` inside a provider | Wrap as `ProviderTimeoutError(name, "timed out after N s")`; surfaces to UI as named chat error | No — user re-selects |
| HTTP 4xx/5xx from Ollama | Wrap as `ProviderAPIError(name, "HTTP <code>")` | No — user re-selects |
| Gemini `PermissionDenied` (invalid API key) | Wrap as `ProviderAPIError("gemini", "invalid API key")` | No |
| Gemini empty response | Wrap as `ProviderAPIError("gemini", "empty response")` | No |
| YAML parse failure from LLM output | Existing `_build_fallback_contract` path — transparent to user | N/A (silent fallback preserved, SC-4) |
| `ValueError` from `build_from_name("unknown")` | Caught at command/settings layer; chat shows "Provider desconhecido" | No |
| `ProviderError` from `build_from_name("gemini")` (missing key) | Caught at settings/command handler; `clear_provider()` + named chat error | No — user fixes Secret or picks another provider |
| Non-provider failure (e.g., Postgres down) | Existing error path — unchanged | Existing behavior |

---

## Configuration

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| `OLLAMA_URL` | string | `http://ollama.portal.svc.cluster.local:11434` | Existing |
| `OLLAMA_MODEL` | string | `llama3.2:3b` | Existing |
| `OLLAMA_TIMEOUT_S` | int | `30` | New — wall-clock budget for an Ollama call |
| `GEMINI_API_KEY` | string | — (required for gemini) | Injected from `gemini-api-secret` Secret |
| `GEMINI_MODEL` | string | `gemini-2.0-flash` | Helm value, tunable without rebuild |
| `GEMINI_TIMEOUT_S` | int | `30` | New — wall-clock budget for a Gemini call |
| `DEFAULT_LLM_PROVIDER` | string | `ollama` | Pre-populates `cl.user_session` on chat start |

---

## Security Considerations

- `GEMINI_API_KEY` is never hardcoded; injected from `gemini-api-secret` via `secretKeyRef`, matching the existing `postgres-source-secret` pattern (`cluster/bootstrap.sh` section 8).
- `gemini-api-secret` is created by `bootstrap.sh` from the operator's environment variable; the placeholder value `placeholder-replace-me` is used if the env var is unset — Gemini will fail cleanly with `ProviderAPIError("gemini", "invalid API key")` rather than leaking the placeholder.
- `manifests/platform/gemini-api-secret.yaml` ships with the literal `REPLACED_BY_BOOTSTRAP`; it is a shape-documentation file, not an applied manifest (the bootstrap script owns the applied Secret).
- Provider error messages include provider name + error category but NEVER the API key value. `ProviderError.__init__` accepts a scrubbed message.
- `cl.user_session` is per-websocket and wiped on disconnect — no cross-session leakage.
- The `google-generativeai` SDK call is outbound HTTPS to `generativelanguage.googleapis.com`; no new ingress or NetworkPolicy impact.

---

## Observability

| Aspect | Implementation |
|--------|----------------|
| Logging | `logging.getLogger(__name__)` per provider module; log WARNING on timeout including provider name and budget (already shown in Pattern 2/3). User-facing errors go through `cl.Message`. No secret values ever logged. |
| Metrics | Out of scope for this feature. |
| Tracing | Out of scope for this feature. |
| Error surface | Named error in Chainlit chat: `"Falha em <provider> (<ExceptionClass>): <message>"` — matches AT-002 / AT-003. |

---

## Pipeline Architecture

> Not applicable. This feature modifies the contract-generation layer of the portal only; it does not introduce new ETL, DAGs, or data models. The downstream Bronze/Silver/Gold pipeline is untouched.

---

## Quality Gate Self-Check

- [x] KB patterns loaded from DEFINE's domains (`python`, `genai`, `testing`, `on-premise-k8s`, `prompt-engineering`)
- [x] ASCII architecture diagram created
- [x] Six decisions with full rationale + rejected alternatives
- [x] Complete file manifest (21 files: 14 create, 7 modify)
- [x] Agent assigned to each file (all four specialists matched; no "general")
- [x] Code patterns are syntactically correct Python 3.11+
- [x] Testing strategy covers all seven acceptance tests (AT-001 to AT-007)
- [x] No shared dependencies across deployable units — providers are self-contained; each provider file depends only on `base.py`
- [x] All five DEFINE assumptions validated or explicitly marked

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-04-23 | design-agent | Initial version from DEFINE_MULTI_LLM_CONTRACT_GEN.md |

---

## Next Step

**Ready for:** `/build .claude/sdd/features/DESIGN_MULTI_LLM_CONTRACT_GEN.md`
