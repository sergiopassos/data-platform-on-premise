# Variable Safety

> **Purpose**: Prevent unbound-variable crashes, unexpected empty expansions, and word-splitting bugs through correct quoting and expansion forms
> **Confidence**: 0.95
> **MCP Validated**: 2026-04-24

## Overview

Bash variable bugs are among the most common causes of silent data corruption or unexpected script behaviour. With `set -u` enabled, accessing an unset variable aborts the script — but only if you expand it without a default. Always double-quote expansions to prevent word-splitting and glob expansion. Use `${VAR:-default}` for optional settings and `${VAR:?message}` for required ones.

## The Concept

```bash
#!/usr/bin/env bash
set -euo pipefail

# ── Required variables — abort with a message if unset ───────────────────────
CLUSTER_NAME="${CLUSTER_NAME:?CLUSTER_NAME must be set (e.g. data-platform)}"

# ── Optional variables with safe defaults ────────────────────────────────────
REGISTRY_PORT="${REGISTRY_PORT:-5001}"
ARGOCD_NS="${ARGOCD_NS:-argocd}"
SSH_KEY="${SSH_KEY_PATH:-$HOME/.ssh/id_ed25519}"
AIRFLOW_FERNET_KEY="${AIRFLOW_FERNET_KEY:-data-platform-local-fernet-key-replace-in-prod==}"

# ── Conditional expansion — only expand if set ───────────────────────────────
# Adds --dry-run only when DRY_RUN is non-empty
kubectl apply ${DRY_RUN:+--dry-run=client} -f manifest.yaml

# ── Safe empty-check in condition ────────────────────────────────────────────
EXISTING_CLUSTER=$(kind get clusters 2>/dev/null | grep "^${CLUSTER_NAME}$" || true)
if [[ -z "$EXISTING_CLUSTER" ]]; then
  kind create cluster --name "$CLUSTER_NAME"
fi

# ── Strip path components ─────────────────────────────────────────────────────
SCRIPT_NAME="${BASH_SOURCE[0]##*/}"   # basename without fork
CONFIG_BASE="${CONFIG_FILE%.yaml}"    # remove .yaml suffix

# ── Arrays — always quote on expansion ───────────────────────────────────────
NAMESPACES=(infra streaming processing serving orchestration governance portal)
for NS in "${NAMESPACES[@]}"; do
  kubectl get pods -n "$NS" --no-headers 2>/dev/null || true
done
```

## Quick Reference

| Form | Behaviour | When to use |
|------|-----------|-------------|
| `"$VAR"` | Expand; word-split protected | Always — the default |
| `${VAR:-default}` | Default if unset or empty | Optional config with fallback |
| `${VAR:?msg}` | Error + msg if unset or empty | Mandatory parameters |
| `${VAR:+val}` | Use `val` only if VAR set | Conditional flags |
| `${VAR#pat}` | Strip shortest prefix | Strip protocol from URL |
| `${VAR##pat}` | Strip longest prefix | Extract basename |
| `${VAR%pat}` | Strip shortest suffix | Strip extension |
| `${#VAR}` | Length of value | Validate non-empty |

## Common Mistakes

### Wrong

```bash
# Unquoted — breaks on spaces and globs
kubectl label pod $POD_NAME env=prod
# Will crash with set -u if GEMINI_API_KEY not exported
API_KEY=$GEMINI_API_KEY
```

### Correct

```bash
kubectl label pod "$POD_NAME" env=prod
API_KEY="${GEMINI_API_KEY:-placeholder-replace-me}"
```

## Related

- [script-header.md](script-header.md)
- [argument-parsing.md](argument-parsing.md)
- [error-handling.md](error-handling.md)
