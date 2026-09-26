# Known Errors & Troubleshooting

Documented root causes and fixes for errors encountered in OpenCompany development and production.

---

## 1. SQLAlchemy Import Hang (Windows)

**Symptom**: Backend hangs at startup with no output after `Importing DI container + all services...`. The process is alive but never binds `$PYTHON_BACKEND_PORT`. `import sqlalchemy` blocks indefinitely.

**Root cause**: Git worktrees nested inside the project root (e.g., `.claude/worktrees/`) cause Windows Defender real-time scanning to fan out across all worktree directories when Python loads `.pyd` (native DLL) files. SQLAlchemy has 5 Cython `.pyd` files (`collections`, `immutabledict`, `processors`, `resultproxy`, `util`) loaded sequentially during import. Defender's scan queue backs up across the worktree copies, blocking `LoadLibrary()` for minutes per file.

**Contributing factors**:
- Each worktree contains its own `.venv/`, `node_modules/`, and source tree (thousands of files)
- Windows Search Indexer and Defender monitor directory trees recursively from the project root
- The package manager's store layout (pnpm's `.pnpm-store` hardlinks at the time; bun's isolated linker now symlinks each worktree's `node_modules/` through its `node_modules/.bun/` store) creates additional file-system contention
- Killing the hung Python process does NOT help -- the next attempt restarts the scan queue from scratch

**Fix**: Move or remove worktrees from inside the project root.

```bash
# Remove worktrees
git worktree remove .claude/worktrees/<name>

# Or move them outside the project root
git worktree move .claude/worktrees/<name> ../opencompany-worktrees/<name>
```

**Prevention**: Keep git worktrees as siblings of the project, not nested inside it. For example:
```
d:/startup/projects/
  OpenCompany/                  # main project
  opencompany-worktrees/        # worktrees outside the project root
    credentials-scaling/
    native-llm-sdk/
```

**Verification**: After removing worktrees, `import sqlalchemy` should complete in <1 second:
```bash
cd server && uv run python -u -c "import time; t=time.time(); import sqlalchemy; print(f'{time.time()-t:.2f}s')"
```

### 1a. SQLAlchemy Hang Persists After Removing Worktrees

**Symptom**: Even after moving worktrees outside the project root and adding the `.venv` to Defender exclusions, `import sqlalchemy` still hangs. Only a system reboot resolves it.

**Root cause**: Defender's minifilter driver (`MpFilter.sys`) caches scan verdicts in a kernel-mode cache keyed by file identity (volume + file reference number + USN). When scans were previously backed up, some entries stay in "pending scan" state indefinitely. Defender exclusions added at runtime do NOT evict existing pending cache entries -- only a Defender service restart or full reboot clears the minifilter's in-memory state.

Contributing factors:
- Killing stuck Python processes via `Stop-Process` can trigger Defender to re-scan the DLL handles those processes had mapped
- SysMain/Superfetch prefetch contention on cold venv imports
- NTFS USN journal backlog from large file-churn operations (pip installs, worktree moves)

**Fix** (without reboot):
```powershell
# Restart Defender service (admin required)
Restart-Service WinDefend

# Or restart SysMain
Restart-Service SysMain
```

**Fix** (if admin is unavailable): **Reboot**. This is what clears the stuck kernel cache reliably.

**Prevention** (`cli/commands/start.py`, `_sqlalchemy_preflight`): a preflight probe times `import sqlalchemy` in the server venv with a 15-second timeout. If the import times out or crashes it fails fast with actionable remediation steps instead of letting uvicorn hang silently; an import that succeeds but takes over 5 seconds only prints a warning.

---

## 2. Temporal `context canceled` / `UpdateTaskQueue` Errors

**Symptom**: Temporal server logs show recurring errors even when no workflows are running:
```
level=ERROR msg="Operation failed with internal error."
error="UpdateTaskQueue failed. Failed to start transaction. Error: context canceled"
component=matching-engine wf-namespace=temporal-system
```

**Root cause**: Temporal's SQLite database runs in DELETE journal mode by default, which allows only one writer at a time. Temporal's internal system workflows (namespace replication, queue metadata maintenance, backlog counters) contend for write access. When multiple internal workflows try to update task queue metadata concurrently, `BeginTx` blocks, the gRPC context deadline elapses, and the transaction is cancelled.

**Status**: benign with the current single-process `temporal server start-dev` setup (the official CLI handles its own SQLite pragmas internally). If you see `persistence_error_with_type` errors in a **user namespace** (e.g. `default` instead of `temporal-system`), it's a real problem — file an issue. Errors in `temporal-system` are auto-retried by Temporal and don't affect workflow execution.

**If you do hit a stuck DB**: delete the db file and let `temporal server start-dev` recreate it on next boot:
```bash
company stop
rm ~/.opencompany/temporal.db          # macOS / Linux
rm "$env:USERPROFILE/.opencompany/temporal.db"   # Windows PowerShell
company start
```

---

### 2a. Temporal OpenTelemetry Lines Appear Twice

**Symptom**: SDK spans appear in exact adjacent pairs with the same operation
and Temporal identity fields:

```text
CompleteWorkflow:AgentWorkflow ... temporalWorkflowID=<same> temporalRunID=<same>
CompleteWorkflow:AgentWorkflow ... temporalWorkflowID=<same> temporalRunID=<same>
StartActivity:node.console.v1 ... temporalWorkflowID=<same> temporalRunID=<same>
StartActivity:node.console.v1 ... temporalWorkflowID=<same> temporalRunID=<same>
```

The OpenCompany `node.console.execute` span may appear only once between those
pairs.

**Root cause**: `TracingInterceptor` was attached to the shared Temporal client
and repeated in `Worker(..., interceptors=...)`. The Python SDK automatically
prepends worker-compatible client interceptors, so the activity and workflow
pass through two tracing interceptors. The two generated OpenTelemetry spans
have distinct span IDs, but the compact console formatter displays only the
operation and Temporal attributes; they therefore look identical.

