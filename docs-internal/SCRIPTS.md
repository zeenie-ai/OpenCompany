# MachinaOS Scripts Reference

## Quick Start

```bash
npm install -g machina
machina start
```

Open http://localhost:3000

## CLI Commands

### Available Commands

| Command | Description |
|---------|-------------|
| `machina start` | Start the development server (checks dependencies first) |
| `machina stop` | Stop all running services |
| `machina build` | Build the project for production (checks dependencies first) |
| `machina clean` | Clean build artifacts and node_modules |
| `machina docker:up` | Start with Docker Compose (detached) |
| `machina docker:down` | Stop Docker Compose services |
| `machina docker:build` | Build Docker images |
| `machina docker:logs` | View Docker logs (follows) |
| `machina help` | Show help message |

### Dependency Checks

The `start` and `build` commands verify these dependencies before running:
- Node.js 22+
- Python 3.12+
- uv (Python package manager)

---

## npm Scripts

Run with `npm run <script>` from the project root.

### Core Scripts

| Script | Command | Description |
|--------|---------|-------------|
| `start` | `node scripts/start.js` | Start all services concurrently |
| `start:temporal` | `cross-env TEMPORAL_ENABLED=true node scripts/start.js` | Start with Temporal enabled |
| `stop` | `node scripts/stop.js` | Stop all running services |
| `build` | `node scripts/build.js` | Build entire project |
| `clean` | `node scripts/clean.js` | Remove build artifacts |

### Service Scripts

| Script | Command | Description |
|--------|---------|-------------|
| `client:start` | `cd client && npm run start` | Start React frontend (Vite) |
| `python:start` | `cd server && uv run uvicorn main:app ...` | Start Python backend |
| `temporal:worker` | `cd server && uv run python -m services.temporal.worker` | Start standalone Temporal worker |

Temporal server lifecycle is managed by `machina start` / `machina dev` / `machina stop` directly (see [Temporal Architecture](./TEMPORAL_ARCHITECTURE.md)). The official `temporal` CLI is downloaded by `pooch` to `<DATA_DIR>/packages/temporal/` (= `~/.machina/packages/temporal/` by default) during `machina build` and spawned as a supervised subprocess.

### Docker Scripts (Development)

| Script | Command | Description |
|--------|---------|-------------|
| `docker:up` | `node scripts/docker.js up` | Start dev stack (detached) |
| `docker:down` | `node scripts/docker.js down` | Stop dev stack |
| `docker:build` | `node scripts/docker.js build` | Build dev images |
| `docker:logs` | `node scripts/docker.js logs` | View logs (follows) |
| `docker:restart` | `node scripts/docker.js restart` | Restart dev stack |

### Docker Scripts (Production)

| Script | Command | Description |
|--------|---------|-------------|
| `docker:prod:up` | `docker-compose -f docker-compose.prod.yml up -d` | Start production stack |
| `docker:prod:down` | `docker-compose -f docker-compose.prod.yml down` | Stop production stack |
| `docker:prod:build` | `docker-compose -f docker-compose.prod.yml build` | Build production images |
| `docker:prod:logs` | `docker-compose -f docker-compose.prod.yml logs -f` | View production logs |

### Deployment Scripts

| Script | Command | Description |
|--------|---------|-------------|
| `deploy` | `bash deploy.sh` | Deploy to production server |
| `deploy:gcp` | `bash deploy.sh` | Deploy to GCP |

---

## Script Details

### start.js

**Location:** `scripts/start.js`

Cross-platform start script that runs all services concurrently.

**What it does** (mirrored by the canonical `machina start` / `machina dev` CLI commands):
1. Validates build artifacts exist
2. Creates `.env` from `.env.template` if missing
3. Frees configured app ports (client, backend, WhatsApp, Node.js executor, Temporal gRPC, Temporal UI)
4. Spawns each service in its own process group (Windows: `CREATE_NEW_PROCESS_GROUP` for graceful `CTRL_BREAK_EVENT` shutdown):
   - Static client server (`serve-client.js`)
   - Python backend (uvicorn) -- supervises the edgymeow Go binary lazily via [server/nodes/whatsapp/_runtime.py](../server/nodes/whatsapp/_runtime.py)
   - Temporal dev server -- the supervised-runtime shim ([server/services/temporal/_supervised_runtime.py](../server/services/temporal/_supervised_runtime.py)) spawning `temporal server start-dev`

**Temporal handling:** The supervisor's TCP readiness probe on 7233 short-circuits if Temporal is already running, so launching `machina start` against a pre-existing Temporal works without conflict.

