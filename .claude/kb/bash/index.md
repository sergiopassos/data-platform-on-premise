# Bash Scripting Knowledge Base

> **Purpose**: Production-grade Bash patterns for Kubernetes automation, data platform bootstrap, smoke tests, and kubectl/curl/jq scripting
> **MCP Validated**: 2026-04-24

## Quick Navigation

### Concepts (< 150 lines each)

| File | Purpose |
|------|---------|
| [concepts/script-header.md](concepts/script-header.md) | Shebang, `set -euo pipefail`, ROOT_DIR, portability |
| [concepts/error-handling.md](concepts/error-handling.md) | `trap`, ERR/EXIT signals, cleanup on failure |
| [concepts/variable-safety.md](concepts/variable-safety.md) | Quoting rules, `${VAR:-default}`, `${VAR:?}`, arrays |
| [concepts/colored-output.md](concepts/colored-output.md) | ANSI codes, helper functions, `_pass`/`_fail`/`_poll` |
| [concepts/argument-parsing.md](concepts/argument-parsing.md) | `getopts`, manual `$@` loops, flag patterns |
| [concepts/functions-and-return-codes.md](concepts/functions-and-return-codes.md) | Function declaration, `local`, return codes, subshells |

### Patterns (< 200 lines each)

| File | Purpose |
|------|---------|
| [patterns/polling-retry-loop.md](patterns/polling-retry-loop.md) | `date +%s` deadline loop, `_poll` helper, timeouts |
| [patterns/kubectl-scripting.md](patterns/kubectl-scripting.md) | Pod lookups, exec, wait, jsonpath, rollout |
| [patterns/curl-jq-rest.md](patterns/curl-jq-rest.md) | curl + jq for REST APIs, HTTP status capture, Airflow/Kafka Connect |
| [patterns/heredoc-herestring.md](patterns/heredoc-herestring.md) | Here-docs, here-strings, multi-line kubectl apply |
| [patterns/tempfile-cleanup.md](patterns/tempfile-cleanup.md) | `mktemp`, EXIT trap cleanup, safe temp directories |

### Specs (Machine-Readable)

| File | Purpose |
|------|---------|
| [specs/bash-patterns.yaml](specs/bash-patterns.yaml) | Machine-readable registry of all patterns and their options |

---

## Quick Reference

- [quick-reference.md](quick-reference.md) — Fast lookup tables for flags, idioms, and common commands

---

## Key Concepts

| Concept | Description |
|---------|-------------|
| **`set -euo pipefail`** | Exit on error, unbound vars, and pipe failures — the production safety net |
| **`${VAR:-default}`** | Safe variable expansion with fallback — never crashes on unset vars |
| **Deadline loop** | `deadline=$(( $(date +%s) + timeout ))` then `while (( $(date +%s) < deadline ))` |
| **Idempotent guards** | `command ... || true` and `--dry-run=client -o yaml \| kubectl apply -f -` |
| **Subshell isolation** | `(cd /tmp && do_work)` — working dir changes don't leak to caller |

---

## Learning Path

| Level | Files |
|-------|-------|
| **Beginner** | concepts/script-header.md → concepts/variable-safety.md → concepts/error-handling.md |
| **Intermediate** | concepts/colored-output.md → patterns/polling-retry-loop.md → patterns/kubectl-scripting.md |
| **Advanced** | patterns/curl-jq-rest.md → patterns/tempfile-cleanup.md → specs/bash-patterns.yaml |

---

## Agent Usage

| Agent | Primary Files | Use Case |
|-------|---------------|----------|
| Bash Agent | concepts/script-header.md, patterns/kubectl-scripting.md | Write/review bootstrap and smoke-test scripts |
| Debug Agent | concepts/error-handling.md, patterns/polling-retry-loop.md | Diagnose script failures and timeouts |
| Platform Agent | patterns/curl-jq-rest.md, patterns/kubectl-scripting.md | Automate platform operations |
