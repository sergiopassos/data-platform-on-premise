# Script Header Best Practices

> **Purpose**: Every production script needs the right shebang, safety flags, and root-dir detection before the first real command
> **Confidence**: 0.95
> **MCP Validated**: 2026-04-24

## Overview

A consistent script header eliminates entire classes of bugs: missing executables, relative-path failures, unset variable explosions, and silent pipe failures. `set -euo pipefail` is the most important single line in any Bash script — it turns silent failures into loud exits. `ROOT_DIR` detection using `${BASH_SOURCE[0]}` makes scripts relocatable so they work correctly regardless of the calling directory.

## The Concept

```bash
#!/usr/bin/env bash
set -euo pipefail

# Resolve the project root regardless of where this script is called from.
# Works even when the script is sourced or invoked via symlink.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# ── Configuration (env-overridable, with safe defaults) ──────────────────────
CLUSTER_NAME="${CLUSTER_NAME:-data-platform}"
REGISTRY_PORT="${REGISTRY_PORT:-5001}"
ARGOCD_NS="${ARGOCD_NS:-argocd}"
SSH_KEY="${SSH_KEY_PATH:-$HOME/.ssh/id_ed25519}"

# ── Require-command guard (call before using any external tool) ───────────────
_require_cmd() {
  command -v "$1" &>/dev/null || {
    echo "[ERROR] '$1' not found. Install it first." >&2
    exit 1
  }
}

_require_cmd kubectl
_require_cmd helm
_require_cmd kind
_require_cmd jq
```

## Quick Reference

| Header element | Purpose | Notes |
|----------------|---------|-------|
| `#!/usr/bin/env bash` | Portable shebang | Finds bash in PATH, not hardcoded `/bin/bash` |
| `set -e` | Exit on error | Any non-zero exit stops the script |
| `set -u` | Unset = error | Catches typos in variable names immediately |
| `set -o pipefail` | Pipe failure propagates | `false \| true` exits 1, not 0 |
| `${BASH_SOURCE[0]}` | Script's own path | Safe even when script is sourced |
| `cd "$ROOT_DIR"` | Anchor working dir | All relative paths work consistently |

## Common Mistakes

### Wrong

```bash
#!/bin/bash
# No set flags — silent failures will happen
cd scripts    # Fails if called from wrong directory
MY_VAR=$UNSET_VAR  # Expands to empty string silently
```

### Correct

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
MY_VAR="${DEFINED_VAR:?DEFINED_VAR must be set}"
```

## Portability Notes

- `#!/usr/bin/env bash` works on macOS, Linux, Alpine (when bash is installed)
- `$(date '+%H:%M:%S')` timestamp format works on both GNU and BSD date
- Avoid `readlink -f` on macOS without coreutils; prefer the `cd + pwd` idiom
- `[[` and `(( ))` are Bash-specific; they are fine since the shebang specifies bash

## Related

- [variable-safety.md](variable-safety.md)
- [error-handling.md](error-handling.md)
- [patterns/kubectl-scripting.md](../patterns/kubectl-scripting.md)
