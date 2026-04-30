# DESIGN: GitHub Actions CI/CD

> Technical specification for two GitHub Actions workflows: `ci.yml` (PR gate) and `release.yml` (tag-triggered release).

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | GITHUB_ACTIONS_CI |
| **Date** | 2026-04-30 |
| **Author** | design-agent |
| **DEFINE** | [DEFINE_GITHUB_ACTIONS_CI.md](./DEFINE_GITHUB_ACTIONS_CI.md) |
| **Status** | Ready for Build |

---

## Architecture Overview

```text
┌─────────────────────────────────────────────────────────────────┐
│                    GITHUB ACTIONS WORKFLOWS                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  TRIGGER: pull_request → main          TRIGGER: push tag v*     │
│           │                                     │               │
│           ▼                                     ▼               │
│  ┌─────────────────────┐          ┌─────────────────────────┐   │
│  │       ci.yml        │          │      release.yml        │   │
│  ├─────────────────────┤          ├─────────────────────────┤   │
│  │  python: 3.13       │          │  python: 3.13           │   │
│  │  ┌────────────────┐ │          │  1. checkout (full)     │   │
│  │  │     test       │ │          │  2. get prev tag        │   │
│  │  └────────────────┘ │          │  3. git log → changelog  │   │
│  │  Steps:             │          │  4. gh release create   │   │
│  │  1. checkout        │          └─────────────────────────┘   │
│  │  2. setup-python    │                    │                   │
│  │  3. restore cache   │                    ▼                   │
│  │  4. pip install -e .│          GitHub Release page           │
│  │  5. ruff check      │          title: "v1.0.0"               │
│  │  6. pytest unit     │          body:  changelog              │
│  │  7. save cache      │                                        │
│  └──────────┬──────────┘                                        │
│             │                                                    │
│             ▼                                                    │
│  PR check: ✅ test                                               │
│  Branch protection blocks merge until green                      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Decisions

### Decision 1: `ubuntu-latest` runner for both workflows

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-30 |

**Context:** GitHub Actions offers `ubuntu-latest`, `windows-latest`, and `macos-latest`. The platform runs on Linux (KIND cluster).

**Choice:** `ubuntu-latest` for both workflows.

**Rationale:** Matches the target OS. Fastest startup. Free tier minutes are 2× more generous than macOS. All tools (`kubectl`, `gh`, `pip`) available out of the box.

**Alternatives Rejected:**
1. `macos-latest` — 10× minute cost on free tier; no benefit for a Linux-native project
2. Self-hosted runner — significant infra overhead; out of scope

---

### Decision 2: Single Python version (3.13), not a matrix

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-30 |

**Context:** The project is an internal data platform running on a single KIND cluster node, not a library distributed to end-users across multiple Python runtimes. `pyproject.toml` declares `requires-python = ">=3.11"` as a floor, not a cross-version compatibility promise.

**Choice:** CI runs on Python 3.13 only. Python version compatibility is documented in `pyproject.toml` rather than validated by a matrix.

**Rationale:** A matrix doubles CI job count and cost for no practical benefit — the platform is deployed as a single Docker image using one pinned Python version. Testing two versions would catch hypothetical cross-version bugs that never occur in production. The `>=3.11` floor in `pyproject.toml` communicates the constraint to tooling (pip, uv) without requiring CI to prove it on every PR.

**Alternatives Rejected:**
1. `matrix: ["3.11", "3.13"]` — doubles job time and quota; no production scenario requires running on 3.11

---

### Decision 3: pip cache keyed on requirements hash, not week number

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-30 |

**Context:** Two cache key strategies are common: time-based (weekly) and content-based (hash of requirements files).

**Choice:** Content-based: `hashFiles('agents/requirements.txt', 'portal/requirements.txt', 'pyproject.toml')`.

**Rationale:** Cache invalidates exactly when dependencies change — not before, not after. Time-based keys cause unnecessary cold installs every Monday.

**Alternatives Rejected:**
1. Weekly key (`${{ env.WEEK }}`) — false cache misses on Mondays, false cache hits mid-week after dep changes

---

### Decision 4: Changelog via `git log`, not a changelog action

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-30 |

**Context:** Several GitHub Actions generate changelogs (e.g. `release-drafter`, `conventional-changelog-action`). These add third-party action dependencies.

**Choice:** Pure `git log <prev-tag>..HEAD --pretty=format:"- %s"` shell command in the workflow.

**Rationale:** Zero external action dependency. Output is predictable. Conventional commit messages are already used in this project (`feat:`, `fix:`, `chore:`), so the plain log is already readable. Adding a changelog action would also require configuration files.

**Alternatives Rejected:**
1. `release-drafter` action — requires `.github/release-drafter.yml` config, adds third-party trust surface
2. `conventional-changelog-action` — heavy Node.js dependency, config overhead

---

### Decision 5: `fetch-depth: 0` only in `release.yml`

| Attribute | Value |
|-----------|-------|
| **Status** | Accepted |
| **Date** | 2026-04-30 |

**Context:** `actions/checkout` defaults to `fetch-depth: 1` (shallow clone). `git log` for changelog and `git describe` for previous tag require full history.

**Choice:** `ci.yml` uses default shallow clone (fast). `release.yml` uses `fetch-depth: 0` (full history).

**Rationale:** CI only needs to run tests on the current commit — no history needed. Release needs full history for changelog. Shallow clone is ~10× faster for large repos.

---

## File Manifest

| # | File | Action | Purpose | Dependencies |
|---|------|--------|---------|--------------|
| 1 | `.github/workflows/ci.yml` | Create | PR gate: lint + unit tests, Python 3.11 + 3.13 matrix, pip cache | None |
| 2 | `.github/workflows/release.yml` | Create | Tag-triggered GitHub Release with auto-generated changelog | None |

---

## Code Patterns

### Pattern 1: `ci.yml` — complete workflow

```yaml
# .github/workflows/ci.yml
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  test:
    name: test
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.13
        uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Restore pip cache
        uses: actions/cache@v4
        with:
          path: ~/.cache/pip
          key: pip-3.13-${{ hashFiles('agents/requirements.txt', 'portal/requirements.txt', 'pyproject.toml') }}
          restore-keys: |
            pip-3.13-

      - name: Install dependencies
        run: pip install -e . -r agents/requirements.txt -r portal/requirements.txt

      - name: Lint (ruff)
        run: ruff check agents/ portal/ tests/

      - name: Unit tests
        run: pytest tests/unit/ -q
