# OpenCompany Scripts Reference

## Quick Start

```bash
npm install -g @zeenie-ai/opencompany
company start
```

Open http://localhost:3000

## CLI Commands (`company`, Python Typer app under `cli/`)

The CLI is the single orchestration surface — every `npm run <verb>` at
the root is a thin wrapper over `python -m cli <verb>`. The old
`scripts/{start,stop,build,clean,docker}.js` orchestrators were retired
(each `cli/commands/<verb>.py` docstring records what it replaced).
`machina` remains as a deprecated alias of `company` (prints a
deprecation warning; kept for upgrade compatibility).

| Command | Description |
|---------|-------------|
| `company start` | Start all services in production mode (static client + uvicorn + temporal) |
| `company dev` | Start in dev mode (Vite HMR + uvicorn + temporal). `--force` re-bundles Vite deps (recovers "Outdated Optimize Dep"); `--daemon` binds backend to 0.0.0.0 |
| `company serve` | Single-port production runtime (uvicorn serves API + WS + built SPA + Node sidecar) — the systemd `ExecStart` on deployed VMs |
| `company stop` | Stop all services and free configured ports |
| `company build` | Full production build (pnpm install → client → sidecar → uv sync → bytecode → temporal binary). Step [0/6] scaffolds `.env` from `.env.template` when missing, generating fresh random secrets (`secrets.token_hex(24)`) for `SECRET_KEY` / `JWT_SECRET_KEY` / `API_KEY_ENCRYPTION_KEY` instead of the dev placeholders; an existing `.env` is untouched |
| `company clean` | Stop services, then remove build artifacts, node_modules, `.venv`, repo-local state (preserves `.opencompany/{workflows,deploy,packages}`) |
| `company deploy up/status/destroy` | Self-deploy a login-gated VM (gcloud preflight + Terraform; see `cli/commands/deploy/`) |
| `company daemon start/stop/status/restart` | Detached backend management (PID file under user data dir) |
| `company version sync` | Propagate the root package.json version |
| `company docs nodes [--check]` | Regenerate (or verify) the `docs-internal/node-logic-flows/` index |
| `company help` | Show help |

### Dependency checks

`start` and `build` verify Node.js 22+, Python 3.12+, and uv before
running.

---

## npm Scripts

Run with `npm run <script>` from the project root (`package.json` is
the source of truth).

### CLI wrappers

| Script | Command |
|--------|---------|
| `start` / `dev` / `serve` / `build` / `clean` / `stop` / `deploy` | `python -m cli <verb>` |
| `start:temporal` | `cross-env TEMPORAL_ENABLED=true python -m cli start` |
| `daemon:start` / `daemon:stop` / `daemon:status` / `daemon:restart` | `python -m cli daemon <verb>` |
| `version:sync` | `python -m cli version sync` |
| `docs:nodes` / `docs:nodes:check` | `python -m cli docs nodes [--check]` |

### Service scripts

| Script | Command | Description |
|--------|---------|-------------|
| `client:start` | `cd client && npm run start` | React frontend (Vite dev server) |
| `python:start` | `cd server && uv run uvicorn main:app --host 127.0.0.1 --port 3010 ...` | Backend only |
| `python:daemon` | same, bound to `0.0.0.0` | Backend only, LAN-reachable |
| `temporal:worker` | `cd server && uv run python -m services.temporal.worker` | Standalone Temporal worker |

Temporal server lifecycle is managed by `company start` / `company dev` / `company stop` directly (see [Temporal Architecture](./TEMPORAL_ARCHITECTURE.md)). The official `temporal` CLI is downloaded by `pooch` to `<DATA_DIR>/packages/temporal/` (= `~/.opencompany/packages/temporal/` by default) during `company build` and spawned as a supervised subprocess.

### Tests

| Script | Command |
|--------|---------|
| `test` | backend + frontend suites |
| `test:backend` | `cd server && uv run pytest tests/ -v` |
| `test:frontend` | `cd client && npm run test` (vitest) |
| `test:nodes` | node-plugin tests with handler coverage |

### Lifecycle hooks

| Script | File | Purpose |
|--------|------|---------|
| `preinstall` / `preuninstall` | `scripts/preinstall.js` | Removes the legacy `machinaos` global package / stale temp dirs before (un)install |
| `postinstall` | `scripts/postinstall.js` | End-user install pipeline for the npm tarball (delegates to `scripts/install.js`) |

---

## Files actually in `scripts/`

| File | Purpose |
|------|---------|
| `install.js` | npm-tarball install pipeline (pnpm/uv install, client + sidecar build, bytecode compile, temporal binary fetch) — mirrors `company build`; the compileall command shape is locked in sync by `cli/tests/test_release_pipeline_config.py` |
| `preinstall.js` | Legacy-package/temp cleanup (also runs on uninstall) |
| `postinstall.js` | npm lifecycle entry that guards recursion and invokes install.js |
| `serve-client.js` | Static client server used by `company start` (production mode, no Vite) |
| `migrate_icons.py`, `migrate_skill_icons.py` | One-off icon-migration utilities (historical) |

There is no Docker tooling: Docker Compose support was removed
(historical topology preserved in
[deployment_legacy.md](./deployment_legacy.md)); deployment is
`company deploy` (Terraform → GCP VM → systemd).

---

## Environment Variables

Key variables in `.env` (see `.env.template` for the full list):

### Ports
| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_CLIENT_PORT` | 3000 | Frontend port |
| `PYTHON_BACKEND_PORT` | 3010 | Backend port |
| `WHATSAPP_RPC_PORT` | 9400 | WhatsApp API port |
| `NODEJS_EXECUTOR_PORT` | 3020 | Node.js code-executor sidecar |
| — | 7233 / 8080 | Temporal gRPC / Temporal Web UI |

### Features
| Variable | Default | Description |
|----------|---------|-------------|
| `TEMPORAL_ENABLED` | (see `.env.template`) | Temporal execution engine |
| `REDIS_ENABLED` | false | Redis cache (SQLite fallback when false) |

---

## Required Dependencies

| Dependency | Version | Install |
|------------|---------|---------|
| Node.js | 22+ | https://nodejs.org/ |
| Python | 3.12+ (CLI); server venv accepts 3.11–3.12 | https://python.org/ |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| pnpm | 9.x | `corepack enable` (root `packageManager` pin) |

---

## Quick Reference

```bash
# Development
company dev            # Vite HMR + backend + temporal
company dev --force    # ...forcing a Vite dependency re-bundle
company start          # Production mode (static client)
company stop           # Stop all services

# Build / clean
company build          # Full production build
company clean          # Clean everything (keeps workflows/deploy/packages state)

# Deploy
company deploy up --provider gcp --owner-email you@example.com
company deploy status
company deploy destroy
```
