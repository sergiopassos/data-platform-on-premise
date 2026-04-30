# DEFINE: GitHub Actions CI/CD

> Two GitHub Actions workflows: a PR gate (lint + unit tests, single Python 3.13 job) and a tag-triggered release workflow that auto-generates a changelog and creates a GitHub Release. Python 3.13 is the documented supported version; `pyproject.toml` declares `requires-python = ">=3.11"` but CI validates only the primary runtime.

## Metadata

| Attribute | Value |
|-----------|-------|
| **Feature** | GITHUB_ACTIONS_CI |
| **Date** | 2026-04-30 |
| **Author** | define-agent |
| **Status** | Ready for Design |
| **Clarity Score** | 15/15 |
| **Source** | `BRAINSTORM_GITHUB_ACTIONS_CI.md` |

---

## Problem Statement

The repository has no CI automation. Pull requests can merge without running the 91 unit tests or lint checks, allowing broken code to land on `main` silently. There is also no release process — platform versions are untracked, making it impossible to reference a known-good state after a deployment.

---

## Target Users

| User | Role | Pain Point |
|------|------|------------|
| Platform Engineer | Opens PRs with new features or fixes | No automated feedback that tests still pass; discovers breakage manually or not at all |
| Repository Maintainer | Reviews and merges PRs | Cannot enforce quality gate — must trust contributors ran tests locally |
| Platform Consumer | Needs a stable version reference after deployment | No GitHub Releases to anchor a changelog or known-good commit |

---

## Goals

| Priority | Goal |
|----------|------|
| **MUST** | Every PR targeting `main` runs `ruff check` and `pytest tests/unit/` on Python 3.13 before merge is allowed |
| **MUST** | CI job failure blocks the merge (enforced via branch protection) |
| **MUST** | pip dependencies are cached between runs to keep CI under 2 minutes |
| **MUST** | Pushing a `v*` tag to `main` automatically creates a GitHub Release with a changelog derived from conventional commits since the previous tag |
| **SHOULD** | Release changelog groups commits by type (`feat`, `fix`, `chore`, etc.) |

---

## Success Criteria

- [ ] Opening a PR against `main` triggers `ci.yml` within 30 seconds
- [ ] `ci.yml` runs a single `test` job on Python 3.13
- [ ] The job installs deps, runs `ruff check agents/ portal/ tests/`, then `pytest tests/unit/ -q`
- [ ] A job that fails (lint error or test failure) blocks the PR merge
- [ ] A job that passes does not block the PR merge
- [ ] Total CI runtime ≤ 2 minutes (with warm pip cache)
- [ ] Pushing `git tag v1.0.0 && git push origin v1.0.0` triggers `release.yml`
- [ ] `release.yml` creates a GitHub Release titled `v1.0.0` with a changelog of commits since the previous tag
- [ ] If no previous tag exists, changelog covers all commits on `main`

---

## Acceptance Tests

| ID | Scenario | Given | When | Then |
|----|----------|-------|------|------|
| AT-001 | PR with passing tests | `main` branch, CI configured, all 91 tests pass | PR is opened targeting `main` | `test` job reports green on Python 3.13; merge is unblocked |
| AT-002 | PR with failing test | A test in `tests/unit/` is intentionally broken | PR is opened targeting `main` | `test` job fails; merge is blocked; PR shows red check |
| AT-003 | PR with lint error | A file in `agents/` has an unused import | PR is opened targeting `main` | `ruff check` step fails; job fails; merge is blocked |
| AT-004 | pip cache hit | CI has run at least once; requirements files unchanged | New PR is opened | Cache restore step shows `Cache hit`; install step takes < 10s |
| AT-005 | Tag push creates release | `main` is clean, previous tag `v0.9.0` exists | `git tag v1.0.0 && git push origin v1.0.0` | GitHub Release `v1.0.0` created with changelog listing commits since `v0.9.0` |
| AT-006 | First-ever tag | No previous tags exist | `git tag v1.0.0 && git push origin v1.0.0` | GitHub Release `v1.0.0` created with full commit history as changelog |
| AT-007 | Tag on non-main commit | Tag pushed on a feature branch commit | `git tag v1.0.0-rc1 && git push origin v1.0.0-rc1` | `release.yml` triggers (tags are not branch-scoped); release created — acceptable behavior |

