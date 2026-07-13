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

Pre-cutover OpenCompany spawned `claude -p --output-format stream-json`
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

### Code layout

Per the canonical plugin-folder pattern, every claude-specific
module lives under `server/nodes/agent/claude_code_agent/`. The
generic framework under `server/services/cli_agent/` owns only
the shared dispatcher + per-provider registries that the plugin
self-registers into on import.

```
server/nodes/agent/claude_code_agent/
├── __init__.py    # plugin class + 4 self-registration lines (provider, pool, materialiser, ws handlers)
├── _provider.py   # AnthropicClaudeProvider — interactive_argv emits the four VSCode-pattern flags
├── _pool.py       # ClaudeSessionPool — subprocess-based warm-process reuse
├── _skills.py     # materialise_skills — per-workflow SKILL.md materialisation + live diff
├── _oauth.py      # claude_binary_path, claude_auth_* — project-local install + the documented CLI subcommands
└── _handlers.py   # claude_code_login / claude_code_logout WS handlers

server/services/cli_agent/  (generic framework — shared by all CLI plugins)
├── factory.py     # register_provider / register_session_pool / register_skill_materialiser registries
├── service.py     # AICliService.run_batch — dispatcher; routes to pool when memory-bound (registry lookup)
├── session.py     # AICliSession — one-shot non-pooled path (still PTY on POSIX; out of scope)
├── mcp_server.py  # BatchContext + FastMCP bridge (shared by every CLI provider)
├── workflow_tools.py / lockfile.py / jsonl_watcher.py / config.py / protocol.py / types.py / _cli_auth.py
└── _handlers.py   # codex WS handlers only (will move once codex_agent migrates)
```

## argv shape

Built by [`AnthropicClaudeProvider.interactive_argv`](../server/nodes/agent/claude_code_agent/_provider.py).
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
  --allowedTools <csv>
  --permission-mode dontAsk
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

