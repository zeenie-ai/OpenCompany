# RFC: How host platforms control CLI coding agents

| Field | Value |
|---|---|
| Status | Draft (research, revision 2) |
| Date | 2026-05-07 |
| Scope | `services/cli_agent` framework — Claude Code, Codex, Gemini-CLI agents spawned by OpenCompany |
| Companion code | [`server/services/cli_agent/`](../server/services/cli_agent/), [`server/nodes/agent/claude_code_agent/`](../server/nodes/agent/claude_code_agent/) |
| Companion docs | [cli_agent_framework.md](./cli_agent_framework.md), [claude_code_agent_architecture.md](./claude_code_agent_architecture.md) |

## Abstract

Host platforms that spawn `claude -p` from a backend (OpenCompany, Cline,
Continue, Cursor as a *client* of MCP) face a small number of well-defined
integration choices. This RFC consolidates the **official Claude Code
specification** ([code.claude.com/docs/en/tools-reference](https://code.claude.com/docs/en/tools-reference),
[code.claude.com/docs/en/mcp](https://code.claude.com/docs/en/mcp),
[code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills),
[code.claude.com/docs/en/cli-reference](https://code.claude.com/docs/en/cli-reference))
and quotes verbatim. It then audits OpenCompany's current
`services/cli_agent` framework against the spec, with file:line evidence,
and proposes the minimum set of changes to align.

This is research output. **No production code is modified by this
document.** The implementation plan that derives from it is tracked
separately.

## 1. Motivation

Runtime logs across multiple sessions show the framework correctly
registers the connected workflow tools, claude correctly receives the tool
list (`Processing request of type ListToolsRequest`), and the bearer-token
auth is correctly validated. But the agent **reaches for built-in
`WebSearch` first, gets denied, gives up** — never invoking the connected
`mcp__opencompany__perplexitySearch` even though it's in the registry. The
question is *why*, and whether OpenCompany's framework follows the documented
patterns. This RFC is the survey that answers that.

## 2. The official Claude Code specification

### 2.1 The built-in tool table

Source: [tools-reference#built-in-tools](https://code.claude.com/docs/en/tools-reference#built-in-tools).
Verbatim header: *"The tool names are the exact strings you use in
permission rules, subagent tool lists, and hook matchers. To disable a tool
entirely, add its name to the `deny` array in your permission settings."*

Tools relevant to host integration:

| Tool | Permission Required | Verbatim description |
|---|---|---|
| `Skill` | **Yes** | "Executes a skill within the main conversation" |
| `ToolSearch` | **No** | "Searches for and loads deferred tools when tool search is enabled" |
| `WebSearch` | **Yes** | "Performs web searches" |
| `WebFetch` | **Yes** | "Fetches content from a specified URL" |
| `Agent` | **No** | "Spawns a subagent with its own context window to handle a task" |
| `TaskCreate / Get / List / Update / Stop` | **No** | task-list management |
| `Bash`, `Edit`, `Write`, `Monitor`, `NotebookEdit`, `PowerShell` | **Yes** | filesystem / shell side-effects |
| `Read`, `Glob`, `Grep`, `LSP`, `ListMcpResourcesTool`, `ReadMcpResourceTool` | **No** | read-only |

**Key correction from earlier drafts:** `ToolSearch` is **permission-free**. It
does not need to be added to `--allowedTools`. It is always callable.

### 2.2 Custom tools and skills (verbatim)

> *"To add custom tools, connect an MCP server."*
>
> *"To extend Claude with reusable prompt-based workflows, write a skill,
> which runs through the existing `Skill` tool rather than adding a new
> tool entry."*

— [tools-reference, intro](https://code.claude.com/docs/en/tools-reference)

This is the load-bearing constraint for I-1 (skills are files, not MCP
tools). A platform that exposes "skill content" via an MCP tool is
working against the spec.

### 2.3 Skill discovery (verbatim)

> *"Claude Code watches skill directories for file changes. Adding,
> editing, or removing a skill … takes effect within the current session
> without restarting."*

Discovery paths ([skills#where-skills-live](https://code.claude.com/docs/en/skills#where-skills-live)):

| Scope | Path |
|---|---|
| Personal | `~/.claude/skills/<skill-name>/SKILL.md` |
| Project | `.claude/skills/<skill-name>/SKILL.md` |
| Plugin | `<plugin>/skills/<skill-name>/SKILL.md` |

The "project" entry is **relative to claude's cwd**. When OpenCompany spawns
`claude -p` with `cwd=<worktree>`, the worktree IS the project, so writing
`<worktree>/.claude/skills/<name>/SKILL.md` makes the skill auto-load.

### 2.4 Tool search and deferred tools (verbatim)

Source: [mcp#scale-with-mcp-tool-search](https://code.claude.com/docs/en/mcp#scale-with-mcp-tool-search).

> *"Tool search is enabled by default. MCP tools are deferred rather than
> loaded into context upfront, and Claude uses a search tool to discover
> relevant ones when a task needs them. Only the tools Claude actually
> uses enter context."*

Control surface:

| Mechanism | Effect |
|---|---|
| `ENABLE_TOOL_SEARCH` env var unset / `=true` | Default: defer all MCP tools, agent calls `ToolSearch` to load. |
| `ENABLE_TOOL_SEARCH=auto` | Load upfront if all MCP tools fit in **10% of context window**; defer overflow. |
| `ENABLE_TOOL_SEARCH=auto:<N>` | Custom percentage of context budget. |
| `ENABLE_TOOL_SEARCH=false` | No deferral — every MCP tool loaded into context. |
| `alwaysLoad: true` in the server's `--mcp-config` entry | Per-server opt-out from deferral. **Blocks startup until that server connects (5s cap).** |
| `_meta["anthropic/alwaysLoad"]: true` on a tool | Per-tool opt-out (sent in `tools/list`). |
| `{"permissions":{"deny":["ToolSearch"]}}` | Disable the search tool entirely. |

Critical model constraint: *"This feature requires models that support
`tool_reference` blocks: Sonnet 4 and later, or Opus 4 and later. Haiku
models do not support tool search."*

Truncation: tool descriptions and server-instructions truncate at **2KB**
each.

### 2.5 List-changed notifications (verbatim)

Source: [mcp#dynamic-tool-updates](https://code.claude.com/docs/en/mcp#dynamic-tool-updates).

> *"Claude Code supports MCP `list_changed` notifications, allowing MCP
> servers to dynamically update their available tools, prompts, and
> resources without requiring you to disconnect and reconnect. When an
> MCP server sends a `list_changed` notification, Claude Code
> automatically refreshes the available capabilities from that server."*

Servers should send it whenever they add/remove/rename a tool, prompt, or
resource at runtime. (Verify FastMCP ≥1.0 emits this on `add_tool` /
`remove_tool` — library default; not version-pinned in this repo.)

### 2.6 The three transports (verbatim, ranked)

Source: [mcp#installing-mcp-servers](https://code.claude.com/docs/en/mcp#installing-mcp-servers).

| Transport | Verbatim |
|---|---|
| **HTTP** (Streamable) | *"HTTP servers are the recommended option for connecting to remote MCP servers. This is the most widely supported transport for cloud-based services."* |
| **SSE** | *"Warning: The SSE (Server-Sent Events) transport is deprecated. Use HTTP servers instead, where available."* |
| **stdio** | *"local processes on your machine. They're ideal for tools that need direct system access or custom scripts."* |

Canonical as of 2026-05: **streamable HTTP**. SSE is deprecated.

### 2.7 `--mcp-config` and `--strict-mcp-config` (verbatim)

> *"Load MCP servers from JSON files or strings (space-separated)."*
> — [`--mcp-config`](https://code.claude.com/docs/en/cli-reference)
>
> *"Only use MCP servers from `--mcp-config`, ignoring all other MCP
> configurations."* — [`--strict-mcp-config`](https://code.claude.com/docs/en/cli-reference)

Important: `--strict-mcp-config` blocks `~/.claude.json`, `.mcp.json`,
plugin-bundled servers, and claude.ai connectors. **It does NOT block
built-in tools** (Bash, WebSearch, Skill, etc.) — those are unconditional.

Accepted JSON shape (same as `.mcp.json`):

```json
{
  "mcpServers": {
    "<name>": {
      "type": "http",
      "url": "https://...",
      "headers": { "Authorization": "Bearer ${TOKEN}" },
      "alwaysLoad": true
    }
  }
}
```

`${VAR}` and `${VAR:-default}` expansion is supported in `command`,
`args`, `env`, `url`, `headers`. ([mcp#project-scope](https://code.claude.com/docs/en/mcp#project-scope))

### 2.8 Permission-rule syntax for MCP tools (verbatim)

Source: [mcp#permission-rule-syntax](https://code.claude.com/docs/en/mcp#permission-rule-syntax).

| Pattern | Match |
|---|---|
| `mcp__<server>` | Every tool exposed by that MCP server |
| `mcp__<server>__<tool>` | Single specific tool |
| `mcp__<server>__<tool>(<arg-glob>)` | Argument-pattern match (Bash-style glob) |

Server/prompt names are normalised: spaces become underscores. **Hook
matchers use the same syntax with regex tail**, e.g. `mcp__<server>__.*`
for `PreToolUse` over all tools from one server. (`Bash(git log *)` style
arg-globbing applies only to Bash.)

### 2.9 Output limits (host-platform relevant)

Source: [mcp#output-size](https://code.claude.com/docs/en/mcp).

- Warning at **10,000 tokens** per tool call.
- Default ceiling **25,000 tokens**; raise via `MAX_MCP_OUTPUT_TOKENS` env var.
- Per-tool override: `_meta["anthropic/maxResultSizeChars"]` (hard ceiling
  500,000 chars).
- Server name `workspace` is reserved.

## 3. The four invariants (revised)

Across the official documentation:

> **I-1. Skills are files on disk, not MCP tools.** Universal.
> Spec text from §2.2: *"To extend Claude with reusable prompt-based
> workflows, write a skill, which runs through the existing `Skill` tool
> rather than adding a new tool entry."* Skills auto-discover from
> `<cwd>/.claude/skills/<name>/SKILL.md` (project-scope; §2.3).

> **I-2. MCP tools come over MCP, period.** Either stdio (local) or
> Streamable-HTTP (remote, SSE deprecated since 2025-Q4 — §2.6). Spawned
> `claude -p` headless mode uses `--mcp-config` (HTTP or stdio); IDE-host
> mode uses WebSocket via the lockfile pattern. The two paths are not
> interchangeable.

> **I-3. `notifications/tools/list_changed` is required** when tools are
> registered dynamically per session. Quoted from §2.5: *"When an MCP
> server sends a `list_changed` notification, Claude Code automatically
> refreshes the available capabilities from that server."* Without it,
> tools registered after the first `tools/list` are invisible.

> **I-4. By default, MCP tools are deferred.** *"MCP tools are deferred
> rather than loaded into context upfront"* (§2.4). The agent uses the
> built-in `ToolSearch` (permission-free) to discover relevant tools
> when a task needs them. To force tools into context at startup, set
> `alwaysLoad: true` on the server entry in `--mcp-config`.

> **I-5. Tool name is the contract; host filters which entries reach
> the model.** Per-vendor namespacing
> (`mcp__<server>__<tool>`, `mcp__ide__*`). Claude Code's IDE extension
> hosts ~12 MCP tools but exposes only 2 to the model
> ([anthropics/claude-code#40766](https://github.com/anthropics/claude-code/issues/40766)).
> The pattern: register many internally, filter the model-visible
> subset.

> **I-6. Native session continuity needs stable cwd + `--resume <UUID>`.**
> Claude stores per-session JSONL under
> `<CLAUDE_CONFIG_DIR>/projects/<project_key>/<session_id>.jsonl` where
> `project_key = re.sub(r"[^a-zA-Z0-9.-]", "-", str(cwd))`. Verified
> against on-disk dir names: a worktree path of
> `D:\startup\projects\OpenCompany\server\...\wt_t_af48d0a7` produces
> `D--startup-projects-OpenCompany-server-...-wt-t-af48d0a7`.
>
> Two consequences:
>
> (a) **System-prompt injection is the wrong primitive for prior
> conversation.** Universal P2 pattern across Cline / Continue.dev /
> Aider / Cursor / Hermes / Anthropic SDK is *re-pass the full
> messages array each turn*. For raw `claude -p` the documented
> equivalent is native session resume; `--append-system-prompt`-stuffed
> markdown is treated by claude as a rendered document, not as turns
> it actually had.
>
> (b) **Per-task ephemeral worktree paths (`wt_t_<random>`) break
> `--resume`.** Every spawn lands in a brand-new project dir with zero
> prior JSONL → "No conversation found with session ID: <UUID>"
> regardless of UUID stability or `--input-format=stream-json`
> stdin protocol.
>
> Fix is structural, not format-related: when memory is wired, spawn
> under `cwd=repo_root` so `project_key` is stable across runs, and
> pass either `--resume <last_session_id>` (subsequent runs) or
> `--session-id <UUID5(memory_node_id, simpleMemory.session_id)>`
> (first run). On "No conversation found" error, auto-clear the stale
> `last_session_id` so the next run falls through to `--session-id
> <UUID5>` and self-heals.

## 4. OpenCompany `cli_agent` alignment audit

File:line evidence from a focused read of `services/cli_agent/`:

### 4.1 Argv flags emitted by the Claude provider

[`claude_code_agent/_provider.py::interactive_argv`](../server/nodes/agent/claude_code_agent/_provider.py)
(the Anthropic/Claude provider moved into the plugin folder; the generic
`services/cli_agent/providers/` now holds only `google_gemini.py` +
`openai_codex.py`). Post-cutover the pool path drives claude in
interactive stream-json mode, not `claude -p` headless — so the argv is
built by `interactive_argv`, not a `-p`-prefixed print-mode command:

**Always-on:** `--output-format stream-json`, `--input-format
stream-json`, `--verbose`, `--ide`, `--model`, `--permission-mode`,
`--allowedTools <csv>`.

**Conditional:** `--mcp-config <json>` + `--strict-mcp-config` (when
`mcp_endpoint_url` and `mcp_bearer_token` set), `--continue`/`--resume`
(memory-bound runs, mutually exclusive), `--max-budget-usd`,
`--append-system-prompt`, `--effort`, `--fallback-model`, `--add-dir`,
`--disallowedTools`, `--agent`.

### 4.2 `--mcp-config` JSON shape

[`claude_code_agent/_provider.py::interactive_argv`](../server/nodes/agent/claude_code_agent/_provider.py):

```json
{
  "mcpServers": {
    "opencompany": {
      "type": "http",
      "url": "<mcp_endpoint_url>",
      "headers": { "Authorization": "Bearer <mcp_bearer_token>" },
      "alwaysLoad": true
    }
  }
}
```

**Closed (I-4).** Per §2.4, with `ENABLE_TOOL_SEARCH` unset (the default
for spawned subprocesses), the agent would otherwise **defer all
`mcp__opencompany__*` tools** and only load them via `ToolSearch`-driven
discovery — which the spawned agent doesn't always call. The
`"alwaysLoad": true` entry (present in `interactive_argv`'s emitted
`--mcp-config`) forces the tools into context at session start; startup
blocks ≤5s waiting for connection.

### 4.3 `--allowedTools` value

[`claude_code_agent/_provider.py::interactive_argv`](../server/nodes/agent/claude_code_agent/_provider.py):

```
mcp__opencompany__<each connected node_type>,
Skill                              # only when >=1 skill is wired
mcp__opencompany__getWorkspaceFiles,
mcp__opencompany__listSkills,
mcp__opencompany__getSkill,
mcp__opencompany__getCredential,
mcp__opencompany__broadcastLog
```

**Post-cutover: strict MCP-only allowlist (gated by `--permission-mode
dontAsk`).** Claude's built-in escape hatches (`Read`, `Edit`, `Bash`,
`Glob`, `Grep`, `Write`, `WebSearch`, `WebFetch`) are intentionally NOT
in the default allowlist — equivalent capability is wired explicitly via
the `fileRead` / `fileModify` / `fsSearch` / `shell` / `browser` /
`perplexitySearch` workflow tools. The built-in `Skill` tool is added
**conditionally**, only when at least one skill is connected through
`input-skill` (paired with SKILL.md materialisation under
`<cwd>/.claude/skills/`). Callers can opt specific built-ins back in
per-task via `ClaudeTaskSpec.allowed_tools`. This reverses the earlier
"WebSearch/WebFetch missing → agent gets denied" gap: the framework now
routes every capability through explicitly wired MCP tools rather than
built-in fallbacks. Locked by
`test_no_claude_builtins_in_default_allowlist`.

`ToolSearch` is **not** needed in the allowlist (permission-free per §2.1).

### 4.4 IDE lockfile

[`lockfile.py:64-82`](../server/services/cli_agent/lockfile.py): writes

```json
{"port": <int>,
 "url": "http://127.0.0.1:<port>/mcp/ide/mcp",
 "authToken": "<token>",
 "workspaceFolders": ["<workspace_dir>"],
 "ideName": "claude",
 "transport": "http",
 "pid": <os.getpid()>}
```

**Spec note.** The lockfile is for IDE-host scenarios (VSCode publishes
this so a spawned `claude` can discover the IDE). The pool path now
emits `--ide` in its argv, so claude does discover and connect to the
lockfile's MCP endpoint at spawn time. `transport: "http"` stays —
that matches what our FastMCP sub-app at `/mcp/ide` actually speaks
(streamable HTTP). The `"ws"` upgrade (matching what Claude's VSCode
extension publishes in its own lockfile per the live extension dump
in [#16434](https://github.com/anthropics/claude-code/issues/16434))
is **deferred** — `"http"` is interoperable and unblocks the pool
path today.

### 4.5 FastMCP server tools

[`mcp_server.py`](../server/services/cli_agent/mcp_server.py),
[`workflow_tools.py`](../server/services/cli_agent/workflow_tools.py):

5 built-in: `getWorkspaceFiles`, `listSkills`, `getSkill`,
`getCredential`, `broadcastLog`.
Plus dynamic per-batch: one `mcp__opencompany__<node_type>` per connected
workflow tool (refcount-tracked, schema inferred from plugin's Pydantic
`Params` field-for-field).

**Spec gap (I-1).** `listSkills` and `getSkill` violate the documented
pattern. Skills should be files in `<worktree>/.claude/skills/`, invoked
via the built-in `Skill` tool. The current MCP-tool wrappers exist but
the agent never calls them.

### 4.6 Spawn env

[`session.py`](../server/services/cli_agent/session.py):
`PYTHONUNBUFFERED=1`, `CLAUDE_CONFIG_DIR=<OPENCOMPANY_CLAUDE_DIR>` (claude only),
`<provider.ide_lock_env_var>=<lockfile_path>`,
`OPENCOMPANY_PARENT_RUN_ID=<workflow_id>:<node_id>:<token[:8]>`.

**Spec consideration.** Setting `ENABLE_TOOL_SEARCH=false` here would
disable tool-search deferral globally — alternative to per-server
`alwaysLoad: true`. Per-server is finer-grained; env var is simpler.
Either works.

### 4.8 Memory bridge — `simpleMemory` → `claude_code_agent` (DONE in `ecbe69b`)

**Status:** native claude session continuity works end-to-end. The
mechanism was later simplified from the UUID5 / `--session-id`
round-trip described in this section to `--continue` (first cold spawn
for a memory-wired run) + intra-process stream-json multi-turn against a
warm subprocess + `--resume <UUID>` for crash recovery — all on a stable
`cwd=repo_root`. The entry point is
[`claude_code_agent/__init__.py::execute_op`](../server/nodes/agent/claude_code_agent/__init__.py)
(sets `continue_session = bool(memory_data)`) and the warm-subprocess
pool at [`claude_code_agent/_pool.py`](../server/nodes/agent/claude_code_agent/_pool.py).
Current canonical description:
[cli_agent_framework.md → Memory bridge](./cli_agent_framework.md#memory-bridge--simplememory--claude_code_agent).
The UUID5/`--session-id` prose below is retained for historical context.

**Project-key derivation verified empirically** by listing
`<DATA_DIR>/claude/projects/` on disk and reproducing each name from
its source cwd via `re.sub(r"[^a-zA-Z0-9.-]", "-", str(cwd))`. Three
sample names matched byte-for-byte:

| cwd | project_key |
|---|---|
| `D:\startup\projects\OpenCompany` | `D--startup-projects-OpenCompany` |
| `D:\startup\projects\OpenCompany\server` | `D--startup-projects-OpenCompany-server` |
| `D:\startup\projects\OpenCompany\server\...\wt_t_af48d0a7` | `D--startup-projects-OpenCompany-server-...-wt-t-af48d0a7` |

**Failure-mode evidence captured during dev** (log fragments):

- `[CC-Agent stderr] No conversation found with session ID: cddd6def-...`
  every spawn → confirmed that ephemeral worktree paths were defeating
  `--resume`.
- After switching to `cwd=repo_root`: same UUID continues across spawns,
  `r.session_id` matches `last_session_id` on subsequent runs.
- `_persist_memory` broadcast addition fixed a UI-staleness issue where
  `memory_content` was correctly written to the DB but the simpleMemory
  parameter panel only showed the update after a page reload.

**Pre-bridge attempts that didn't work** (recorded for posterity so
this isn't re-attempted):

- **System-prompt injection of markdown turns.** Claude treated
  `### **Human** (timestamp)` headers as a rendered document
  description, not as actual conversation it had. Even with explicit
  framing ("the following is your prior conversation, continue
  naturally") claude responded "I don't have context from a previous
  conversation about what to continue" — the system-prompt channel
  isn't the right primitive.
- **JSONL injection in the same channel.** Same fundamental problem;
  format is irrelevant when the channel is wrong.
- **Always `--session-id <UUID5>` without `--resume`.** Claude
  accepted the UUID but didn't auto-load prior history from disk —
  `--session-id` only *specifies* the UUID for THIS conversation, it
  is not idempotent across spawns.
- **`--resume <UUID>` against random-suffix worktree cwd.** Always
  failed because the project_key changed every run.

The Anthropic SDK Python package's `materialize_resume_session()`
solves the same problem by writing the JSONL to a temp
`CLAUDE_CONFIG_DIR` and spawning with `--resume`. Our approach is
equivalent in outcome (claude finds its own JSONL via `--resume`)
without the SDK dependency — we just keep cwd stable so the JSONL
claude wrote on its previous turn stays findable.

### 4.7 Worktree contents

[`session.py:_pre_spawn`](../server/services/cli_agent/session.py): only
the git-worktree directory + the IDE lockfile in
`provider.ide_lockfile_dir`. **No `.claude/skills/`, no `CLAUDE.md`, no
`AGENTS.md`.**

**Spec gap (I-1).** Materialise `<worktree>/.claude/skills/<name>/SKILL.md`
for each connected skill in `_pre_spawn`. Optional: write a synthesised
`<worktree>/CLAUDE.md` with the connected-tool list — not strictly required
since `--append-system-prompt` covers it, but `CLAUDE.md` is the
documented project-instruction surface.

## 5. Alignment matrix

| Invariant | Status | Action |
|---|---|---|
| **I-1** Skills as files | **DONE.** [`claude_code_agent/_skills.py::materialise_skills`](../server/nodes/agent/claude_code_agent/_skills.py) writes connected skills to `<cwd>/.claude/skills/<name>/`, invoked from both the pool spawn ([`_pool.py`](../server/nodes/agent/claude_code_agent/_pool.py)) and the non-pool `session.py::_pre_spawn` (via the `get_skill_materialiser` registry in [`services/cli_agent/factory.py`](../server/services/cli_agent/factory.py)). `mcp__opencompany__listSkills` / `getSkill` retained as a transitional fallback. | Drop `getSkill`/`listSkills` MCP tools after one release. |
| **I-2** MCP transport | **Aligned.** Streamable-HTTP via `--mcp-config`. | None. |
| **I-3** `list_changed` notification | **DONE (`b40011e`).** [`workflow_tools._schedule_list_changed_notify`](../server/services/cli_agent/workflow_tools.py) fires after each `add_tool` / `remove_tool` since FastMCP doesn't emit it automatically. | Optional: unit test asserting `session.send_tool_list_changed` is called. |
| **I-4** Tool-search deferral | **DONE.** `"alwaysLoad": true` set on the `opencompany` server entry in [`claude_code_agent/_provider.py::interactive_argv`](../server/nodes/agent/claude_code_agent/_provider.py). | None. |
| **I-5** Visible-tool filtering | **Gap.** All 5 built-in OpenCompany MCP tools (including `getCredential`, `broadcastLog`) are visible to the model. | Mark internal-only tools `_meta["anthropic/alwaysLoad"]: false` or filter via FastMCP middleware. **Defer** — not breaking today. |
| **I-6** Native session continuity | **DONE.** [`session.py`](../server/services/cli_agent/session.py) keeps a stable cwd for memory-bound spawns; the warm-subprocess pool at [`claude_code_agent/_pool.py`](../server/nodes/agent/claude_code_agent/_pool.py) preserves the session across turns. [`claude_code_agent/__init__.py`](../server/nodes/agent/claude_code_agent/__init__.py) sets `continue_session = bool(memory_data)` → argv emits `--continue` (first cold spawn) with `--resume <UUID>` reserved for crash recovery. [`service.py:_persist_memory`](../server/services/cli_agent/service.py) appends turns to `memory_content`, saves `last_session_id` (display-only), broadcasts `node_parameters_updated`, and auto-clears stale UUIDs via `_clear_stale_session_id`. See §4.8. | Markdown `memory_content` remains the UI mirror, not the resume channel. |
| **System-prompt directive** (Cursor / `CLAUDE.md` pattern) | **DONE (`b40011e`).** Second `--append-system-prompt` listing connected `mcp__opencompany__*` tools. | None. |
| `--allowedTools` strict MCP-only allowlist | **DONE (superseded R3).** Built-in escape hatches (`Read`/`Edit`/`Bash`/`Glob`/`Grep`/`Write`/`WebSearch`/`WebFetch`) are NOT in the default allowlist; `Skill` is added conditionally (only when a skill is wired). `default_allowed_tools: ""` in [`ai_cli_providers.json`](../server/config/ai_cli_providers.json); allowlist assembled in [`_provider.py::interactive_argv`](../server/nodes/agent/claude_code_agent/_provider.py). Gated by `--permission-mode dontAsk`. | None. |
| Composio-style server-side credentials | **Aligned.** `getCredential` allowlist + `auth_service.get_api_key`. | None. |
| Hermes-style `provider_data` envelope | **Aligned.** [`protocol.py:SessionResult.provider_data`](../server/services/cli_agent/protocol.py). | None. |
| Composio-style parent-run-id | **Aligned.** `OPENCOMPANY_PARENT_RUN_ID` env var. | None. |

## 6. Recommendations (mapped to the spec)

**R1 (DONE in `b40011e`). Skills materialised into `<cwd>/.claude/skills/<name>/`
on `_pre_spawn` (Closes I-1).** Uses
[`skill_loader.load_skill_async(name)`](../server/services/skill_loader.py).
For memory-bound runs the cwd is `repo_root` so skills land at
`<repo_root>/.claude/skills/`; for non-memory runs they go under the
per-task worktree. MCP `listSkills` / `getSkill` retained as a fallback.

**R2 (DONE in `b40011e`). `"alwaysLoad": true` set on the `opencompany`
entry in `--mcp-config` (Closes I-4).** Per §2.4: *"…also blocks startup
until that server connects (5s cap)."* The 5s wait is acceptable — the
FastMCP server is already up before spawn.

**R3 (DONE, then superseded by the strict-allowlist cutover).** An
earlier revision extended `--allowedTools` with `Skill,WebSearch,WebFetch`.
The current design instead ships a strict **MCP-only** allowlist:
`default_allowed_tools: ""` in
[`ai_cli_providers.json`](../server/config/ai_cli_providers.json), and
[`_provider.py::interactive_argv`](../server/nodes/agent/claude_code_agent/_provider.py)
assembles `mcp__opencompany__<node_type>` per wired tool + the 5 infra MCP
tools + the built-in `Skill` (conditionally, when a skill is wired),
gated by `--permission-mode dontAsk`. `WebSearch`/`WebFetch`/`Bash`/etc.
are intentionally excluded — equivalent capability is wired explicitly
via workflow tools. `ToolSearch` intentionally NOT added — it's
permission-free.

**R4 (DONE in `b40011e`). System-prompt directive when MCP tools are
wired (the `CLAUDE.md` / Cursor-rules pattern).** Second
`--append-system-prompt` listing connected `mcp__opencompany__*` tools.

**R5 (followup).** Filter `getCredential` and `broadcastLog` from the
model-visible tool list — they're host-internal RPC, the model has no
business calling them. Use FastMCP middleware on `tools/list`.
**Pending** — not breaking.

**R6 (DONE in `ecbe69b`). Native session continuity for memory-bound
runs (Closes I-6).** Three coupled mechanisms:

1. **Stable cwd.** [`AICliSession.cwd()`](../server/services/cli_agent/session.py)
   returns `self._repo_root` when `memory_bound=True`. `_pre_spawn`
   skips `git worktree add`; `cleanup` skips `git worktree remove`.
   Confirms claude's `project_key` is constant across runs.

2. **`--continue` first-run / warm-subprocess multi-turn / `--resume`
   crash recovery.**
   [`claude_code_agent/__init__.py`](../server/nodes/agent/claude_code_agent/__init__.py)
   sets `continue_session = bool(memory_data)`; the argv-builder
   [`_provider.py::interactive_argv`](../server/nodes/agent/claude_code_agent/_provider.py)
   emits `--continue` on the first cold spawn for a memory-wired run
   (claude auto-loads the most recent conversation under the stable
   cwd's `project_key`). Subsequent turns are written as stream-json to
   the same warm subprocess held by
   [`_pool.py`](../server/nodes/agent/claude_code_agent/_pool.py), which
   preserves the session UUID in-process. If the subprocess dies between
   batches, the pool respawns with `--resume <current_session_uuid>`.
   `--continue` and `--resume` are mutually exclusive in argv emission.
   (This supersedes the earlier UUID5 / `--session-id` first-run dance.)

3. **Auto-clear stale `last_session_id`.**
   [`_persist_memory`](../server/services/cli_agent/service.py)
   substring-matches `"No conversation found with session ID"` in any
   result's `error`; when matched, fires `_clear_stale_session_id`
   which wipes `simpleMemory.last_session_id` only (preserves
   `memory_content`). Next run falls into the first-run branch and
   recovers automatically.

4. **Live UI refresh.** `_persist_memory` broadcasts
   `node_parameters_updated` after `save_node_parameters` so the
   simpleMemory parameter panel refetches the moment the run
   completes — mirrors the manual save broadcast in
   [`routers/websocket.py:handle_save_node_parameters`](../server/routers/websocket.py).
   Post-commit `7c9e873`, all three emission sites (Claude CLI
   `_persist_memory`, parameter-panel save, F4.B AgentWorkflow
   per-turn) wrap [`WorkflowEvent.node_parameters_updated`](../server/services/events/envelope.py)
   (CloudEvents v1.0, `type="com.opencompany.node.parameters.updated"`)
   via the shared
   [`StatusBroadcaster.broadcast_node_parameters_updated`](../server/services/status_broadcaster.py)
   wrapper. `data.source` (`"user"` / `"cli"` / `"agent"`) marks the
   origin; locked by `tests/test_cloudevents_node_parameters.py`.

5. **Parallel-batch guard.** `len(tasks) > 1` with memory wired raises
   `NodeUserError` at handler entry — concurrent `--continue` spawns
   against one project_key would race claude's session resolution.

Markdown is the UI mirror. The `simpleMemory.memory_content` field
keeps mirroring conversation turns via
[`services.memory.append_to_memory_markdown`](../server/services/memory/markdown.py)
+ [`trim_markdown_window`](../server/services/memory/markdown.py) for
human readability. **It is not the resume channel** — claude reads its
own JSONL via `--resume`. User edits to `memory_content` do not
influence claude's next response; the canonical record lives in
claude's on-disk JSONL.

## 7. Open questions

- **Concurrent batches sharing the FastMCP registry.** Refcount in
  [`workflow_tools.py`](../server/services/cli_agent/workflow_tools.py)
  is unlocked. Theoretical race; today's call ordering is synchronous.
  Add `threading.Lock` for safety.
- **Verifying FastMCP emits `notifications/tools/list_changed`.**
  Library default; pin a unit test.
- **Worktree-as-cwd skill discovery semantics.** Documented for
  project-scope but not explicitly for `git worktree add` paths. Smoke-test
  per claude-code release.
- **`MAX_MCP_OUTPUT_TOKENS` / `_meta["anthropic/maxResultSizeChars"]`.**
  We have 0 tests for output-truncation behaviour. Tools that return
  large blobs (browser screenshots, file contents) will hit the 25K
  default ceiling silently. **Defer**, document in
  [cli_agent_framework.md](./cli_agent_framework.md).
- **`headersHelper` for short-lived tokens.** Today our token is
  per-batch (lifetime ≈ minutes); doesn't need rotation. If we ever add
  longer-lived MCP servers, the `headersHelper` mechanism is the
  documented path.

## 8. References (verbatim official sources)

- [code.claude.com/docs/en/tools-reference](https://code.claude.com/docs/en/tools-reference) — built-in tool table, custom-tools/skills boundary, Bash carry-over, naming convention.
- [code.claude.com/docs/en/mcp](https://code.claude.com/docs/en/mcp) — three transports, install scopes, `list_changed`, OAuth 2.0/2.1, output limits, **§ Scale with MCP tool search**.
- [code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills) — discovery paths, frontmatter, file-watch reload semantics.
- [code.claude.com/docs/en/cli-reference](https://code.claude.com/docs/en/cli-reference) — `--mcp-config`, `--strict-mcp-config`, `--allowedTools`, `--add-dir`, `--append-system-prompt`.
- [code.claude.com/docs/en/permissions](https://code.claude.com/docs/en/permissions) — permission-rule syntax for MCP tools.
- [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks) — `PreToolUse` matcher syntax (`mcp__<server>__.*`).
- [anthropics/claude-code#16434](https://github.com/anthropics/claude-code/issues/16434) — IDE-extension lockfile shape (transport=`ws`).
- [anthropics/claude-code#40766](https://github.com/anthropics/claude-code/issues/40766) — visible-tool filtering precedent.
- [cursor.com/docs/rules](https://cursor.com/docs/rules) — per-request system-prompt prepend.
- [agentclientprotocol.com](https://agentclientprotocol.com/get-started/introduction) — Cursor's editor↔agent ACP (not used by Claude Code).
- [docs.composio.dev/docs/mcp-quickstart](https://docs.composio.dev/docs/mcp-quickstart) — server-side credential injection model.
- [github.com/NousResearch/hermes-agent — agent/transports/types.py](https://github.com/NousResearch/hermes-agent/blob/main/agent/transports/types.py) — `provider_data` envelope.

## Appendix A — Source coverage caveats

- **Codex VSCode extension** is closed-source ([openai/codex#5822](https://github.com/openai/codex/issues/5822)).
- **`alwaysLoad: true` 5s startup cap** — documented in §2.4; the
  `MCP_CONNECTION_NONBLOCKING=1` env var does **not** override this for
  always-load servers per the spec.
- **`ENABLE_TOOL_SEARCH` model gating** — Sonnet 4 / Opus 4 and later
  only. Haiku models have no tool search; with Haiku, all MCP tools are
  loaded upfront regardless. OpenCompany default model
  (`claude-sonnet-4-6`) is in scope; Haiku users get implicit
  always-load behaviour.
- **`workspace` server name reserved** — verbatim from the spec. Don't
  use it.
- **FastMCP `list_changed` behaviour is library-version dependent.** ≥1.0
  is documented to emit; verify before relying.