This does **not** mean that the node executed twice. `StartActivity` is the
workflow-side scheduling span, `RunActivity` is the worker-side invocation
span, and `node.<type>.execute` surrounds the application body. Separate
`CompleteWorkflow:AgentWorkflow` and `CompleteWorkflow:MachinaWorkflow` lines
represent the child and parent workflow. A `CompleteWorkflow` duration of
`0ms` is normal for Temporal's replay-safe terminal marker.

**Fix**: keep exactly one `TracingInterceptor` on `Client.connect(...)`. Do not
repeat it in worker interceptor lists; workers explicitly register only
`ObservabilityWorkerInterceptor` and distinct plugin-owned interceptors. Apply
the invariant to manager, specialized-pool, standalone, helper, and test worker
construction paths.

**Verification**:

1. Compare operation name, `temporalWorkflowID`, `temporalRunID`, and
   `temporalActivityID`; each identity tuple should occur once per phase.
2. Confirm Temporal Event History contains one activity attempt. A genuine
   retry has another attempt and is reported by the application observability
   interceptor.
3. Confirm one `node.<type>.execute` span for one plugin-body invocation.
4. Keep the expected single `StartActivity`, `RunActivity`, and workflow
   completion entries; they are different lifecycle layers.

---

### 2b. `GetTaskQueue operation failed ... context canceled` After Launch

**Symptom**: some time after launch the aggregated log shows:
```
level=ERROR msg="Operation failed with internal error."
error="GetTaskQueue operation failed. Failed to check if task queue /_sys/code-exec/2 of type Activity existed. Error: context canceled"
error-type=serviceerror.Unavailable operation=GetTaskQueue
```

`/_sys/<queue>/<n>` is Temporal's internal partition naming — `code-exec` here
is one of the worker pool's per-plugin task queues; any low-traffic queue and
partition can appear.

