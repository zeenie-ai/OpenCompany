# Claude Code Agent — Documentation Hub

`claude_code_agent` is a OpenCompany agent node that drives the real `claude` CLI as a
long-lived **interactive** subprocess (stdio pipes, no PTY) over the VSCode-extension
stream-json protocol — not the `claude -p` headless path. A warm `ClaudeSessionPool`
(keyed by the connected `simpleMemory.node_id`) preserves the session across turns;
memory continuity is claude-native via `--continue` / `--resume <UUID>` on a stable
`cwd=repo_root`. Connected tools and skills reach claude through an MCP bridge; the spawn
runs under `--permission-mode dontAsk` with a strict `--allowedTools` allowlist and stays
in interactive billing (entrypoint `claude-vscode`, not `sdk-cli`).

This page is a **router**. The detail lives in the documents below — start here, then jump.

## Where to read what

| Your question | Read |
|---|---|
| How does the whole integration work end-to-end? (architecture, memory & context compaction, sub-agent creation, data structures) | [claude_code_agent_architecture.md](./claude_code_agent_architecture.md) |
| How does interactive mode work? (stdio pipes vs PTY, stream-json events, `system/init` / `assistant` / `result`, session continuity, the four `claude.session.*` CloudEvents) | [claude_code_interactive_mode.md](./claude_code_interactive_mode.md) |
| The generic multi-provider runtime (`AICliService.run_batch`, worktree isolation, FastMCP bridge, the memory bridge `--continue`/`--resume`, plugin-folder layout) | [cli_agent_framework.md](./cli_agent_framework.md) |
| Does our implementation match the official Claude Code spec? (six invariants + status) | [cli_agent_canonical_patterns_rfc.md](./cli_agent_canonical_patterns_rfc.md) |
| Which CLI subcommands/flags exist, and which subset we emit | [claude_code_cli_reference.md](./claude_code_cli_reference.md) (snapshot) |
| Environment variables (`CLAUDE_CONFIG_DIR`, `MAX_MCP_OUTPUT_TOKENS`, `ENABLE_TOOL_SEARCH`, Bedrock/Vertex, telemetry) | [claude_code_env_vars_reference.md](./claude_code_env_vars_reference.md) (snapshot) |
| Permission-mode semantics (`default` / `acceptEdits` / `plan` / `dontAsk` / `bypassPermissions`); why OpenCompany uses `dontAsk` | [claude_code_permission_modes_reference.md](./claude_code_permission_modes_reference.md) (snapshot) |
| Headless / print mode (`claude -p`, `--output-format`, stream-json event schema) — the schema the pool parses off stdout | [claude_code_headless_reference.md](./claude_code_headless_reference.md) (snapshot) |
| SKILL.md frontmatter spec, discovery paths, `context: fork` — what we materialise skills against | [claude_code_skills_reference.md](./claude_code_skills_reference.md) (snapshot) |

The four `cli-reference` / `env-vars` / `permission-modes` / `headless` / `skills`
documents are **verbatim snapshots** of code.claude.com docs (fetched 2026-05-11), kept so
the contract the pool parses is pinned even if upstream changes.

## Plugin folder — [server/nodes/agent/claude_code_agent/](../server/nodes/agent/claude_code_agent/)

All claude-specific code is self-contained here; the generic framework at
`server/services/cli_agent/` imports nothing from `nodes/`.

| File | Responsibility |
|---|---|
| `__init__.py` | `ClaudeCodeAgentNode(SpecializedAgentBase)` + `Params`/`Output`; self-registers via `factory.py` (`register_provider` / `register_session_pool` / `register_skill_materialiser`) + `register_ws_handlers`. |
| `_provider.py` | Builds the spawn argv (`--output-format stream-json --input-format stream-json --verbose --ide`, `--permission-mode dontAsk`, `--allowedTools`, `--continue`/`--resume`). |
| `_pool.py` | `ClaudeSessionPool` — warm subprocess keyed by `simpleMemory.node_id`; stdout reader, session-UUID capture, crash-recovery respawn. |
| `_skills.py` | `materialise_skills` — writes connected SKILL.md trees under `<workspace>/.claude/skills/`, diff-based on warm reuse. |
| `_oauth.py` | Isolated `CLAUDE_CONFIG_DIR` (`<DATA_DIR>/claude/`), browser-OAuth bridge, binary install. |
| `_handlers.py` | WebSocket handlers (`cli_login` / `cli_auth_status`). |

## See also

- Memory bridge specifics (how `simpleMemory` feeds `--continue`, why `last_session_id` is display-only): [cli_agent_framework.md → Memory bridge](./cli_agent_framework.md).
- Generic CLI agent runtime shared with Codex / Gemini providers: `server/services/cli_agent/`.
