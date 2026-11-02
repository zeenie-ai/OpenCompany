# Legacy multi-container Docker topology

> **Status: Historical.** This preserves the retired multi-container section
> from `deployment_legacy.md` verbatim. It is not current deployment guidance.
> Use [current Docker deployment](../docker.md) and
> [self-deploy CLI summary](../deployment_legacy.md) instead.
> Relative source references inside the historical body retain their original context.

## Docker Deployment (LEGACY REFERENCE — removed from repo)

> **Legacy reference only.** The 4-container stack's files (its `docker-compose.yml` and
> `docker-compose.prod.yml`, `docker/Dockerfile.whatsapp`, `client/Dockerfile`,
> `server/Dockerfile`, and `scripts/docker.js`) are **no longer in the repository**. This section
> documents that historical topology for reference; it is not a runnable path today. The
> `docker-compose.yml` and `docker/` in the repo now are the one-container self-hosting image
> described in [docker.md](./docker.md).

The project previously deployed using Docker Compose with an nginx reverse proxy.

### Docker Configuration (4-Container Stack)

**Services:**

| Container | Image | Port | Description |
|-----------|-------|------|-------------|
| redis | redis:7-alpine | 6379 | Cache and pub/sub for workflows |
| backend | opencompany-backend | 3010 | FastAPI Python backend |
| frontend | opencompany-frontend | 3000 | React app via nginx |
| whatsapp | opencompany-whatsapp | 5000 | Go WhatsApp bridge service |

Every port number in this Docker section (3000 / 3010 / 3020 / 5000 / 9400 / 6379) is the
historical compose-era value, kept verbatim as a record. The current runtime declares its
serial port block once in `.env.template` (`PYTHON_BACKEND_PORT`, `NODEJS_EXECUTOR_PORT`,
`WHATSAPP_RPC_PORT`, ...); nothing below applies to it.

**Frontend (`client/Dockerfile`):**
- Multi-stage build: Node.js builder -> nginx:alpine production
- Serves static files via nginx on port 80 (mapped to 3000)
- Size: ~54 MB

**Backend (`server/Dockerfile`):**
- Python 3.12-slim base with Node.js 22.x for JS/TS execution
- Includes Playwright chromium for JS-rendered web scraping (crawleeScraper node)
- Includes persistent Node.js server (Express + tsx) on port 3020
- Optimized bytecode compilation (`python -O -m compileall`)
- Health check endpoint on port 3010
- Startup script (`start.sh`) runs both Python and Node.js servers (must have LF line endings, not CRLF)
- Depends on: redis, whatsapp
- Size: ~800 MB (includes Playwright chromium)

**WhatsApp (`docker/Dockerfile.whatsapp`):**
- Uses npm package `whatsapp-rpc` with pre-built binaries
- Node.js 20-alpine base with `npx whatsapp-rpc api --foreground`
- Binary downloaded from GitHub releases during npm postinstall
- Exposed on port 9400 (configurable via `PORT`, `WHATSAPP_RPC_PORT` env vars, or `--port` CLI flag)
- QR codes generated as base64 PNG in memory (no file I/O)
- Also published to PyPI as `whatsapp-rpc` (async Python client)
- Size: ~150 MB (includes Node.js runtime)

**Redis:**
- Official redis:7-alpine image
- Healthcheck: `redis-cli ping`
- Persistent volume: `redis_data`
- No authentication (internal network only)

**Development Compose (the legacy `docker-compose.yml`):**
```yaml
services:
  # Redis uses profiles - only starts when REDIS_ENABLED=true
  redis:
    image: redis:7-alpine
    profiles:
      - redis  # Only starts with --profile redis flag
    ports: ["${REDIS_PORT:-6379}:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

  backend:
    build: ./server
    ports: ["${PYTHON_BACKEND_PORT:-3010}:${PYTHON_BACKEND_PORT:-3010}"]
    volumes:
      - ./server:/app
      - /app/nodejs/node_modules  # Preserve Linux binaries (prevents Windows esbuild conflict)
    depends_on:
      whatsapp: { condition: service_healthy }  # No Redis dependency
    environment:
      - REDIS_ENABLED=${REDIS_ENABLED:-false}
      - REDIS_URL=redis://redis:6379

  frontend:
    build: ./client
    ports: ["${VITE_CLIENT_PORT:-3000}:${VITE_CLIENT_PORT:-3000}"]

  whatsapp:
    build:
      context: .
      dockerfile: docker/Dockerfile.whatsapp
    ports: ["${WHATSAPP_RPC_PORT:-9400}:${WHATSAPP_RPC_PORT:-9400}"]
```

**Docker Scripts Wrapper (`scripts/docker.js`):**
Auto-detected `REDIS_ENABLED` in `.env` and added the `--profile redis` flag when enabled:
```javascript
// Reads .env and checks REDIS_ENABLED value
function isRedisEnabled() {
  const content = readFileSync(resolve(ROOT, '.env'), 'utf8');
  const match = content.match(/^REDIS_ENABLED\s*=\s*(.+)$/m);
  const value = match?.[1].trim().toLowerCase();
  return value === 'true' || value === '1' || value === 'yes';
}

// Adds --profile redis when enabled
if (isRedisEnabled()) {
  composeArgs.push('--profile', 'redis');
}
```