`--permission-mode dontAsk` is the documented mode for *"only
pre-approved tools, no prompts"* per
[`code.claude.com/docs/en/permission-modes`](https://code.claude.com/docs/en/permission-modes).
It gates `--allowedTools` strictly — anything not in the allowlist
returns a permission denial without surfacing a TUI prompt
(non-interactive automation has no human at the keyboard to click
"Allow"). The earlier `bypassPermissions` default *"skips the
permission layer entirely"* (same doc, §"Skip all checks"), which
turned `--allowedTools` / `--disallowedTools` into documentation-only
fields and let claude's built-in `Read` / `Edit` / `Bash` /
`Glob` / `Grep` / `Write` / `Skill` / `WebSearch` / `WebFetch` fire
regardless of wiring. `acceptEdits` was the prior config default but
would prompt for any non-Edit tool — hanging the headless agent.
`dontAsk` is the only mode that combines no-prompts with a strict
allowlist gate.

## Session pool — warm-process reuse

[`ClaudeSessionPool`](../server/nodes/agent/claude_code_agent/_pool.py) is
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
lifetime of its process — the JSON blob (token included) is frozen
into argv and can never be rotated without respawning. The pool
stashes the spawn-time token on
[`PooledClaudeSession.batch_token`](../server/nodes/agent/claude_code_agent/_pool.py)
so subsequent batches can find and update its bound `BatchContext`.

Token lifecycle, three phases:

1. **Cold spawn.** `AICliService.run_batch` issues a fresh token T,
   calls `register_batch(T, ctx)` (which adds T to `_active_tokens`
   and bumps FastMCP refcounts for each `ctx.connected_tools` entry
   via `expose_workflow_tools`), then passes T to `pool.acquire`. The
   pool spawns claude with T baked into `--mcp-config` argv and
   records `session.batch_token = T`.
2. **Warm reuse with a changed surface.** A subsequent batch issues
   a NEW token T_new and registers `ctx_new` against it. Pool
   `acquire` detects warm reuse and calls
   [`rebind_batch`](../server/services/cli_agent/mcp_server.py)
   on T (the persistent token claude bakes into MCP requests),
   transplanting `ctx_new`'s `connected_tools` /
   `connected_skill_names` / `allowed_credentials` /
   `workspace_dir` onto T's `BatchContext` in place. The diff is
   applied to FastMCP — `unexpose_workflow_tools` decrements
   refcounts for tools dropped between batches (so a disconnected
   `duckduckgoSearch` falls off the FastMCP tool list when its
   refcount hits zero), `expose_workflow_tools` increments for
   added tools. T_new is then immediately unregistered (it would
   sit orphaned otherwise — claude's argv never references it).
3. **Termination.** `_terminate_locked` calls `unregister_batch(T)`
   so the dying subprocess's FastMCP refcounts drain cleanly on
   pool eviction / `pool.clear` / `shutdown_all`. Closes the
   long-standing leak where every pooled run permanently incremented
   the refcount of every wired tool.

The per-handler scope check in
[`workflow_tools._build_handler`](../server/services/cli_agent/workflow_tools.py)
reads `ctx.connected_tools` at call time — so even if claude's
frozen `--allowedTools` argv still lists a tool that's since been
disconnected, the handler returns
`{"error": "...not connected to this batch", "status": 403}`.
Combined with `--permission-mode dontAsk` (strict allowlist gate on
the claude side) this gives a double-lock: the tool surface visible
to claude is what the batch wired, no more no less, even across
warm-reuse turns.

## CloudEvents broadcasts

Four typed `WorkflowEvent` factories on
[`server/services/events/envelope.py`](../server/services/events/envelope.py),
fired via the corresponding `broadcaster.broadcast_claude_session_*`
methods on
[`server/services/status_broadcaster.py`](../server/services/status_broadcaster.py):

| Event type | When fired |
|---|---|
| `com.opencompany.claude.session.spawned` | Cold spawn (new subprocess) |
| `com.opencompany.claude.session.cleared` | Explicit `pool.clear` → new UUID on next acquire |
| `com.opencompany.claude.session.terminated` | Pool terminate (reason: `idle` / `crashed` / `evicted` / `shutdown` / `explicit`) |
| `com.opencompany.claude.session.usage` | Each `result` event (per-turn cost + tokens + duration + num_turns) |

The usage event is the source of truth for the simpleMemory usage panel
— `/usage` is TUI-only plain text per Anthropic and not parseable.
Frontend listeners switch on `envelope.type` and route the inner `data`
payload (`memory_node_id`, `session_uuid`, cost, tokens, …) into the
store that renders the panel.

## Tools and skills

**Tools (MCP) — strict allowlist, no claude built-ins.** Spawn argv
carries `--mcp-config <json>` (HTTP transport, bearer header) +
`--strict-mcp-config` so the spawned claude only loads OpenCompany's
FastMCP server. The `mcpServers.opencompany` block sets `alwaysLoad:
true` to opt out of MCP tool-search deferral so all
`mcp__opencompany__*` tools enter context at session start.
`--allowedTools` is the explicit allowlist, and it does NOT include
claude's built-in escape hatches (`Read`, `Edit`, `Bash`, `Glob`,
`Grep`, `Write`, `WebSearch`, `WebFetch`) — every workflow already
wires the equivalents (`fileRead`, `fileModify`, `fsSearch`,
`shell`, `browser`, `perplexitySearch`, …) as MCP tools, and the
built-ins were the leakage path that let the agent invoke
capabilities the workflow didn't explicitly grant. The default
allowlist is:

- `mcp__opencompany__<node_type>` per node connected to the agent's
  `input-tools` handle (LLM sees their typed Pydantic schemas via
  FastMCP's `tools/list` reflection on the plugin `Params`).
- The claude built-in `Skill` — **conditional**, present iff at
  least one skill is wired through the agent's `input-skill`
  handle. Paired with the `materialise_skills` helper (below)
  which writes the connected SKILL.md trees under
  `<cwd>/.claude/skills/` so the built-in skill loader has
  something to discover. The one exception to the
  "no claude built-ins" rule, because the alternative (forcing the
  agent to load every skill through the `getSkill` MCP round-trip)
  is materially worse UX for the common case where the workflow
  pre-wired which skills should be on the table.
- The five OpenCompany MCP infrastructure tools — `getWorkspaceFiles`,
  `listSkills`, `getSkill`, `getCredential`, `broadcastLog` — which
  are how the agent reads its workspace, discovers connected skills
  (when no wiring is present), fetches scoped credentials, and
  surfaces intermediate progress to the FE.

Callers wanting specific claude built-ins back in opt in per-task via
`ClaudeTaskSpec.allowed_tools` (the field is honored verbatim — no
auto-merge with workflow tools). Default value: empty string.
Combined with `--permission-mode dontAsk`, the strict allowlist is
actually enforced (see "argv shape" above for why `bypassPermissions`
was the wrong default).

**Skills — per-workflow workspace, live-watched, diff-based.**
[`nodes/agent/claude_code_agent/_skills.py::materialise_skills`](../server/nodes/agent/claude_code_agent/_skills.py)
writes one `SKILL.md` tree per connected-and-enabled skill under
`<workspace_dir>/.claude/skills/<name>/`. The workspace dir
(`~/.opencompany/workspaces/<workflow_slug>/` — Wave 14 keys it by the
human-readable slug, not the UUID id) is already passed via
`--add-dir`, and per the skills spec's
[Automatic discovery from parent and nested directories](https://code.claude.com/docs/en/skills#automatic-discovery-from-parent-and-nested-directories)
rule, claude scans `.claude/skills/` inside every `--add-dir` path.
Two properties fall out:

- **Per-workflow isolation.** Workflow A's wired skills never bleed
  into workflow B's subprocess even when both spawn with
  `cwd=repo_root`. The workspace dir is unique per workflow.
- **Live add/remove.** Claude live-watches the skills tree (same
  skills spec, [Live change detection](https://code.claude.com/docs/en/skills#live-change-detection)),
  so warm-reuse turns can toggle skills without respawning. Pool's
  `acquire` calls `materialise_skills(workspace_dir, new_set,
  previous_skill_names=session.materialised_skills)` to apply just
  the delta — `rmtree`'d skills disappear from claude's registry,
  newly-written ones become invocable. Same UX win as the MCP tool
  rebind.

Filesystem skills (declared under `server/skills/<group>/<name>/`)
are copied wholesale via `shutil.copytree` so `scripts/` and
`references/` subdirs survive. Database skills (user-created in
the UI) reconstruct the frontmatter (`name`, `description`,
`allowed-tools`, `metadata`) + the markdown body. Failure modes
are non-fatal: a skill that fails to load or write logs at WARN
and is skipped — the spawn continues. Same helper is called from
`AICliSession._pre_spawn` (non-pool path) and
`ClaudeSessionPool._spawn` (pool path), so behaviour is uniform
across the two transports.

**Migration note.** Pre-cutover code wrote SKILL.md trees into
`<repo_root>/.claude/skills/` so they accumulated in the user's
actual repo. The repo's `.claude/` is gitignored but visible;
future runs only write into per-workflow workspaces. Run
`rm -rf .claude/skills/` once from the repo root to clean
accumulated trees — we deliberately do NOT auto-prune because
the repo's `.claude/` may contain user-authored skills outside
the OpenCompany registry.

**Why filesystem (not MCP).** We surveyed alternatives: MCP
`resources`/`prompts` require explicit `@mention`/`/command`
invocation by the user, not auto-loaded into context. Hooks fire
after skill discovery, not before. `settings.json` doesn't expose
per-session skill paths. Anthropic's own
[Agent SDK](https://code.claude.com/docs/en/agent-sdk/skills) uses
the same filesystem-first pattern. There is no programmatic
skill-injection channel — workspace-dir + live-watch is canonical.

**Workspace dir — routed via `--add-dir`.**
[`AICliService.run_batch`](../server/services/cli_agent/service.py)
splices the per-workflow workspace
(`~/.opencompany/workspaces/<workflow_slug>/`, injected into `ctx.raw` by
`workflow.py:_get_workspace_dir`) into each task's `add_dir` list
right after `task_list` is built — `interactive_argv` already emits
`--add-dir <path>` per entry. Runs BEFORE the pool/non-pool branch
split so both warm-subprocess and one-shot paths get it. Without
this, the workspace is invisible to claude: memory-bound runs spawn
with `cwd=repo_root` (stable for `--continue`'s `project_key`
resolution) and non-memory runs with `cwd=worktree`, neither of which
sees files dropped by upstream nodes (`fileDownloader`,
`documentParser`, code executors, etc.). Mirrors the ai_agent
pattern ([`services/ai.py:1899`](../server/services/ai.py) —
`config['workspace_dir'] = context.get('workspace_dir', '')`) but
uses claude's native `--add-dir` instead of MCP-tool-config injection
because claude has its own filesystem tools (`Read`, `Edit`, `Glob`,
`Grep`, `Write`, `Bash`) rather than MCP-injected ones. Verified
end-to-end: a marker file dropped into a workspace is reachable via
claude's `Read` tool inside a pooled turn.

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