---

## Out of Scope

- Integration tests in CI — require live KIND cluster; not feasible on GitHub Actions standard runners
- Docker image build or push — images are loaded locally into KIND; no public registry
- `mypy` type checking — no mypy baseline in `pyproject.toml`; would generate noise without prior setup
- Coverage reports or badges — marginal value for current team size
- Dependabot automated dependency PRs — not requested
- Scheduled/nightly runs — can be added in v2
- Self-hosted GitHub Actions runner with KIND pre-installed — significant infra investment; out of scope for MVP
- Semantic release automation (auto-bump from commits) — manual tag gives more control for this project

---

## Constraints

| Type | Constraint | Impact |
|------|------------|--------|
| Technical | `pip install` installs `chainlit==2.0.4` which has heavy transitive deps | Cache is critical to stay under 2-min target; without cache, install alone may take 60s+ |
| Technical | `portal/requirements.txt` pins `google-generativeai==0.8.3` — pinned version must be available on PyPI for both Python versions | If package is yanked, CI breaks; acceptable risk |
| Technical | `pyproject.toml` declares `requires-python = ">=3.11"` — Python 3.13 satisfies this; 3.11 compatibility is declared but not CI-validated | Acceptable trade-off: project runs on a single-node KIND cluster, not a multi-version deployment |
| Operational | Branch protection rules must be set manually in GitHub Settings after workflows are merged | Document as post-deploy checklist — cannot be automated via workflow files |
| Operational | `release.yml` needs `GITHUB_TOKEN` write permission to create releases | Use `permissions: contents: write` in the workflow — no extra secret needed for public repos |

---

## Post-Deploy Checklist (manual steps after merging `ci.yml`)

These steps must be performed in GitHub repository Settings → Branches → Branch protection rules for `main`:

- [ ] Enable **Require status checks to pass before merging**
- [ ] Add required status check: `test`
- [ ] Enable **Require branches to be up to date before merging** (optional but recommended)
- [ ] Enable **Do not allow bypassing the above settings** (prevent admin bypass)

---

## Technical Context

| Aspect | Value |
|--------|-------|
| CI platform | GitHub Actions (free tier — public repo) |
| Workflow location | `.github/workflows/` |
| Install command | `pip install -e . -r agents/requirements.txt -r portal/requirements.txt` |
| Lint command | `ruff check agents/ portal/ tests/` |
| Test command | `pytest tests/unit/ -q` |
| Cache key | `requirements-${{ hashFiles('agents/requirements.txt', 'portal/requirements.txt', 'pyproject.toml') }}` |
| Cache path | `~/.cache/pip` |
| Release tool | `gh release create` (pre-installed on `ubuntu-latest` runners) |
| Changelog source | `git log <prev-tag>..HEAD --pretty=format:"- %s"` |

---

## Clarity Score Breakdown

| Element | Score (0-3) | Notes |
|---------|-------------|-------|
| Problem | 3 | Specific: no CI, broken code merges silently, no release tracking |
| Users | 3 | Three personas with concrete pain points |
| Goals | 3 | MoSCoW-prioritized, all measurable |
| Success | 3 | 9 testable criteria with concrete conditions |
| Scope | 3 | 8 explicit out-of-scope items; post-deploy checklist explicit |
| **Total** | **15/15** | |

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-04-30 | define-agent | Initial version from BRAINSTORM_GITHUB_ACTIONS_CI.md |

---

## Next Step

**Ready for:** `/design .claude/sdd/features/DEFINE_GITHUB_ACTIONS_CI.md`