**Production Compose (`docker-compose.prod.yml`):**
```yaml
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    volumes: ["redis_data:/data"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

  whatsapp:
    build:
      context: .
      dockerfile: docker/Dockerfile.whatsapp
    ports: ["9400:9400"]
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:9400/health"]

  backend:
    build: ./server
    ports: ["3010:3010"]
    depends_on:
      redis: { condition: service_healthy }
      whatsapp: { condition: service_healthy }
    environment:
      - REDIS_ENABLED=true
      - REDIS_URL=redis://redis:6379

  frontend:
    build: ./client
    ports: ["3000:80"]
```

### Nginx Configuration

Located at `/etc/nginx/sites-available/flow.opencompany.sh`:
- Frontend: `/` -> `http://127.0.0.1:3000`
- Backend API: `/api/` -> `http://127.0.0.1:3010/api/`
- WebSocket: `/ws/` -> `http://127.0.0.1:3010/ws/` (with upgrade headers)
- Webhook: `/webhook/` -> `http://127.0.0.1:3010/webhook/`
- Health: `/health` -> `http://127.0.0.1:3010/health`
- SSL via Let's Encrypt certbot

### Environment Configuration

**Development** (`server/.env`):
- `DEBUG=true`
- `CORS_ORIGINS` includes localhost ports
- `REDIS_ENABLED=false` (uses SQLite cache for local dev)

**Production** (Docker environment variables):
- `DEBUG=false`
- `CORS_ORIGINS=["https://your-domain.com"]`
- `REDIS_ENABLED=true` (Docker Redis container)
- `REDIS_URL=redis://redis:6379`
- Environment set in `docker-compose.prod.yml`, not the `.env` file

### Frontend API URL Resolution

In the Docker era the frontend sniffed `window.location.hostname` to decide between an
explicit `http://localhost:3010` dev backend and a same-origin production backend. That
branch is gone. `client/src/config/api.ts` is now same-origin everywhere — in production
uvicorn serves the SPA and the API on one port, and in dev the Vite server proxies the backend
prefixes (`/api`, `/ws`, `/webhook`, `/health`, `/mcp`) — with `VITE_PYTHON_SERVICE_URL` left
only as an explicit escape hatch for a remote backend:

```typescript
// client/src/config/api.ts (current)
return {
  PYTHON_BASE_URL: viteEnv.VITE_PYTHON_SERVICE_URL || '',
};
```

The historical shape, for the record:

```typescript
// client/src/config/api.ts (Docker era — no longer in the tree)
const isProduction = typeof window !== 'undefined' &&
  !window.location.hostname.includes('localhost') &&
  !window.location.hostname.includes('127.0.0.1');
PYTHON_BASE_URL: viteEnv.VITE_PYTHON_SERVICE_URL || (isProduction ? '' : 'http://localhost:3010'),
```

WebSocket URL derivation is unchanged and still lives in `getWebSocketUrl()`
(`client/src/contexts/WebSocketContext.tsx`): an empty base URL means the current origin, a
non-empty one has its `http(s)` scheme rewritten to `ws(s)`:
```typescript
// client/src/contexts/WebSocketContext.tsx
if (!baseUrl) {
  const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${wsProtocol}://${window.location.host}/ws/status`;
}
```

### Resource Usage (GCP e2-micro, legacy Docker deployment)

| Resource | Value |
|----------|-------|
| CPU | 2 cores (Intel Xeon @ 2.20GHz) |
| RAM | 1.9 GB total, ~820 MB used |
| Disk | 14 GB total, ~9.2 GB used |
| Backend Memory | ~144 MB |
| Frontend Memory | ~3.4 MB |

### Useful Commands (legacy Docker deployment)

```bash
# View logs (all containers)
ssh $DEPLOY_HOST 'cd /opt/opencompany && docker-compose logs -f'

# View specific service logs
ssh $DEPLOY_HOST 'cd /opt/opencompany && docker-compose logs -f backend'
ssh $DEPLOY_HOST 'cd /opt/opencompany && docker-compose logs -f whatsapp'

# Restart all services
ssh $DEPLOY_HOST 'cd /opt/opencompany && docker-compose restart'

# Restart specific service
ssh $DEPLOY_HOST 'cd /opt/opencompany && docker-compose restart backend'

# Check container status
ssh $DEPLOY_HOST 'docker ps'

# Check resource usage
ssh $DEPLOY_HOST 'docker stats --no-stream'

# Check Redis connection
ssh $DEPLOY_HOST 'docker exec opencompany-redis-1 redis-cli ping'

# Check backend health (shows redis_enabled status)
curl -s https://$DEPLOY_DOMAIN/health | jq

# Clean up Docker resources (if disk full)
ssh $DEPLOY_HOST 'docker system prune -af && docker builder prune -af'
```

### Local Docker Development (legacy)

For testing the full production stack locally (when the compose files existed):

```bash
# Build and start all containers
docker-compose -f docker-compose.prod.yml up --build

# Access locally
# Frontend: http://localhost:3000
# Backend API: http://localhost:3010
# WhatsApp RPC: http://localhost:9400
# Redis: localhost:6379

# Stop all containers
docker-compose -f docker-compose.prod.yml down

# Remove volumes (clean slate)
docker-compose -f docker-compose.prod.yml down -v
```

---

## Local Development Build (current, non-Docker)

```bash
# Full production build (company build: bun install, client, sidecar, uv sync --extra docs, bytecode, Temporal binary)
bun run build

# Serve the built SPA + API on one port, exactly as a deployed VM does
company start        # or: company serve

# Vite's static preview of client/dist alone (no backend) — there is no root `preview` script
cd client && bun run preview
```
