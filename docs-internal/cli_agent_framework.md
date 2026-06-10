# AI CLI Agent Framework

Multi-instance, multi-provider runtime for AI CLI agents (Claude Code, Codex, Gemini). One workflow node spawns N parallel CLI sessions over a list of tasks, each isolated in its own git worktree, each able to call back into MachinaOs over MCP.

| Provider | Status | Login flow |
|---|---|---|
| Claude Code (`@anthropic-ai/claude-code`) | shipping | Shared-tree install + spawn (`nodes/agent/claude_code_agent/_oauth.py:run_claude_login`) |
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
- `nodes/agent/claude_code_agent/_oauth.py` — Claude `auth login` / `auth status` / `auth logout` wrappers, npm install into the shared MachinaOs tree at `<DATA_DIR>/packages/` (binary resolves to `<DATA_DIR>/packages/node_modules/.bin/claude[.cmd]`), `CLAUDE_CONFIG_DIR=<DATA_DIR>/claude/`. The `login` spawn passes `stdin=PIPE` (un-written) so the native CLI's stdin reader blocks instead of EOFing — keeps its localhost OAuth callback server alive until the browser flow completes
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
    def interactive_argv(self, task, *, defaults) -> list[str]: ...
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

### Claude argv (`AnthropicClaudeProvider.interactive_argv`)

Spawned per task — the binary path comes from
`services.claude_oauth.claude_binary_path()` (the same project-local
install the credentials Login button uses) and `CLAUDE_CONFIG_DIR` is
injected on the spawn env so the agent shares one credential store with
the auth surface.

The pooled path (`ClaudeSessionPool`) spawns claude as a plain
subprocess with stdio pipes — no PTY — and drives it over the
VSCode-extension protocol: prompts written as stream-json to
`proc.stdin`, events read as stream-json off `proc.stdout`. Stays in
the interactive billing bucket (entrypoint `claude-vscode`, NOT
`sdk-cli`) since `-p` / `--print` is never emitted.

```
~/.machina/packages/node_modules/.bin/claude[.cmd]
  --output-format stream-json     # events on stdout
  --input-format stream-json      # user turns to stdin as JSON
  --verbose                       # required with stream-json for full event detail
  --ide                           # VSCode auto-connect via lockfile
  --model <model>
  [--session-id <UUID> | --resume <UUID>]   # mutually exclusive
  --allowedTools <csv>
  --permission-mode <mode>
  [--append-system-prompt <text>]
  [--effort <low|medium|high|xhigh|max>]
  [--add-dir <path>]*  [--disallowedTools <csv>]  [--agent <name>]
  [--mcp-config <json>]  [--strict-mcp-config]
```

**Dropped from the pool path** (vs. the previous headless/`-p` shape):
`-p`, `--print`, `--include-partial-messages`, `--include-hook-events`,
`--max-turns`, `--max-budget-usd`, `--fallback-model`. The positional
`-- "<prompt>"` is also never emitted — prompts arrive as JSON on
stdin (`{"type":"user","message":{"role":"user","content":"..."}}\n`).
`ClaudeTaskSpec` keeps the corresponding Pydantic fields for
back-compat; they're silently dropped in `interactive_argv`.

