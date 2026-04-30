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
│  │  matrix: [3.11,3.13]│          │  python: 3.13           │   │
│  │  ┌────────────────┐ │          │  1. checkout (full)     │   │
│  │  │ test (3.11)    │ │◄─parallel│  2. get prev tag        │   │
│  │  │ test (3.13)    │ │          │  3. git log → changelog  │   │
│  │  └────────────────┘ │          │  4. gh release create   │   │
│  │  Each leg:          │          └─────────────────────────┘   │
│  │  1. checkout        │                    │                   │
│  │  2. setup-python    │                    ▼                   │
│  │  3. restore cache   │          GitHub Release page           │
│  │  4. pip install -e .│          title: "v1.0.0"               │
│  │  5. ruff check      │          body:  changelog              │
│  │  6. pytest unit     │                                        │
│  │  7. save cache      │                                        │
│  └──────────┬──────────┘                                        │
│             │                                                    │
│             ▼                                                    │
│  PR check: ✅ test (3.11) + ✅ test (3.13)                       │
│  Branch protection blocks merge until both green                 │
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

### Decision 2: pip cache keyed on requirements hash, not week number

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

### Decision 3: Changelog via `git log`, not a changelog action

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

### Decision 4: `fetch-depth: 0` only in `release.yml`

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
    name: test (${{ matrix.python-version }})
    runs-on: ubuntu-latest

    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.13"]

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Restore pip cache
        uses: actions/cache@v4
        with:
          path: ~/.cache/pip
          key: pip-${{ matrix.python-version }}-${{ hashFiles('agents/requirements.txt', 'portal/requirements.txt', 'pyproject.toml') }}
          restore-keys: |
            pip-${{ matrix.python-version }}-

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
| ci.yml triggers on PR | Open a test PR against `main` | Both `test (3.11)` and `test (3.13)` jobs appear in PR checks |
| ci.yml passes on green code | Current codebase, all tests pass | Both jobs green |
| ci.yml fails on broken lint | Add `import os` unused to any file | At least one job fails with ruff error |
| ci.yml fails on broken test | Add `assert False` to any unit test | At least one job fails with pytest exit code 1 |
| cache works | Run CI twice without changing deps | Second run: `Cache hit` in restore step, install < 10s |
| release.yml creates release | `git tag v0.0.1-test && git push origin v0.0.1-test` | GitHub Release page shows `v0.0.1-test` with changelog |
| release.yml with no prev tag | First tag in repo | Release created with full commit history |

---

## Post-Deploy Checklist

After merging the workflows to `main`, configure branch protection in **GitHub → Settings → Branches → Add rule → `main`**:

- [ ] Check **Require status checks to pass before merging**
- [ ] Search and add required check: `test (3.11)`
- [ ] Search and add required check: `test (3.13)`
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
