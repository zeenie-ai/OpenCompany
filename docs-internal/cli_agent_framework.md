# AI CLI Agent Framework

Multi-instance, multi-provider runtime for AI CLI agents (Claude Code, Codex, Gemini). One workflow node spawns N parallel CLI sessions over a list of tasks, each isolated in its own git worktree, each able to call back into MachinaOs over MCP.

| Provider | Status | Login flow |
|---|---|---|
| Claude Code (`@anthropic-ai/claude-code`) | shipping | Local-install + spawn (`services/claude_oauth.py:initiate_claude_oauth`) |
| OpenAI Codex (`@openai/codex`) | shipping (no login flow yet) | User runs `codex login` manually; UI returns a graceful "not yet wired" error |
| Google Gemini (`@google/gemini-cli`) | v2 stub | factory raises `NotImplementedError` |

## Architecture

```
ClaudeCodeAgentNode (claude_code_agent)        CodexAgentNode (codex_agent)
        │ Params.tasks: list[ClaudeTaskSpec]            list[CodexTaskSpec]
        ▼
AICliService.run_batch(provider, tasks, *, node_id, workflow_id, workspace_dir)
        │ provider = create_cli_provider(name)  ("claude" | "codex" | "gemini"→NotImpl)
        │ allocate per-batch bearer token; register BatchContext in MCP token registry
        │ asyncio.gather under Semaphore(5)
        │
        ├──► AICliSession_0 (BaseProcessSupervisor + ClaudeProvider + ClaudeTaskSpec)
        │       _pre_spawn():   git worktree add  +  write ~/.claude/ide/<pid>.lock
        │       _do_start():    anyio.open_process + NDJSON stdout/stderr consumers
        │                       env: CLAUDE_IDE_LOCK + MACHINA_PARENT_RUN_ID
        │       wait_for_completion(timeout)
        │       cleanup():      stop()=terminate_then_kill(5s) + rm lockfile + worktree remove
        │
        └──► AICliSession_N (CodexProvider + CodexTaskSpec)
                 (no lockfile yet — Codex CLI doesn't honor it; written when upstream supports it)
        │
        │  ◄────  CLI calls back via MCP/HTTP at /mcp/ide
        │           Authorization: Bearer <batch-token>  (per-batch isolation)
        │           tools: getWorkspaceFiles, listSkills, getSkill, getCredential, broadcastLog
        ▼
BatchResult { tasks: [SessionResult, ...], n_succeeded, n_failed, total_cost_usd?, wall_clock_ms }
        │ token deregistered in finally
        ▼
existing Temporal heartbeat fires per WS broadcast (services/temporal/activities.py:228)
existing tool-result envelope truncates response at 4000 chars (services/handlers/tools.py:678)
```

Reuses (do not duplicate):

- `services/_supervisor/{base,process,util}.py` — `BaseProcessSupervisor` (locked start/stop, `kill_tree`, `terminate_then_kill(5s)`, drain tasks, Windows `CTRL_BREAK_EVENT`)
- `services/llm/{protocol,factory,config}.py` — Protocol + lazy-import factory + JSON config blueprint
- `services/handlers/tools.py:678` — 4000-char truncation
- `services/status_broadcaster.py` — `update_node_status`, `broadcast_terminal_log`
- `services/skill_loader.py` — `scan_skills` / `load_skill` consumed by MCP `listSkills` / `getSkill`
- `services/auth.py` — `AuthService.get_api_key` consumed by MCP `getCredential`
- `services/credential_registry.py` — deep-merge `extends` for `_cli_base` entry
- `services/claude_oauth.py` — Claude `auth login` / `auth status` / `auth logout` wrappers, project-local npm install at `<repo>/data/claude-machina/npm/`, inherited stdio so the CLI opens the browser itself
- `nodes/stripe/_handlers.py` — pattern reference for marker-token + catalogue broadcast

## Provider abstraction (mirrors `services/llm/`)

