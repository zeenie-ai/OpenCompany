# Claude Code interactive mode (subprocess + stream-json)

> **TL;DR.** `ClaudeSessionPool` keeps a warm `claude` subprocess per
> `simpleMemory.node_id` and drives it with the same flags Anthropic's
> own VSCode extension uses — stdio pipes, `--output-format stream-json
> --input-format stream-json --verbose --ide`. Multi-turn happens by
> writing newline-delimited JSON to `proc.stdin` of the long-lived
> subprocess. Events arrive on `proc.stdout`. The on-disk JSONL is no
> longer the runtime contract — claude writes it for `--continue` /
> `--resume` persistence but the `result` event is stdout-only in
> stream-json mode.

## Why we cut over from `-p`

Pre-cutover MachinaOs spawned `claude -p --output-format stream-json`
and parsed NDJSON events on stdout. Two reasons to leave `-p`:

1. **Anthropic billing change (2026-06-15).** Per
   [`code.claude.com/docs/en/headless`](https://code.claude.com/docs/en/headless),
   subscription plans bill `claude -p` and Agent-SDK usage from a
   separate "monthly Agent SDK credit." The interactive entrypoint
   (`claude-vscode`, not `sdk-cli`) stays on the subscription's
   interactive bucket. Dropping `-p` puts us in the user's interactive
   bucket when they supply their own API key.
2. **Match how Anthropic itself drives `claude` programmatically.** The
   VSCode extension at
   `C:/Users/Tgroh/.vscode/extensions/anthropic.claude-code-2.1.140-win32-x64/extension.js:156`
   spawns claude with exactly four flags (`--output-format stream-json
   --input-format stream-json --verbose --ide`) and never emits `-p`.
   That's the wire we reuse.

## Architecture

One transport (`asyncio.create_subprocess_exec` with `stdin/stdout/stderr=PIPE`),
one protocol (line-delimited stream-json events on stdout). No PTY.
A background `stdout_reader_task` parses each line and dispatches via
`_handle_stream_event`.

| Layer | Mechanism |
|---|---|
| **Transport** | `asyncio.subprocess.Process` with stdio pipes — same on POSIX + Windows |
| **Send turn** | `proc.stdin.write(json.dumps({"type":"user","message":{"role":"user","content":...}}) + "\n")` |
| **Read events** | `proc.stdout.readline()` loop → `json.loads(line)` → `_handle_stream_event` |
| **Completion detect** | stream-json event where `type == "result"` (set via `provider.is_final_event`) |
| **Context reset (`/clear` equivalent)** | kill subprocess + drop captured UUID — stream-json input has no slash-command path (confirmed in VSCode extension source) |
| **Crash recovery** | `acquire()` checks `process.returncode`; respawns with `--resume <captured_uuid>` so the same on-disk JSONL keeps growing |

### Code layout (`server/services/cli_agent/`)

```
cli_agent/
├── providers/
│   └── anthropic_claude.py   # interactive_argv — emits the four VSCode-pattern flags
├── jsonl_watcher.py          # still used by the non-pooled AICliSession; NOT used by the pool
├── session.py                # AICliSession — one-shot non-pooled path (PTY on POSIX; out of scope for this refactor)
├── session_pool.py           # ClaudeSessionPool — subprocess-based warm-process reuse
└── service.py                # AICliService.run_batch — entry point; routes to pool when memory-bound
```

## argv shape

Built by [`AnthropicClaudeProvider.interactive_argv`](../server/services/cli_agent/providers/anthropic_claude.py).
Stable head, task-driven tail:

```
claude
  --output-format stream-json
  --input-format stream-json
  --verbose
  --ide
  [--mcp-config <json> --strict-mcp-config]
  --model <name>
  [--resume <UUID> | --continue]
  [--allowedTools <csv>]
  --permission-mode bypassPermissions
  [--append-system-prompt <text>]
  [--effort low|medium|high]
  [--add-dir <path>]...
  [--disallowedTools <csv>]
  [--agent <name>]
```

Notable absences (intentional):

| Flag | Reason omitted |
|---|---|
| `-p` / `--print` | Drops us into the SDK billing bucket; the whole point of the cutover. |
| Positional prompt (`-- "<text>"`) | Prompt arrives via stdin in stream-json input mode; an argv positional would double-send the first turn. `interactive_argv`'s `include_prompt` parameter is kept for back-compat but ignored. |
| `--session-id <UUID>` | Rejected by claude in non-headless mode. We discover the UUID from the first event that carries `session_id`. |
| `--include-partial-messages`, `--include-hook-events`, `--max-turns`, `--max-budget-usd`, `--fallback-model` | All `-p`-only. `ClaudeTaskSpec` keeps the fields for back-compat; they're silently dropped here. External cost / turn caps are a Phase 2 follow-up. |

`--permission-mode bypassPermissions` is required for non-interactive
automation — there's no human at the keyboard to approve the per-tool
prompts `acceptEdits` would surface. Documented at
[`code.claude.com/docs/en/permission-modes`](https://code.claude.com/docs/en/permission-modes).

## Session pool — warm-process reuse

[`ClaudeSessionPool`](../server/services/cli_agent/session_pool.py) is
keyed by `simpleMemory.node_id`. Each entry is a
`PooledClaudeSession` carrying the live `asyncio.subprocess.Process`,
the captured session UUID (filled in from the first event that has
one), the MCP bearer token embedded in the spawn argv, a per-session
`asyncio.Lock`, an `asyncio.Event` signalled by the stdout reader on
`result`, and a per-turn event buffer.

| Operation | Behaviour |
|---|---|
| `acquire(memory_node_id, ...)` | Live + healthy entry → return as-is (warm reuse). Live + dead entry → drop, capture its UUID, spawn fresh with `--resume <UUID>` so the same JSONL keeps growing. No entry → cold spawn. At cap → LRU evict (skipping in-flight). |
| `send_turn(session, prompt, ...)` | Holds `session.lock`. Clears `result_event` + `events_this_turn`. Writes one stream-json line to `proc.stdin`, awaits `result_event` (timeout = 600s default). Returns a `SessionResult` built from the per-turn event buffer. Persists `result.session_id` onto `session.current_session_uuid`. |
| `clear(session, ...)` | Kill the subprocess, drop the captured UUID. Next `acquire` spawns fresh with no continuity flag (claude assigns a new UUID). Emits `claude.session.cleared`. |
| `release(session)` | Updates `last_used_at` so the reaper measures idle from now. |
| `terminate(memory_node_id)` | Force-drop a specific entry: close stdin → 2s grace → `proc.kill()` → cancel reader/drain tasks. |
| `shutdown_all()` | Stop the reaper + terminate every entry. Wire into FastAPI's lifespan `shutdown`. |

Lifecycle policy:

- **Idle TTL** 30 min (`_DEFAULT_IDLE_TTL`). Background reaper task
  terminates pooled sessions whose `last_used_at` exceeds the cap AND
  aren't currently locked.
- **Max size** 16 (`_DEFAULT_MAX_SIZE`). LRU eviction at cap (skipping
  in-flight entries).
- **Concurrency** — per-key `asyncio.Lock` serialises turns against
  the same pooled subprocess so two `send_turn` calls can't interleave
  their stream-json lines on stdin. `lock.locked()` doubles as the
  reaper's in-flight detector.

## Stream-json event dispatch

`_handle_stream_event` has three concerns:

1. **UUID capture.** Any event carrying `session_id` (or `sessionId`)
   seeds `session.current_session_uuid` if it isn't already set —
   `system/init` fires this for fresh spawns, `result` carries the
   authoritative value at turn end. Crash-recovery respawns read this
   field to emit `--resume <UUID>`.
2. **Per-turn buffering.** Every event is appended to
   `events_this_turn`. When `provider.is_final_event(event)` returns
   True (i.e. `event.type == "result"`), `result_event` is set and
   `send_turn` returns. `SessionResult` is built by
   `AnthropicClaudeProvider.event_to_session_result` over the buffered
   events — same shape `-p` used to produce.
3. **Native compaction forwarding.** `type == "system"` +
   `subtype == "compact_boundary"` events forward to
   `CompactionService.record(...)` via `_record_native_compaction` so
   the local-threshold path doesn't double-fire on claude's auto-
   compaction. Trigger metadata (`pre_tokens`, `trigger: auto|manual`)
   is preserved on the compaction record.

## Continuity across process restarts

The pool's continuity model is two-layered:

- **Intra-process (warm pool).** Within a live subprocess, every turn
  writes to the same `proc.stdin` and claude itself keeps the conversation
  in memory. No `--continue` / `--resume` needed — same session UUID
  flows across turns. Verified: turn 1 `13ac5819-...`, turn 2 same UUID.
- **Cross-process (cold spawn / crash recovery).** Cold spawns of a
  memory-bound run emit `--continue` (claude auto-finds the latest
  conversation under the cwd via `project_key`). If `acquire` finds the
  pooled subprocess has died, it captures the dead session's UUID
  before respawning and splices `--resume <UUID>` into the spec so the
  new subprocess continues writing to the same on-disk JSONL.

`ClaudeTaskSpec` carries `continue_session: bool` and
`resume_session_id: Optional[str]`. `resume_session_id` wins if both
are set. `simpleMemory.last_session_id` is display-only metadata —
`_persist_memory` writes it for the UI but the agent doesn't read it
back (continuity rides through `--continue` + the warm pool, not a
ferried UUID).

## MCP bearer token lifecycle

`claude` handles its own MCP authentication. We embed the bearer in
`--mcp-config` at spawn time; the spawned claude uses it for the
lifetime of its process. The pool stashes the token on
`PooledClaudeSession.bearer_token` at spawn time so warm-reuse callers
(`AICliService.run_batch`'s pool path) can re-register the batch
context against the SAME token claude already has in its argv. The pool
itself never issues / unregisters — that's `run_batch`'s job, with the
twist that for pooled sessions the token persists across batches and
orphans on pool eviction (bounded by `max_size`, acceptable for Phase 1).

## CloudEvents broadcasts

Four typed `WorkflowEvent` factories on
[`server/services/events/envelope.py`](../server/services/events/envelope.py),
fired via the corresponding `broadcaster.broadcast_claude_session_*`
methods on
[`server/services/status_broadcaster.py`](../server/services/status_broadcaster.py):

| Event type | When fired |
|---|---|
| `com.machinaos.claude.session.spawned` | Cold spawn (new subprocess) |
| `com.machinaos.claude.session.cleared` | Explicit `pool.clear` → new UUID on next acquire |
| `com.machinaos.claude.session.terminated` | Pool terminate (reason: `idle` / `crashed` / `evicted` / `shutdown` / `explicit`) |
| `com.machinaos.claude.session.usage` | Each `result` event (per-turn cost + tokens + duration + num_turns) |

The usage event is the source of truth for the simpleMemory usage panel
— `/usage` is TUI-only plain text per Anthropic and not parseable.
Frontend listeners switch on `envelope.type` and route the inner `data`
payload (`memory_node_id`, `session_uuid`, cost, tokens, …) into the
store that renders the panel.

## Tools and skills

**Tools (MCP) — unchanged from the `-p` era.** Spawn argv carries
`--mcp-config <json>` (HTTP transport, bearer header) + `--strict-mcp-config`
so the spawned claude only loads MachinaOs's FastMCP server. The
`mcpServers.machinaos` block sets `alwaysLoad: true` to opt out of MCP
tool-search deferral so all `mcp__machinaos__*` tools enter context at
session start. `--allowedTools` lists the explicit allowlist:
five built-ins (`Read,Edit,Bash,Glob,Grep,Write,Skill,WebSearch,WebFetch`),
every `mcp__machinaos__<connected_tool>` passed by the caller, plus the
five core MCP tools (`getWorkspaceFiles`, `listSkills`, `getSkill`,
`getCredential`, `broadcastLog`).

**Skills — known gap on the pool path.** `AICliSession._materialise_skills`
writes connected skills under `<cwd>/.claude/skills/` for the one-shot
non-pooled path. The pool path skips that materialisation; agents
discover skills via MCP `listSkills` / `getSkill` tools at runtime
instead. Fine for now, but pool-side skill materialisation is an open
follow-up.

## `/usage` and `/compact` semantics

- **`/usage`** — TUI-only plain text per Anthropic. Not parseable. We
  surface the same data (cost, tokens, duration, num_turns) via the
  `claude.session.usage` CloudEvent built from every `result` event.
- **`/clear`** — the stream-json input wire has no slash-command path
  (confirmed in the VSCode extension source). Context reset is
  `pool.clear(session)` — kill subprocess, drop UUID, next acquire
  spawns fresh.
- **`/compact`** — emits `system/compact_boundary` on stdout with
  `compact_metadata.pre_tokens` + `compact_metadata.trigger` (auto vs
  manual). `_handle_stream_event` forwards it to
  `CompactionService.record(...)` so the local-threshold path doesn't
  double-fire on claude's native auto-compaction.

## What changed (historical note)

The previous iteration of this doc described a PTY transport
(`pywinpty` on Windows, `ptyprocess` on POSIX) reading events from the
on-disk session JSONL via `JsonlWatcher` / `JsonlDirWatcher`. That path
was abandoned for the pool: `pywinpty`'s ConPTY emulation did not
deliver keystrokes to claude's Ink TUI on Windows (empirically
confirmed across four test variants). Stdio pipes + stream-json work
cross-platform and match the Anthropic-blessed pattern. The non-pooled
`AICliSession` still uses a PTY on POSIX for its one-shot
`AICliService.run_batch` path — out of scope for this refactor.
