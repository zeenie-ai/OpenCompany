# CLI-Based Services Integration Guide

OpenCompany integrates external services that manage their own lifecycle via CLI tools. These services own their ports, data directories, and processes -- OpenCompany does not manage them directly.

## Principles

1. **Install globally** -- CLI tools must be available system-wide (`npm install -g <package>`)
2. **Use the CLI** -- Check status, start, stop via the package's own commands. No port-sniffing, no TCP socket checks, no hardcoded port detection.
3. **Don't manage their ports** -- External service ports are NOT added to `allPorts` in `utils.js`. OpenCompany only kills ports it owns (client, backend, WhatsApp, Node.js executor).
4. **Handle "already running"** -- Before adding a service to `concurrently`, check if it's already up. If it is, skip it. This prevents `--kill-others` cascade kills in `start.js`.
5. **Keep dependencies in package.json** -- Even if installed globally, keep the package in `dependencies` so npm scripts (`npm run temporal:start`) work.

## Integrated CLI Services

### Temporal Server (official `temporal` CLI)

Wraps the official `temporal` CLI's `server start-dev` mode (per [docs.temporal.io/develop/python/set-up-your-local-python](https://docs.temporal.io/develop/python/set-up-your-local-python)) — SQLite-backed dev server, single process, gRPC + Web UI both embedded.

**Install:** Automated. `company build` step [6/6] runs `python -m services.temporal._install`, which uses `pooch` to download the official CLI archive from `https://temporal.download/cli/archive/latest?platform=<os>&arch=<arch>` into `<DATA_DIR>/packages/temporal/` (= `~/.opencompany/packages/temporal/` by default, on every OS — via `core.paths.package_dir("temporal")`). No npm package, no system install required.

**Lifecycle:** Managed entirely by the CLI commands (`company start` / `company dev` / `company stop`) — there's no separate `temporal:start` npm script. The supervisor at [cli/supervisor.py](../cli/supervisor.py) spawns the official CLI via the supervised-runtime shim at [server/services/temporal/_supervised_runtime.py](../server/services/temporal/_supervised_runtime.py).

**Ports (declared in `.env.template`, freed by `company stop`'s port-kill pre-flight):**
| Service | Port | Env var |
|---------|------|---------|
| gRPC    | 7233 | `TEMPORAL_FRONTEND_GRPC_PORT` |
| Web UI  | 8080 | `TEMPORAL_UI_PORT` (CLI default is 8233; we override) |

Both bound by the same `temporal.exe` process. Killing the process releases both.

**Persistence + resumption:**
- SQLite db at `~/.opencompany/temporal.db` (`TEMPORAL_SQLITE_PATH=temporal.db`, resolved under `DATA_DIR`). History is preserved across restarts; the Temporal UI keeps showing every workflow that ever ran.
- Workflow auto-resumption is disabled at boot: [`TemporalClientWrapper.terminate_running_workflows`](../server/services/temporal/client.py) runs once after client connect and terminates every `Running` workflow with `reason="OpenCompany startup: auto-resumption disabled"`. Workflows show as `Terminated` (not deleted) in the UI. Gated by `TEMPORAL_TERMINATE_RUNNING_ON_STARTUP=true`. Flip to `false` once `DeploymentManager` reconcile-against-Visibility lands.

**Embedded worker:**
The Temporal worker runs inside the Python backend via `TemporalWorkerManager` in `main.py` lifespan. No separate worker process needed for single-server deployments. For horizontal scaling, run standalone workers:
```bash
cd server && python -m services.temporal.worker
```

---

## Adding a New CLI Service

Follow this pattern when integrating a new external CLI service:

### 1. Install globally and add to dependencies

```bash
npm install -g <service-package>
npm install <service-package>
```

### 2. Add npm scripts in package.json

```json
{
  "<service>:start": "<service-cli> start",
  "<service>:stop": "<service-cli> stop",
  "<service>:status": "<service-cli> status"
}
```

### 3. Integrate in start.js (with --kill-others protection)

```javascript
let serviceRunning = false;
try {
  const status = execSync('<service-cli> status', {
    encoding: 'utf-8', timeout: 5000, stdio: 'pipe'
  });
  serviceRunning = /running|UP/i.test(status);
} catch {
  serviceRunning = false;
}

if (serviceRunning) {
  log('<Service> already running, skipping');
}

// Add to services list only if not running
if (!serviceRunning) services.push('npm:<service>:start');

// Add ready-detection pattern only if not running
if (!serviceRunning) {
  readyPatterns.push({
    name: '<Service>',
    pattern: /<service>.*started|<service>.*ready/i
  });
}
```

### 4. Integrate in stop.js

```javascript
// Kill by process name pattern -- NOT by port
const pids = await killByPattern('<service>');
if (pids.length > 0) {
  console.log(`Killed ${pids.length} <service> processes`);
}
```

### 5. Do NOT add ports to allPorts

The service manages its own ports. Do not add them to `loadEnvConfig().allPorts` in `utils.js`.

### 6. dev.js -- usually no special handling needed

`dev.js` does not use `--kill-others`, so services exiting early is harmless. Just add `npm:<service>:start` to the services list unconditionally.

---

## Common Mistakes to Avoid

| Mistake | Why it's wrong | Correct approach |
|---------|---------------|-----------------|
| TCP socket check (`net.connect(port)`) | Fragile, races with other services, hardcodes ports | Use `<service-cli> status` |
| Adding service ports to `allPorts` | `killPort()` would kill the service during startup | Service manages its own ports |
| Resolving `node_modules/.bin/<cli>` path | Breaks if not in PATH, tribal workaround | Install globally |
| Using `npx <service-cli>` in `execSync` | Slow, may use wrong version, npx overhead | Install globally, call directly |
| Wrapping CLI in a JS script | Unnecessary indirection | Use CLI commands directly |
| Hardcoding port numbers for detection | Breaks if service config changes | Use CLI status command |
