# CI/CD Pipeline

Internal reference for OpenCompany's GitHub Actions setup. Workflow inventory, release flow, and the composite setup action.

The repo currently ships **five workflows** plus one composite action. Code scanning is live but has no workflow file: it runs through GitHub's **default CodeQL setup**, which is why alerts land in the Security tab with nothing in `.github/workflows/` to point at (see [Code scanning](#code-scanning)). The other hardening workflows described in earlier revisions of this doc (zizmor, rollback, reusable PyPI publish, cross-platform test-install) are **not yet implemented** — see [Planned (not yet implemented)](#planned-not-yet-implemented) at the end.

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
  manual dispatch -------> |   release.yml    |---+    | - build-and-lint    |
                           |                  |        | - backend-tests     |
                           | predeploy gate,  |        | - cli-tests         |
                           | then publish     |        | - test-build-start  |
                           +------------------+        |   (3 OS)            |
                                  |                    +---------------------+
                                  +-- publish-npm
                                  +-- publish-github-packages
```

> Documentation is NOT deployed from this repo: the Mintlify docs live in
> the separate [zeenie-ai/docs-OpenCompany](https://github.com/zeenie-ai/docs-OpenCompany)
> repo and auto-deploy to docs.opencompany.sh via the Mintlify GitHub app
> (that repo's own workflow only runs `mintlify validate` + broken-links).

### Workflow summary

| Workflow | File | Triggers | Purpose |
|----------|------|----------|---------|
| CI | `.github/workflows/ci.yml` | push/PR → main | Delegates to predeploy.yml |
| Predeploy | `.github/workflows/predeploy.yml` | `workflow_call` | build/lint + JS dependency audit + backend tests + CLI tests + cross-OS build/start smoke |
| Release | `.github/workflows/release.yml` | `v*.*.*` tag, `workflow_dispatch` | predeploy gate → publish (npm + GitHub Packages) |
| Desktop CI | `.github/workflows/desktop-ci.yml` | PR touching `desktop/**` or the backend host-contract files; push to main touching `desktop/**` | typecheck + unit + staged-tree invariants + `electron-vite build` + Playwright Electron smoke on ubuntu-22.04 (xvfb) and windows-latest |
| Desktop release | `.github/workflows/desktop-release.yml` | `v*.*.*` tag, `workflow_dispatch` | `prepare` drafts the GitHub Release from the annotated tag's message (first line = title, rest = notes); a 3-runner matrix (windows-latest x64, macos-14 arm64+x64, ubuntu-22.04 x64) checks the synced version against the tag, builds installers with electron-builder and uploads into that one **draft**; `finalize` undrafts once every leg is green. Artifacts-only on dispatch unless `publish` is ticked |
| Setup | `.github/actions/setup/action.yml` | (composite) | bun 1.4 + Node 22 (CI-only: bun runs vite / vitest / eslint on it) + Python 3.12 + uv v8 + editable CLI install |

### Toolchain pin

- **`.python-version`** — single source of truth (`3.12`). The composite setup action currently hard-codes `python-version: '3.12'` in `setup-python` rather than reading the file via `python-version-file:`; keep the two values in sync when bumping.

### Required secrets

| Secret | Used by | Description |
|--------|---------|-------------|
| `NPM_TOKEN` | release.yml | npmjs publish access to the public `@zeenie-ai` scope (`publish-npm`) |
| `GITHUB_TOKEN` | release.yml, desktop-release.yml | GitHub Packages publish; desktop installer upload to the draft release and the `finalize` undraft (auto-provided; the desktop workflow needs `contents: write`) |

Desktop code signing is deliberately absent for the first releases: `CSC_IDENTITY_AUTO_DISCOVERY=false` is set so electron-builder never looks for a certificate. Adding it later is secrets-only (`CSC_LINK` + `CSC_KEY_PASSWORD` for Windows; a Developer ID cert plus `APPLE_ID` / `APPLE_APP_SPECIFIC_PASSWORD` / `APPLE_TEAM_ID` for macOS) plus the three mac flags in `desktop/electron-builder.yml` — see [desktop_app.md](./desktop_app.md#signing-and-updates).

---

## Composite setup action

**File:** [.github/actions/setup/action.yml](../.github/actions/setup/action.yml)

```yaml
- uses: ./.github/actions/setup
- uses: ./.github/actions/setup
  with:
    node-version: '20'   # override Node only; Python is fixed at 3.12
```

| Tool | Version source | Action |
|------|---------------|--------|
| bun | `oven-sh/setup-bun` v2.2.0 — reads the root `packageManager` pin (`bun@1.4.0`) | Immutable commit SHA |
| Node.js | `node-version` input, default `22` — a CI-only convenience: bun runs vite / vitest / eslint on it via their node shebangs (open bun-runtime bugs in vitest and eslint); nothing shipped needs Node | `actions/setup-node` v6.5.0, immutable commit SHA; package cache disabled |
| Python | hard-coded `3.12` (matches `.python-version`) | `actions/setup-python` v5.6.0, immutable commit SHA |
| uv | `astral-sh/setup-uv` v8.1.0, cache disabled | Immutable commit SHA |

`setup-node`'s `cache:` is intentionally omitted — its post-job cache save fails with a `Path Validation Error` in lanes that didn't run a JS install (CLI / backend test jobs), which would mark the whole job red even when every test passed. No dependency cache is configured for bun either (unchanged from the pnpm era).

After tool install the composite installs the supervisor CLI editably (`uv pip install --system -e .`).

---

## Predeploy validation

**File:** [.github/workflows/predeploy.yml](../.github/workflows/predeploy.yml)

Reusable `workflow_call` workflow with four independent jobs (no plan/change-detection gate, no aggregator — every job runs on every call):

- `build-and-lint` — `bun install --frozen-lockfile` + `bun run build`, then client lint (`bun run --filter react-flow-client lint`), TypeScript check (`... typecheck`), frontend tests (`... test`, vitest), and the JS dependency audit (`bun run audit:deps` at the root and in `desktop/`; see "Dependency update policy" below). Runs on `ubuntu-latest`.
- `backend-tests` — `uv lock --check` (the committed `server/uv.lock` must match `pyproject.toml`; the desktop app installs from it with `--frozen`), a pinned `pip-audit` check of the locked Python dependencies including all extras, then `uv sync` + `uv run pytest tests/ -v` in `server/`. Whole suite, unsharded. Runs on `ubuntu-latest`.
- `cli-tests` — `uv pip install --system pytest pytest-asyncio pyyaml` + `python -m pytest cli/tests/ -v`. Runs on `ubuntu-latest`.
- `test-build-start` — cross-OS matrix (`ubuntu-latest`, `macos-latest`, `windows-latest`, `fail-fast: false`). Runs `bun run build`, then `bun run tsc --version` (proves the per-platform TypeScript 7 Go binary delivered via `optionalDependencies` resolves on every OS — the type-check gate itself runs on ubuntu only; `bun run`, never `bunx`, so it resolves strictly from the root `node_modules/.bin`), then a start smoke test. On Unix it backgrounds `bun run start`, reads `PYTHON_BACKEND_PORT` out of `.env.template` and polls `http://localhost:${APP_PORT}/health` (`curl -sf -m 5`, every 2 s) for up to 90 s, giving up early if the start process exits. It always runs `bun run stop` afterwards, then fails unless `/health` answered. On Windows it starts the supervisor as a background job, waits 15 s, and fails if the job already exited.

---

## Release workflow

**File:** [.github/workflows/release.yml](../.github/workflows/release.yml)

### Triggers

| Trigger | Behaviour |
|---------|-----------|
| Push of `v*.*.*` tag | predeploy gate, then publish to npm + GitHub Packages |
| `workflow_dispatch` | Publish an existing tag again, to one or both registries (inputs `tag`, `registries`); the publish jobs check out that tag, the predeploy gate runs against the dispatching branch. Never cuts a new version |

The workflow default is `contents: read`. Publishing permissions are scoped to the job that needs them: the npmjs job needs nothing beyond `contents: read` (bun authenticates with the `NPM_TOKEN` it writes to `~/.npmrc`; npm provenance attestation, and the `id-token: write` it needed, went away with the npm CLI), while GitHub Packages receives `packages: write`. Checkout credentials are not persisted in either publishing job.

### Job graph

```
predeploy (uses predeploy.yml)
   |
   +-------------------+
   v                   v
publish-npm                    publish-github-packages
   |                              |
   +- build                       +- build
   +- cli version sync            +- cli version sync
   +- write ~/.npmrc + bun pm whoami +- write ~/.npmrc (scope route + token)
   +- publish canonical package   +- publish mirror package
      @zeenie-ai/opencompany         @zeenie-ai/opencompany
      npmjs, bun publish --access public   npm.pkg.github.com, bun publish
```

- Both publish jobs `needs: predeploy`, run on `ubuntu-latest`, and share the same prefix: immutable `actions/checkout` with `persist-credentials: false` → composite setup → `bun install --frozen-lockfile` → `bun run build` → `python -m cli version sync`. Neither job has a `setup-node` step of its own, but the composite setup installs Node (see the table above), which bun uses to run vite during `bun run build`. There is no npm CLI anywhere in the release: bun packs, authenticates and publishes.
- `publish-npm` — writes `//registry.npmjs.org/:_authToken=<NPM_TOKEN>` to `~/.npmrc`, validates it with `bun pm whoami`, then publishes the canonical public npmjs package `@zeenie-ai/opencompany` via `bun publish --access public`. npm provenance attestation was an npm-CLI feature and is gone with it.
- `publish-github-packages` — writes `@zeenie-ai:registry=https://npm.pkg.github.com/` and the `//npm.pkg.github.com/:_authToken=<GITHUB_TOKEN>` line to `~/.npmrc`, then a plain `bun publish`. bun 1.4 ignores `publishConfig.registry`, and it keeps an `.npmrc` token only for the registry that same file points at, so the route and the token must sit together; the v0.2.0 mirror publish went to npmjs with the GitHub token and failed with `missing authentication` before this was known ([errors.md #24](./errors.md#24-github-packages-publish-fails-with-missing-authentication-although-the-job-wrote-a-token-for-npmpkggithubcom)).
- `bun publish` runs the root `prepublishOnly` script in both jobs, so `python -m cli version sync` runs a second time inside the publish step (a no-op after the explicit sync step), followed by a `bun -e` check that `package.json` still has `bin` and `version`. The root `publishConfig` still carries `registry: https://registry.npmjs.org` beside `access: public` (`test_root_package_uses_public_zeenie_scope` locks both), but bun ignores that `registry` key: each job's `~/.npmrc` alone decides where it publishes.

Both registries use `@zeenie-ai/opencompany`, matching the npm and GitHub
organization owned by the project.

There is currently no audit gate, no PyPI publish, no SLSA `attest-build-provenance` step, no GitHub Release step in `release.yml` itself (the desktop workflow drafts the release from the tag message), and no test-install stage — see [Planned](#planned-not-yet-implemented).

---

## Cutting a release

The tag is the version. Everything else is derived from it, and there is no
CHANGELOG file: the annotated tag's message is the release notes (first line =
release title, the rest = body), which `desktop-release.yml` copies onto the
GitHub Release. Nothing publishes until a `v*.*.*` tag is pushed. A
`workflow_dispatch` run never cuts a version: it publishes an existing tag
again (inputs `tag` and `registries`), which is the retry path when a token
had lapsed or one registry failed.

1. **Pre-flight.** `main` green on CI. `NPM_TOKEN` unexpired: the
   `bun pm whoami` preflight fails only the npmjs job, after the predeploy
   gate, while GitHub Packages and the installers still publish, so a lapsed
   token means a partial release (rotate the secret, then dispatch the
   Release workflow with `tag` set to the release and `registries` set to
   the registry that failed). `bun publish --dry-run --access public`
   packs cleanly from the checkout.
2. **Bump.** `python -m cli version sync vX.Y.Z` writes the version into the
   root, client and desktop `package.json`, `pyproject.toml` and
   `cli/__init__.py` (never `server/pyproject.toml`, which `server/uv.lock`
   records). Commit as `chore(release): vX.Y.Z`. This commit is load-bearing:
   the desktop workflow ships the committed desktop version and refuses to
   build when it differs from the tag, while the registry jobs re-run
   `version sync` from the tag themselves. `cli/tests/test_version.py`
   fails when the checked-in files disagree.
3. **Tag.** `git tag -a vX.Y.Z -F notes.md` on that commit, the notes in the
   shape of the previous tags (`git tag -l --format='%(contents)' v0.1.0`).
4. **Push** the branch, then the tag. The tag starts `release.yml` (predeploy
   gate, then npmjs + GitHub Packages) and `desktop-release.yml` (draft with
   the notes, three installer legs, undraft) in parallel.
5. **Verify.** `gh run watch`; the registry's `latest` dist-tag; the release
   page shows the notes with the installers and the three `latest*.yml`
   updater feeds; on a clean machine the install script finishes through
   `company provision`.

Semver in 0.x: a change that breaks the install or upgrade path bumps the
minor (0.1 -> 0.2), everything else the patch. There is no un-publish:
`npm deprecate` has no bun equivalent, so a bad release is followed by a
patch release. If one desktop leg fails, the release stays a draft holding
the notes and the successful legs' assets; re-run the failed job and
`finalize` undrafts it.

---

## Documentation deployment (separate repo)

The public Mintlify docs (docs.opencompany.sh) live in the separate
[zeenie-ai/docs-OpenCompany](https://github.com/zeenie-ai/docs-OpenCompany)
repo. Deployment happens automatically via the Mintlify GitHub app on
pushes to that repo's `main`; its own workflow only validates
(`mintlify validate` + broken-links). This repo previously carried a
`docs.yml` deploy workflow targeting an in-repo `docs-OpenCompany/`
folder — both the folder and the workflow are gone.

---

## Source files

| File | Purpose |
|------|---------|
| `.github/workflows/ci.yml` | CI entry point (delegates to predeploy.yml) |
| `.github/workflows/predeploy.yml` | Reusable validation (build/lint + JS dependency audit + backend tests + CLI tests + OS matrix build/start) |
| `.github/workflows/release.yml` | Tag / manual release: predeploy gate → publish npm + GitHub Packages |
| `.github/workflows/desktop-ci.yml` | Desktop shell checks on PRs: typecheck, unit, invariants, build, Playwright smoke |
| `.github/workflows/desktop-release.yml` | Tag-triggered desktop installers (NSIS / DMG+zip / AppImage+deb) into a draft release, then undraft. Separate from `release.yml` so npm publish is never blocked |
| `.github/actions/setup/action.yml` | Composite: bun + Node + Python + uv + editable CLI install |
| `.github/dependabot.yml` | **Manual dependency updates** — scheduled PRs suppressed; automatic security-update attempts also require the repository setting to be disabled; see below |
| `.python-version` | Toolchain pin (`3.12`) — single source of truth |

---

## Code scanning

CodeQL runs through GitHub's **default setup** (Python + JS/TS), configured
in repository settings rather than by a workflow file. There is no
`codeql.yml` to read, so the only places the configuration is visible are
the Security tab and the `CodeQL` check on each push.

Triage has two outcomes, and picking the right one matters because the
wrong one leaves a permanent lie in the code:

- **Fix it** when the call site can be restructured into a shape the query
  recognises. For `py/path-injection` that shape is normalise-then-prefix-
  check *before* the path is used: join, `os.path.normpath`, then refuse
  anything that does not start with the real base directory plus a
  separator. `core/approot.py::resolve_static_asset` is the worked example
  (alerts #158-160, September 2026).
- **Dismiss it** when the flagged sink is the containment helper itself.
  A guard placed after the `resolve()` cannot clear the taint and an
  interprocedural `fullmatch` in a helper is not treated as a barrier, so
  the alert will return on every scan no matter what is written. Dismiss
  with a comment naming the containment invariant; do not add a decorative
  check that implies the alert was addressed. `nodes/filesystem/_backend.py
  ::resolve_within` and `nodes/_visuals.py` are the standing examples.

Two findings are dismissed as by-design rather than fixed:

| Query | Where | Why |
|---|---|---|
| `js/code-injection` | `server/nodejs/src/index.ts` `/execute` | That route **is** the JavaScript executor behind the `javascriptExecutor` / `typescriptExecutor` nodes, so evaluating request-supplied code is its purpose. It binds to localhost and is spawned and called only by the same-machine backend; `vm` is documented as not a security boundary. |
| `py/path-injection` | containment helpers | See above. |

Inline `// codeql[...]` / `# codeql[...]` comments do **not** suppress
anything under default setup — several sat in the sidecar for months while
the alerts stayed open. Dismiss in the Security tab instead.

## Dependency update policy (Dependabot disabled)

The policy is **no automatic pull requests**, neither version bumps nor
security updates (since 2026-09-12). Every entry in `.github/dependabot.yml`
carries `open-pull-requests-limit: 0` and `ignore: dependency-name: "*"`.
This suppresses scheduled updates but does not disable GitHub's
alert-triggered security-update attempts. Enforce the policy with the
repository setting described below. Dependencies move by hand with tests:

- JS: bump the range in the relevant `package.json` (or the top-level
  `overrides` block in the root manifest for transitive pins), `bun install`,
  `bun run audit:deps` (at the root and in `desktop/`), run the suites.
- pip: `uv lock --upgrade-package <name>` in `server/`, `uv sync`, run the
  suites (`predeploy.yml` runs `uv lock --check` and the audit below).

The Python gate exports exact pins from `uv.lock` without installing optional
extras. `pip-audit==2.10.1` queries advisories with `--strict --disable-pip
--no-deps`; it fails on vulnerabilities or packages it cannot audit. The export
is temporary and must not become a second committed dependency source. To run
the same gate locally from `server/`:

```sh
uv export --locked --all-extras --no-hashes --no-emit-project --output-file /tmp/opencompany-audit.txt
uvx --from pip-audit==2.10.1 pip-audit --strict --disable-pip --no-deps -r /tmp/opencompany-audit.txt
```

On Windows, use a file under `$env:TEMP` in both commands. Advisory lookup
requires network access; Python markers select the current platform, so the
Ubuntu CI audit also covers Linux-only dependencies.

The five entries (`bun /`, `bun /desktop`, `pip /server`, `uv /server`,
`github-actions /`) declare the intended updaters; they do not guarantee
the ecosystem chosen for alert-triggered jobs. The
[2026-10-06 Sharp update attempt](https://github.com/zeenie-ai/OpenCompany/actions/runs/37474349729)
selected `npm_and_yarn` for `/desktop`, with no ignore conditions, despite
the existing Bun entry. It failed because that updater cannot modify
`bun.lock`. Do not add a second package-manager lockfile to work around it.
`server/` retains both entries because its uv updater also needs the
manual-update policy; ignoring only pip previously allowed uv PRs.

Alerts themselves still appear in the repository's Security tab; they are
useful and cost nothing. Those for `server/` come from `uv.lock`, which
Dependabot's "uv in /server" graph job resubmits whenever a push changes
it, so they close as fixed once the lock carries the fix.
`server/requirements.txt` is no longer committed; pip users export it on
demand (`SETUP.md`). As a tracked copy of the lock's pins it received every
Python alert twice, and GitHub matched it against a snapshot that only
Dependabot's pip graph job for the repository root refreshes, so those
copies never closed (the last 18 were dismissed on 2026-10-03). GitHub keeps
that snapshot until the job runs again (it last ran on 2026-09-12, for the
v0.2.0 release commit, which changed the root `pyproject.toml`); until then,
dismiss any alert filed against `server/requirements.txt` as inaccurate.
To stop automatic security-update attempts, a repository administrator
must disable **Dependabot security updates** under **Settings > Code
security > Dependabot**. Keep **Dependabot alerts** and the dependency
graph enabled. This stops automatic patch PR attempts while retaining
vulnerability detection and the CI audit gates. The equivalent command,
run with an administrator's GitHub CLI authentication, is:

```sh
gh api --method DELETE repos/zeenie-ai/OpenCompany/automated-security-fixes
```

This endpoint does not disable alerts. A YAML ignore rule cannot replace
this setting, and updating dependencies does not change historical failed
Actions runs. Fix current alerts, push the tested lockfiles, and verify
the latest checks instead.

GitHub's dependency graph does not read `bun.lock`; it sees only the ranges
in each `package.json`, so no Dependabot alert ever covers a resolved JS
version. `bun run audit:deps` is the check: `predeploy.yml` runs it at the
root and in `desktop/` (`bun audit` reads the lockfile and needs no install).
The root audit has no exclusions. The desktop audit fails on any advisory
except the explicit exception below. An advisory
is ignored only when it has no patched release and cannot be reached from
untrusted input here:

| Advisory | Package | Reached through | Why it is ignored |
|---|---|---|---|
| [GHSA-ch52-4w7c-c8xp](https://github.com/advisories/GHSA-ch52-4w7c-c8xp) | http-cache-semantics 4.2.0 | `desktop/`: Electron Builder → app-builder-lib → @electron/get 3.1.0 → got → cacheable-request | No patched release as of 2026-10-09. Build-time artifact downloading does not share authenticated user-response caches; this chain is absent from the shipped desktop runtime dependencies. |

The unused installed shadcn CLI was removed from `client/package.json` and the
root lockfile on 2026-10-09, removing `braces` and its advisory entirely. The
existing `bun x shadcn@latest add <name>` development command still works with
the checked-in generated components; it is not part of application execution.

Electron itself uses `@electron/get` 5.1.0, which uses native fetch. Stable
Electron Builder 26.17.0 (the `v26` tag; `latest` remains 26.15.3) still requires
3.x. Do not force a 5.x transitive override: its removed GotDownloader API,
changed download options and proxy handling are incompatible with that caller.
The migration is in Electron Builder 27 prereleases; wait for a supported
stable release and validate packaging before removing the exception. See the
[downloader migration notes](https://github.com/electron/get/releases/tag/v5.0.0).

When the affected package gets a patch, drop `--ignore` and pin the fix in
`overrides`; a compatible parent upgrade that removes the package also resolves
the advisory. A passing audit with this exception does not mean the raw desktop
audit is clean.

Re-enable updates deliberately, never by just deleting the ignore rules:
raise `open-pull-requests-limit`, add `groups` with `patterns: ["*"]` +
`update-types: [minor, patch]`, and `cooldown: {semver-major-days: 30}`.

---

## Planned (not yet implemented)

The following existed in earlier drafts of this doc but are **not present in the current repo**. Listed here so the intent is preserved without misrepresenting the shipped pipeline:

- **`predeploy.yml` change-detection + aggregator** — a `plan` job (`dorny/paths-filter`) gating downstream jobs, a `pre-commit` job, pytest sharding by domain, and a `ci-passed` aggregator (`re-actors/alls-green`) as the single branch-protection target. Today every predeploy job runs unconditionally and there is no aggregator job.
- **`release.yml` hardening** — `workflow_dispatch` dry-run default, a once-per-release `build-for-publish` artifact, `actions/attest-build-provenance` SLSA attestations, and a `create-github-release` job for the registry publish (the desktop workflow's `prepare` job now drafts the GitHub Release from the tag message; `release.yml` itself still creates none). Dependency audits already run in the reusable predeploy workflow.
- **`publish-pypi.yml`** — reusable PyPI publish (OIDC trusted publishing, `uv build --no-sources`, `pypa/gh-action-pypi-publish`). No PyPI distribution is published today.
- **`test-install.yml`** — cross-platform end-user install smoke (`bun add -g`, git clone, install script) across 3 OS.
- **`rollback.yml`** — manual registry deprecate (`npm deprecate` has no bun equivalent, so this would be the one place the npm CLI reappears) + optional revert PR.
- **`check-zizmor.yml`** — workflow-security linter (SARIF to the Security tab).
- **`.pre-commit-config.yaml`** — ruff / prettier / eslint / actionlint hooks (note: the project rule is to verify with pytest + the root `typecheck` gate + eslint, not ruff). This file is **not currently present in the tree**; the entry describes intent, not a live hook.
