# Function Patterns and Return Codes

> **Purpose**: Structure reusable logic into testable functions that communicate success/failure via return codes, not global state
> **Confidence**: 0.95
> **MCP Validated**: 2026-04-24

## Overview

Bash functions share the calling script's scope unless `local` is used. Declare all variables inside functions with `local` to prevent accidental global mutation. Functions communicate success/failure via return codes (0 = success, 1–255 = failure) — callers test with `if`, `&&`, or `||`. Use `echo` or nameref variables for output; avoid relying on global variables for return values.

## The Concept

```bash
#!/usr/bin/env bash
set -euo pipefail

# ── Simple predicate function (returns 0/1) ───────────────────────────────────
_pod_ready() {
  local namespace="$1" label="$2"
  local pod_name
  pod_name=$(kubectl get pod -n "$namespace" -l "$label" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null) || return 1
  [[ -n "$pod_name" ]] || return 1
  kubectl get pod -n "$namespace" "$pod_name" \
    -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' \
    2>/dev/null | grep -q "True"
}

# ── Function with output via echo (capture with $()) ─────────────────────────
_get_pod_name() {
  local namespace="$1" label="$2"
  kubectl get pod -n "$namespace" -l "$label" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null
}

# ── Function that modifies its caller's named variable (Bash 4.3+) ───────────
_get_argocd_password() {
  local -n _result=$1  # nameref — writes to the caller's variable
  _result=$(kubectl -n argocd get secret argocd-initial-admin-secret \
    -o jsonpath='{.data.password}' | base64 -d)
}

# ── Kubectl exec wrapper with error context ───────────────────────────────────
_kubectl_exec_postgres() {
  local pod
  pod=$(_get_pod_name infra "app=postgres")
  [[ -z "$pod" ]] && { echo "[ERROR] No postgres pod found" >&2; return 1; }
  kubectl exec -n infra "$pod" -- psql -U postgres -d sourcedb "$@"
}

# ── Usage ─────────────────────────────────────────────────────────────────────
if _pod_ready orchestration "component=webserver,release=airflow"; then
  echo "Airflow webserver is ready"
else
  echo "Airflow webserver not ready — retrying..." >&2
fi

POSTGRES_POD=$(_get_pod_name infra "app=postgres")
_kubectl_exec_postgres -c "SELECT COUNT(*) FROM customers;"

ARGOCD_PASS=""
_get_argocd_password ARGOCD_PASS
echo "ArgoCD password: $ARGOCD_PASS"
```

## Quick Reference

| Pattern | Syntax | Notes |
|---------|--------|-------|
| Declare local variable | `local name="value"` | Prevents global leak |
| Return success | `return 0` (implicit on `true`) | Default if function exits normally |
| Return failure | `return 1` | Any non-zero code works |
| Capture output | `result=$(func_name args)` | Subshell — be careful with globals |
| Test return code | `if func_name; then` | Clean, no `$?` needed |
| Nameref output | `local -n ref=$1` | Bash 4.3+; avoids subshell |

## Common Mistakes

### Wrong

```bash
# Global variable leaks out of function
get_pod() {
  POD_NAME=$(kubectl get pod ...)  # POD_NAME is now global
}
```

### Correct

```bash
get_pod() {
  local pod_name
  pod_name=$(kubectl get pod ...)
  echo "$pod_name"  # caller captures with $()
}
POD=$( get_pod )
```

## Related

- [error-handling.md](error-handling.md)
- [variable-safety.md](variable-safety.md)
- [patterns/kubectl-scripting.md](../patterns/kubectl-scripting.md)
