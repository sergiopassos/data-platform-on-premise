# Temporary Files and Cleanup Traps

> **Purpose**: Create temp files safely with `mktemp`, guarantee their deletion via EXIT trap, and avoid polluting `/tmp` even when scripts exit with errors
> **MCP Validated**: 2026-04-24

## When to Use

- Storing curl response bodies for status-code + body capture pattern
- Accumulating multi-line output from kubectl before processing
- Staging a YAML manifest before applying (avoid inline here-doc for long configs)
- Any situation where a command needs a real file path, not stdin

## Implementation

```bash
#!/usr/bin/env bash
set -euo pipefail

# ── Single-file pattern ───────────────────────────────────────────────────────
# mktemp creates a uniquely named file in /tmp (or $TMPDIR).
RESP_FILE=$(mktemp)
trap 'rm -f "$RESP_FILE"' EXIT

# Use it — curl status+body pattern
HTTP_CODE=$(curl -s \
  -o "$RESP_FILE" \
  -w "%{http_code}" \
  -X POST "${KAFKA_CONNECT_URL:-http://localhost:8083}/connectors" \
  -H "Content-Type: application/json" \
  -d '{"name":"test","config":{}}' 2>/dev/null)

if [[ "$HTTP_CODE" == "201" ]]; then
  CONNECTOR_NAME=$(jq -r '.name' "$RESP_FILE")
  echo "[PASS] Created connector: $CONNECTOR_NAME"
else
  ERR=$(jq -r '.message // .error_code // .' "$RESP_FILE" 2>/dev/null || cat "$RESP_FILE")
  echo "[FAIL] HTTP ${HTTP_CODE}: ${ERR}" >&2
  exit 1
fi
# RESP_FILE is cleaned up automatically when the script exits (any reason)

# ── Directory pattern — temp dir for multiple files ───────────────────────────
WORK_DIR=$(mktemp -d)

_cleanup_work() {
  rm -rf "$WORK_DIR"
}
# Reset EXIT trap to also clean WORK_DIR
trap '_cleanup_work' EXIT

# Stage generated manifests
cat > "${WORK_DIR}/namespace.yaml" <<'EOF'
apiVersion: v1
kind: Namespace
metadata:
  name: processing
EOF

cat > "${WORK_DIR}/configmap.yaml" <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: platform-config
  namespace: infra
data:
  cluster: "${CLUSTER_NAME:-data-platform}"
EOF

# Apply all at once
kubectl apply -f "$WORK_DIR/"

# ── Named trap that also reports the error context ────────────────────────────
_cleanup_and_report() {
  local exit_code=$?
  rm -rf "$WORK_DIR"
  if [[ $exit_code -ne 0 ]]; then
    echo "[ERROR] Script failed (exit=$exit_code); work dir cleaned up." >&2
  fi
}
trap '_cleanup_and_report' EXIT

# ── Safe temp file in a script that also has an ERR trap ─────────────────────
# Order matters: declare trap BEFORE creating the file, so the trap is
# registered even if mktemp would somehow fail.
STAGING_FILE=""
trap '[[ -n "$STAGING_FILE" ]] && rm -f "$STAGING_FILE"' EXIT
STAGING_FILE=$(mktemp --suffix=.yaml)

kubectl get configmap platform-config -n infra -o yaml > "$STAGING_FILE"
# Modify and re-apply
sed -i 's/local/staging/' "$STAGING_FILE"
kubectl apply -f "$STAGING_FILE"
```

## Configuration

| Tool | Options | Notes |
|------|---------|-------|
| `mktemp` | (none) | Creates file in `$TMPDIR` or `/tmp` |
| `mktemp -d` | `-d` | Creates a directory |
| `mktemp --suffix=.yaml` | `--suffix` | GNU only; adds extension for clarity |
| `mktemp -t prefix.XXXXXX` | `-t` | Portable prefix+random suffix |

## Common Mistakes

### Wrong

```bash
# Hardcoded name — not unique, race condition, not cleaned up on error
TMPFILE=/tmp/response.json
curl -s -o "$TMPFILE" ...
# Script crashes — /tmp/response.json left behind forever
```

### Correct

```bash
# Unique name, guaranteed cleanup even on crash
TMPFILE=$(mktemp)
trap 'rm -f "$TMPFILE"' EXIT
curl -s -o "$TMPFILE" ...
```

## Pattern: Multiple Traps Without Overwriting

```bash
# WRONG: second trap replaces first
trap 'rm -f "$FILE1"' EXIT
trap 'rm -f "$FILE2"' EXIT  # File1 will NOT be cleaned up

# CORRECT: append to existing trap
FILE1=$(mktemp); trap 'rm -f "$FILE1"' EXIT
FILE2=$(mktemp); trap 'rm -f "$FILE1" "$FILE2"' EXIT  # restate all
# Or use a cleanup function and update it
```

## See Also

- [concepts/error-handling.md](../concepts/error-handling.md)
- [patterns/curl-jq-rest.md](curl-jq-rest.md)
- [patterns/heredoc-herestring.md](heredoc-herestring.md)
