# CI/CD Pipeline

Internal reference for MachinaOs's GitHub Actions setup. Workflow inventory, security posture, release flow.

---

## Workflow inventory

```
                           +------------------+
  push to main ----------> |     ci.yml       |---+
  PR to main ------------> |                  |   |
                           +------------------+   |
                                                  |    +---------------------+
                                                  +--> |   predeploy.yml     |
                                                  |    |   (workflow_call)   |
  v*.*.* tag push -------> +------------------+   |    |                     |
  manual dispatch -------> |   release.yml    |---+    | - plan              |
                           |   (dry-run by    |        | - pre-commit        |
                           |    default for   |        | - build-and-lint    |
                           |    manual runs)  |        | - backend-tests     |
                           +------------------+        |   (3 shards)        |
                                  |                    | - cli-tests         |
                                  +-- predeploy        | - test-build-start  |
                                  +-- audit            |   (3 OS)            |
                                  +-- build-for-publish| - ci-passed (alls)  |
                                  +-- publish-npm      +---------------------+
                                  +-- publish-github-packages
                                  +-- publish-pypi  --> publish-pypi.yml (reusable)
                                  +-- create-github-release
                                  +-- test-install --> test-install.yml

  manual dispatch -------> +------------------+
                           |   rollback.yml   |  npm deprecate + (optional) revert PR
                           +------------------+

  weekly + on PR --------> +------------------+
                           |   codeql.yml     |  Python + JS/TS SAST (security-extended)
                           +------------------+

  on .github changes ----> +------------------+
                           |  check-zizmor.yml|  workflow-security linter (SARIF)
                           +------------------+

  push to docs-MachinaOs/ +------------------+
                           |    docs.yml      |  Mintlify documentation deploy
                           +------------------+
```

### Workflow summary

| Workflow | File | Triggers | Purpose |
|----------|------|----------|---------|
| CI | `.github/workflows/ci.yml` | push/PR → main | Delegates to predeploy.yml |
| Predeploy | `.github/workflows/predeploy.yml` | `workflow_call` | Plan + pre-commit + build/lint + sharded tests + OS-matrix build/start + alls-green |
| Release | `.github/workflows/release.yml` | `v*.*.*` tag, manual (dry-run default) | predeploy → audit → publish (npm + GH Packages + PyPI) → SLSA attest → release → test-install |
| Publish PyPI | `.github/workflows/publish-pypi.yml` | `workflow_call` | OIDC trusted publishing for `machinaos-server` |
| Test Install | `.github/workflows/test-install.yml` | manual, called from release | Cross-platform install smoke (3 OS × 3 paths) |
| Rollback | `.github/workflows/rollback.yml` | manual (`workflow_dispatch`) | npm deprecate + optional revert PR |
| CodeQL | `.github/workflows/codeql.yml` | push/PR + weekly Sun 20:30 UTC | Python + JS/TS SAST |
| Zizmor | `.github/workflows/check-zizmor.yml` | `.github/**` changes + weekly Mon 07:00 UTC | Workflow-security linter |
| Docs | `.github/workflows/docs.yml` | push to `docs-MachinaOs/**` | Mintlify deploy |
| Setup | `.github/actions/setup/action.yml` | (composite) | pnpm 9 + Node 22 + Python (from `.python-version`) + uv |

### Supply chain

