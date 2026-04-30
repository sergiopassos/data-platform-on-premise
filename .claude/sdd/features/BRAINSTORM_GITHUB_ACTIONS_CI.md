# BRAINSTORM: GitHub Actions CI/CD

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | GITHUB_ACTIONS_CI |
| **Date** | 2026-04-30 |
| **Status** | Ready for Define |
| **Source** | User request + discovery dialogue |

---

## Problem Statement

The repository has no CI automation. Every PR can merge without running the 91 unit tests or lint checks. There is also no release process — versions are untracked. Two risks: broken code lands on `main` silently, and there is no way to mark a stable platform version for reference.

---

## Discovery Summary

| Question | Answer |
|----------|--------|
| What must block a merge? | Unit tests + ruff lint |
| Additional workflows needed? | Yes — release on tag push |
| Release versioning strategy | Manual tag (`v*`) → auto-generated changelog + GitHub Release |
| Python version(s) | Matrix: 3.11 + 3.13 |

---

## Context

| Aspect | Detail |
|--------|--------|
| Test suite | 91 unit tests across `tests/unit/agents/` and `tests/unit/portal/` — all pass locally in ~11s |
| Lint | `ruff check` with `pyproject.toml` config (`E`, `F`, `I` rules, line-length 100) |
| Install | `pip install -e . -r agents/requirements.txt -r portal/requirements.txt` |
| Python | `requires-python = ">=3.11"`; dev uses 3.13 |
| Existing CI | None — no `.github/` directory |
| Integration tests | Require live KIND cluster — excluded from CI (inviável no GitHub Actions) |

---

## Selected Approach: Two Independent Workflows

### Workflow 1: `ci.yml` — PR gate

**Trigger:** `pull_request` targeting `main`

**Jobs:**
1. Matrix setup: Python 3.11 + 3.13 (parallel)
2. `pip install -e . -r agents/requirements.txt -r portal/requirements.txt`
3. `ruff check agents/ portal/ tests/`
4. `pytest tests/unit/ -q`

**Expected runtime:** ~2 min per matrix leg

**Branch protection:** CI job must pass before merge is allowed (configured via GitHub repo settings — documented in DEFINE)

---

### Workflow 2: `release.yml` — GitHub Release on tag

**Trigger:** `push` with tag matching `v*`

**Jobs:**
1. Python 3.13 setup
2. Generate changelog from `git log <prev-tag>..HEAD --oneline` (conventional commits format)
3. `gh release create $TAG --notes "$CHANGELOG" --title "Release $TAG"`

**Usage:**
```bash
git tag v1.0.0
git push origin v1.0.0
# → Release created automatically with changelog
```

---

## YAGNI — Removed from Scope

| Feature | Reason |
|---------|--------|
| Docker image build | Images loaded locally into KIND; no public registry |
| Integration tests in CI | Require live KIND cluster — not feasible on GitHub Actions runners |
| `mypy` type checking | No mypy config in `pyproject.toml`; would add noise without baseline |
| Coverage report + badge | Marginal value for current team size |
| Dependabot | Useful but outside requested scope |
| Scheduled weekly run | Not requested; can be added in v2 |

---

## File Manifest (preliminary)

| # | File | Action | Purpose |
|---|------|--------|---------|
| 1 | `.github/workflows/ci.yml` | Create | PR gate: lint + unit tests on Python 3.11 + 3.13 |
| 2 | `.github/workflows/release.yml` | Create | Tag-triggered release with auto-generated changelog |

---

## Resolved Questions

| Question | Answer |
|----------|--------|
| Run on push to `main` too? | No — only on `pull_request` targeting `main` |
| pip cache? | Yes — use `actions/cache` on `~/.cache/pip` keyed by requirements hash |
| Branch protection rules? | Repo is public; rules must be set manually in GitHub Settings — document as a post-deploy checklist in DEFINE |

---

## Next Step

**Ready for:** `/define .claude/sdd/features/BRAINSTORM_GITHUB_ACTIONS_CI.md`
