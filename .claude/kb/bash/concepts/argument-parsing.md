# Argument Parsing

> **Purpose**: Parse flags and positional arguments reliably using manual `$@` loops (for long flags) or `getopts` (for POSIX short flags)
> **Confidence**: 0.95
> **MCP Validated**: 2026-04-24

## Overview

Bash has two built-in argument parsing mechanisms: `getopts` for POSIX-style short flags (`-n`, `-v VALUE`) and manual `$@` loop for long-style flags (`--no-seed`, `--step=3`). For data-platform scripts with human-friendly flags like `--no-spark` or `--step 3`, the manual loop is simpler and avoids the long-option limitations of `getopts`. Both patterns should set named variables that default via `${VAR:-default}` before the loop.

## The Concept

```bash
#!/usr/bin/env bash
set -euo pipefail

# ── Defaults (env-overridable + flag-overridable) ─────────────────────────────
SKIP_SEED="${SKIP_SEED:-false}"
SKIP_SPARK_BUILD="${SKIP_SPARK_BUILD:-false}"
SINGLE_STEP="${SINGLE_STEP:-}"
VERBOSE="${VERBOSE:-false}"

# ── Manual $@ loop — handles --flag and --flag=value and --flag VALUE ─────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-seed)
      SKIP_SEED=true
      ;;
    --no-spark)
      SKIP_SPARK_BUILD=true
      ;;
    --step=*)
      SINGLE_STEP="${1#--step=}"
      ;;
    --step)
      SINGLE_STEP="${2:?--step requires an argument}"
      shift
      ;;
    -v|--verbose)
      VERBOSE=true
      set -x   # Enable command tracing
      ;;
    -h|--help)
      echo "Usage: $0 [--no-seed] [--no-spark] [--step N] [-v]"
      exit 0
      ;;
    --)
      shift; break  # Everything after -- is positional
      ;;
    -*)
      echo "[ERROR] Unknown flag: $1" >&2; exit 1
      ;;
    *)
      break  # First non-flag is positional — stop parsing
      ;;
  esac
  shift
done

# Remaining positional args are in "$@"
TARGET="${1:-all}"
```

## getopts Alternative (Short Flags Only)

```bash
# Use getopts when POSIX short flags are sufficient
while getopts ":hvs:n:" opt; do
  case "$opt" in
    h) echo "Usage: $0 [-h] [-v] [-s step] [-n namespace]"; exit 0 ;;
    v) VERBOSE=true ;;
    s) SINGLE_STEP="$OPTARG" ;;
    n) NAMESPACE="$OPTARG" ;;
    :) echo "[ERROR] -$OPTARG requires an argument" >&2; exit 1 ;;
    ?) echo "[ERROR] Unknown option: -$OPTARG" >&2; exit 1 ;;
  esac
done
shift $(( OPTIND - 1 ))
```

## Quick Reference

| Pattern | Handles | Limitation |
|---------|---------|------------|
| Manual `case "$1"` loop | Long flags, `--flag=val`, mixed | More boilerplate |
| `getopts` | Short `-f`, `-f val` | No long options natively |
| `"$@"` in loop | All args safe | Must shift explicitly |

## Common Mistakes

### Wrong

```bash
# Positional-only — fragile, order-dependent
STEP=$1
SKIP=$2
```

### Correct

```bash
# Named flags — self-documenting and order-independent
while [[ $# -gt 0 ]]; do
  case "$1" in
    --step) SINGLE_STEP="$2"; shift ;;
    --no-seed) SKIP_SEED=true ;;
    *) break ;;
  esac
  shift
done
```

## Related

- [variable-safety.md](variable-safety.md)
- [script-header.md](script-header.md)