**Ports (configurable in .env):**
- `VITE_CLIENT_PORT` - Frontend (default: 3000)
- `PYTHON_BACKEND_PORT` - Backend (default: 3010)
- `WHATSAPP_RPC_PORT` - WhatsApp (default: 9400)

**Platform support:** Windows, macOS, Linux, WSL, Git Bash

---

### build.js

**Location:** `scripts/build.js`

Production build script that compiles all components.

**Build steps:**
1. `[0/6]` Create `.env` from template if missing
2. `[1/6]` Install root dependencies (`npm ci` or `npm install`)
3. `[2/6]` Install client dependencies
4. `[3/6]` Build client (`vite build`)
5. `[4/6]` Install Python dependencies (`uv venv && uv sync`)
6. `[5/6]` Install WhatsApp dependencies
7. `[6/6]` Build WhatsApp Go binary

**Dependencies checked:**
- Node.js, npm, Python, uv, Go

---

### stop.js

**Location:** `scripts/stop.js`

Cross-platform stop script that kills all MachinaOS processes.

**What it does:**
1. Reads port configuration from `.env`
2. Finds processes on each port
3. Kills processes including child processes
4. Verifies processes are stopped
5. Retries stubborn processes with force kill
6. Kills Temporal processes via `killByPattern('temporal')`

**Platform-specific commands:**
| Platform | Find processes | Kill process |
|----------|----------------|--------------|
| Unix/Mac | `lsof -ti:PORT` | `kill -15`, then `kill -9` |
| Linux | `ss -tlnp` (fallback) | `kill -15`, then `kill -9` |
| Windows | `netstat -ano \| findstr` | `taskkill /PID` |

---

### clean.js

**Location:** `scripts/clean.js`

Removes build artifacts and dependencies.

**Directories removed:**
- `node_modules/` - Root dependencies
- `client/node_modules/` - Frontend dependencies
- `client/dist/` - Built frontend
- `client/.vite/` - Vite cache
- `server/.venv/` - Python virtual environment
- `.machina/` - Repo-local DATA_DIR opt-out (only present when `DATA_DIR=.machina`; the default `~/.machina/` lives in your home dir and is never touched)

---

### docker.js

**Location:** `scripts/docker.js`

Docker Compose wrapper with automatic Redis profile detection.

**Usage:**
```bash
node scripts/docker.js <command> [args...]
```

**Commands:** `up`, `down`, `build`, `logs`, `restart`

**Features:**
- Creates `.env` from template if missing
- Reads `REDIS_ENABLED` from `.env`
- Adds `--profile redis` flag when Redis is enabled
- `up` runs detached (`-d`) by default
- `logs` follows (`-f`) by default

**Example output:**
```
[Docker] Redis profile enabled (REDIS_ENABLED=true in .env)
[Docker] Running: docker-compose --profile redis up -d
```

---

## Environment Variables

Key variables in `.env` (see `.env.template` for full list):

### Ports
| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_CLIENT_PORT` | 3000 | Frontend port |
| `PYTHON_BACKEND_PORT` | 3010 | Backend port |
| `WHATSAPP_RPC_PORT` | 9400 | WhatsApp API port |

### Features
| Variable | Default | Description |
|----------|---------|-------------|
| `TEMPORAL_ENABLED` | false | Enable Temporal worker |
| `REDIS_ENABLED` | false | Enable Redis cache (uses SQLite if false) |

### Redis (when enabled)
| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | redis://redis:6379 | Redis connection URL |
| `REDIS_PORT` | 6379 | Redis port |

---

## Required Dependencies

### For `start` and `build` commands

| Dependency | Version | Install |
|------------|---------|---------|
| Node.js | 18+ | https://nodejs.org/ |
| Python | 3.11+ | https://python.org/ |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Go | latest | https://go.dev/dl/ (for WhatsApp binary) |

### For Docker commands

| Dependency | Install |
|------------|---------|
| Docker | https://docs.docker.com/get-docker/ |
| Docker Compose | Included with Docker Desktop |

---

## Quick Reference

```bash
# Development
machina start          # Start all services
machina stop           # Stop all services

# Build
machina build          # Build for production
machina clean          # Clean everything

# Docker (development)
machina docker:up      # Start containers
machina docker:down    # Stop containers
machina docker:logs    # View logs
machina docker:build   # Rebuild images

# Docker (production)
npm run docker:prod:up   # Start production
npm run docker:prod:down # Stop production
npm run deploy           # Deploy to server
```
