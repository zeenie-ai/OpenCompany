# OpenCompany - Development Setup

## Project Structure

```
OpenCompany/
├── client/                 # React frontend (Vite dev server on VITE_CLIENT_PORT, proxying the backend; production build served by uvicorn on PYTHON_BACKEND_PORT)
│   ├── src/
│   └── package.json
├── server/                 # Python FastAPI backend (port PYTHON_BACKEND_PORT)
│   ├── services/           # Business logic (workflow, AI, etc.)
│   ├── routers/            # API endpoints
│   ├── core/               # DI container, database, cache
│   ├── models/             # SQLModel definitions
│   ├── nodes/              # Plugin folders (one per node; the WhatsApp bridge is the
│   │                       #   `edgymeow` package `bun add`ed under DATA_DIR/packages/, not in-tree)
│   └── requirements.txt
├── scripts/                # Install lifecycle helpers, run by bun (install/preinstall/postinstall)
└── package.json            # Workspace root; bun@1.4.0 scripts wrapping `python -m cli`
```

## Quick Start

```bash
bun add -g @zeenie-ai/opencompany
company start
```

bun (https://bun.sh, 1.4+) and Python 3.12 are the only prerequisites — no
Node.js, no npm. `bun add -g` runs no lifecycle scripts, so the first `company`
command provisions the Python side (uv, the venvs, bytecode; a minute or two).
`curl -fsSL https://opencompany.sh/install.sh | bash` (or `install.ps1` on
Windows) installs bun, Python and uv first and provisions eagerly.

**Known issue on 0.2.0 and 0.2.1**: a registry install provisions but then
`company start` (and `company serve`) stops with "Project not built", and the
package ships without the JS executor sidecar bundle; `company build` in the
installed package works around both. Details and status in
[errors.md #25 and #26](./errors.md). The desktop app is not affected and is
the recommended install.

Open `http://localhost:$PYTHON_BACKEND_PORT` — `company start` is single-port (API +
WebSocket + built SPA on the backend port).

Notes for Linux servers:

- **Memory**: plan on at least 1 GB of RAM. The backend plus the Temporal dev
  server it spawns idle at about 350 MB; a 512 MB VM is OOM-killed in a loop
  (see [errors.md #17](./errors.md#17-backend-oom-killed-in-a-loop-on-small-vms-512-mb)).
- **uv** is installed by the provisioning step (`scripts/install.js`, run by
  `company provision` on the first `company` command or eagerly by
  `install.sh`): via pip where allowed,
  otherwise via uv's official installer (PEP 668 distros such as Ubuntu 24.04, see
  [errors.md #15](./errors.md#15-npm-install--g-fails-with-externally-managed-environment-ubuntu-2404)).
- **No `sudo`**: `bun add -g` installs as the login user (shim
  `~/.bun/bin/company`; the package itself lands wherever bun keeps its
  global packages, which nothing needs to know because `company provision`
  runs from the shim's own root), so the venvs, the uv-managed Python and
  `~/.opencompany` all belong to the login user with no prefix to configure.
  The root-owned-venv failure of the npm era (see
  [errors.md #16](./errors.md#16-company-start-says-python-not-found-after-a-sudo-npm-install--g))
  cannot recur; `install.sh` evicts a legacy `npm install -g` copy when npm
  happens to be present.

### Local Development (from source)

**Prerequisites:** bun 1.4+ (https://bun.sh — the package manager and the only JavaScript runtime anything shipped needs), Python 3.12 (CLI needs >=3.12, server venv pins >=3.11,<3.13), uv. Node.js is optional: when present, bun runs vite / vitest / eslint on it via their node shebangs (CI installs 22 for that reason); `company build` treats it as optional.

```bash
git clone https://github.com/zeenie-ai/OpenCompany.git OpenCompany
cd OpenCompany
bun install
bun run build
bun run start
```

The default server install stays lightweight and does not install the
`sentence-transformers` stack. OpenAI and Ollama embeddings work through their
native SDKs in the default install. To enable local Hugging Face embeddings for
the embedding node and long-term memory, install the optional extra:

```bash
cd server && uv sync --extra local-embeddings
```

Services (production `company start`; every port is declared once in `.env.template`):
- **App (API + WS + SPA)**: `http://localhost:${PYTHON_BACKEND_PORT}`
- **WhatsApp Service**: `WHATSAPP_RPC_PORT` (backend-spawned on demand)
- **Temporal dev server**: gRPC `TEMPORAL_FRONTEND_GRPC_PORT`, Web UI `TEMPORAL_UI_PORT` (backend-spawned when `TEMPORAL_ENABLED`)

`company dev` serves the same app URL from the Vite HMR server, which proxies /api /ws /webhook /health /mcp to the backend; `.env.dev` moves the backend one port up (`PYTHON_BACKEND_PORT` override) so the app URL stays put.

### Desktop app (Electron shell)

End users can skip Python, bun and Node entirely: the installers attached to each
GitHub Release (`OpenCompany-windows-x64.exe`, `OpenCompany-macos-arm64.dmg` /
`-macos-x64.dmg`, `OpenCompany-linux-x86_64.AppImage` / `-linux-amd64.deb`;
the names carry no version, so
`https://github.com/zeenie-ai/OpenCompany/releases/latest/download/<file>`
always fetches the newest one, and the README links exactly those) bundle
`uv`, a standalone CPython 3.12 and bun 1.4 (the only JS
runtime the backend needs — no Node, no npm), provision
the backend's virtual environment into the app's data directory on first
launch (one-time download of the Python wheels, plus the Temporal binary the
CLI also downloads), and host the same backend-served UI in a native window.
Data lives in `~/.opencompany` exactly as for a CLI install, so both can be
used on one machine; the shell even attaches to a `company serve` that is
already running on the app port instead of starting a second backend.

Developing the shell (from a built checkout — `client/dist` and the sidecar
bundle must exist):

```bash
cd desktop
bun install
bun run stage        # app-root + pinned runtimes for this machine
bun run dev          # or: OPENCOMPANY_DESKTOP_APP_ROOT=.. OPENCOMPANY_DESKTOP_VENV_PYTHON=../server/.venv/bin/python bun run dev
```

Full reference: [desktop_app.md](./desktop_app.md); the backend's side of
the arrangement is [desktop_host_contract.md](./desktop_host_contract.md).

## Services Overview

### Frontend (React — always at the app port; Vite dev server proxying the backend, or the production build served by uvicorn)
- React 19 with TypeScript
- React Flow for workflow canvas
- Zustand for state management
- WebSocket connection to backend

### Backend (Python FastAPI - `PYTHON_BACKEND_PORT`)
- FastAPI with async support
- SQLAlchemy + SQLite database
- Native Anthropic and Google Gen AI providers plus the OpenAI SDK for OpenAI-compatible chat through `ChatUnifier`; Ollama's SDK is used for local model discovery and embeddings
- WebSocket for real-time updates

**Key Endpoints:**
- `GET /health` - Health check
- `WS /ws/status` - Real-time status WebSocket
- `ANY /webhook/{path}` - Dynamic webhook endpoints
- `/api/android/*` - Android device operations (plugin router in `nodes/android/_router.py`)
- AI model execution has no REST surface — it is WebSocket-only (`execute_ai_node`, `get_ai_models`, `execute_node`)

### WhatsApp Service (Go — optional, on-demand; port `WHATSAPP_RPC_PORT`)
- Go service using whatsmeow library
- QR code authentication (base64 PNG in memory, no file I/O)
- Message send/receive via JSON-RPC
- Port configurable via `--port` flag, `PORT` or `WHATSAPP_RPC_PORT` env vars

### Temporal Server (Distributed Execution)
- Provides durable workflow execution with per-node retry and horizontal scaling
- Official `temporal` CLI downloaded by `pooch` from `https://temporal.download/cli/archive/latest` on `company build` (or first backend boot with Temporal enabled)
- Backend-owned: the FastAPI lifespan starts `temporal server start-dev` via `TemporalServerRuntime.ensure_started()` when `TEMPORAL_ENABLED` and the configured address is loopback (external clusters are detected via TCP probe and left alone) — SQLite at `~/.opencompany/temporal.db`
- Ports: gRPC `TEMPORAL_FRONTEND_GRPC_PORT`, Web UI `TEMPORAL_UI_PORT` (values in `.env.template`)
- Embedded worker runs inside the Python backend (`TemporalWorkerManager` + `TemporalWorkerPool`, built by `services/temporal/lifecycle.py`; `main.py` only schedules `run_temporal_lifecycle`)
- Running and paused deployments survive restarts: `TEMPORAL_TERMINATE_RUNNING_ON_STARTUP=false` is the default, and the boot-time reconcile pass re-arms them. Setting it `true` is a debug-only sweep (history preserved; active control rows still veto it)
- See [Temporal Architecture](./TEMPORAL_ARCHITECTURE.md) and [CLI Services Guide](./cli_services_integration.md)

### Database (SQLite)
- **workflows** - Workflow definitions
- **node_parameters** - Node parameter storage
- **conversation_messages** - AI conversation history
- **cache_entries** - Execution cache (when Redis disabled)
- **users** - Authentication (single/multi-user modes)

## Environment Configuration

Copy the example environment file:

```bash
cp .env.template .env
```

Alternatively, `company build` scaffolds `.env` from `.env.template` automatically when it is missing (step `[0/6]`) and generates fresh random secrets for `SECRET_KEY` / `JWT_SECRET_KEY` / `API_KEY_ENCRYPTION_KEY` instead of the dev placeholders. An existing `.env` is never modified. If you copy the template by hand and later enable auth (or set `DEPLOYMENT_MODE` to anything other than `local`), the server logs a non-fatal error banner at startup until the placeholder secrets are replaced.

### Key Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_CLIENT_PORT` | see `.env.template` | App port (Vite dev server; proxies backend prefixes). Same value as `PYTHON_BACKEND_PORT` in production |
| `PYTHON_BACKEND_PORT` | see `.env.template` | Backend port (`.env.dev` moves it one up in dev, behind the Vite proxy) |
| `AUTH_MODE` | single | Authentication mode (single/multi) |
| `REDIS_ENABLED` | false | Enable Redis cache (production) |
| `DEBUG` | false | Debug mode |

### API Keys (Optional)
Add these to `.env` or configure via the Credentials UI:
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_AI_API_KEY`
- `GOOGLE_MAPS_API_KEY`

### Authentication Toggle
| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_AUTH_ENABLED` | false | Auth is OFF by default (`.env.template`); set to `true` to require login. `company deploy` cloud installs enable it automatically |

When `VITE_AUTH_ENABLED=false` (the default):
- Login page is skipped entirely
- User is set as anonymous with owner privileges
- Encryption service auto-initializes with `API_KEY_ENCRYPTION_KEY` as the password
- API keys can be saved/retrieved without user authentication
- Useful for local development and testing

**Redis (optional):** Set `REDIS_ENABLED=true` in `.env`.

**Docker:** `docker compose up -d --build` runs a self-hosting image
built from source; see [docker.md](./docker.md).

## Local Commands

| Command | Description |
|---------|-------------|
| `bun run start` | Start the app (backend-owned daemons start on demand) |
| `bun run stop` | Stop all services |
| `bun run build` | Full production build (`company build`: bun install, client, sidecar, uv sync, bytecode, Temporal binary) |
| `bun run dev` | Start development server |

## Troubleshooting

### Port already in use
Change the port in `.env` (the values below are illustrative; the defaults live in `.env.template`):
```bash
VITE_CLIENT_PORT=6679
PYTHON_BACKEND_PORT=6678
```

### Python dependencies fail
The server is uv-managed — prefer `uv sync` from `server/` (creates `server/.venv` against the committed `uv.lock`; after editing `pyproject.toml` run `uv lock` and commit the result, CI checks it with `uv lock --check`). The pip fallback works too:
```bash
cd server
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
```
`server/requirements.txt` is an exact-pin export of the lock — regenerate after dependency changes with:
```bash
uv export --frozen --no-emit-project --no-hashes --no-dev -o requirements.txt
```

### Database issues
SQLite databases are created automatically under `DATA_DIR`
(`~/.opencompany/` by default; `<repo>/.opencompany/` in dev mode).
Delete `workflow.db` there to reset all data.

## Development Workflow

1. **Make changes** in client/ or server/
2. **Hot reload** automatically updates running services
3. **WebSocket** provides real-time status updates
4. **Database** persists data between restarts

## Architecture Notes

- **WebSocket-First**: WS message handlers replace most REST APIs (live set = `MESSAGE_HANDLERS` in `server/routers/websocket.py` + plugin-registered handlers)
- **n8n-inspired**: Node definitions follow n8n INodeProperties pattern
- **Cache Fallback**: Redis (production) → SQLite (dev) → Memory
- **Event-Driven**: Trigger nodes use asyncio.Future for event waiting
