# Claude Code interactive mode (in-process PTY + on-disk JSONL)

> **TL;DR.** MachinaOs drives the same interactive `claude` invocation a
> normal user runs at their terminal — not `claude -p`. A PTY keeps the
> TUI alive in-process; events come off the on-disk session JSONL,
> not stdout. A pool keyed by `simpleMemory.node_id` keeps the warm
> process across batches.

## Why we cut over from `-p`

Pre-cutover MachinaOs spawned `claude -p --output-format stream-json`
and parsed NDJSON events on stdout. Two-direction motivation:

1. **Match how users actually use Claude Code.** The headless path is
   sanctioned for automation, but most Claude Code users run the
   interactive TUI. Driving the same surface lets us inherit fixes /
   behavior changes the team prioritises for the human-facing mode.
2. **Anthropic billing change (2026-06-15).** Per
   [`code.claude.com/docs/en/headless`](https://code.claude.com/docs/en/headless),
   subscription plans now bill `claude -p` and Agent-SDK usage from a
   separate "monthly Agent SDK credit." Interactive usage stays on the
   subscription's interactive bucket. The cutover puts us in the
   user's interactive bucket when they supply their own API key.

The corollary: every protocol detail still works in interactive mode
because the on-disk JSONL is the same format `-p` wrote to stdout
(Claude Code CHANGELOG 2.1.101 / 2.1.126 — shared writer). The argv
flags we care about — `--mcp-config`, `--strict-mcp-config`,
`--allowedTools`, `--permission-mode`, `--append-system-prompt`,
`--resume`, `--model`, `--effort`, `--add-dir`, `--disallowedTools`,
`--agent` — all work in interactive mode.

## What we dropped

| Flag | Why dropped |
|---|---|
| `-p` / `--print` | The headless toggle itself. |
| `--output-format stream-json` | Only meaningful under `-p`. |
| `--verbose` | Only meaningful with `-p --output-format stream-json`. |
| `--include-partial-messages` | Only meaningful with stream-json. |
| `--include-hook-events` | Only meaningful with stream-json. The hooks fire regardless and we can detect them via JSONL. |
| `--max-turns` | `-p`-only. External counter is a Phase 2 follow-up. |
| `--max-budget-usd` | `-p`-only. External monitor on `result.total_cost_usd` is a Phase 2 follow-up. |
| `--session-id <UUID>` | No longer pre-mint UUIDs. First-run claude assigns its own; we discover it via the new JSONL filename. |
| `--fallback-model` | `-p`-only. |

`ClaudeTaskSpec` keeps the corresponding Pydantic fields for
back-compat; they're silently dropped in `interactive_argv`.

## Architecture — transport vs. protocol

Two orthogonal layers:

| Layer | macOS / Linux | Windows |
|---|---|---|
| **Transport** (PTY holds `claude` alive) | `ptyprocess>=0.7.0` | `pywinpty>=3.0.3` (Rust-backed via PyO3 + winpty-rs + Maturin) |
| **Protocol** (read events) | tail `<CLAUDE_CONFIG_DIR>/projects/<project_key>/<session>.jsonl` | identical |
| **Send turn** | `pty.write((text + '\r').encode())` (NOT close stdin — see [#15553](https://github.com/anthropics/claude-code/issues/15553)) | identical |
| **Send slash command** | `pty.write(b"/clear\r")` | identical |
| **Completion detect** | JSONL `result` entry | identical |
| **Resume across spawns** | `claude --resume <UUID>` | identical |

`pywinpty` v3.x is now production-grade (used by Jupyter Lab terminals
and Spyder's IPython console) so the historical reason to route Windows
through a Node.js sidecar (pywinpty flakiness) no longer holds. Both
backends run in-process behind a single `PtyTransport` Protocol; the
factory in [`server/services/cli_agent/transports/__init__.py`](../server/services/cli_agent/transports/__init__.py)
picks per `sys.platform`.

### Code layout (`server/services/cli_agent/`)

```
cli_agent/
├── providers/
│   └── anthropic_claude.py         # interactive_argv (replaces headless_argv)
├── transports/
│   ├── __init__.py                 # get_pty_transport() factory
│   ├── base.py                     # PtyTransport / PtyHandle Protocols
│   ├── posix.py                    # PosixPtyTransport (ptyprocess)
│   └── windows.py                  # WindowsPtyTransport (pywinpty)
├── jsonl_watcher.py                # JsonlWatcher (tail-f) + JsonlDirWatcher
├── session.py                      # AICliSession — one-shot non-pooled path
├── session_pool.py                 # ClaudeSessionPool — warm-process reuse
└── service.py                      # AICliService.run_batch + _run_pooled_turn
```

## Session pool — warm-process reuse

[`ClaudeSessionPool`](../server/services/cli_agent/session_pool.py) is
keyed by `simpleMemory.node_id`. Successive batches against the same
memory node reuse the same warm `claude` PTY:

- **Acquire (warm)** — returns the existing live session. **No
  implicit `/clear`** — the next prompt appends to claude's current
  JSONL so the conversation continues. This is the load-bearing
  invariant for `simpleMemory` continuity.
- **Acquire (cold)** — spawn fresh via `PtyTransport`, locate the
  newly-created JSONL by mtime, start a `JsonlWatcher` on it. Stash
  the MCP bearer token on the `PooledClaudeSession` so re-acquires
  use the same token the spawned claude already has in its argv.
- **Explicit `clear(session)`** — sends `/clear` to the PTY, awaits
  the new JSONL filename via the persistent `JsonlDirWatcher`, swaps
  the file watcher. Per
  [`claude-code#32871`](https://github.com/anthropics/claude-code/issues/32871),
  `/clear` mints a **new UUID** with a **new JSONL file** rather than
  clearing in-place. Pre-clear conversation stays resumable by its
  old UUID.

Lifecycle policy:

- **Idle TTL** 30 min (`_DEFAULT_IDLE_TTL`). Background reaper task
  terminates pooled sessions whose `last_used_at` exceeds the cap
  AND aren't currently locked.
- **Max size** 16 (`_DEFAULT_MAX_SIZE`). LRU eviction at cap.
- **Crash recovery** — `acquire` calls `pty_handle.is_alive()` and
  respawns transparently when the pooled PTY has died.
- **Concurrency** — per-key `asyncio.Lock` serialises turns against
  the same pooled session. `lock.locked()` doubles as the reaper's
  in-flight detector.
- **Shutdown** — `ClaudeSessionPool.shutdown_all()` is the target for
  FastAPI's lifespan `shutdown` event (TODO: wire from `main.py`).

## MCP bearer token lifecycle

`claude` handles its own MCP authentication. We embed the bearer in
`--mcp-config` at spawn time; the spawned claude uses it for the
lifetime of its process. The pool does NOT issue or unregister tokens
— the caller (`AICliService.run_batch`) owns the lifecycle, with the
twist that for pooled sessions the token persists with the warm PTY
across batches:

```python
# run_batch — non-pool path (unchanged)
token = issue_token()
register_batch(token, ctx)
try:
    results = await asyncio.gather(*(run_one(t) for t in tasks))
finally:
    unregister_batch(token)

# run_batch — pool path
token = issue_token()
register_batch(token, ctx)
try:
    result = await self._run_pooled_turn(..., mcp_bearer_token=token)
    # token stays embedded in the warm PTY for the next batch.
    # On pool eviction / shutdown the registration orphans (bounded
    # by max pool size, acceptable for Phase 1).
finally:
    if not use_pool:
        unregister_batch(token)
```

## CloudEvents broadcasts

Four typed `WorkflowEvent` factories on
[`server/services/events/envelope.py`](../server/services/events/envelope.py),
fired via the corresponding `broadcaster.broadcast_claude_session_*`
methods on
[`server/services/status_broadcaster.py`](../server/services/status_broadcaster.py):

| Event type | Wire key | When fired |
|---|---|---|
| `com.machinaos.claude.session.spawned` | `claude_session_event` | Cold spawn (new PTY) |
| `com.machinaos.claude.session.cleared` | `claude_session_event` | Explicit `pool.clear` → new UUID |
| `com.machinaos.claude.session.terminated` | `claude_session_event` | Pool terminate (reason: `idle` / `crashed` / `evicted` / `shutdown` / `explicit`) |
| `com.machinaos.claude.session.usage` | `claude_session_usage` | Each `result` event (per-turn cost + tokens) |

Frontend listeners should switch on `envelope.type` and route the
inner `data` payload (memory_node_id, session_uuid, cost, tokens, …)
into whichever store renders the simpleMemory usage panel.

## `/usage` and `/compact` semantics

Per the research:

- **`/usage`** — TUI-only plain text per Anthropic. Not parseable.
  The data it displays (cost, tokens, duration, num_turns) is already
  on every `result` JSONL event under `usage` + `total_cost_usd`. We
  surface it via the `claude.session.usage` CloudEvent instead.
- **`/clear`** — mints a new session UUID + new JSONL file (issue
  [#32871](https://github.com/anthropics/claude-code/issues/32871)).
  Pool exposes this as `pool.clear(session)` for explicit reset; not
  fired on warm acquire.
- **`/compact`** — emits `system/compact_boundary` with
  `compact_metadata.pre_tokens` + `compact_metadata.trigger` (auto vs
  manual). Both `AICliSession._on_jsonl_event` and
  `ClaudeSessionPool._handle_jsonl_event` forward it to
  `CompactionService.record(...)` so the local-threshold path doesn't
  double-fire.

Commands MachinaOs must **avoid** in PTY-driven automation (they open
interactive dialogs, kill the session, or open a browser):
`/feedback`, `/bug`, `/exit`, `/quit`, `/login`, `/logout`,
`/permissions`, `/agents`, `/mcp`, `/model` (with no arg), `/rewind`,
`/config`, `/install-github-app`, `/install-slack-app`, `/teleport`,
`/heapdump`.

## Memory bridge — unchanged

The `simpleMemory.last_session_id` ↔ `--resume <UUID>` contract works
identically in interactive mode because the on-disk JSONL is the
same format. The invariants:

- `memory_bound=True` makes the spawn cwd = `repo_root` so claude's
  `project_key` stays constant across batches.
- First run: claude assigns its own UUID; the pool / `AICliSession`
  discovers it via the new JSONL filename, saves it to
  `simpleMemory.last_session_id`.
- Subsequent runs (non-pooled): argv `--resume <last_session_id>`.
- Subsequent runs (pooled, warm): no respawn — the warm PTY continues
  in its current UUID.
- Subsequent runs (pooled, after `pool.clear`): a new UUID is captured
  on the next `result` event and persisted.
- Auto-clear-stale: if claude reports `No conversation found with
  session ID: <UUID>`, `_persist_memory` wipes the broken
  `last_session_id` so the next run falls into the first-run branch.

## Out-of-process pty-host (deferred upgrade path)

If `pywinpty` / `ptyprocess` stability becomes an issue (crashes
bringing down FastAPI), promote `PtyTransport` to an out-of-process
Python sidecar — VSCode's `ptyHostMain.ts` pattern, in Python.
Supervised by `machina/tree.py`'s Job Object, JSON-RPC over stdio.
The `PtyTransport` Protocol boundary makes the swap cheap (~1 day).
See VSCode issue
[#74620](https://github.com/microsoft/vscode/issues/74620) for the
rationale (heartbeat, flow control, isolation from main app).

## Open follow-ups

- **Cancellation path.** Pool sessions bypass
  `AICliService._active_sessions`, so `cancel_workflow` /
  `cancel_node` don't reach the pooled PTY. Add pool-in-flight
  tracking or hook cancel into `pool.terminate`.
- **Lifespan integration.** Wire `pool.shutdown_all()` to FastAPI's
  shutdown event from `main.py`. Currently the pool dies with the
  process; orphan PTY children are caught by `machina/tree.py` Job
  Object on Windows and `os.setsid` on POSIX.
- **Frontend usage panel.** `claude.session.usage` CloudEvents are
  flowing; the React hook + simpleMemory panel UI are the missing
  half (Step 7 of the implementation plan).
- **`--fork-session` UI.** `--fork-session` is the documented working
  fork primitive; surface a "Branch this conversation" `<ActionButton>`
  on simpleMemory + a `branches: List[{uuid, name, created_at}]`
  param. Avoids `/rewind` which has bug
  [#55347](https://github.com/anthropics/claude-code/issues/55347)
  (documented as fork but implemented as in-place mutation).
- **External cost cap / turn cap.** Replacements for the dropped
  `--max-budget-usd` and `--max-turns` flags. Aggregate
  `result.total_cost_usd` / count `assistant` events with
  `stop_reason: "tool_use"` and kill the PTY at the limit.