- **All action `uses:` references are pinned to commit SHAs.** Enforced by [zizmor](https://github.com/woodruffw/zizmor) on every PR touching `.github/`. Dependabot rewrites pins via the `github-actions` ecosystem group.
- **`.github/dependabot.yml`** — weekly grouped updates for npm + pip (root + server/) + github-actions ecosystems. Each grouped PR is one ecosystem × one dependency-type.
- **`.pre-commit-config.yaml`** — ruff (format + lint), prettier (client/), eslint, actionlint, stdlib hygiene. Mirrored as a CI job in predeploy.yml so missed local installs still gate merges.
- **`.python-version`** — single source of truth (`3.12`). Consumed via `python-version-file:` in `setup-python` everywhere except the `test-install.yml` jobs which run after `actions/checkout` so the file is available.

### Required secrets

| Secret | Used by | Description |
|--------|---------|-------------|
| `NPM_TOKEN` | release.yml, rollback.yml | npm registry publish + deprecate |
| `GITHUB_TOKEN` | release.yml, rollback.yml | GitHub Packages publish + revert-PR creation (auto-provided) |
| `MINTLIFY_TOKEN` | docs.yml | Mintlify documentation deployment |

PyPI publishing uses **OIDC trusted publishing** (no PYPI_TOKEN secret needed). One-time setup: configure MachinaOs as a trusted publisher on PyPI with workflow=`publish-pypi.yml`, environment=`pypi`.

---

## Composite setup action

**File:** [.github/actions/setup/action.yml](../.github/actions/setup/action.yml)

```yaml
- uses: ./.github/actions/setup
- uses: ./.github/actions/setup
  with:
    node-version: '20'   # override Node only; Python tracks .python-version
```

| Tool | Version source | Action (pinned SHA) |
|------|---------------|---------------------|
| pnpm | hard-coded `9` (default) | `pnpm/action-setup` |
| Node.js | `node-version` input, default `22` | `actions/setup-node` |
| Python | `.python-version` file (`3.12`) | `actions/setup-python` |
| uv | latest in v8 line | `astral-sh/setup-uv` |

After tool install, the composite installs the supervisor CLI editably (`uv pip install --system -e .`) and runs a verify step that prints each tool's version.

---

## Predeploy validation

**File:** [.github/workflows/predeploy.yml](../.github/workflows/predeploy.yml)

Six jobs gated by a `plan` job that emits change-detection booleans (lifted from [astral-sh/uv's `ci.yml`](https://github.com/astral-sh/uv/blob/main/.github/workflows/ci.yml)):

- `plan` — `dorny/paths-filter` decides which downstream jobs need to run. Boolean outputs: `backend_changed`, `frontend_changed`, `cli_changed`, `workflows_changed`, `docs_only`.
- `pre-commit` — runs `pre-commit run --all-files`. Catches lint/format issues for contributors who haven't installed the hook locally. Skipped for doc-only PRs (unless workflows also changed).
- `build-and-lint` — full `pnpm run build` + frontend tests (vitest, 148 cases) + ESLint + tsgo type-check. Skipped for doc-only PRs.
- `backend-tests` — pytest sharded by domain (prefect pattern):
  - `nodes` — `tests/nodes/`
  - `services` — `tests/services/` + `tests/temporal/` + `tests/llm/` + `tests/credentials/`
  - `root` — everything else (cross-cutting invariants)

  Gated on `backend_changed || workflows_changed`. Each shard runs the same `uv sync` + `uv run pytest` pattern.
- `cli-tests` — 106 CLI tests via `python -m pytest cli/tests/`. Gated on `cli_changed || workflows_changed`.
- `test-build-start` — cross-OS (`ubuntu-latest`, `macos-latest`, `windows-latest`) build + start smoke. Verifies `client/dist/` + `server/.venv/` materialise and the supervisor brings up the backend reachable at `localhost:3010/health`. Skipped for doc-only PRs.
- `ci-passed` — single aggregator job using [`re-actors/alls-green`](https://github.com/re-actors/alls-green) (hatch pattern). Branch protection targets this one job name; matrix changes don't break protection rules. `allowed-skips` lets the plan-job optimisations skip downstream jobs without failing the aggregator.

---

## Release workflow

**File:** [.github/workflows/release.yml](../.github/workflows/release.yml)

### Triggers
| Trigger | Behaviour |
|---------|-----------|
| Push of `v*.*.*` tag | Full release (npm + GH Packages + PyPI + SLSA attest + GitHub release + test-install) |
| `workflow_dispatch` | Dry-run by default; explicit `dry_run=false` required to publish (gemini-cli pattern) |

### Job graph

```
predeploy (uses predeploy.yml)
   |
   v
audit              -- pnpm audit --prod --audit-level moderate (BLOCKING)
   |
   v
build-for-publish  -- once-per-release client build, uploaded as artifact
   |  |  |
   v  v  v
publish-npm        publish-github-packages       publish-pypi
   |                  |                          (uses publish-pypi.yml)
   +- SLSA           +- npm scope rewrite        +- uv build --no-sources
   +- attest-build-provenance@v2                 +- attest-build-provenance@v2
   +- conditional on dry_run                     +- pypa/gh-action-pypi-publish
                                                 +- OIDC, no token
   |  |  |
   v  v  v
create-github-release  -- generate-notes + verify-tag (skipped on dry-run)
   |
   v
test-install (uses test-install.yml)
```

### Hardening (lifted from gh CLI + vercel + wrangler + uv)

- `NPM_CONFIG_PROVENANCE=true` set at workflow env-level — supersedes per-command `--provenance` flag.
- `pnpm audit --prod --audit-level moderate` is **blocking** (previous `|| true` masked vulnerabilities).
- `workflow_dispatch` defaults `dry_run=true`; explicit override required to publish.
- `cache: ''` on `setup-node` in publish jobs — prevents poisoned-cache supply-chain contamination (wrangler pattern).
- `actions/attest-build-provenance@v2` on every published artifact (npm tarball + Python wheel + sdist). SLSA Level 3. Verifiable via `gh attestation verify <artifact> --repo zeenie-ai/MachinaOS`.

---

## Reusable: publish-pypi.yml

**File:** [.github/workflows/publish-pypi.yml](../.github/workflows/publish-pypi.yml)

Called via `workflow_call` from release.yml. Three jobs:

1. `build` — `uv build --no-sources` in `server/`, produces wheel + sdist, uploads artifact.
2. `attest` — `actions/attest-build-provenance@v2` signs each artifact via OIDC.
3. `publish` — `pypa/gh-action-pypi-publish` with `environment: pypi` for the OIDC subject claim. `skip-existing: true` so re-runs after partial failures don't error.

Inputs:
- `package_dir` (default `server`)
- `dry_run` (boolean) — skips the publish job

---

## Rollback workflow

**File:** [.github/workflows/rollback.yml](../.github/workflows/rollback.yml)

Manual `workflow_dispatch` with inputs:
- `version` (required, e.g. `v0.0.72`)
- `deprecate_msg` (default: security-conservative template)
- `open_revert_pr` (boolean, default `false`)

Three jobs:
1. `deprecate-npm` — `npm deprecate` on both `machinaos@VERSION` (npm) and `@zeenie-ai/machinaos@VERSION` (GitHub Packages).
2. `yank-pypi-notice` — prints PyPI yank instructions (PyPI yank is UI-only, no API).
3. `open-revert-pr` — opens a `rollback/<version>` branch + revert PR (skipped unless `open_revert_pr=true`).

A `summary` job posts a step-summary table of what ran.

---

## Security workflows

### CodeQL

**File:** [.github/workflows/codeql.yml](../.github/workflows/codeql.yml)

Two-language matrix:
- `python` — covers cli/, server/, scripts/.
- `javascript-typescript` — covers client/, server/nodejs/.

Triggers: push to main + PR to main (both with `paths-ignore` excluding docs) + weekly schedule (Sun 20:30 UTC). Query pack: `security-extended` (broader than default, narrower than `security-and-quality` which adds noisy code-quality queries).

### Zizmor

**File:** [.github/workflows/check-zizmor.yml](../.github/workflows/check-zizmor.yml)

Lints workflows for over-granted permissions, unpinned action `uses:`, insecure `${{ ... }}` template expansion, and `pull_request_target` + checkout-of-PR-ref combinations. Triggers on `.github/**` changes + weekly Mon 07:00 UTC (before the Mon 06:00 Dependabot batch).

Outputs SARIF to the Security tab.

---

## Test-install workflow

**File:** [.github/workflows/test-install.yml](../.github/workflows/test-install.yml)

Validates the end-user install paths after a release publishes (or on manual dispatch). Three jobs × three OS = nine matrix cells:

| Job | What it does |
|-----|--------------|
| `test-npm-install` | `npm install -g machinaos`, run `machina --help` / `--version`, smoke-start the backend |
| `test-git-clone` | `git clone` the public repo, `pnpm run build`, smoke-start |
| `test-install-script-unix` / `test-install-script-windows` | Curl/iwr the install script from the repo, validate the resulting `machina` is on PATH |

Each job runs an `actions/checkout` first so `.python-version` is available for `setup-python` — independent of the install path being tested.

---

## Docs workflow

**File:** [.github/workflows/docs.yml](../.github/workflows/docs.yml)

Triggers on push to `docs-MachinaOs/**` on main + manual dispatch. Single job: install Mintlify CLI, run `mintlify deploy` with `MINTLIFY_TOKEN`. No predeploy gate — docs are static, independent of the application's build.

---

## Source files

| File | Purpose |
|------|---------|
| `.github/workflows/ci.yml` | CI entry point |
| `.github/workflows/predeploy.yml` | Reusable validation (plan + pre-commit + build/lint + tests + OS matrix + alls-green) |
| `.github/workflows/release.yml` | Tag-triggered release pipeline (dry-run default on manual) |
| `.github/workflows/publish-pypi.yml` | Reusable: build wheel + sdist + OIDC trusted publish |
| `.github/workflows/rollback.yml` | Manual deprecate + revert PR |
| `.github/workflows/codeql.yml` | SAST (Python + JS/TS) |
| `.github/workflows/check-zizmor.yml` | Workflow-security linter |
| `.github/workflows/test-install.yml` | Cross-platform user-install smoke |
| `.github/workflows/docs.yml` | Mintlify documentation deploy |
| `.github/actions/setup/action.yml` | Composite: pnpm + Node + Python + uv |
| `.github/dependabot.yml` | Weekly grouped updates (npm + pip × 2 + actions) |
| `.pre-commit-config.yaml` | ruff + prettier + eslint + actionlint hooks |
| `.python-version` | Toolchain pin (`3.12`) — single source of truth |