```python
# server/services/cli_agent/protocol.py
@runtime_checkable
class AICliProvider(Protocol):
    name: str
    package_name: str
    binary_name: str
    ide_lock_env_var: Optional[str]   # CLAUDE_IDE_LOCK | GEMINI_IDE_LOCK | None
    ide_lockfile_dir: Optional[Path]  # ~/.claude/ide | <tmpdir>/gemini/ide

    def binary_path(self) -> Path: ...
    def headless_argv(self, task, *, defaults) -> list[str]: ...
    def login_argv(self) -> list[str]: ...                      # CLI's own login command
    def auth_status_argv(self) -> Optional[list[str]]: ...      # cheap probe
    def detect_auth_error(self, stderr, exit_code) -> bool: ...
    def parse_event(self, line: str) -> Optional[dict]: ...
    def is_final_event(self, event: dict) -> bool: ...
    def event_to_session_result(self, events, stderr, exit_code) -> dict: ...
        # Returns: {...shared, "provider_data": {<vendor-specific>}}
        # provider_data carries Anthropic reasoning_details, Codex call_id,
        # Gemini extra_content — pattern from Hermes agent/transports/types.py.
    def canonical_usage(self, events) -> CanonicalUsage: ...
        # Normalises vendor token-counting; feeds services/pricing.py.
    def supports(self, feature: str) -> bool: ...
        # max_budget | max_turns | session_id | resume | mcp_runtime
        # | json_cost | ide_lockfile | sandbox
```

### Claude argv (`AnthropicClaudeProvider.headless_argv`)

Spawned per task — the binary path comes from
`services.claude_oauth.claude_binary_path()` (the same project-local
install the credentials Login button uses) and `CLAUDE_CONFIG_DIR` is
injected on the spawn env so the agent shares one credential store with
the auth surface.

```
<repo>/data/claude-machina/npm/node_modules/.bin/claude[.cmd]
  -p <prompt>
  --output-format stream-json
  --verbose                       # required when stream-json + --print
  --include-partial-messages
  --include-hook-events           # SessionStart/hook events into stream
  --ide                           # documented VSCode auto-connect via lockfile
  --model <model>
  [--session-id <UUID> | --resume <UUID>]
  --max-turns <N>
  [--max-budget-usd <D>]
  --allowedTools <csv>
  --permission-mode <mode>
  [--append-system-prompt <text>]
  [--effort <low|medium|high|xhigh|max>]
  [--fallback-model <model>]
  [--add-dir <path>]*  [--disallowedTools <csv>]  [--agent <name>]
```

