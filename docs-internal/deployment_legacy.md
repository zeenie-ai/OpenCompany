# Deployment — Current Self-Deploy CLI + Legacy Docker Reference

This doc is the single home for OpenCompany deployment topology. The **current** path is the
`company deploy` self-deploy CLI (top section). The **Docker Compose** path below it is a
**legacy reference only** — that topology's compose files, `docker/` directory, and
`scripts/docker.js` wrapper were removed from the repo. It is retained solely as documentation
of the historical container topology. The current container path is a single self-hosting
image built from source: see [docker.md](./docker.md).

---

## Self-Deploy CLI (`company deploy`) — current path

One command provisions a login-gated OpenCompany VM on GCP or AWS. Two stages:

1. **Operator's cloud CLI** (`providers/gcp.py`, `providers/aws.py`): gcloud handles auth +
   project/region/zone resolution + ADC verification + API enablement; the aws CLI handles
   auth (`sts get-caller-identity`) + region, hands its resolved credentials to Terraform
   (`aws configure export-credentials`, so `aws login` sessions work), and checks the region
   has a default VPC. Each adapter also names its default `--machine-type` and the `--source`
   values its module supports.
2. **Terraform** (`cli/terraform/<provider>/`, one shared variable set) owns all resources.
   GCP: firewall, artifact bucket (local `bun pm pack` source), service account, and a
   startup script (`startup.sh.tftpl`, GCE metadata, re-run on every boot) that installs bun +
   uv (no Node, no npm), `bun add -g`s the package, provisions its Python side with
   `company provision`, and runs `company serve` under systemd. AWS: a security group and an
   EC2 instance whose cloud-init script (first boot only) runs `install.sh` as `ubuntu` with a
   published release (`--source release` only) and runs `company serve` under systemd as that
   user; a later apply never replaces or restarts the instance (`ignore_changes = [ami,
   user_data]`). See [cli/terraform/aws/README.md](../cli/terraform/aws/README.md).

> **Known issue in 0.2.0 and 0.2.1 ([errors.md #25 and #26](./errors.md))**: on a registry
> install, `company serve` stops with `Project not built. Run "company build" first.`, so the
> VM's systemd service fails and keeps restarting, and the package also lacks the JS executor
> sidecar bundle. #25 is fixed after 0.2.1; on those two releases run `company build` once on the VM as the
> service user, then `systemctl restart opencompany` (`machinaos` on a pre-rebrand deployment).

Login gate = built-in auth (`VITE_AUTH_ENABLED=true`, `AUTH_MODE=single`) with the owner
credential generated at deploy time and seeded on first boot. `build_app_env`
(`cli/commands/deploy/_secrets.py`) also sets `DEPLOYMENT_MODE=cloud` on the VM and mints
fresh `JWT_SECRET_KEY` / `SECRET_KEY` / `API_KEY_ENCRYPTION_KEY` per deploy. Temporal, the
event framework and `HOST` keep their `.env.template` values (Temporal on), as in the Docker
image, so chat, triggers and cron work on the VM.

`--name` (default `opencompany`) names a deployment: its state folder and its cloud resource
id. One machine drives one VM per name; a name stays with the provider that created it.

```bash
company deploy up --provider gcp --owner-email you@example.com   # provision + install + print URL/creds
company deploy up --provider aws --name acme-corp --owner-email owner@acme.example
company deploy status [--name acme-corp]                         # URL + /health
company deploy destroy [--name acme-corp]                        # terraform destroy + clear state
```

### Key files

| Path | Responsibility |
|------|----------------|
| `cli/commands/serve.py` | Single-port runtime: uvicorn fronts API + WS + built SPA. `serve` supervises exactly one process — the JS code-exec sidecar (on bun), WhatsApp and the Temporal dev server are backend-owned and spawn on demand |
| `cli/commands/deploy/` | Verbs (`up.py` / `status.py` / `destroy.py`), `_secrets.py`, `_state.py`, `_terraform.py` (Terraform driver), `providers/` (`gcp.py` / `aws.py` provider CLI adapters) |
| `cli/terraform/gcp/` | HCL module (`main.tf` / `variables.tf` / `outputs.tf`) + `startup.sh.tftpl` GCE startup-script template |
| `cli/terraform/aws/` | HCL module (`terraform.tf` / `main.tf` / `variables.tf` / `outputs.tf`) + `startup.sh.tftpl` cloud-init template |

### State + delinking

- Deployment state lives at `<DATA_DIR>/deploy/<name>/` (`~/.opencompany/deploy/opencompany/` by
  default); only `company deploy destroy` removes it. `company clean` keeps `.opencompany/deploy`
  inside the repo (see `_OPENCOMPANY_KEEP` in `cli/commands/clean.py`), which matters only when
  `DATA_DIR` points there.
- Upgrade compatibility is deliberate: the CLI discovers pre-rebrand
  `deploy/machinaos/` state under the configured data root, `~/.machina`, or a
  checkout-local `.machina` directory. It retains the `machinaos` cloud and
  systemd resource id for that deployment, preventing Terraform from replacing
  or orphaning live infrastructure. Fresh deployments use `opencompany`.
- The `machina` executable remains only as a deprecated legacy alias; use `company`
  in new commands and automation.
- The deploy code is fully delinked from `company build` / `company clean`: lazy verb stubs in
  `cli/cli.py` mean nothing in the build pipeline imports it.

The legacy `deploy.sh` (docker-compose images over SCP to a GCE box) was removed.

---

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