**Root cause**: the dev server logs ERROR when a poller's long-poll context is
canceled mid-persistence-read — routine events with this topology (~10 workers
long-polling a single-writer SQLite dev server): poller autoscaling settling
after boot, a worker restart, backend shutdown, laptop sleep/wake breaking gRPC
channels, or the server's own idle background jobs. Staff-confirmed benign
upstream ([community.temporal.io/t/18635](https://community.temporal.io/t/temporal-server-errors-with-no-app-running/18635),
[temporalio/cli#633](https://github.com/temporalio/cli/issues/633)): a canceled
poll never loses a task — tasks are persisted, the SDK re-polls immediately,
and the server redelivers.

**Status**: benign noise. `TemporalServerRuntime.stderr_log`
([`services/temporal/_runtime.py`](../server/services/temporal/_runtime.py))
downgrades lines matching both `context canceled` and the internal-error /
`GetTaskQueue` markers to DEBUG; genuine dev-server errors keep surfacing at
ERROR through the supervisor pipe.

**When it is NOT benign**: if activities on that queue actually hang, check
whether the queue's pool worker died (Temporal Web UI → task queue pollers).
Pool workers self-restart with a fresh Worker instance per attempt
(`TemporalWorkerPool._run_queue_worker`); a queue with zero pollers after
repeated restarts indicates a real crash loop — read the
`[Pool] Worker for queue ... crashed` log lines for the underlying error.

---

## 3. Temporal Activity `CancelledError` on Long-Running Nodes

**Symptom**: Nodes that run for more than ~2 minutes (Coding Agent, browser automation, AI multi-tool loops) fail with:
```
asyncio.exceptions.CancelledError
```
in the Temporal activity at `activities.py` line `async for msg in ws:`.

The Temporal UI shows the activity failed with `TIMEOUT_TYPE_HEARTBEAT`.

**Root cause**: The activity WebSocket read loop only sent heartbeats when a non-matching WebSocket message arrived. During long-running operations where the backend processes internally without broadcasting any WS messages, no heartbeats fire. Temporal's 2-minute `heartbeat_timeout` expires and cancels the activity.

**Fix** (applied in activities.py): Replace the `async for msg in ws:` iterator with an explicit `asyncio.wait_for(ws.receive(), timeout=30.0)` loop. On timeout (no message in 30s), a heartbeat fires and the loop continues. This guarantees heartbeats every 30 seconds regardless of WebSocket traffic.

```python
# Before (broken for long-running nodes):
async for msg in ws:
    if msg.type == aiohttp.WSMsgType.TEXT:
        response = json.loads(msg.data)
        if response.get("request_id") == request_id:
            return response
        activity.heartbeat(f"Waiting for {node_id}")  # Only fires on messages

# After (heartbeats even when no messages arrive):
while True:
    try:
        msg = await asyncio.wait_for(ws.receive(), timeout=30.0)
    except asyncio.TimeoutError:
        activity.heartbeat(f"Waiting for {node_id}")  # Fires every 30s guaranteed
        continue
    # ... handle msg
```

Also changed `receive_timeout=540` to `receive_timeout=None` on `ws_connect()` -- the old 9-minute aiohttp-level timeout was a second hard cap that could kill activities independently of the heartbeat mechanism. Liveness is now managed entirely by Temporal heartbeats.

**Timeout configuration reference**:

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `start_to_close_timeout` | 24 h | Maximum total time for an activity (`_NODE_ACTIVITY_START_TO_CLOSE`, `services/temporal/workflow.py`) |
| `heartbeat_timeout` | 2 min | Maximum gap between heartbeats before Temporal cancels (workflow.py) |
| `asyncio.wait_for` timeout | 30s | Periodic heartbeat interval in the WS read loop (activities.py) |
| `ws_connect heartbeat` | 30s | WebSocket protocol-level ping/pong keepalive (activities.py) |
| `receive_timeout` | None | No aiohttp-level hard cap (was 540s) |

---

## 4. WhatsApp RPC Timeout

**Symptom**: Backend logs show:
```
WhatsApp RPC timeout - Go service not responding at ws://localhost:${WHATSAPP_RPC_PORT}/ws/rpc
```

WhatsApp service health check (`/health`) returns 200 OK, but the WebSocket RPC connection fails.

**Root cause**: The RPCClient WebSocket connect timeout was set to 2.0 seconds (in the WhatsApp RPC client, today `server/nodes/whatsapp/_service.py`). The Go whatsmeow service's WebSocket handshake can take 2-3 seconds on Windows, especially on cold start or when Defender is scanning the binary. A 2.1s handshake exceeds the 2.0s deadline.

**Fix**: Increased the connect timeout from 2.0s to 5.0s in `RPCClient.connect()`:

```python
self.ws = await asyncio.wait_for(
    websockets.connect(self.url, ping_interval=30, max_size=100*1024*1024),
    timeout=5.0  # Was 2.0 -- too tight for Windows cold start
)
```

**Note**: This was also triggered by upgrading `edgymeow` from 0.0.18 to 0.0.19, where the newer Go binary had a slightly slower WebSocket handshake. Reverted to 0.0.18 pending investigation of the Go-side slowdown. The 5.0s timeout fix is correct regardless of version.

---

## 5. `ERR_CONNECTION_REFUSED` on Frontend Auth Check

**Symptom**: After `bun run dev`, browser console shows repeated errors:
```
GET http://localhost:${PYTHON_BACKEND_PORT}/api/auth/status net::ERR_CONNECTION_REFUSED
Failed to check auth status (attempt 4/6): TypeError: Failed to fetch
```

**Root cause**: An older version of the FastAPI lifespan blocked on Temporal client connection for up to 30 seconds before yielding to uvicorn. During that window, uvicorn was not accepting HTTP connections, so the frontend retry window could exhaust before the backend started serving.

**Status**: fixed. Temporal initialization runs in a background `asyncio.create_task()` ([server/main.py:_init_temporal_background](../server/main.py)) so the lifespan yields immediately. WorkflowService falls back to parallel/sequential execution until Temporal connects in the background.

---

## 6. `temporal` CLI Binary Not Found

**Symptom**: Supervisor logs:
```
FileNotFoundError: temporal binary not found at ...
```
or pooch fails to download during `company start`.

**Root cause**: The official `temporal` CLI archive is downloaded by `pooch` to `<DATA_DIR>/packages/temporal/` (= `~/.opencompany/packages/temporal/` by default, on every OS — via `core.paths.package_dir("temporal")`) during `company build` step [6/6]. If the build was skipped or interrupted, or if network access to `temporal.download` was blocked, the binary is missing.

**Fix**:
```bash
# Re-fetch the binary (idempotent — cache hit if already downloaded)
cd server
uv run python -m services.temporal._install

# Or re-run the build step
company build
```

If pooch keeps failing, check connectivity to `https://temporal.download/cli/archive/latest?platform=<os>&arch=<arch>` (the URL the docs document at https://docs.temporal.io/develop/python/set-up-your-local-python). Behind a corporate proxy, set `HTTPS_PROXY` in your environment before running.

---

## 7. Python Version Mismatch Warning

**Symptom**: Every `uv run` command shows:
```
warning: `VIRTUAL_ENV=C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13...`
does not match the project environment path `.venv` and will be ignored
```

**Root cause**: A parent process (e.g., Claude Code's harness) leaked a `VIRTUAL_ENV` environment variable pointing to the Windows Store Python 3.13 installation. This is NOT a virtualenv -- it's a system Python install directory incorrectly set as `VIRTUAL_ENV`.

**Impact**: None. uv correctly ignores the leaked env var and uses the project's `.venv` (Python 3.12.8). The warning is cosmetic noise from the parent shell environment.

**Fix**: Unset the variable in your shell:
```bash
unset VIRTUAL_ENV
```

Or start a fresh terminal that doesn't inherit from the Claude Code harness.

---

## 8. `install.js` Python Version Check Accepts 3.13+

**Symptom**: `scripts/install.js` reports `Python: Python 3.13.7` as valid, but `pyproject.toml` requires `>=3.11,<3.13`.

**Root cause**: The version check at `install.js:76` uses `minor >= 12` with no upper bound:
```js
if (major >= 3 && minor >= 12) { return { cmd, version }; }
```

This accepts Python 3.13, 3.14, etc. even though the project constraint is `<3.13`.

**Impact**: Low -- `uv sync` independently enforces `requires-python` from `pyproject.toml` and downloads a compatible Python (3.12.x) regardless of what `install.js` reports. The user sees misleading output but the .venv is built correctly.

**Fix**: Update the check to match `pyproject.toml`:
```js
if (major === 3 && minor >= 11 && minor < 13) { return { cmd, version }; }
```

---

## 9. Claude Code OAuth: Browser Stuck on Raw `localhost/callback` URL

**Symptom**: After clicking **Authorize** on Anthropic's OAuth page, the browser lands on a bare URL like:
```
http://localhost:52985/callback?code=Gq7kw...&state=0nEN...
```
with no "Signed in" page — yet the backend log shows the login succeeded and credentials were written. The Credentials modal still flips to Connected (via the broadcast), only the browser tab is left ugly.

**Root cause**: `@anthropic-ai/claude-code >= 2.1.162` ships a **native binary** (`bin/claude.exe`, ~240 MB via platform-specific `optionalDependencies`) instead of the prior JS shim. The native binary reads stdin while waiting for the browser OAuth callback. Under the FastAPI daemon (no TTY, parent stdin closed) it sees immediate EOF, exits, and kills its localhost callback server *before* the browser redirect arrives — so the redirect hits a dead socket. Credentials were already written before the early exit, which is why the modal still connects.

**Fix** (landed at tag v0.0.88): `services/events/cli.py::run_cli_command()` gained an optional `stdin` parameter (default `None` = inherit, unchanged for every existing caller). `nodes/agent/claude_code_agent/_oauth.py::_run_auth` passes `stdin=asyncio.subprocess.PIPE` **for the `login` subcommand only** so the CLI's stdin read blocks instead of EOFing, keeping the callback server alive until the flow completes naturally. `status` / `logout` stay on inherit-stdin (one-shot, no stdin read).

Note the binary install path also moved this release: the claude CLI now lives in the shared OpenCompany packages tree at `<DATA_DIR>/packages/node_modules/.bin/claude` (was `<DATA_DIR>/claude/npm/...`; the Windows shim was `claude.cmd` under npm at the time and is bun's `claude.exe` + `claude.bunx` since the tree moved to `bun add`). The fresh install triggered by that move is what pulled the 2.1.162 native binary that surfaced this bug — the path change was the trigger, the `stdin=PIPE` is the actual fix.

---

## 10. Stripe Login Falsely Reports Success / `exceeded max attempts`

**Symptom**: Backend log during a Stripe connect:
```
[Stripe] login step 2 CLI failure: exceeded max attempts | stderr='exceeded max attempts'
[Stripe] auth successful — credentials written to ...config.toml
```
The "auth successful" line fires even when you only *initiated* the login and never authorised in the browser.

**Root cause**: `_complete_login` used two signals that each lie in isolation:
1. **CLI exit code** — `stripe login --complete` is known to exit `1` with `stderr='exceeded max attempts'` *even after* successfully writing credentials. Exit code alone can't confirm failure.
2. **`is_logged_in()`** — only checks for `_api_key` presence in `~/.config/stripe/config.toml`, which is `True` for *any* prior login (the Stripe CLI manages that file globally, outside OpenCompany). On-disk presence alone can't confirm *this* attempt wrote anything.

So an incomplete login against a config left over from a previous session was reported as success.

**Fix** (landed at tag v0.0.88): `nodes/stripe/_handlers.py` snapshots `config.toml`'s mtime at step 1 (`pre_mtime`), threads it into `_complete_login(binary, next_step, pre_mtime)`, and requires `post_mtime > pre_mtime AND is_logged_in()` to declare success. The mtime advance is ground truth for "*this* attempt wrote fresh credentials". The `exceeded max attempts` stderr is forgiven only when the mtime actually advanced.

---

## 11. `bun install` Copies Every Package / 2-Minute First Vite Build (Windows)

**Symptom**: After a fresh install, `company build` step `[1/6]` takes ~70 s and step `[2/6]` (Vite) takes ~2 min, while a second Vite build on the same tree takes ~35 s. The same `build.log` shows uv complaining `Failed to hardlink files; falling back to full copy ... different filesystems`. Under pnpm the same cycle was ~15 s + ~16 s.

**Root cause**: installs that *write* files instead of linking them. pnpm keeps its store per drive on the project's drive (`D:\.pnpm-store`) and hardlinks from it, so a fresh install wrote no new bytes and Windows Defender's real-time first-touch scan had nothing new to scan on the next build. bun's cache lives under the user profile on `C:`; with the project on `D:` NTFS cannot hardlink across volumes, and bun 1.4.0 was also observed to copy on Windows even from a same-drive cache unless `--backend=hardlink` is forced. Every install therefore re-writes ~600 MB into `node_modules/.bun/`, and the first build pays the Defender scan on all of it (measured: 626 MB store, one hardlink path per file). uv has the identical cross-drive problem (`%LOCALAPPDATA%\uv\cache` on `C:`).

**Fix**: `company build` passes `--backend=hardlink` on Linux/Windows. That only helps when the cache is on the project's drive, which is a per-machine setting — set user environment variables `BUN_INSTALL_CACHE_DIR` (e.g. `D:\startup\projects\.bun-cache`) and `UV_CACHE_DIR` on the same drive as the checkout. Measured after both: purge → `bun install` 15 s (files show two hardlink paths: cache + store) → first Vite build 41 s. A Defender exclusion for the checkout removes the first-touch tax for whatever still gets written.

---

## 12. processManager Rejects Every `start`: `Working directory must be inside workspace` (Windows)

**Symptom**: A delegated agent's `process_manager` tool call fails on every `start` and the agent loops re-issuing the same call:
```
dispatch op NodeUserError: Working directory must be inside workspace (D:\...\.opencompany\workspaces) or daemons (D:\...\.opencompany\daemons). node_id=2:processManager:4
```
A downstream `httpRequest` probe of the dev server the agent tried to launch then fails with `httpx.ConnectError: All connection attempts failed` and a double full traceback.

**Root cause**: Canvas node ids are colon-namespaced (`2:processManager:4`; fresh seeds use `<uuid>:processManager:4`). `processManager` defaulted its cwd to `os.path.join(workspace_dir, ctx.node_id)`, and `ntpath.join` parses a leading `2:` as a drive letter — discarding `workspace_dir` entirely. `.resolve()` keeps the drive-relative garbage, so the `ProcessService.start` containment guardrail correctly rejected it. `WindowsPath / node_id` has the same drive-reset behaviour (the `cli_agent` one-shot worktree path). Latent since the April 2026 guardrail; surfaced when `4f35db97` made the Task_Completed re-delegation flow actually run tool work. Reproduce on any OS:
```
python -c "import ntpath; print(ntpath.join(r'D:\ws\AI_Employee_1', '2:processManager:4'))"   # -> 2:processManager:4
```

**Fix** (`21fb1a9a`): `core.paths.safe_path_component` sanitizes a wire identifier before it becomes a path component; `processManager` and `cli_agent/session.py` route the node id through it. The guardrail error now names the rejected resolved path and says to omit `cwd` (the designed happy path — the process-manager skill never passes one), and `httpRequest` maps `ConnectError` / `TimeoutException` to `NodeUserError`. Rule: never join a node id, model-supplied name, or any other wire identifier into a path raw — go through `safe_path_component`. Locked by `tests/nodes/test_process_manager_cwd.py`.

---

## 13. `Exception during reset or similar` / `CancelledError` in aiosqlite rollback on WebSocket disconnect

**Symptom**: Right after `[StatusBroadcaster] Client connected`, the operator log shows one or more SQLAlchemy tracebacks ending in `asyncio.exceptions.CancelledError` from `aiosqlite/core.py ... rollback`, then `[StatusBroadcaster] Send failed: Cannot call "send" once a close message has been sent`, then `Client disconnected`. Typical trigger: a client that disconnects within ~100 ms of connecting (React Strict Mode double mount in `company dev`, a health probe), i.e. while the connect-time init burst (chat / console / terminal history, credential probes) is still running.

**Root cause**: the `/ws/status` endpoint's cleanup cancelled every in-flight handler task the instant the socket closed. A handler cancelled while inside a database call receives `CancelledError` at the aiosqlite await; the session teardown then returns the connection to the pool, whose reset-on-return rollback is itself interrupted by the pending cancellation. SQLAlchemy's `_finalize_fairy` logs the traceback at ERROR, invalidates the connection, and re-raises, so nothing was actually broken (the pool rebuilt the connection) but every reconnect produced a traceback per cancelled handler. The `Send failed` warning came from a broadcast racing the close frame.

**Fix**: `core/session_teardown.py` runs rollback + close under `asyncio.shield` in both `Database.get_session` and `CredentialsDatabase.get_session`, so a cancelled caller cannot interrupt the pool reset; `_drain_handler_tasks` in `routers/websocket.py` gives in-flight handlers a one-second grace to finish before cancelling stragglers; `StatusBroadcaster.broadcast` skips sockets that are no longer `CONNECTED` on either side and prunes them quietly. Locked by `tests/test_ws_disconnect_cancellation.py`.

---

## 14. `claude_code_agent` never completes a plain run: `timeout after 600s`, empty response, or `CLAUDE_CONFIG_DIR is misconfigured` (GitHub #132 / #133 / #134)

**Symptom**: A Claude Code Agent with nothing wired (no Memory / Context) either fails after 5 s with "No session JSONL appeared ... the CLAUDE_CONFIG_DIR is misconfigured", or burns the full `timeout_seconds` and reports an empty response, even though the CLI is installed and logged in. Memory-bound runs silently start a fresh conversation every batch. On a headless server every Login click leaves another `claude auth login` process parked for ten minutes (#129).

**Root cause**, three independent defects: (1) plain runs went through `AICliSession`, which spawned the CLI on a PTY and tailed the on-disk session JSONL, but never wrote the prompt to the process at all, and a PTY stdin makes the CLI reject `--input-format stream-json`; (2) `AICliSession` watched `<CLAUDE_CONFIG_DIR>/projects/<key>/` with a key regex that preserved dots, while the CLI replaces every non-`[a-zA-Z0-9-]` character with `-`, so `.opencompany` paths pointed at a directory the CLI never wrote to; (3) `ClaudeSessionPool._consume_stdout` returned silently on stdout EOF, so a child that died mid-turn never woke `send_turn`. Separately, memory continuity relied on `--continue`, which per the CLI reference skips sessions created non-interactively (`-p` / stream-json), i.e. every session the pool creates.

**Fix**: every Claude run routes through `ClaudeSessionPool` (plain pipes, stream-json, the same invocation the Agent SDK uses); unbound runs get an ephemeral session per task keyed by `<node_id>:<turn_execution_id>:<index>` and terminated after the batch. The project-key regex drops the dot. Stdout EOF sets `result_event` and `send_turn` reports `claude exited (code N) before emitting a result event`. A cold spawn mints `--session-id <uuid4>`, memory-bound runs pass `--resume <last_session_id>` instead of `--continue`. The login handler is single-flight with a strong task reference. The CLI version is pinned through `package_version` in `config/ai_cli_providers.json`. Locked by `tests/services/cli_agent/test_claude_pool_turn.py`, `test_claude_login_handlers.py`, and the updated `test_service.py` / `test_providers.py`.

---

## 15. `npm install -g` Fails With `externally-managed-environment` (Ubuntu 24.04+)

**Historical note (bun channel)**: the install channel is now `bun add -g @zeenie-ai/opencompany`, which runs no lifecycle scripts; the same provisioning (`scripts/install.js`) runs on the first `company` command instead, or eagerly from `install.sh` / `install.ps1`, so this failure would surface there rather than in an npm postinstall. The fix below still applies unchanged, and the installer scripts install uv themselves before provisioning.

**Symptom**: `npm install -g @zeenie-ai/opencompany` (the install channel at the time) aborted inside the postinstall with:
```
Installing uv via pip...
error: externally-managed-environment
```
Seen on Ubuntu 24.04 (EC2 `ubuntu-noble` AMI). `python3 -m ensurepip` also fails there with `No module named ensurepip` because Debian ships pip as a separate package.

**Root cause**: `scripts/install.js` installed uv with `python3 -m pip install uv` and had no other path. PEP 668 marks the distro Python as externally managed, so the system pip refuses every install outside a venv.

**Fix** (shipped after 0.1.1): `installUv` tries pip first and, on failure, runs uv's official standalone installer (`curl -LsSf https://astral.sh/uv/install.sh | sh`, `irm https://astral.sh/uv/install.ps1 | iex` on Windows) and prepends `~/.local/bin` to `PATH` for the rest of the install. On a 0.1.1 install, run that installer yourself first; the provisioning step then finds `uv` and continues.

---

## 16. `company start` Says `python: not found` After a `sudo npm install -g`

**Symptom**: The global install completes, but `company start` as the login user prints:
```
> @zeenie-ai/opencompany@0.1.1 start
> python -m cli start
sh: 1: python: not found
```
Seen on Ubuntu 26.04 (system Python 3.14).

**Historical note (bun channel)**: this failure mode belonged to the npm channel. `bun add -g` installs as the login user (shim `~/.bun/bin/company`; the package itself sits wherever bun keeps its global packages, which `company provision` never needs to know) — there is no global prefix to make writable and no reason to reach for `sudo`, so the venvs, the uv-managed Python and `~/.opencompany` belong to the login user by construction. `install.sh` / `install.ps1` run `bun add -g` as the invoking user and, when npm happens to be present, evict a legacy npm install of the scoped package or `machinaos` (the shims would otherwise shadow the bun one). A leftover root install from the npm era is still removed with `sudo npm uninstall -g @zeenie-ai/opencompany && sudo rm -rf /root/.opencompany`.

**Root cause** (npm era): `server/pyproject.toml` pins `requires-python = ">=3.11,<3.13"`. When the system Python falls outside that range, `uv sync` downloads a managed CPython 3.12 into the invoking user's `~/.local/share/uv/python/` and both `server/.venv` and `.cli-venv` symlink into it. With `sudo npm install -g` that user was root, and `/root` is mode 700, so the venv interpreters were unusable by anyone else. `bin/cli.js` then fell back to the script-runner path, which needs a bare `python` on `PATH`; Ubuntu has only `python3`.

**Fix** (npm era): do the global install without `sudo`, using a user-writable npm prefix (Ubuntu 26.04 has no `python3.12` apt package to fall back on):
```bash
npm config set prefix ~/.npm-global      # npm era only; bun add -g is user-owned under ~/.bun
echo 'export PATH=$HOME/.npm-global/bin:$PATH' >> ~/.bashrc && source ~/.bashrc
npm install -g @zeenie-ai/opencompany
company start
```
uv then downloads its 3.12 into `~/.local/share/uv`, the venvs are usable by the login user, and data lands in `~/.opencompany`. Verified on an EC2 t3a.small running Ubuntu 26.04, and end to end on a fresh Ubuntu 24.04 t3.micro through `install.sh`. The bun channel keeps the same rule — install as the login user, never `sudo` — and gets the user-owned layout for free.

---

## 17. Backend OOM-Killed in a Loop on Small VMs (512 MB)

**Symptom**: `company start` comes up and `/health` answers, then within a minute the machine stops responding; `dmesg` shows `Out of memory: Killed process ... (python)` repeatedly. Seen on an EC2 t2.nano (451 MB usable, no swap).

**Root cause**: The idle footprint is roughly 200 MB for the uvicorn backend plus 150 MB for the Temporal dev server it spawns, on top of the OS baseline (about 220 MB on a stock Ubuntu cloud image). The supervisor restarts the killed backend, so the box thrashes until it is rebooted.

**Fix**: Use at least 1 GB of RAM. Measured on a t2.micro (951 MB): backend 197 MB RSS, Temporal 156 MB, about 300 MB still available after two minutes, zero OOM kills.

---

## 18. Claude Code Agent ignores its wired Context node: `context=no` in the log, a fresh session per chat message, zero rows in `agent_conversations`

**Symptom**: A deployed workflow has a Context node wired to `input-context` on a `claude_code_agent` (or `rlm_agent`). Every chat message starts a brand-new claude session, the Context panel never shows a conversation, and the node log prints `[Claude Code] Collected: ... context=no` even though the edge exists. `agent_conversations` stays empty across generations.

**Root cause**: `MachinaWorkflow` stamps `generation`, `graphVersion`, `context_execution_id` and `context_session_id` on each node's activity context, but the per-type activity wrapper in `services/plugin/base.py::as_activity` rebuilds the node context by calling `workflow_service.execute_node` with a fixed argument list and forwards only an allowlist of extra keys. The conversation-scope keys were not on it. The Context descriptor builder (`nodes/context/_descriptor.py`) returns `None` when `generation` is 0, and the edge walker treats `None` as "this edge contributes nothing", so the Context edge was silently dropped before the bridge ever ran. Native agents were unaffected because they run as an `AgentWorkflow` child that reads `generation` from its own payload; the nodes that always take the activity path were the ones affected. The in-process adapter `WorkflowService._execute_node_adapter` dropped the same keys.

**Fix**: both handoffs now forward `generation`, `graphVersion`, `root_execution_id`, `context_execution_id`, `context_session_id` and `data_scope_id` as extras. Locked by `tests/temporal/test_context_scope_forwarding.py`. With the descriptor intact the pool key is `(workflow_id, agent_node_id, generation)`, so chat messages within a deployment reuse one warm claude process and each turn is recorded in the Context store.

---

## 19. JS/TS Executor Fails on a Fresh npm Install: `Cannot find package 'express'`

**Historical note (moot on the bun channel)**: the sidecar is now built with `bun build src/index.ts --target=bun --outfile=dist/index.js`, a self-contained bundle with `express` inlined, and runs as `bun dist/index.js` (`nodes/code/_runtime.py` via `core/js_runtime.py`). There is no runtime dependency left to resolve, so the root `express` dependency this fix added has been removed again. Kept for the record.

**Symptom** (npm era): On a machine set up with `npm install -g @zeenie-ai/opencompany`, the first JavaScript or TypeScript executor node run failed with `Node.js executor did not become ready`, and the sidecar log showed `Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'express' imported from .../server/nodejs/dist/index.js`.

**Root cause** (npm era): The sidecar bundle was built with esbuild's `--packages=external`, so Express stayed a runtime dependency, but it was declared only in `server/nodejs/package.json`. The package excludes every `node_modules`, and `nodes/code/_runtime.py` only checks that `dist/index.js` exists, so nothing ever installed Express on an npm-installed copy. Source checkouts never saw it because `bun install` provisions the workspace.

**Fix** (npm era): `express` was declared in the root `package.json` `dependencies`, so npm installed it beside the package and Node resolved it from `server/nodejs/dist` by walking up to the package root. Superseded by the `--target=bun` bundle above.

## 20. Graceful backend shutdown hangs, then the supervisor tree-kills it (Temporal / node / edgymeow orphaned)

**Historical browser implementation:** the `_service.py` and agent-browser
driver named below have been retired. The current
[native browser runtime](./browser.md) owns managed Chrome, CLI daemons and
live-stream tasks. This incident remains relevant to the rule that shutdown
must never lazily install or start a runtime.

**Symptom**: `company stop`, Ctrl+C, or the desktop app's quit takes the full grace window and ends in a tree-kill. With the lifespan markers (`Lifespan shutdown: ...` in stdout) the log stops after `proxy stopped`, or never prints `Lifespan shutdown begin` at all.

**Root causes** (two, found while building the desktop shell; the CLI's 5 s grace + tree-kill had masked both):

1. `nodes/browser/_service.py::shutdown_browser_service` called `get_browser_service()`, which lazily ran the `agent-browser@latest` install on first call (`npm install` at the time; that retired driver later used `bun add`) — so a process that never used the browser ran a network install *at teardown*, synchronously on the event loop, and every graceful exit wedged there.
2. uvicorn's `timeout_graceful_shutdown` defaults to `None`: it waits forever for open connections and in-flight handler tasks before sending the lifespan shutdown. A browser WebSocket that disappears with a TCP reset (the tab or window closing) can leave its handler task lingering, and the lifespan teardown — the part that reaps the child daemons — is never reached.

**Fix**: the browser hook reads its module singleton and closes only a service that was actually created; every plugin shutdown hook now runs under `HOOK_TIMEOUT_SECONDS` (10 s, `services/plugin/shutdown_hooks.py`) and names the offender at WARNING; `company serve` / `company start` and the desktop shell pass `--timeout-graceful-shutdown 5` (`cli/_common.py::UVICORN_GRACEFUL_SHUTDOWN_SECONDS`). Note that the hook timeout cannot interrupt a hook that blocks the loop synchronously — keep hooks async.

## 21. Electron boots as plain Node: `Cannot read properties of undefined (reading 'isPackaged')` / Playwright `Process failed to launch!`

**Symptom**: Launching the desktop shell (or its Playwright smoke) from a terminal inside VS Code, Claude Code or another Electron-hosted editor crashes immediately with `electron.app` undefined; Playwright only reports `Process failed to launch!`.

**Root cause**: Editor-hosted terminals export `ELECTRON_RUN_AS_NODE=1`. Electron honours it and starts as a bare Node runtime, so `require("electron")` returns the binary path string instead of the API.

**Fix**: `unset ELECTRON_RUN_AS_NODE` before launching Electron by hand; `desktop/tests/e2e/smoke.spec.ts` strips it from the environment it passes to `electron.launch`, and `desktop/src/main/env.ts` strips it from the backend's environment too.

## 22. Desktop runtime extraction fails on Windows: `tar: This does not look like a tar archive` / `Cannot connect to D: resolve failed`

**Symptom**: `bun run stage` / `bun run fetch-runtimes` fails while extracting the uv zip or a Node archive.

**Root cause**: The `tar` first on PATH in a Git Bash shell is Git for Windows' GNU tar, which cannot read `.zip` and parses `D:\...` as `host:path`. The system bsdtar at `%SystemRoot%\System32\tar.exe` handles zip, tar.gz and tar.xz.

**Fix**: `desktop/scripts/_lib.ts::tarBinary()` prefers the System32 tar on Windows and always passes the archive as a path relative to the extraction directory so no argument carries a drive colon.

## 23. `bun add -g` fails with `InvalidNPMLockfile: failed to migrate lockfile: 'package-lock.json'`, or `company` lands in `~/node_modules`

**Symptom**: `bun add -g @zeenie-ai/opencompany` aborts with `InvalidNPMLockfile: failed to migrate lockfile: 'package-lock.json'`; or it succeeds but `bun pm ls -g` reports the root as your home directory and the package sits in `~/node_modules/@zeenie-ai/opencompany`; or `company` is not found in the same shell after the install.

**Root cause**: bun resolves the "project" for a global install by walking up from its global directory (`$BUN_INSTALL/install/global`, default `~/.bun/install/global`) until it meets a `package.json`. On a fresh bun that directory is empty, so the walk continues into `$HOME`. A stray `package.json` or `package-lock.json` there (typically an old `npm init` / `npm install` run in the home directory; the lockfile is an empty `{"name": "<user>", "lockfileVersion": 3, "packages": {}}`) becomes the global project: the package is installed into `~/node_modules`, and when only the npm lockfile is present bun tries to migrate it and fails. Seen on Windows with bun 1.4.0, where the leftover pair had sat in the profile since 2025. The not-found variant is separate: bun's global bin dir (`bun pm bin -g`, `~/.bun/bin`) is not on the PATH of a shell opened before the bun installer ran.

**Fix**: delete the stray `package.json`, `package-lock.json`, `bun.lock` and `node_modules` from your home directory (after checking they are not a real project), then give bun's global directory its own manifest and re-run the install:

```bash
mkdir -p ~/.bun/install/global
[ -f ~/.bun/install/global/package.json ] || echo '{ "private": true }' > ~/.bun/install/global/package.json
bun add -g @zeenie-ai/opencompany
```

`install.sh`, `install.ps1` and the cloud-init templates seed that manifest before `bun add -g`, so the installer path never hits this. For the PATH variant open a new shell, or `export PATH="$HOME/.bun/bin:$PATH"` (`$env:USERPROFILE\.bun\bin` on Windows). Nothing in OpenCompany depends on where the global package lives: `company provision` runs from the shim's own package root, `uninstall.sh` uses `bun remove -g` rather than listing, and the installers address the shim through `bun pm bin -g`.

## 24. GitHub Packages publish fails with `missing authentication` although the job wrote a token for `npm.pkg.github.com`

**Symptom**: The `Publish to GitHub Packages` job of `release.yml` packs the tarball and then aborts with `error: missing authentication (run bunx npm login)`, while `Publish to npm` in the same run succeeds. First seen on v0.2.0, the first release published by bun.

**Root cause**: Two bun 1.4 behaviours. `bun publish` ignores `publishConfig.registry` in package.json, so the `bun -e` rewrite the job used to do was inert and the publish went to the default registry, npmjs, where that job holds no token. And bun keeps an `.npmrc` `//host/:_authToken` line only when `host` is the registry that same file points at (the default `registry=` or a scoped `@scope:registry=`) at parse time; `--registry` on the command line comes too late, so a token for `npm.pkg.github.com` on its own is dropped whichever of `~/.npmrc` or the project `.npmrc` carries it. Verified with `bun publish --dry-run`: with only `publishConfig.registry` it prints `Registry: https://registry.npmjs.org/`; with `--registry https://npm.pkg.github.com/` and the token in either file it still reports `missing authentication`; with `registry=https://npm.pkg.github.com/` or `@zeenie-ai:registry=https://npm.pkg.github.com/` next to the token it prints `Registry: https://npm.pkg.github.com/` and succeeds.

**Fix**: The job writes the scope route and the token into one `~/.npmrc` (`@zeenie-ai:registry=https://npm.pkg.github.com/` plus `//npm.pkg.github.com/:_authToken=<GITHUB_TOKEN>`) and runs a plain `bun publish`; the package.json rewrite is gone. To publish a mirror that failed this way, dispatch the Release workflow with `tag` set to the release and `registries` set to `github-packages`; the job checks out that tag. Locked by `test_github_packages_release_routes_the_scope_through_npmrc` in `cli/tests/test_release_pipeline_config.py`.

## 25. OPEN: `company start` / `company serve` from a `bun add -g` install stops with `Project not built. Run "company build" first.`

**Status**: open in 0.2.0 and 0.2.1 (found 2026-09-12 by running the README steps in a clean Ubuntu 24.04 container; the fix is small but has not shipped). Affects every registry install, including the installer scripts and the GCP / AWS VM templates, which end in `company serve`. The desktop app is unaffected.

**Symptom**: `bun add -g @zeenie-ai/opencompany` (or `install.sh` / `install.ps1`) provisions the Python side fine, then the first `company start` prints `Error: Project not built. Run "company build" first.` and exits.

**Root cause**: `cli/buildenv.py::validate_build` requires a `node_modules/` directory next to the package for every verb. That is an npm-era layout assumption: `npm install -g` nested a package's dependencies under the package, while bun keeps a global package's dependencies in its own global tree, so the directory never exists. Nothing at runtime needs it: `company start` is uvicorn plus the built SPA, and the JS executor sidecar is a self-contained bundle. Only `company dev` (Vite) genuinely needs `node_modules`.

**Workaround**: run `company build` once from the installed package (it runs `bun install` there, which creates the directory), then `company start`. **Fix, when shipped**: require `node_modules` only for `dev`; keep the server venv and the built client as the check for `start` / `serve`.

## 26. OPEN: JS / TS executor nodes fail on a registry install because the sidecar bundle is not in the tarball

**Status**: open in 0.2.0 and 0.2.1. `tar -tzf` of the published tarball lists `client/dist/` but no `server/nodejs/dist/`; the npm-era 0.1.1 tarball had it.

**Symptom**: `javascriptExecutor` / `typescriptExecutor` return the "JavaScript executor is unavailable" envelope on a `bun add -g` install; the backend log shows the sidecar spawn refusing because `server/nodejs/dist/index.js` does not exist.

**Root cause**: `bun pm pack` honours a nested `.gitignore` even for paths under an explicit root `files` entry. `server/nodejs/.gitignore` lists `dist/`, so the bundle `company build` had just produced is dropped at pack time (npm's packer kept it). Verified with `bun pm pack --dry-run`: adding `server/nodejs/dist/` to `files` alone changes nothing; removing the nested rule includes the file (the root `.gitignore`'s `dist` rule still keeps it untracked).

**Workaround**: `company build` from the installed package rebuilds the bundle. **Fix, when shipped**: drop `dist/` from `server/nodejs/.gitignore`, add `server/nodejs/dist/` to the root `files` list, and change `test_sidecar_dist_is_gitignored` to assert the root rule instead.

## 27. OPEN: `install.sh` on a bare box fails at `error: unzip is required to install bun`

**Status**: open in 0.2.1. Seen on a bare Ubuntu 24.04 container; Ubuntu cloud images usually ship `unzip`, minimal images and Debian netinst do not.

**Root cause**: bun's own installer refuses to run without `unzip`, and `install.sh` hands off to it without checking. The script also calls `sudo` unconditionally for apt, which does not exist in containers that run as root.

**Workaround**: `apt-get install -y unzip` (or the distro equivalent) before the one-liner. **Fix, when shipped**: install `unzip` via the detected package manager before calling bun's installer, and run package-manager commands directly when already root.

## 28. Every chat message fails with `ConversationTooLarge` after an agent made a few large tool calls

**Symptom**: A deployed chat workflow whose AI Agent has a Context node answers a few turns, and each `agent.execute_llm_step` logs a Temporal `PayloadSizeWarning: [TMPRL1103] ... Size: 528134 bytes, Limit: 524288 bytes` as the input grows (1.4 MB by the sixth tool call). The next chat message fails in `agent.prepare_payload`, and so does every message after it:

```
ApplicationError: ConversationTooLarge: Stored conversation for agent '1:aiAgent:1' is 1429978 bytes (limit 1000000). Clear the conversation from the Context panel or lower the compaction threshold, then run again.
```

Seen 2026-09-05 with a `tikhubAction` tool whose results were about 400,000 characters each.

**Root cause**: four gaps lined up.
- Nothing bounded a tool result before it entered the transcript. Both agent loops appended the serialized result whole, and every later turn re-sent it.
- Compaction never fired. `AgentWorkflow` compared the running SUM of every step's token usage with 80% of the model's window (838,860 tokens for a 1,048,576-token Gemini model), while the limits actually being hit were in bytes: Temporal's payload sizes and the 1 MB seed cap.
- The LLM step saves `[...sent, assistant]` after each turn with no size check, so the 1.4 MB transcript was stored.
- The seed guard in `agent.prepare_payload` is a hard, non-retryable failure by design, because an agent that silently runs without its memory is worse. So one oversized row broke every later firing.

**Fix**:
- Each external tool result is cut to `TOOL_RESULT_MAX_CHARS` characters (default 100,000; per user under Settings > Tool Result Limit; `0` disables) before it enters the model's conversation, with a note telling the model to call the tool again with narrower parameters. Delegated agents' answers, skill loads and Task Manager results are never cut, and the tool's own result is untouched (`services/tool_output.py`, used by both loops).
- `AgentWorkflow` runs whose prepare-payload result records `context_pressure_version` 1 keep the transcript under a byte budget of three quarters of Temporal's payload warning (`services/temporal/agent_context_pressure.py`):
  - past the budget, results from earlier turns become a short placeholder, oldest first and external tools first;
  - the compaction gate measures the next request instead of a running sum;
  - a summary covers only the earlier turns and keeps the latest one verbatim;
  - a latest turn that alone overflows is cut to fit.

  Runs recorded before the key existed replay the original rules unchanged.
- `ConversationTooLarge` stays a hard failure. Its message now points at the Context panel and the Tool Result Limit, and a save over half the cap logs a WARNING.

Locked by `server/tests/services/test_tool_output.py`, `server/tests/temporal/test_agent_context_pressure.py`, `server/tests/temporal/test_agent_workflow_pressure.py` and `server/tests/temporal/test_prepare_payload_pressure.py`. Design record: [ARCHIVE/AGENT_COMPACTION_FIX_PLAN.md](./ARCHIVE/AGENT_COMPACTION_FIX_PLAN.md).

**Recovery for a row saved before the fix**: clear the conversation once from the Context panel (or Reset the deployment). New transcripts stay well under the cap.
