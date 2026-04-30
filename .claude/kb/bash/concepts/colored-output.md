# Colored Output

> **Purpose**: Structured, colored log helpers that make test/bootstrap scripts immediately scannable in CI and terminal output
> **Confidence**: 0.95
> **MCP Validated**: 2026-04-24

## Overview

ANSI escape codes turn plain text into color when output goes to a terminal. The pattern used throughout this project's scripts (`_pass`, `_fail`, `_skip`, `_poll`, `_header`) gives every output line a consistent prefix and color so a reader can scan hundreds of lines and spot failures instantly. Counters (`PASS`, `FAIL`, `SKIP`) let the script self-report a final summary.

## The Concept

```bash
#!/usr/bin/env bash
set -euo pipefail

# ── ANSI palette ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

# ── Counters ──────────────────────────────────────────────────────────────────
PASS=0; FAIL=0; SKIP=0; MANUAL=0

# ── Output helpers ────────────────────────────────────────────────────────────
_pass()   { echo -e "${GREEN}  [PASS]${RESET} $*";   (( PASS++ )); }
_fail()   { echo -e "${RED}  [FAIL]${RESET} $*";     (( FAIL++ )); }
_skip()   { echo -e "${YELLOW}  [SKIP]${RESET} $*";  (( SKIP++ )); }
_manual() { echo -e "${CYAN}  [MANUAL]${RESET} $*";  (( MANUAL++ )); }
_info()   { echo -e "${BLUE}  [INFO]${RESET} $*"; }
_header() { echo -e "\n${BOLD}${YELLOW}▶ $*${RESET}"; }
log()     { echo "[$(date '+%H:%M:%S')] $*"; }

# ── Generic check wrapper ─────────────────────────────────────────────────────
_check() {
  local label="$1"; shift
  if "$@" &>/dev/null; then _pass "$label"; return 0
  else _fail "$label"; return 1; fi
}

# ── Summary footer ────────────────────────────────────────────────────────────
_summary() {
  echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo -e "${BOLD}  Test Summary${RESET}"
  echo -e "${GREEN}  PASS:   $PASS${RESET}"
  echo -e "${RED}  FAIL:   $FAIL${RESET}"
  echo -e "${YELLOW}  SKIP:   $SKIP${RESET}"
  echo -e "${CYAN}  MANUAL: $MANUAL${RESET}"
  echo -e "${BOLD}${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  [[ "$FAIL" -gt 0 ]] && { echo -e "${RED}${BOLD}RESULT: FAILED${RESET}"; return 1; }
  echo -e "${GREEN}${BOLD}RESULT: ALL CHECKS PASSED${RESET}"
}

# ── Usage ─────────────────────────────────────────────────────────────────────
_header "Step 1 — Checking cluster"
_check "kubectl cluster accessible" kubectl cluster-info
_check "ArgoCD namespace exists" kubectl get ns argocd

_info "Cluster: data-platform"
_skip "Spark image check skipped (--no-spark)"
_manual "Open MinIO at http://localhost:9001 (minio / minio123)"
_summary
```

## Quick Reference

| Helper | Color | Counter | Use |
|--------|-------|---------|-----|
| `_pass "msg"` | Green | `PASS++` | Automated check succeeded |
| `_fail "msg"` | Red | `FAIL++` | Automated check failed |
| `_skip "msg"` | Yellow | `SKIP++` | Step intentionally skipped |
| `_manual "msg"` | Cyan | `MANUAL++` | Requires human action |
| `_info "msg"` | Blue | none | Informational, no verdict |
| `_header "msg"` | Bold yellow | none | Section separator |
| `log "msg"` | none | none | Timestamped progress line |

## Common Mistakes

### Wrong

```bash
echo "PASS: cluster reachable"    # No color, no counter
echo -e "\e[32mOK\e[0m something" # Inconsistent prefix style
```

### Correct

```bash
_pass "cluster reachable"         # Consistent prefix, green, counter updated
_check "cluster reachable" kubectl cluster-info  # Even better: wraps command
```

## Related

- [script-header.md](script-header.md)
- [patterns/polling-retry-loop.md](../patterns/polling-retry-loop.md)
