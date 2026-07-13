# OpenCompany - Development Setup

## Project Structure

```
OpenCompany/
├── client/                 # React frontend (port 3000)
│   ├── src/
│   └── package.json
├── server/                 # Python FastAPI backend (port 3010)
│   ├── services/           # Business logic (workflow, AI, etc.)
│   ├── routers/            # API endpoints
│   ├── core/               # DI container, database, cache
│   ├── models/             # SQLModel definitions
│   ├── whatsapp-rpc/       # Go WhatsApp service (port 9400)
│   └── requirements.txt
├── scripts/                # Build and utility scripts
└── package.json            # Workspace root with npm scripts
```

## Quick Start

```bash
npm install -g @zeenie/opencompany
company start
```

Open http://localhost:3000

### Local Development (from source)

**Prerequisites:** Node.js 22+, Python 3.12+, uv, 

```bash
git clone https://github.com/zeenie-ai/OpenCompany.git OpenCompany
cd OpenCompany
npm run build
npm run start
```

### Docker

```bash
git clone https://github.com/zeenie-ai/OpenCompany.git OpenCompany
cd OpenCompany
npm run docker:up
```

Services will be available at:
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:3010
- **WhatsApp Service**: http://localhost:9400

## Services Overview

### Frontend (React - Port 3000)
- React 19 with TypeScript
- React Flow for workflow canvas
- Zustand for state management
- WebSocket connection to backend

### Backend (Python FastAPI - Port 3010)
- FastAPI with async support
- SQLAlchemy + SQLite database
- LangChain for AI integrations
- WebSocket for real-time updates

**Key Endpoints:**
- `GET /health` - Health check
- `WS /ws/status` - Real-time status WebSocket
- `ANY /webhook/{path}` - Dynamic webhook endpoints
- `POST /api/ai/*` - AI model execution
- `POST /api/android/*` - Android device operations

### WhatsApp Service (Go - Default Port 9400)
- Go service using whatsmeow library
- QR code authentication (base64 PNG in memory, no file I/O)
- Message send/receive via JSON-RPC
- Port configurable via `--port` flag, `PORT` or `WHATSAPP_RPC_PORT` env vars

### Temporal Server (Distributed Execution)
- Provides durable workflow execution with per-node retry and horizontal scaling
- Official `temporal` CLI downloaded by `pooch` from `https://temporal.download/cli/archive/latest` on `company build` (or first `company start`)
- Supervised by OpenCompany as `temporal server start-dev` — single process, SQLite at `~/.opencompany/temporal.db`
- Ports: gRPC 7233, Web UI 8080
- Embedded worker runs inside Python backend (`TemporalWorkerManager` in `main.py`)
- Workflow auto-resumption disabled at startup (history preserved); see `TEMPORAL_TERMINATE_RUNNING_ON_STARTUP`
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
cp .env.example .env
```

### Key Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_CLIENT_PORT` | 3000 | Frontend port |
| `PYTHON_BACKEND_PORT` | 3010 | Backend API port |
| `AUTH_MODE` | single | Authentication mode (single/multi) |
| `REDIS_ENABLED` | false | Enable Redis cache (production) |
| `DEBUG` | true | Debug mode |

### API Keys (Optional)
Add these to `.env` or configure via the Credentials UI:
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_AI_API_KEY`
- `GOOGLE_MAPS_API_KEY`

### Authentication Toggle
| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_AUTH_ENABLED` | true | Set to `false` to bypass login entirely |

When `VITE_AUTH_ENABLED=false`:
- Login page is skipped entirely
- User is set as anonymous with owner privileges
- Encryption service auto-initializes with `API_KEY_ENCRYPTION_KEY` as the password
- API keys can be saved/retrieved without user authentication
- Useful for local development and testing

## Docker Commands

| Command | Description |
|---------|-------------|
| `npm run docker:up` | Start containers |
| `npm run docker:down` | Stop containers |
| `npm run docker:logs` | View logs |
| `npm run docker:build` | Rebuild images |

**Redis (optional):** Set `REDIS_ENABLED=true` in `.env`

## Local Commands

| Command | Description |
|---------|-------------|
| `npm run start` | Start all services |
| `npm run stop` | Stop all services |
| `npm run build` | Install dependencies |
| `npm run dev` | Start development server |

## Troubleshooting

### Port already in use
Change the port in `.env`:
```bash
VITE_CLIENT_PORT=3001
PYTHON_BACKEND_PORT=3011
```

### Python dependencies fail
The server is uv-managed — prefer `uv sync` from `server/` (creates `server/.venv` against `uv.lock`). The pip fallback works too:
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
SQLite database is created automatically at `server/workflow.db`.
Delete the database file to reset all data.

### Docker containers won't start
Check logs and rebuild:
```bash
docker-compose logs -f
docker-compose down
docker-compose up --build -d
```

## Development Workflow

1. **Make changes** in client/ or server/
2. **Hot reload** automatically updates running services
3. **WebSocket** provides real-time status updates
4. **Database** persists data between restarts

## Architecture Notes

- **WebSocket-First**: 25 message handlers replace most REST APIs
- **n8n-inspired**: Node definitions follow n8n INodeProperties pattern
- **Cache Fallback**: Redis (production) → SQLite (dev) → Memory
- **Event-Driven**: Trigger nodes use asyncio.Future for event waiting
