# Bash Quick Reference

> Fast lookup tables. For code examples, see linked files.
> **MCP Validated**: 2026-04-24

## Script Safety Flags

| Flag | Effect | Always Use? |
|------|--------|-------------|
| `set -e` | Exit on any non-zero command | Yes |
| `set -u` | Treat unset variables as error | Yes |
| `set -o pipefail` | Pipe fails if any stage fails | Yes |
| `set -x` | Print each command (debug mode) | Debug only |
| Combined: `set -euo pipefail` | All three together | Yes — line 2 of every script |

## Variable Expansion Forms

| Syntax | Meaning | Example |
|--------|---------|---------|
| `${VAR:-default}` | Use default if unset or empty | `${PORT:-8080}` |
| `${VAR:?msg}` | Abort with message if unset | `${CLUSTER:?must set CLUSTER}` |
| `${VAR:+val}` | Use val only if VAR is set | `${DEBUG:+-v}` |
| `${VAR#prefix}` | Strip shortest prefix match | `${path#*/}` |
| `${VAR##prefix}` | Strip longest prefix match | `${path##*/}` → basename |
| `${VAR%suffix}` | Strip shortest suffix match | `${file%.yaml}` |
| `"$@"` | All args, each individually quoted | Always use over `$*` |

## ANSI Color Codes

| Variable | Code | Use |
|----------|------|-----|
| `RED` | `\033[0;31m` | Failures, errors |
| `GREEN` | `\033[0;32m` | Pass, success |
| `YELLOW` | `\033[1;33m` | Warnings, headers |
| `BLUE` | `\033[0;34m` | Info messages |
| `CYAN` | `\033[0;36m` | Manual/interactive steps |
| `BOLD` | `\033[1m` | Section headers |
| `RESET` | `\033[0m` | Reset all attributes |

## kubectl Patterns

| Task | Command |
|------|---------|
| Get first pod name by label | `kubectl get pod -n NS -l KEY=VAL -o jsonpath='{.items[0].metadata.name}'` |
| Wait for pod ready | `kubectl wait pod -l KEY=VAL -n NS --for=condition=Ready --timeout=180s` |
| Wait for pod to appear | `until kubectl get pod -l KEY=VAL -n NS --no-headers 2>/dev/null \| grep -q .; do sleep 5; done` |
| Exec into pod | `kubectl exec -n NS "$POD" -- command args` |
| Apply idempotently | `kubectl create ... --dry-run=client -o yaml \| kubectl apply -f -` |
| Get custom field | `kubectl get TYPE NAME -n NS -o jsonpath='{.status.field}'` |
| Rollout restart | `kubectl rollout restart deployment/NAME -n NS` |
| Rollout wait | `kubectl rollout status deployment/NAME -n NS --timeout=600s` |

## curl + jq Patterns

| Task | Snippet |
|------|---------|
| GET with auth | `curl -sf -u user:pass URL` |
| POST JSON | `curl -sf -X POST URL -H "Content-Type: application/json" -d '{"key":"val"}'` |
| Capture HTTP status separately | `curl -s -o /tmp/resp.json -w "%{http_code}" ...` |
| Extract field | `jq -r '.field' /tmp/resp.json` |
| Test boolean | `jq -e '.status == "RUNNING"'` (exit 1 if false) |
| Null-safe extract | `jq -r '.field // "default"'` |

## Polling Loop Idioms

| Pattern | Code |
|---------|------|
| Deadline from now | `deadline=$(( $(date +%s) + TIMEOUT_SECS ))` |
| Check deadline | `while (( $(date +%s) < deadline )); do ...; sleep 5; done` |
| Simple retry (n attempts) | `for attempt in 1 2 3 4 5; do ... && break || sleep 10; done` |
| Wait for pod to appear | `until kubectl get pod -l app=X -n NS --no-headers 2>/dev/null \| grep -q .; do sleep 5; done` |

## Decision Matrix

| Use Case | Choose |
|----------|--------|
| Wait for known-duration resource | `kubectl wait --for=condition=Ready --timeout=Ns` |
| Wait for unknown-duration resource | deadline loop with `date +%s` |
| Retry a flaky command | `for attempt in 1..N; do cmd && break \|\| sleep DELAY; done` |
| Capture HTTP status code | `curl -s -o OUTFILE -w "%{http_code}" URL` |
| Multi-line kubectl input | here-doc with `kubectl apply -f -` |
| Idempotent secret creation | `kubectl create ... --dry-run=client -o yaml \| kubectl apply -f -` |

## Common Pitfalls

| Don't | Do |
|-------|-----|
| `#!/bin/bash` hardcode | `#!/usr/bin/env bash` for portability |
| `cd scripts && ./run.sh` (relative) | `ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"` |
| `$VAR` unquoted in conditions | `"$VAR"` — always double-quote |
| `[ "$VAR" == "val" ]` | `[[ "$VAR" == "val" ]]` — use `[[` in Bash |
| `curl URL \| jq ...` (silent failures) | `curl -sf URL \| jq ...` with `-s -f` |
| Forget to clean temp files | Trap EXIT to remove `mktemp` files |
| `set -e` then `cmd \|\| true` everywhere | Use `|| true` only for genuinely optional commands |

## Related Documentation

| Topic | Path |
|-------|------|
| Script Header | `concepts/script-header.md` |
| Error Handling | `concepts/error-handling.md` |
| Variable Safety | `concepts/variable-safety.md` |
| kubectl Scripting | `patterns/kubectl-scripting.md` |
| Full Index | `index.md` |
