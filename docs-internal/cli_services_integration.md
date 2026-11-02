# CLI-Based Services Integration Guide

OpenCompany integrates external services that manage their own lifecycle via CLI tools. These services own their ports, data directories, and processes -- OpenCompany does not manage them directly.

## Principles

1. **Plugin-owned binaries** -- OpenCompany-managed CLIs/binaries install into `<DATA_DIR>/packages/` from the plugin's own `_install.py` (pooch for release archives, or `core.js_runtime.add_package` — `bun add` into the shared bun-managed packages tree — for npm-registry packages; no Node, no npm); truly external tools stay system-installed.
2. **Backend-owned lifecycle** -- long-lived daemons are `BaseProcessSupervisor` subclasses in the plugin folder, spawned on demand and stopped by `shutdown_all_supervisors()` at lifespan shutdown.
3. **Ports are declared in `.env.template`** -- the single place port numbers live. The CLI frees only the ports in `cli.config.Config.all_ports`; plugin daemons own theirs.
4. **Status is passive** -- status refreshes and WS status commands consult the supervisor (`is_running()`), never spawn.

## Integrated CLI Services

### Temporal Server (official `temporal` CLI)

Wraps the official `temporal` CLI's `server start-dev` mode (per [docs.temporal.io/develop/python/set-up-your-local-python](https://docs.temporal.io/develop/python/set-up-your-local-python)) — SQLite-backed dev server, single process, gRPC + Web UI both embedded.

**Install:** Automated. `company build` step [6/6] runs `python -m services.temporal._install`, which uses `pooch` to download the CLI release pinned in `server/config/temporal_cli.json`, checked against that file's SHA-256, into `<DATA_DIR>/packages/temporal/<version>/` (= `~/.opencompany/packages/temporal/<version>/` by default, on every OS — via `core.paths.package_dir("temporal")`), and deletes every other CLI there. No npm package, no system install required. A `temporal.db` the pinned server minor did not create is deleted before the server starts (see [TEMPORAL_ARCHITECTURE.md](./TEMPORAL_ARCHITECTURE.md#server-management)).

**Lifecycle:** Backend-owned (July 2026): the FastAPI lifespan starts the dev server via `TemporalServerRuntime.ensure_started()` ([server/services/temporal/_runtime.py](../server/services/temporal/_runtime.py)) when `TEMPORAL_ENABLED` and the configured address is loopback; `shutdown_all_supervisors()` stops it at shutdown. The CLI no longer supervises Temporal (the `_supervised_runtime.py` shim and the `_temporal_specs.py` command helpers were deleted); `company stop` still frees the Temporal ports, then kills any other Temporal process whose command line names this installation's data directory, where the binary and its SQLite database live (`cli/ports.py::kill_by_pattern`). Another checkout's or worktree's Temporal server, and a test run that merely mentions `temporal`, are left running. Its orphan cleanup works the same way: it takes the repository's worktrees from `git worktree list`, and a process working in another worktree is left alone even when it runs this checkout's Python.