All flags documented at
[code.claude.com/docs/en/cli-reference](https://code.claude.com/docs/en/cli-reference).
Worktree, lockfile, and bearer-token MCP server are wired in
`session_pool.py:_spawn`; the `--ide` flag tells the CLI to discover
that lockfile via `CLAUDE_IDE_LOCK`.

The non-pooled `AICliSession` path (one-shot prompt-in-argv runs that
don't need session reuse) still uses PTY (`ptyprocess` POSIX /
`pywinpty>=3.0.3` Windows) and remains out of scope for the
subprocess+stream-json refactor.

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
    allowed_tools: Optional[str] = None  # default: "" — see "Strict allowlist"
    permission_mode: Literal["default", "acceptEdits", "plan", "auto",
                             "dontAsk", "bypassPermissions"] = "dontAsk"
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
2. Otherwise schedule `_finalize_claude_login()` (in `nodes/agent/claude_code_agent/_handlers.py`), which calls `run_claude_login()` from `_oauth.py`:
   - MachinaOs-managed install of `@anthropic-ai/claude-code` into the shared npm tree at `<DATA_DIR>/packages/` via `npm install --prefix <packages_dir>` (same tree as `edgymeow` / `agent-browser`; skipped if already installed). Binary resolves to `<DATA_DIR>/packages/node_modules/.bin/claude[.cmd]`.
   - `claude auth login` via `run_cli_command(..., env={..., CLAUDE_CONFIG_DIR=<DATA_DIR>/claude/}, stdin=asyncio.subprocess.PIPE)` — same way the VSCode Claude Code extension delegates to the binary. Anthropic doesn't expose `--print-url` or a programmatic OAuth helper (issue [anthropics/claude-code#7100](https://github.com/anthropics/claude-code/issues/7100), closed "not planned"), so we let the CLI open the user's browser via its own OS-level call. `stdin=PIPE` is **load-bearing** for claude-code >= 2.1.162's native binary: it reads stdin while waiting for the browser callback, and an inherited (closed) stdin EOFs it into an early exit that kills the localhost callback server before the redirect arrives — `stdin=PIPE` (never written) makes the read block so the server stays up.
3. Schedule a background task that polls `claude auth status` every 2s up to 600s. On exit-0, write the synthetic `"cli-managed"` marker via `auth_service.store_oauth_tokens("claude_code", ...)` and broadcast `credential_catalogue_updated`. The catalogue's `stored` flag flips and the existing `OAuthConnect.tsx` primitive renders the modal as Connected.

**Logout**: runs `claude auth logout`, drops the marker via `auth_service.remove_oauth_tokens()`, and broadcasts.

**Codex login**: not yet wired. The handler returns a graceful error pointing the user at `npm install -g @openai/codex` + `codex login` manual flow. Follow-up: mirror `nodes/agent/claude_code_agent/_oauth.py` for codex with a `HOME=<DATA_DIR>/codex/` env redirect (Codex has no `CONFIG_DIR` equivalent).

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

Bearer-token middleware (`mcp_server.py:_BearerAuthMiddleware`) validates each request against an in-memory per-batch `BatchContext` registry. **Non-pool path**: tokens registered at `AICliService.run_batch()` entry, unregistered in `finally` so 401s flip immediately when a batch settles. **Pool path** (`use_pool=True`, see [Claude Code Interactive Mode](./claude_code_interactive_mode.md#mcp-bearer-token-lifecycle) for the full lifecycle): claude bakes the bearer into argv (`--mcp-config`) at spawn time and can't rotate without respawning, so the pool stashes the spawn-time token on `PooledClaudeSession.batch_token` and rebinds the `BatchContext` in place on warm reuse via `rebind_batch(token, connected_tools=..., ...)` — closes the "disconnected tool still works" leak by (a) diffing `connected_tools` and decrementing FastMCP refcounts for tools dropped between batches, and (b) updating the per-handler scope check's data so `workflow_tools._build_handler` returns 403 for stale tools. `_terminate_locked` calls `unregister_batch(session.batch_token)` so refcounts drain on pool eviction / `clear` / `shutdown_all`.

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

### Generic framework — `server/services/cli_agent/`

Shared by every CLI provider plugin. Imports nothing from `nodes/`.

| File | Purpose |
|---|---|
| `__init__.py` | Imports `_handlers.py` (codex WS handlers) + registers codex provider; framework-level self-registration only. Claude self-registers from its own plugin folder. |
| `protocol.py` | `AICliProvider` Protocol + `CanonicalUsage` / `SessionResult` / `BatchResult` dataclasses. |
| `types.py` | Pydantic discriminated-union task specs + `BatchResultModel` for serialisation. |
| `config.py` | Loads `server/config/ai_cli_providers.json` (binary, package, defaults, supports flags per provider). |
| `factory.py` | Three registries (`register_provider`, `register_session_pool`, `register_skill_materialiser`) + lookups + `create_cli_provider(name)`. |
| `lockfile.py` | VSCode-style IDE lockfile read/write/sweep. |
| `mcp_server.py` | FastMCP sub-app at `/mcp/ide` with bearer-token middleware + 5 tools + `rebind_batch` for warm-reuse context updates. |
| `workflow_tools.py` | Per-batch MCP tool exposure (`mcp__machinaos__<node_type>`) + handler scope check + `tools/list_changed` notify. |
| `session.py` | `AICliSession(BaseProcessSupervisor)` — generic non-pool path (still PTY on POSIX). |
| `service.py` | `AICliService.run_batch()` — dispatcher; routes to pool when memory-bound via `factory.get_session_pool(provider_name)`. |
| `_cli_auth.py` | CLI-agnostic `mark_logged_in` / `mark_logged_out` / `broadcast_credential_event` + `"cli-managed"` marker token. Shared by claude + codex handlers. |
| `_handlers.py` | Codex WS handlers (`codex_cli_login` / `codex_cli_logout`). Claude's moved to the plugin folder. |
| `providers/openai_codex.py` | Codex provider — sandbox-first, no session continuity. Will move when codex_agent adopts the per-folder layout. |
| `providers/google_gemini.py` | v2 stub. |
| `jsonl_watcher.py` | Still used by the non-pooled `AICliSession` (out-of-scope path). |

### Claude plugin — `server/nodes/agent/claude_code_agent/`

Self-contained per the canonical plugin-folder pattern (telegram is
the reference implementation). Four self-registration calls in
`__init__.py` wire it into the framework — zero changes to
`services/cli_agent/` when claude internals change.

| File | Purpose |
|---|---|
| `__init__.py` | `ClaudeCodeAgentNode(ActionNode)` + 4 self-registration calls: `register_provider`, `register_session_pool`, `register_skill_materialiser`, `register_ws_handlers`. |
| `_provider.py` | `AnthropicClaudeProvider` — full claude argv builder + stream-json event parsers + ide_lockfile_dir derivation. |
| `_pool.py` | `ClaudeSessionPool` — subprocess + stream-json warm-reuse pool, MCP rebind on acquire, skill diff on warm reuse. |
| `_skills.py` | `materialise_skills(workspace_dir, names, previous_skill_names)` — per-workflow SKILL.md materialisation + diff-based add/remove. |
| `_oauth.py` | `MACHINA_CLAUDE_DIR`, `claude_binary_path`, `claude_auth_*` — project-local install + the documented CLI subcommands. |
| `_handlers.py` | `claude_code_login` / `claude_code_logout` WS handlers (registered from `__init__.py` via `register_ws_handlers`). |

### Other touchpoints

| File | Purpose |
|---|---|
| `server/config/ai_cli_providers.json` | Per-provider config (binary names, npm packages, login/auth_status argvs, supports flags). |
| `server/nodes/agent/codex_agent/__init__.py` | Codex node plugin — `Params.tasks: list[CodexTaskSpec]`. (Provider class still lives in `services/cli_agent/providers/openai_codex.py` until this folder adopts the per-folder layout.) |
| `server/nodes/visuals.json` | `claude_code_agent` + `codex_agent` icon/color entries. |
| `server/config/credential_providers.json` | `_cli_base` abstract + `claude_code` + `codex_cli` entries. |
| `server/services/claude_code_service.py` | Slimmed back-compat shim — builds one `ClaudeTaskSpec` and calls `AICliService.run_batch("claude", ...)`. Eventually deletable. |
| `server/services/workflow.py` | `cancel_deployment` calls `get_ai_cli_service().cancel_workflow(workflow_id)`. |
| `server/main.py` | Mounts `/mcp/ide` sub-app, composes its lifespan, runs stale-lockfile sweep on startup. Side-effect imports `services.cli_agent` so its WS handlers self-register. Claude's plugin folder is discovered separately by the node registry. |
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

> See [memory_lifecycle.md](./memory_lifecycle.md) for the shared markdown surface (every agent uses the same `parse_memory_markdown` / `append_to_memory_markdown` / `trim_markdown_window` helpers to maintain `simpleMemory.memory_content`). This section documents what's UNIQUE to `claude_code_agent`: the markdown is the UI mirror, not the resume channel.

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
`~/.machina/claude/projects/` listing — Python reproduces three
encoded directory names byte-for-byte. The pre-bridge per-task
worktree (`<workspace>/<node_id>/wt_t_<random_8hex>`) changed cwd on
every spawn → fresh project_key every run → `--resume <UUID>` looked
under a brand-new directory with zero prior JSONL ("No conversation
found with session ID").

The fix: when memory is wired, spawn under `cwd=repo_root` and skip
the worktree entirely. Same cwd every run → same project_key → claude
finds its own JSONL.

### Argv contract

| Run state | Argv emitted | Why |
|---|---|---|
| First cold spawn under a memory-wired node | `--continue` | Claude auto-loads the most recent conversation under cwd's `project_key` (per [code.claude.com/docs/en/cli-reference](https://code.claude.com/docs/en/cli-reference)). No UUID round-trip needed — claude tracks its own latest session per cwd on disk. |
| Subsequent turn, SAME warm subprocess | nothing argv-level — stream-json line on `proc.stdin` | The pool keeps the subprocess alive between turns. Claude maintains the conversation in-process; same `session_id` across turns (verified end-to-end). |
| Crash recovery (subprocess died between batches) | `--resume <captured_uuid>` | `ClaudeSessionPool.acquire` detects `process.returncode is not None`, captures the dead session's `current_session_uuid`, and respawns with `--resume`. Same `cwd=repo_root` → same `project_key` → claude finds the same JSONL it was writing before the crash. Mutually exclusive with `--continue`. |

Argv emission lives in
[`nodes/agent/claude_code_agent/_provider.py:interactive_argv`](../server/nodes/agent/claude_code_agent/_provider.py).
The `ClaudeTaskSpec` carries `continue_session: bool` and
`resume_session_id: Optional[str]`. `claude_code_agent.execute_op` sets
`continue_session = bool(memory_data)` for the cold-spawn case; the
pool's crash-recovery path injects `resume_session_id =
session.current_session_uuid` into the spec before respawning.
`--session-id <UUID5>` (the pre-cutover UUID-round-trip primitive) is
intentionally NOT emitted in interactive mode — claude rejects it.

### Plumbing

```
ClaudeCodeAgentNode.execute_op
  ├─ collect_agent_connections() → memory_data {node_id, session_id,
  │                                              memory_content, window_size,
  │                                              long_term_enabled,
  │                                              last_session_id (display-only)}
  ├─ continue_session = bool(memory_data)
  ├─ ClaudeTaskSpec(..., continue_session=continue_session,
  │                      resume_session_id=None)
  └─ AICliService.run_batch(..., connected_memory=memory_data,
                            broadcaster=...)
       └─ For memory-wired runs, route through ClaudeSessionPool:
            ├─ pool.acquire(memory_node_id, spec, cwd=repo_root, env, ...)
            │    ├─ cold: spawn `claude --output-format stream-json
            │    │         --input-format stream-json --verbose --ide
            │    │         --continue ...` as subprocess with stdio pipes;
            │    │         no PTY. stdout_reader_task parses each event line
            │    │         and dispatches to _handle_stream_event.
            │    └─ warm reuse: return the existing PooledClaudeSession;
            │                   the next prompt rides the same subprocess.
            │    └─ crash recovery: capture current_session_uuid from the
            │                       dead session, splice resume_session_id
            │                       onto the spec, spawn fresh with --resume.
            ├─ pool.send_turn(session, prompt):
            │    ├─ write {"type":"user","message":{...}} + "\n" to proc.stdin
            │    └─ await session.result_event (set by the stdout reader
            │                                   when claude emits `result`)
            ├─ pool.release(session)   ← marks idle for the reaper
            └─ _persist_memory(connected_memory, results, broadcaster):
                 ├─ saves params["last_session_id"] = most_recent.session_id
                 │  (display-only — claude_code_agent no longer reads it)
                 ├─ appends user/assistant turns to params["memory_content"]
                 │  via append_to_memory_markdown + trim_markdown_window
                 ├─ database.save_node_parameters(memory_node_id, params)
                 └─ broadcaster.broadcast_node_parameters_updated(
                        memory_node_id,
                        parameters=params,
                        source_hint="cli",
                    )
                    # Emits a CloudEvents v1.0 envelope (RFC §6.4):
                    #   { type: "node_parameters_updated",
                    #     data: { specversion, id, time,
                    #             source: "machinaos://services/parameters",
                    #             type: "com.machinaos.node.parameters.updated",
                    #             subject: <memory_node_id>,
                    #             workflow_id?,
                    #             data: { node_id, parameters, version, source } } }
                    # The FE handler in `client/src/contexts/WebSocketContext.tsx`
                    # casts ``data`` to ``WorkflowEvent<{node_id, parameters,
                    # version, source}>`` and routes the inner payload to
                    # ``setNodeParameters`` + ``queryClient.setQueryData``,
                    # so the simpleMemory parameter panel auto-refreshes
                    # live (no page reload needed). Snake-case wire keys
                    # preserved (``node_id``, not ``nodeId``).
```

### Parallel-batch guard

Memory continuity requires serial execution — N concurrent `--continue`
spawns against the same `project_key` would race claude's
session-resolution. When `memory_data` is wired AND `len(tasks) > 1`,
`claude_code_agent` raises `NodeUserError("Memory-bound batches must
run one task at a time. ...")` at handler entry.

### Markdown mirror

`memory_content` (the markdown surface the simpleMemory UI shows) is
a **display mirror**, not the resume channel. Claude's own JSONL on
disk is what `--continue` and `--resume` load from. `_persist_memory`
appends each successful run's prompt + response to `memory_content`
via `append_to_memory_markdown` so the UI shows the conversation grow
live. User edits to `memory_content` do NOT influence claude's next
response.

To reset both: click the simpleMemory's clear button or invoke the
`clear_memory` WS handler — backend wipes `memory_content` to the
default placeholder AND clears `last_session_id` in one DB write.
Claude's on-disk JSONL is left alone (orphan, harmless). The next run
spawns fresh with `--continue` against a project_key that has no
matching prior session, and claude assigns a brand-new UUID.

### Logs to watch

```
[Claude Code memory] memory_node=<id> -> --continue
   (claude auto-finds latest session under cwd)

[CC-Agent run_batch] enter ... memory=<memory_node_id> ...

[ClaudeSessionPool] spawned new session memory_node=<id> pid=<N>
   (cold spawn — argv carries --continue or --resume <UUID>)
[ClaudeSessionPool] warm reuse memory_node=<id> pid=<N> uuid=<UUID>
   (intra-process turn — stream-json on stdin)

[CC-Agent _persist_memory] saved memory_node=<id> last_session_id=<UUID>
   appended_turns=1 archived_blocks=0 content_length=<N>
```

On a crash-recovery path the trace shows:

```
[ClaudeSessionPool] dropping dead session memory_node=<id>
   pid=<dead-pid> exit=<code> — will respawn

[ClaudeSessionPool] spawned new session memory_node=<id> pid=<new-pid>
   (argv: --resume <UUID> taken from the dead session's
    current_session_uuid; same project_key, same JSONL continues)
```

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

1. Empty `~/.machina/claude/` + `~/.machina/packages/`. Open Credentials Modal → click "Login with Claude Code CLI". Confirm the npm install runs (visible in backend logs), `~/.machina/packages/node_modules/.bin/claude[.cmd]` appears, browser opens for Anthropic OAuth. Modal flips Connected within ~2s of CLI exit (background `claude auth status` poll detects success). The browser tab should render the CLI's own "Signed in" success page (this needs the `stdin=PIPE` spawn — without it the native binary exits early and the tab is left on the bare `localhost/callback` URL).
2. Refresh the page. Modal stays Connected (`auth_service.get_oauth_tokens("claude_code")` still returns the marker; idempotent re-click also stays Connected).
3. Click Disconnect. Modal flips Disconnected (`claude auth logout` clears CLI creds + marker dropped).
4. Add a `claude_code_agent` node, set `tasks=[{prompt:"echo A"},{prompt:"echo B"},{prompt:"echo C"}]`, run. Three distinct `claude:<task_id>` Terminal streams interleaved. Three distinct session_ids. Three worktrees created and removed. `summary.wall_clock_ms < sum(duration_ms)` (proves parallelism).
5. With a Claude task running, `cat ~/.claude/ide/<pid>.lock` and confirm format. Stream-json shows an `mcp__machina__*` tool invocation.
6. `curl -H "Authorization: Bearer <wrong>" http://127.0.0.1:3010/mcp/ide/...` → 401.

## Risks / open considerations

- **Codex login not yet wired.** v1 returns a graceful error directing the user to `npm install -g @openai/codex` + `codex login`. Follow-up: a codex `_oauth.py` mirroring `nodes/agent/claude_code_agent/_oauth.py` with a `HOME=<DATA_DIR>/codex/` env redirect (Codex has no `CONFIG_DIR` env; `HOME` redirect is risky on Windows, so Windows may need a different strategy or accept user-global Codex auth).
- **Gemini deferred.** `factory.create_cli_provider("gemini")` raises `NotImplementedError`. v2 work: implement `providers/google_gemini.py`, drop the factory branch, add `nodes/agent/gemini_cli_agent.py`. ~430 LoC. No abstraction changes needed.
- **`--include-partial-messages`** assumes a recent Claude CLI; older versions fall back gracefully via the parser's `parse_event` returning `None` for unknown shapes.
- **Native-binary stdin sensitivity.** claude-code >= 2.1.162 ships a native binary that reads stdin during `auth login`. We spawn it with `stdin=asyncio.subprocess.PIPE` (never written) so the read blocks and the OAuth callback server stays alive; an inherited/closed stdin EOFs the binary into an early exit that drops the browser callback. If a future CLI version changes its stdin contract this is the spot to revisit.
- **Marker token written without verifying CLI is actually functional** — we trust `claude auth status`'s exit code. If Anthropic invalidates the token server-side and the CLI hasn't re-checked, the modal still shows Connected until the next session attempt's `detect_auth_error` catches it.
- **MCP SDK is pre-1.0-stable.** Pinned at `mcp>=1.0.0`. The surface is isolated in `mcp_server.py` so an SDK breaking change touches one file.
- **Concurrent install safety.** `_oauth.py:claude_binary_path` doesn't currently use a lock — two simultaneous login clicks within 2s could race the npm install into the shared tree. Low-risk in practice (modal debounces clicks); a follow-up could add an `asyncio.Lock`.
- **Worktree leak on hard crash.** Out-of-scope cleanup pass; document.
