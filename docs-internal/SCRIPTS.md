# OpenCompany Scripts Reference

## Quick Start

```bash
bun add -g @zeenie-ai/opencompany
company start
```

bun (https://bun.sh) is the only JavaScript runtime and package manager
involved — no Node.js, no npm. `bun add -g` runs no lifecycle scripts, so the
first `company` command provisions the Python side (`bin/cli.js` runs
`scripts/install.js` when `.cli-venv` is missing); `install.sh` / `install.ps1`
install bun, Python and uv first and run `company provision` eagerly. Nothing
spells bun's global package directory (it varies by platform and
configuration): the shim knows its own package root.

Open the app URL — `http://localhost:${PYTHON_BACKEND_PORT}` (the port
is declared once in `.env.template`; see [SETUP.md](./SETUP.md) for the
default).

## CLI Commands (`company`, Python Typer app under `cli/`)

The CLI is the single orchestration surface — every `bun run <verb>` at
the root is a thin wrapper over `python -m cli <verb>`. The old
`scripts/{start,stop,build,clean,docker}.js` orchestrators were retired
(each `cli/commands/<verb>.py` docstring records what it replaced).
`machina` remains as a deprecated alias of `company` (prints a
deprecation warning; kept for upgrade compatibility).

| Command | Description |
|---------|-------------|
| `company start` | Production mode, single port: uvicorn serves API + WS + built SPA on `PYTHON_BACKEND_PORT`. Optional daemons (Temporal dev server, WhatsApp) are backend-owned, started from the lifespan when enabled |
| `company dev` | Start in dev mode (Vite HMR + uvicorn). `--force` re-bundles Vite deps (recovers "Outdated Optimize Dep"); `--daemon` binds backend to 0.0.0.0 |
| `company serve` | Single-port production runtime (uvicorn serves API + WS + built SPA; optional daemons incl. the JS executor sidecar (bun) are backend-spawned on demand) — the systemd `ExecStart` on deployed VMs |
| `company stop` | Stop all services and free configured ports |
| `company build` | Full production build (bun install → client → sidecar → uv sync → bytecode → temporal binary). Step [0/6] scaffolds `.env` from `.env.template` when missing, generating fresh random secrets (`secrets.token_hex(24)`) for `SECRET_KEY` / `JWT_SECRET_KEY` / `API_KEY_ENCRYPTION_KEY` instead of the dev placeholders; an existing `.env` is untouched |
| `company clean` | Stop services, then remove build artifacts, node_modules, `.venv`, repo-local state (preserves `.opencompany/{workflows,deploy,packages}`) |
| `company deploy up/status/destroy` | Self-deploy a login-gated VM (gcloud preflight + Terraform; see `cli/commands/deploy/`) |
| `company daemon start/stop/status/restart` | Detached backend management (PID file under user data dir) |
| `company version sync [tag]` | Write a git tag's version (default: the latest) into every version file: root / client / desktop `package.json`, `pyproject.toml`, `cli/__init__.py`. Never `server/pyproject.toml`, which `server/uv.lock` records. The release procedure is in [ci_cd.md -> Cutting a release](./ci_cd.md#cutting-a-release) |
| `company docs nodes [--check]` | Regenerate (or verify) the `docs-internal/node-logic-flows/` index |

There is no `help` verb: `company` with no arguments, `company --help`, and
`company <verb> --help` print Typer's help (`no_args_is_help=True` in `cli/cli.py`).

### Desktop shell scripts (`desktop/package.json`, run from `desktop/`)

The Electron shell is its own bun package with its own lockfile; root `bun run` does not reach it.