**Ports (declared in `.env.template`, freed by `company stop`'s port-kill pre-flight):**
| Service | Env var (value in `.env.template`) | Note |
|---------|------|---------|
| gRPC    | `TEMPORAL_FRONTEND_GRPC_PORT` | passed as `--port` (Temporal's own default is 7233) |
| Web UI  | `TEMPORAL_UI_PORT` | passed as `--ui-port` (the CLI would otherwise pick `--port + 1000`) |

Both bound by the same `temporal.exe` process. Killing the process releases both.

**Persistence + resumption:**
- SQLite db at `~/.opencompany/temporal.db` (`TEMPORAL_SQLITE_PATH=temporal.db`, resolved under `DATA_DIR`). History is preserved across restarts; the Temporal UI keeps showing every workflow that ever ran.
- Running and paused deployments survive restarts. `TEMPORAL_TERMINATE_RUNNING_ON_STARTUP` defaults to **`false`** and the boot-time reconcile pass (`reconcile_active_controls_on_boot`) re-arms live generations from their persisted graph snapshot. Setting it `true` is a debug-only sweep: [`services/temporal/lifecycle.py`](../server/services/temporal/lifecycle.py) then calls [`TemporalClientWrapper.terminate_running_workflows`](../server/services/temporal/client.py) once after client connect (reason `"OpenCompany startup: auto-resumption disabled"`), workflows show as `Terminated` (not deleted) in the UI — and even then any active workflow-control row vetoes the sweep so a live deployment is never killed.

**Embedded worker:**
The Temporal worker runs inside the Python backend: `services/temporal/lifecycle.py` builds `TemporalWorkerManager` + `TemporalWorkerPool` inside `run_temporal_lifecycle`, which `main.py` schedules from the lifespan. No separate worker process needed for single-server deployments. For horizontal scaling in distributed mode, run standalone workers:
```bash
cd server && uv run python -m services.temporal.worker
```
A standalone worker refuses to start unless `DISTRIBUTED_MODE=true`: otherwise the backend's embedded worker runs every task, and a second process without the backend's plugins and credentials would fail the tasks it took.

---

## Adding a New CLI Service

Follow the plugin-runtime pattern (references: `nodes/whatsapp/_runtime.py`,
`nodes/code/_runtime.py`, `services/temporal/_runtime.py`):

1. **Install** — plugin-owned `_install.py` that materialises the binary
   under `<DATA_DIR>/packages/<name>/` (pooch for release archives; the
   shared bun-managed packages tree via `core.js_runtime.add_package` for
   npm-registry packages, whose bin shims then run on bun). Idempotent; callable one-shot from
   `company build` when pre-caching is worth it. Pin the version, and for
   an npm package compare `core.js_runtime.installed_version(name)` with
   the pin so a bump reaches machines that already have it
   (`nodes/cloudflare/_install.py`).
2. **Supervise** — a `BaseProcessSupervisor` subclass in the plugin folder
   (`_runtime.py`) owning argv/cwd/env, with `ensure_started()`
   (probe-or-spawn) for on-demand starts. Register the singleton via
   `services._supervisor.register_supervisor` from the plugin
   `__init__.py` so lifespan shutdown reaches it.
3. **Start on demand** — the demand signals own the starts: node
   execution, user-initiated connect/login WS commands, or deploy-time
   trigger prechecks. Never start from a status refresh.
4. **Configure via env** — the service's port/vars are declared in
   `.env.template` (annotated plugin-owned) and read through
   `core.env_defaults` — no fallback literals in code.

## Common Mistakes to Avoid

| Mistake | Why it's wrong | Correct approach |
|---------|---------------|-----------------|
| Spawning from a status refresh | a passive probe boots an optional daemon | Consult the supervisor (`is_running()`); demand signals own the starts |
| Adding plugin-daemon ports to `Config.all_ports` | the CLI would kill a backend-owned daemon during startup | The backend supervises them; the CLI carries no plugin knowledge |
| Spelling `node_modules/.bin/<cli>[.cmd]` by hand | Drifts per platform (bun writes `<name>.exe` + `<name>.bunx` on Windows, `<name>` elsewhere) | `core.js_runtime.shared_tree_bin(name)` after `add_package(spec)` |
| Running the CLI through `bun x` / `npx` per call | Slow, unpinned version, no shared tree | `add_package("<pkg>@<version>")` once into the shared packages tree, then call the shim directly |
| Treating an existing shim as the pinned version | A pin bump never reaches machines that already have the package (`company clean` keeps `packages/`) | Compare `core.js_runtime.installed_version(name)` with the pin and re-run `add_package` on a mismatch (`nodes/cloudflare/_install.py`) |
| Wrapping CLI in a JS script | Unnecessary indirection | Use CLI commands directly |
| Hardcoding port numbers in code or docs | drifts when ports change | Declare in `.env.template`; read via `core.env_defaults` |
