# Known Errors & Troubleshooting

Documented root causes and fixes for errors encountered in OpenCompany development and production.

---

## 1. SQLAlchemy Import Hang (Windows)

**Symptom**: Backend hangs at startup with no output after `Importing DI container + all services...`. The process is alive but never binds port 5678. `import sqlalchemy` blocks indefinitely.

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

**Prevention** (`cli/commands/start.py`, `_sqlalchemy_preflight`): a preflight probe times `import sqlalchemy` in the server venv. If it exceeds 8 seconds, it fails fast with actionable remediation steps instead of letting uvicorn hang silently.

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
| `start_to_close_timeout` | 10 min | Maximum total time for an activity (workflow.py) |
| `heartbeat_timeout` | 2 min | Maximum gap between heartbeats before Temporal cancels (workflow.py) |
| `asyncio.wait_for` timeout | 30s | Periodic heartbeat interval in the WS read loop (activities.py) |
| `ws_connect heartbeat` | 30s | WebSocket protocol-level ping/pong keepalive (activities.py) |
| `receive_timeout` | None | No aiohttp-level hard cap (was 540s) |

---

## 4. WhatsApp RPC Timeout

**Symptom**: Backend logs show:
```
WhatsApp RPC timeout - Go service not responding at ws://localhost:5683/ws/rpc
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
GET http://localhost:5678/api/auth/status net::ERR_CONNECTION_REFUSED
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

**Root cause**: The version check at `install.js:59` uses `minor >= 12` with no upper bound:
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

Note the binary install path also moved this release: the claude CLI now lives in the shared OpenCompany npm tree at `<DATA_DIR>/packages/node_modules/.bin/claude[.cmd]` (was `<DATA_DIR>/claude/npm/...`). The fresh `npm install` triggered by that move is what pulled the 2.1.162 native binary that surfaced this bug — the path change was the trigger, the `stdin=PIPE` is the actual fix.

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

## 13. Failed Plugin Nodes Never Retried on Temporal / `errors: []` for a Failed Run

**Symptom**: A node that fails with a transient error (HTTP 503, timeout, connection refused) on a deployed workflow fails the run on its first attempt even though the plugin declares a three-attempt `retry_policy`, and the Temporal Web UI shows the activity as *completed*, not failed. `TemporalExecutor.execute_workflow` reports `errors: []` for the failed run. A user-correctable `NodeUserError` is indistinguishable in the history from an infrastructure crash, and on an `AgentWorkflow` tool call the model would see `{"error": "ActivityError: Activity task failed"}` instead of the plugin's message.

**Root cause**: `BaseNode.as_activity` returned the structured `{success: False}` envelope as a normal activity completion, so Temporal recorded success and never consulted the RetryPolicy; the legacy `execute_node_activity` did the same. Every per-plugin `retry_policy` and the `NON_RETRYABLE_ERROR_TYPES` list were dead configuration on the deployed path (the in-process `WorkflowExecutor` did retry on the envelope). Two follow-on defects hid it: `MachinaWorkflow._wait_any_complete` stringified the `ActivityError` ("Activity task failed") instead of reading its cause, and the executor read `result["error"]` while the workflow returns `errors`. Exceptions escaping the plugin (the payload-size guard raised while serialising a result) reached `NodeExecutor.execute` with no `error_type` at all.

**Fix** (PR #131 plus maintainer fix-ups): the envelope carries a source-classified `retryable` verdict (`services/plugin/retryability.py`); the activity boundary raises a typed `ApplicationError` with the envelope as `details[0]` and self-caps attempts from `activity.info()` (`services/temporal/_failures.py`); `BaseNode.effective_retry_policy()` gives triggers and mutating nodes one attempt; both workflows unwrap the cause with `activity_failure_envelope`; the `AgentWorkflow` tool call carries the plugin's policy under `machina-plugin-failure-retries-v1` (it previously had none, Temporal's unlimited default); the executor reads `errors`. Rollback: `TEMPORAL_PLUGIN_FAILURE_RETRIES=false`. Locked by `tests/temporal/test_activity_failure_boundary.py`, `tests/test_failure_classifier.py`, `tests/test_effective_retry_policy.py`, `tests/temporal/test_machina_failure_unwrap.py`, `tests/temporal/test_agent_tool_retry_policy.py`, and the tool-call scenario in `tests/temporal/test_agent_workflow_replay.py`.

**Verification**: a retried node shows attempt N with the `ApplicationError` type in the Temporal Web UI; the `executing` node-status broadcast carries `attempt` / `max_attempts` (and `last_error` on a non-final failure); the `activity_retry` WARN line fires once per re-dispatch. Expect more of those WARN lines than before: they now cover retryable plugin failures, not only worker crashes.