| Command | Description |
|---------|-------------|
| `bun run stage` | Assemble `stage/`: the backend sibling layout from `bun pm pack --dry-run` (CLI, install scripts, client sources and backend tests dropped; `server/uv.lock` force-included; the JS executor sidecar built with its package's own `bun run build` — `bun build --target=bun`, express inlined — and `dist/index.js` copied) plus the pinned runtimes for this host. `--target mac-arm64,mac-x64` stages other targets; `--skip-runtimes` stages app-root only |
| `bun run fetch-runtimes` | Just the runtimes: download uv / python-build-standalone / bun pinned in `runtimes.json`, verify against the upstream checksum manifests (bun's `SHASUMS256.txt`; its `LICENSE.md` is fetched from the bun repo since the zip carries none), cache in `vendor/`, prune stale runtime dirs (a leftover `node/`), extract into `stage/runtime/<os>-<arch>/` |
| `bun run dev` | electron-vite dev against `stage/` (`OPENCOMPANY_DESKTOP_APP_ROOT=..` and `OPENCOMPANY_DESKTOP_VENV_PYTHON=../server/.venv/...` run against the checkout without staging or provisioning) |
| `bun run build` | electron-vite build of main / preload / setup renderer into `out/` |
| `bun run typecheck` / `bun run test` / `bun run test:invariants` / `bun run test:e2e` | TS7 gate; vitest unit tests; staged-tree invariants (after `stage`); Playwright Electron smoke (after `build`) |
| `bun run gen-icons` | Render `build/icon.svg` to `build/icon.png` (electron-builder derives icns / ico / Linux PNGs) |
| `bun run sync-version` | Copy the root `package.json` version into `desktop/package.json` (CI runs it before `dist`) |
| `bun run pack` / `bun run dist` | electron-builder unpacked dir / installers into `release/` |

### Dependency checks

`build` requires bun (installs the workspace and runs every JS build step — a
missing bun is fatal) and reports Node.js as optional (when present, bun runs
vite / vitest / eslint on it via their node shebangs; when absent, bun runs the
build tools itself — nothing shipped needs Node), verifies Python 3.12+
(`_check_python`), and installs `uv` via pip if missing. `start` runs
no toolchain check, only the `_sqlalchemy_preflight` venv-health probe.

---

## package.json scripts (run with bun)

Run with `bun run <script>` from the project root (`package.json` is
the source of truth). The dev package manager is bun@1.4.0 —
`scripts/preinstall.js` rejects `npm install` in a source checkout.
The commands below are quoted verbatim from `package.json`. No script
hops through `npm run` any more: cross-package scripts use
`bun --cwd=<dir> run <script>` (the `=` form is mandatory on bun 1.4),
and the tools they reach (vite, vitest) execute on Node when it is present
(dev/CI only — their bins carry node shebangs, which bun respects).

### CLI wrappers

| Script | Command |
|--------|---------|
| `start` / `dev` / `serve` / `build` / `clean` / `stop` / `deploy` | `python -m cli <verb>` |
| `start:temporal` | `cross-env TEMPORAL_ENABLED=true python -m cli start` |
| `daemon:start` / `daemon:stop` / `daemon:status` / `daemon:restart` | `python -m cli daemon <verb>` |
| `version:sync` | `python -m cli version sync` |
| `docs:nodes` / `docs:nodes:check` | `python -m cli docs nodes [--check]` |

Two `company` verbs live in the launcher itself (`bin/cli.js`) with no
`python -m cli` counterpart: `company doctor` (environment report) and
`company provision [--force]` (the Python-side setup of a global install —
uv, the venvs, bytecode, the Temporal binary; automatic on the first
`company` command, explicit here for the installers and for repairs).

### Service scripts

| Script | Command | Description |
|--------|---------|-------------|
| `client:start` | `bun --cwd=client run start` | React frontend (Vite dev server) |
| `python:start` | `cd server && uv run python main.py` | Backend only (`main.py` reads `HOST` / `PYTHON_BACKEND_PORT` from the env) |
| `python:daemon` | `cd server && cross-env HOST=0.0.0.0 uv run python main.py` | Backend only, LAN-reachable |
| `temporal:worker` | `cd server && uv run python -m services.temporal.worker` | Standalone Temporal worker |

The Temporal dev server is backend-owned: the FastAPI lifespan starts it via `TemporalServerRuntime.ensure_started()` when `TEMPORAL_ENABLED` (see [Temporal Architecture](./TEMPORAL_ARCHITECTURE.md)). The official `temporal` CLI is downloaded by `pooch` to `<DATA_DIR>/packages/temporal/` (= `~/.opencompany/packages/temporal/` by default) during `company build`.

### Tests

| Script | Command |
|--------|---------|
| `test` | backend + frontend suites |
| `test:backend` | `cd server && uv run pytest tests/ -v` |
| `test:frontend` | `bun --cwd=client run test` (vitest) |
| `test:nodes` | node-plugin tests with handler coverage |

### Lifecycle hooks

| Script | File | Purpose |
|--------|------|---------|
| `preinstall` / `preuninstall` | `scripts/preinstall.js` | Removes the legacy `machinaos` global package / stale temp dirs before (un)install |
| `postinstall` | `scripts/postinstall.js` | Install pipeline entry (delegates to `scripts/install.js`). Reached from `bun install` in a checkout; a global `bun add -g` runs no dependency lifecycle scripts, so `bin/cli.js` provisions on the first `company` command instead |

---

## Files actually in `scripts/`

| File | Purpose |
|------|---------|
| `install.js` | End-user provisioning pipeline (`#!/usr/bin/env bun`; run by `company provision` — eagerly from `install.sh` / `install.ps1` / the cloud-init templates, or lazily by `bin/cli.js` on the first `company` command when `.cli-venv` is missing and the tree is not a source checkout; client build only when `client/dist` is missing from the tarball, `uv sync`, bytecode compile, CLI runtime venv, non-fatal Temporal binary fetch; the JS executor sidecar `dist/index.js` ships pre-built in the tarball) — mirrors `company build`; the compileall command shape is locked in sync by `cli/tests/test_release_pipeline_config.py` |
| `preinstall.js` | Gates source checkouts to bun; legacy-package/temp cleanup (also runs on uninstall) |
| `postinstall.js` | Lifecycle entry (`bun install` in a checkout) that guards recursion and invokes install.js |
| `migrate_icons.py`, `migrate_skill_icons.py` | One-off icon-migration utilities (historical) |

(`serve-client.js` was retired July 2026: `company start` is single-port —
the backend serves the built SPA itself via `SERVE_STATIC_CLIENT`.)

Docker: `docker compose up -d --build` builds a one-container
self-hosting image from source (`docker/Dockerfile`,
`docker-compose.yml`); see [docker.md](./docker.md). No script or CLI
verb wraps it. The old multi-container topology is preserved in
[deployment_legacy.md](./deployment_legacy.md). Cloud VM deployment is
`company deploy` (Terraform → GCP VM → systemd).

---

## Environment Variables

Key variables in `.env` (see `.env.template` for the full list):

### Ports
| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_CLIENT_PORT` | `.env.template` | App port (Vite dev server; proxies backend prefixes). Equal to `PYTHON_BACKEND_PORT` in production |
| `PYTHON_BACKEND_PORT` | `.env.template` | Backend port (`.env.dev` moves it one up in dev, behind the Vite proxy) |
| `WHATSAPP_RPC_PORT` | `.env.template` | WhatsApp API port (plugin-owned) |
| `NODEJS_EXECUTOR_PORT` | `.env.template` | JS code-executor sidecar, runs on bun (plugin-owned; the env var name is unchanged) |
| `TEMPORAL_FRONTEND_GRPC_PORT` / `TEMPORAL_UI_PORT` | `.env.template` | Temporal gRPC / Temporal Web UI |

All values live in the serial block declared at the top of `.env.template`; no
code or doc should carry the numerals.

### Features
| Variable | Default | Description |
|----------|---------|-------------|
| `TEMPORAL_ENABLED` | (see `.env.template`) | Temporal execution engine |
| `REDIS_ENABLED` | false | Redis cache (SQLite fallback when false) |

---

## Required Dependencies

| Dependency | Version | Install |
|------------|---------|---------|
| bun | 1.4+ — the only JavaScript runtime and package manager anything shipped needs: it runs the `company` shim, the JS executor sidecar and the plugin CLIs (incl. the Cloudflare `cf` CLI, whose `engines.node >= 22` bun ignores) and installs the package (`bun add -g`) | official installer (https://bun.sh; `install.sh` / `install.ps1` run it); root `packageManager` pin read by `oven-sh/setup-bun` in CI; the desktop app bundles it |
| Python | 3.12+ (CLI); server venv accepts 3.11–3.12 | https://python.org/ |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js | optional, dev/CI only — when present, bun runs vite / vitest / eslint on it via their node shebangs (CI installs 22 for that reason); `company build` reports it as optional | https://nodejs.org/ |

---

## Quick Reference

```bash
# Development
company dev            # app at PYTHON_BACKEND_PORT (Vite HMR; backend one port up via .env.dev, behind the proxy)
company dev --force    # ...forcing a Vite dependency re-bundle
company start          # Production mode (single port: PYTHON_BACKEND_PORT)
company stop           # Stop all services

# Build / clean
company build          # Full production build
company clean          # Clean everything (keeps workflows/deploy/packages state)

# Deploy
company deploy up --provider gcp --owner-email you@example.com
company deploy status
company deploy destroy
```