```

---

### Pattern 2: `release.yml` — complete workflow

```yaml
# .github/workflows/release.yml
name: Release

on:
  push:
    tags:
      - "v*"

permissions:
  contents: write

jobs:
  release:
    name: Create GitHub Release
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Generate changelog
        id: changelog
        run: |
          CURRENT_TAG="${GITHUB_REF_NAME}"
          PREV_TAG=$(git describe --tags --abbrev=0 "${CURRENT_TAG}^" 2>/dev/null || echo "")

          if [ -n "$PREV_TAG" ]; then
            RANGE="${PREV_TAG}..${CURRENT_TAG}"
            HEADER="## Changes since ${PREV_TAG}"
          else
            RANGE="${CURRENT_TAG}"
            HEADER="## All changes"
          fi

          COMMITS=$(git log "${RANGE}" --pretty=format:"- %s" --no-merges)

          {
            echo "notes<<EOF"
            echo "${HEADER}"
            echo ""
            echo "${COMMITS}"
            echo "EOF"
          } >> "$GITHUB_OUTPUT"

      - name: Create GitHub Release
        run: |
          gh release create "${{ github.ref_name }}" \
            --title "Release ${{ github.ref_name }}" \
            --notes "${{ steps.changelog.outputs.notes }}"
        env:
          GH_TOKEN: ${{ github.token }}
```

---

## Testing Strategy

These are YAML configuration files — no unit tests apply. Validation is functional:

| Test | Method | Pass Condition |
|------|--------|----------------|
| ci.yml triggers on PR | Open a test PR against `main` | `test` job appears in PR checks |
| ci.yml passes on green code | Current codebase, all tests pass | `test` job green |
| ci.yml fails on broken lint | Add `import os` unused to any file | `test` job fails with ruff error |
| ci.yml fails on broken test | Add `assert False` to any unit test | `test` job fails with pytest exit code 1 |
| cache works | Run CI twice without changing deps | Second run: `Cache hit` in restore step, install < 10s |
| release.yml creates release | `git tag v0.0.1-test && git push origin v0.0.1-test` | GitHub Release page shows `v0.0.1-test` with changelog |
| release.yml with no prev tag | First tag in repo | Release created with full commit history |

---

## Post-Deploy Checklist

After merging the workflows to `main`, configure branch protection in **GitHub → Settings → Branches → Add rule → `main`**:

- [ ] Check **Require status checks to pass before merging**
- [ ] Search and add required check: `test`
- [ ] Optionally enable **Require branches to be up to date before merging**
- [ ] Save the rule

> Note: Status check names appear in the dropdown only after CI has run at least once. Merge the workflow PR first (bypassing protection), let CI run, then add the protection rule.

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-04-30 | design-agent | Initial version from DEFINE_GITHUB_ACTIONS_CI.md |

---

## Next Step

**Ready for:** `/build .claude/sdd/features/DESIGN_GITHUB_ACTIONS_CI.md`