All flags documented at
[code.claude.com/docs/en/cli-reference](https://code.claude.com/docs/en/cli-reference).
Worktree, lockfile, and bearer-token MCP server are wired in
`session.py:_pre_spawn`; the `--ide` flag tells the CLI to discover that
lockfile via `CLAUDE_IDE_LOCK`.

Factory:

```python
# server/services/cli_agent/factory.py
SUPPORTED_PROVIDERS = frozenset({"claude", "codex"})  # gemini is v2 stub

def create_cli_provider(name: str) -> AICliProvider:
    if name == "claude": return AnthropicClaudeProvider()
    if name == "codex":  return OpenAICodexProvider()
    if name == "gemini": raise NotImplementedError("gemini deferred to v2")
    raise ValueError(f"Unknown CLI provider: {name!r}")
```

## Task specs (discriminated union)

```python
# server/services/cli_agent/types.py
class BaseAICliTaskSpec(BaseModel):
    task_id: Optional[str] = None
    prompt: str
    branch: Optional[str] = None
    model: Optional[str] = None
    timeout_seconds: int = Field(600, ge=10, le=3600)
    system_prompt: Optional[str] = None
    # Strict input validation: typo'd task fields raise ValidationError
    # at the spec boundary instead of being silently dropped.
    model_config = ConfigDict(extra="forbid")

class ClaudeTaskSpec(BaseAICliTaskSpec):
    provider: Literal["claude"] = "claude"
    session_id: Optional[str] = None
    resume_session_id: Optional[str] = None
    max_turns: Optional[int] = None
    max_budget_usd: Optional[float] = None
    allowed_tools: Optional[str] = None
    permission_mode: Literal["default", "acceptEdits", "plan", "auto",
                             "dontAsk", "bypassPermissions"] = "acceptEdits"
    # Optional documented CLI flags
    effort: Optional[Literal["low", "medium", "high", "xhigh", "max"]] = None
    fallback_model: Optional[str] = None
    add_dir: List[str] = Field(default_factory=list)
    disallowed_tools: Optional[str] = None
    agent: Optional[str] = None

class CodexTaskSpec(BaseAICliTaskSpec):
    provider: Literal["codex"] = "codex"
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] = "workspace-write"
    ask_for_approval: Literal["untrusted", "on-request", "never"] = "never"

class GeminiTaskSpec(BaseAICliTaskSpec):
    provider: Literal["gemini"] = "gemini"
    session_id: Optional[str] = None
    resume: Optional[str] = None
    yolo: bool = False
    sandbox: bool = False

AICliTaskSpec = Annotated[
    Union[ClaudeTaskSpec, CodexTaskSpec, GeminiTaskSpec],
    Field(discriminator="provider"),
]
```

Each plugin's `Params.tasks` is hard-typed to one variant — the LLM tool-schema fast-path at `services/ai.py:2898` produces a clean per-provider schema (no `$defs`/`$ref`).

## Auth model — Stripe-style CLI-managed OAuth

CLI auth is delegated to the CLI's own login flow + a synthetic marker token in MachinaOs's catalogue. Mirrors `nodes/stripe/_handlers.py` (commit `a32f671`).

**Per-provider WS handler names** (Twitter / Google / Stripe convention — frontend dispatches `{}` payload, handler name encodes provider):

| Catalogue id | Login handler | Logout handler |
|---|---|---|
| `claude_code` | `claude_code_login` | `claude_code_logout` |
| `codex_cli`   | `codex_cli_login` (returns "not yet wired") | `codex_cli_logout` |

**Claude flow** (`server/services/cli_agent/_handlers.py:handle_claude_code_login`) uses the documented CLI subcommands from [code.claude.com/docs/en/cli-reference](https://code.claude.com/docs/en/cli-reference):

| Subcommand | Purpose |
|---|---|
| `claude auth login` | Opens the browser, writes credentials |
| `claude auth status` | Exits 0 when logged in, 1 otherwise |
| `claude auth logout` | Clears credentials |

Steps:

1. Run `claude auth status`. If it exits 0, write the marker + broadcast and return immediately (idempotent re-click).
2. Otherwise call `services.claude_oauth.initiate_claude_oauth()`:
   - Project-local install of `@anthropic-ai/claude-code` into `<repo>/data/claude-machina/npm/` via `npm install --prefix` (mirrors WhatsApp's `<repo>/node_modules/edgymeow/` layout; skipped if already installed).
   - `asyncio.create_subprocess_exec(claude, "auth", "login", env={..., CLAUDE_CONFIG_DIR=<repo>/data/claude-machina})` with **inherited stdio** — same way the VSCode Claude Code extension delegates to the binary. Anthropic doesn't expose `--print-url` or a programmatic OAuth helper (issue [anthropics/claude-code#7100](https://github.com/anthropics/claude-code/issues/7100), closed "not planned"), so we let the CLI open the user's browser via its own OS-level call. Returns `{success: True, pid}` immediately.
3. Schedule a background task that polls `claude auth status` every 2s up to 600s. On exit-0, write the synthetic `"cli-managed"` marker via `auth_service.store_oauth_tokens("claude_code", ...)` and broadcast `credential_catalogue_updated`. The catalogue's `stored` flag flips and the existing `OAuthConnect.tsx` primitive renders the modal as Connected.

**Logout**: runs `claude auth logout`, drops the marker via `auth_service.remove_oauth_tokens()`, and broadcasts.

**Codex login**: not yet wired. The handler returns a graceful error pointing the user at `npm install -g @openai/codex` + `codex login` manual flow. Follow-up: write `services/codex_oauth.py` mirroring `claude_oauth.py` with `HOME=~/.codex-machina` env redirect (Codex has no `CONFIG_DIR` equivalent).

**Frontend**: no changes. The existing `client/src/components/credentials/primitives/OAuthConnect.tsx:42-44` already documents and supports the Stripe-style fieldless-CLI case (`config.fields = []`, `kind: "oauth"`, `stored` flag drives Connected state).

## VSCode-style IDE MCP server

Spawned CLI sessions auto-discover MachinaOs over MCP via the lockfile pattern VSCode's Claude Code extension uses. No custom IPC.

Lockfile path: `~/.claude/ide/<pid>.lock` (Claude) or `<tmpdir>/gemini/ide/gemini-ide-server-<pid>-<port>.json` (Gemini, v2). Format mirrors VSCode exactly:

```json
{
  "port": 3010,
  "url": "http://127.0.0.1:3010/mcp/ide",
  "authToken": "<32-byte hex>",
  "workspaceFolders": ["<absolute path to per-task git worktree>"],
  "ideName": "machinaos",
  "transport": "http",
  "pid": 12345
}
```

Bearer-token middleware (`mcp_server.py:_BearerAuthMiddleware`) validates each request against an in-memory per-batch `BatchContext` registry. Tokens registered at `AICliService.run_batch()` entry; unregistered in `finally` so 401s flip immediately when a batch settles.

Tools exposed (mirror MachinaOs capabilities; deferred ones marked):

| Tool | Maps to | Returns |
|---|---|---|
| `mcp__machina__getWorkspaceFiles` | `Path.rglob` over `workspace_dir` | `{files: [{path, size, mtime, content?}]}` |
| `mcp__machina__listSkills` | `SkillLoader.scan_skills()` filtered to `BatchContext.connected_skill_names` | `{skills: [{name, description, allowed_tools, category}]}` (~100 tokens each) |
| `mcp__machina__getSkill` | `SkillLoader.load_skill(name)` | `{name, instructions, allowed_tools, scripts, references}` |
| `mcp__machina__getCredential` | `auth_service.get_api_key(name)`, gated by `BatchContext.allowed_credentials` | `{name, value}` or 403 |
| `mcp__machina__broadcastLog` | `broadcaster.broadcast_terminal_log()` | `{success}` |

Lifespan: `main.py` enters `mcp_app.router.lifespan_context()` so `StreamableHTTPSessionManager`'s task group is alive (Starlette doesn't auto-propagate `app.mount()` lifespans). Stale-PID lockfile sweep on startup (mirrors VSCode's behaviour).

## Files (current state)

| File | Purpose |
|---|---|
| `server/services/cli_agent/__init__.py` | Public re-exports + self-registers WS handlers via `services.ws_handler_registry` (telegram-style plugin-folder pattern). |
| `server/services/cli_agent/protocol.py` | `AICliProvider` Protocol + `CanonicalUsage` / `SessionResult` / `BatchResult` dataclasses. |
| `server/services/cli_agent/types.py` | Pydantic discriminated-union task specs + `BatchResultModel` for serialisation. |
| `server/services/cli_agent/config.py` | Loads `server/config/ai_cli_providers.json` (binary, package, defaults, supports flags per provider). |
| `server/services/cli_agent/factory.py` | `create_cli_provider(name)` lazy-import factory. |
| `server/services/cli_agent/lockfile.py` | VSCode-style IDE lockfile read/write/sweep. |
| `server/services/cli_agent/mcp_server.py` | FastMCP sub-app at `/mcp/ide` with bearer-token middleware + 5 tools. |
| `server/services/cli_agent/session.py` | `AICliSession(BaseProcessSupervisor)` per task — heart of framework. |
| `server/services/cli_agent/service.py` | `AICliService.run_batch()` — `asyncio.gather` under `Semaphore(5)` (no separate pool class), cancel hooks. |
| `server/services/cli_agent/_handlers.py` | Per-provider login/logout WS handlers (`claude_code_*`, `codex_cli_*`). |
| `server/services/cli_agent/providers/anthropic_claude.py` | Reference Claude provider — full feature surface. |
| `server/services/cli_agent/providers/openai_codex.py` | Codex provider — sandbox-first. |
| `server/services/cli_agent/providers/google_gemini.py` | v2 stub. |
| `server/config/ai_cli_providers.json` | Per-provider config (binary names, npm packages, login/auth_status argvs, lockfile dirs, supports flags). |
| `server/nodes/agent/claude_code_agent.py` | Refactored — `Params.tasks: list[ClaudeTaskSpec]` + legacy single-prompt fallback. |
| `server/nodes/agent/codex_agent.py` | New plugin — `Params.tasks: list[CodexTaskSpec]` + sandbox-focused defaults. |
| `server/nodes/visuals.json` | `claude_code_agent` + `codex_agent` icon/color entries. |
| `server/config/credential_providers.json` | `_cli_base` abstract + `claude_code` + `codex_cli` entries. |
| `server/services/claude_code_service.py` | Slimmed to a back-compat shim that builds one `ClaudeTaskSpec` and calls `AICliService.run_batch("claude", ...)`. Kept for legacy callers; eventually deletable. |
| `server/services/claude_oauth.py` | Unchanged. The new framework's Claude login handler reuses this directly. |
| `server/services/workflow.py` | `cancel_deployment` also calls `get_ai_cli_service().cancel_workflow(workflow_id)`. |
| `server/main.py` | Mounts `/mcp/ide` sub-app, composes its lifespan, runs stale-lockfile sweep on startup. Side-effect imports `services.cli_agent` so its WS handlers self-register. |
| `server/routers/websocket.py` | No CLI handlers inline — discovered via `services.ws_handler_registry.get_ws_handlers()`. |

## Output contract

```json
{
  "tasks": [{
    "task_id": "t_<8hex>",
    "session_id": "<UUID|null>",
    "provider": "claude|codex|gemini",
    "prompt": "...",
    "branch": "machina/t_<8hex>",
    "worktree_path": "<abs path, removed after batch>",
    "response": "<truncated to 4000 chars>",
    "cost_usd": 0.42,
    "duration_ms": 18234,
    "num_turns": 7,
    "tool_calls": 12,
    "canonical_usage": {
      "input_tokens": 5000, "output_tokens": 1000,
      "cache_read": 500, "cache_write": 0,
      "reasoning_tokens": 0, "request_count": 7
    },
    "provider_data": {
      "reasoning_details": "...",   // Claude only
      "call_id": "...",             // Codex only
      "extra_content": [...]        // Gemini only (v2)
    },
    "success": true, "error": null
  }],
  "summary": {
    "n_tasks": 3, "n_succeeded": 2, "n_failed": 1,
    "total_cost_usd": 1.23,
    "wall_clock_ms": 19002
  },
  "provider": "claude|codex|gemini",
  "timestamp": "2026-05-04T12:00:00Z"
}
```

`cost_usd` is the provider's reported value when available (Claude). For Codex (no native USD), `service.py:_derive_cost` falls back to `services.pricing.PricingService.calculate_cost()` from `canonical_usage` — single source of truth for LLM cost across MachinaOs. `summary.total_cost_usd` is `null` if any task didn't surface cost.

## Memory bridge — `simpleMemory` → `claude_code_agent`

Connecting a `simpleMemory` node to a `claude_code_agent` makes the
spawned `claude -p` resume its prior session natively across runs.
**No system-prompt injection, no JSONL synthesis, no API fallback.**
Claude maintains its own session JSONL on disk under
`<CLAUDE_CONFIG_DIR>/projects/<project_key>/<session_id>.jsonl`; we
just feed it the right argv and keep the cwd stable so the project
key doesn't drift.

### Why native resume needs a stable cwd

Claude derives `project_key` from cwd via `re.sub(r"[^a-zA-Z0-9.-]", "-", str(cwd))`
— every `:`, `\`, `/`, `_` becomes `-`. Verified against the on-disk
`data/claude-machina/projects/` listing — Python reproduces three
encoded directory names byte-for-byte. The pre-bridge per-task
worktree (`<workspace>/<node_id>/wt_t_<random_8hex>`) changed cwd on
every spawn → fresh project_key every run → `--resume <UUID>` looked
under a brand-new directory with zero prior JSONL ("No conversation
found with session ID").

The fix: when memory is wired, spawn under `cwd=repo_root` and skip
the worktree entirely. Same cwd every run → same project_key → claude
finds its own JSONL.

### Argv contract

| Run state | Argv | Why |
|---|---|---|
| First run (no `last_session_id` in DB) | `--session-id <UUID5(memory_node_id, simpleMemory.session_id)>` | Deterministic UUID lets us address the same session predictably; survives session_id forks via the user-visible `simpleMemory.session_id` slot. |
| Subsequent run (`last_session_id` saved) | `--resume <last_session_id>` | Native claude session resume. Same `cwd=repo_root` → same project_key → claude finds the JSONL it wrote on the previous turn. |
| After "No conversation found" error | argv same as above on the spawn that errored; `_persist_memory` auto-clears `last_session_id` so the NEXT run falls into the first-run branch with `--session-id <UUID5>`. | Self-heal from a stale or wiped JSONL. |

Argv emission lives in
`providers/anthropic_claude.py:headless_argv` — picks up
`task.session_id` vs `task.resume_session_id` (mutually exclusive
fields on `ClaudeTaskSpec`).

### Plumbing

```
ClaudeCodeAgentNode.execute_op
  └─ collect_agent_connections() → memory_data {node_id, session_id,
                                                memory_content, window_size,
                                                long_term_enabled,
                                                last_session_id}
  └─ derive resume_session_id (= memory_data.last_session_id) OR
            first_run_session_uuid (= uuid5(NAMESPACE_OID,
                                            f"{memory_node_id}:{session_id}"))
  └─ ClaudeTaskSpec(..., session_id=..., resume_session_id=...)
  └─ run_batch(..., connected_memory=memory_data, broadcaster=...)
       └─ AICliService.run_batch:
            ├─ AICliSession(..., memory_bound=True)   # ← drives cwd switch
            │    └─ cwd() returns self._repo_root
            │    └─ _pre_spawn skips `git worktree add`
            │    └─ cleanup skips `git worktree remove`
            └─ asyncio.gather → SessionResults
            └─ _persist_memory(connected_memory, results, broadcaster):
                 ├─ saves params["last_session_id"] = most_recent.session_id
                 ├─ appends user/assistant turns to params["memory_content"]
                 │  via append_to_memory_markdown + trim_markdown_window
                 │  (the same helpers aiAgent / chatAgent / deep_agent / rlm_agent
                 │  use — reuse, don't duplicate)
                 ├─ database.save_node_parameters(memory_node_id, params)
                 └─ broadcaster.broadcast({"type": "node_parameters_updated",
                                           "node_id": memory_node_id, ...})
                    # ← the UI's parameter panel auto-refreshes the moment
                    # the run completes. Without this the DB has the
                    # latest conversation but the UI keeps showing the
                    # stale snapshot it loaded at workflow open.
```

### Self-healing on stale UUID

`AICliService._clear_stale_session_id(connected_memory)` substring-
matches `"No conversation found with session ID"` in any result's
`error`. When matched it wipes `simpleMemory.last_session_id` from
the DB but preserves `memory_content` (the user-visible markdown
transcript). Next run derives a fresh `--session-id <UUID5>` and
proceeds with a clean session.

Triggered automatically inside `_persist_memory` when zero results
succeed. Without this the same stale UUID would re-fire every run
and the user would be stuck.

### Parallel-batch guard

Memory continuity requires serial execution — N concurrent
`--resume <UUID>` spawns against one session JSONL would race. When
`memory_data` is wired AND `len(tasks) > 1`, `claude_code_agent`
raises `NodeUserError("Memory-bound batches must run one task at a
time. Reduce Tasks to a single entry, or disconnect the memory
node.")` at handler entry — never spawns.

### Markdown mirror

`memory_content` (the markdown surface the simpleMemory UI shows) is
**a display mirror, not the canonical record**. Claude's own JSONL
is canonical. `_persist_memory` appends each successful run's prompt
+ response to `memory_content` via `append_to_memory_markdown` so the
UI shows the conversation grow live. User edits to `memory_content`
do NOT influence claude's next response — claude reads its own JSONL
via `--resume`.

To reset both: click the simpleMemory's clear button or invoke the
`clear_memory` WS handler with `memory_node_id` — backend wipes
`memory_content` to the default placeholder AND clears
`last_session_id` in one DB write. Claude's on-disk JSONL is left
alone (orphan, harmless). The next run with the same memory wired
will spawn a fresh session at a NEW deterministic UUID5 (because
the user's `simpleMemory.session_id` is part of the UUID5 inputs;
unchanged after clear, but `last_session_id` being None routes us
through the first-run branch).

### Logs to watch

```
[Claude Code memory] memory_node=<id> last_session_id=<UUID|None>
   -> --resume <UUID>   |   --session-id <UUID5> (first run)

[CC-Agent run_batch] enter ... memory=<memory_node_id> ...

[AICliSession_*] memory-bound: skipping worktree, using cwd=<repo_root>

[CC-Agent stream] result is_error=False subtype=success
   session_id=<UUID> duration_ms=...

[CC-Agent _persist_memory] memory_node=<id> results=1 successful=1
   session_ids=['<UUID>']
[CC-Agent _persist_memory] saved memory_node=<id> last_session_id=<UUID>
   appended_turns=1 archived_blocks=0 content_length=<N>
```

On a stale-resume failure path the trace ends with:

```
[CC-Agent stderr] No conversation found with session ID: <UUID>
[CC-Agent stream] result is_error=True subtype=error_during_execution
   session_id=<new-UUID-claude-assigned-to-the-failed-attempt>
[CC-Agent _persist_memory] no successful runs; skipping save ...
[CC-Agent _persist_memory] cleared stale last_session_id=<UUID>
   from memory_node=<id>; next run will spawn a fresh claude session
   and persist its new UUID.
```

— and the next run succeeds with `--session-id <UUID5>`.

## Status broadcast events

| Phase | When | Payload |
|---|---|---|
| `batch_started` | `run_batch` entry | `{provider, n_tasks, max_parallel, isolation:"worktree"}` |
| `ai_cli_subtask` | per-task partial (NDJSON event) | `{task_id, provider, status:"running", message, cost_usd?, num_turns?}` |
| `ai_cli_subtask` | per-task final | `{task_id, provider, status:"succeeded"|"failed", cost_usd?, duration_ms, num_turns?, error?}` |
| `batch_complete` | aggregator finish | `{provider, n_succeeded, n_failed, total_cost_usd?, wall_clock_ms}` |

Plus `broadcast_terminal_log(source=f"{provider}:{task_id}", level)` on every NDJSON line — surfaced in the Terminal tab.

## Plugin contract — adding a new CLI provider

To add a fourth provider (e.g. Mistral CLI) post-v1:

1. New file `server/services/cli_agent/providers/<vendor>.py` implementing `AICliProvider`.
2. New entry in `server/config/ai_cli_providers.json`.
3. New `<vendor>TaskSpec` in `types.py` + register in the discriminated union.
4. New branch in `factory.create_cli_provider`.
5. New entry in `server/config/credential_providers.json` (`extends: "_cli_base"`).
6. New per-provider WS handlers in `_handlers.py` (login + logout) + entry in `WS_HANDLERS`.
7. New plugin file `server/nodes/agent/<vendor>_agent.py` (mirror `codex_agent.py`).
8. New entry in `server/nodes/visuals.json`.

No edits to `routers/websocket.py`, `main.py`, or any existing handler/service. The plugin auto-registers via `BaseNode.__init_subclass__` (Wave 11) + the `services.ws_handler_registry` self-registration in `cli_agent/__init__.py`.

## Verification

Unit tests (in `server/tests/services/cli_agent/`):

- `test_providers.py` — Claude + Codex argv shapes, parse_event fidelity (vendored NDJSON), event_to_session_result reconstruction, auth-error detection, `supports()` flags. Factory raises `NotImplementedError` for `gemini`.
- `test_mcp_server.py` — bearer-token registry register/lookup/unregister, 401 on missing/malformed/unknown/expired tokens, lockfile shape matches VSCode convention, stale-PID sweep.
- `test_service.py` — `not_git_repo` abort path, resolver contract (explicit `repo_root` doesn't fall back to cwd), cancel-when-idle, singleton.

Plugin contract: `tests/test_plugin_contract.py` + `tests/test_node_spec.py` — clean per-provider Params schemas, no `$defs`/`$ref` in the fast-path.

Live verification (needs a real Claude install + auth):

1. Empty `<repo>/data/claude-machina/`. Open Credentials Modal → click "Login with Claude Code CLI". Confirm the npm install runs (visible in backend logs), `<repo>/data/claude-machina/npm/node_modules/.bin/claude[.cmd]` appears, browser opens for Anthropic OAuth. Modal flips Connected within ~2s of CLI exit (background `claude auth status` poll detects success).
2. Refresh the page. Modal stays Connected (`auth_service.get_oauth_tokens("claude_code")` still returns the marker; idempotent re-click also stays Connected).
3. Click Disconnect. Modal flips Disconnected (`claude auth logout` clears CLI creds + marker dropped).
4. Add a `claude_code_agent` node, set `tasks=[{prompt:"echo A"},{prompt:"echo B"},{prompt:"echo C"}]`, run. Three distinct `claude:<task_id>` Terminal streams interleaved. Three distinct session_ids. Three worktrees created and removed. `summary.wall_clock_ms < sum(duration_ms)` (proves parallelism).
5. With a Claude task running, `cat ~/.claude/ide/<pid>.lock` and confirm format. Stream-json shows an `mcp__machina__*` tool invocation.
6. `curl -H "Authorization: Bearer <wrong>" http://127.0.0.1:3010/mcp/ide/...` → 401.

## Risks / open considerations

- **Codex login not yet wired.** v1 returns a graceful error directing the user to `npm install -g @openai/codex` + `codex login`. Follow-up: `services/codex_oauth.py` mirroring `claude_oauth.py` with `HOME=~/.codex-machina` env redirect (Codex has no `CONFIG_DIR` env; `HOME` redirect is risky on Windows, so Windows may need a different strategy or accept user-global Codex auth).
- **Gemini deferred.** `factory.create_cli_provider("gemini")` raises `NotImplementedError`. v2 work: implement `providers/google_gemini.py`, drop the factory branch, add `nodes/agent/gemini_cli_agent.py`. ~430 LoC. No abstraction changes needed.
- **`--include-partial-messages`** assumes a recent Claude CLI; older versions fall back gracefully via the parser's `parse_event` returning `None` for unknown shapes.
- **Browser-prompt phrasing** can change between Claude CLI versions; `b"yes\nyes\n"` might land on the wrong question. Defensible because that's exactly what `claude_oauth.py` has been doing in production.
- **Marker token written without verifying CLI is actually functional** — we trust `claude auth status`'s exit code. If Anthropic invalidates the token server-side and the CLI hasn't re-checked, the modal still shows Connected until the next session attempt's `detect_auth_error` catches it.
- **MCP SDK is pre-1.0-stable.** Pinned at `mcp>=1.0.0`. The surface is isolated in `mcp_server.py` so an SDK breaking change touches one file.
- **Concurrent install safety.** `claude_oauth.py:_get_claude_cmd` doesn't currently use a lock — two simultaneous login clicks within 2s could race the npm install. Low-risk in practice (modal debounces clicks); a follow-up could add an `asyncio.Lock`.
- **Worktree leak on hard crash.** Out-of-scope cleanup pass; document.
