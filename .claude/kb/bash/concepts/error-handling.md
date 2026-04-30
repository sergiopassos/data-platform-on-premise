# Error Handling and Traps

> **Purpose**: Catch failures early, report context, and guarantee cleanup even when `set -e` exits the script
> **Confidence**: 0.95
> **MCP Validated**: 2026-04-24

## Overview

`trap` registers a command to execute when Bash receives a signal or a pseudo-signal. The two most useful pseudo-signals are `ERR` (any command exits non-zero) and `EXIT` (script ends for any reason, including errors). Combining them gives you automatic cleanup and actionable error context without wrapping every command in `if/else`.

## The Concept

```bash
#!/usr/bin/env bash
set -euo pipefail

# ── Trap: print context on any error ─────────────────────────────────────────
_on_error() {
  local exit_code=$?
  local line_no=${1:-unknown}
  echo "[ERROR] Script failed at line ${line_no} with exit code ${exit_code}" >&2
  echo "[ERROR] Command: ${BASH_COMMAND}" >&2
}
trap '_on_error $LINENO' ERR

# ── Trap: cleanup on exit (runs after ERR trap) ───────────────────────────────
TMPDIR_WORK=""
_cleanup() {
  if [[ -n "${TMPDIR_WORK:-}" ]] && [[ -d "$TMPDIR_WORK" ]]; then
    rm -rf "$TMPDIR_WORK"
    echo "[INFO] Temp dir cleaned up." >&2
  fi
}
trap _cleanup EXIT

TMPDIR_WORK=$(mktemp -d)

# ── Intentional fallthrough (|| true) ────────────────────────────────────────
# Use || true only when failure is truly acceptable.
docker network connect kind "kind-registry" 2>/dev/null || true

# ── Detect a failure and add context before exiting ──────────────────────────
MIGRATE_PHASE=$(kubectl get pod airflow-db-migrate -n orchestration \
  -o jsonpath='{.status.phase}' 2>/dev/null || echo "NOT_FOUND")

if [[ "$MIGRATE_PHASE" != "Succeeded" ]]; then
  echo "[ERROR] airflow db migrate failed (phase=$MIGRATE_PHASE). Logs:" >&2
  kubectl logs airflow-db-migrate -n orchestration >&2 || true
  exit 1
fi
```

## Quick Reference

| Signal | Fires when | Common use |
|--------|-----------|------------|
| `ERR` | Any command exits non-zero (with `set -e`) | Print line number + `$BASH_COMMAND` |
| `EXIT` | Script ends (any reason) | Cleanup temp files, port-forwards |
| `INT` | Ctrl-C | Graceful abort with cleanup |
| `TERM` | `kill` / container stop | Graceful shutdown |

## Common Mistakes

### Wrong

```bash
# Trap only on EXIT — by the time EXIT fires, you've lost the line number
trap 'rm -rf /tmp/work' EXIT
```

### Correct

```bash
# Two-trap pattern: ERR for diagnostics, EXIT for cleanup
trap '_on_error $LINENO' ERR
trap _cleanup EXIT
```

## Pattern: Retry with Error Context

```bash
_require_success() {
  local label="$1"; shift
  if ! "$@"; then
    echo "[FAIL] $label" >&2
    return 1
  fi
  echo "[PASS] $label"
}

# Will print the label when the command fails, and ERR trap fires with context
_require_success "ArgoCD health" kubectl get applications -n argocd
```

## Related

- [script-header.md](script-header.md)
- [patterns/tempfile-cleanup.md](../patterns/tempfile-cleanup.md)
- [patterns/polling-retry-loop.md](../patterns/polling-retry-loop.md)
