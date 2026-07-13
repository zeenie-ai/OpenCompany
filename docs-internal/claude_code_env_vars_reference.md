# Claude Code environment variables reference (snapshot)

> **Source:** [code.claude.com/docs/en/env-vars](https://code.claude.com/docs/en/env-vars)
> **Fetched:** 2026-05-11
> **Note:** the upstream page was returned partially-truncated by the fetcher;
> the categories below cover what was visible. Refetch when adding a new env
> var to verify the full list. The companion CLI reference is
> [`claude_code_cli_reference.md`](./claude_code_cli_reference.md).

This is the set of environment variables Claude Code reads at startup. Where
relevant we annotate which ones OpenCompany's `services/cli_agent/` framework
sets or relies on.

## Authentication & API configuration

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | API key sent as `X-Api-Key` header. |
| `ANTHROPIC_AUTH_TOKEN` | Custom `Authorization` header value (prefixed with `Bearer`). |
| `ANTHROPIC_AWS_API_KEY` | Workspace API key for Claude Platform on AWS. |
| `ANTHROPIC_AWS_BASE_URL` | Override Claude Platform on AWS endpoint URL. |
| `ANTHROPIC_AWS_WORKSPACE_ID` | Required workspace ID for AWS (sent as header). |
| `ANTHROPIC_BASE_URL` | Override API endpoint (proxy / gateway routing). |
| `ANTHROPIC_CUSTOM_HEADERS` | Custom headers in `Name: Value` format. |

## Bedrock & Vertex

| Variable | Purpose |
|---|---|
| `ANTHROPIC_BEDROCK_BASE_URL` | Override Bedrock endpoint URL. |
| `ANTHROPIC_BEDROCK_MANTLE_BASE_URL` | Override Bedrock Mantle endpoint URL. |
| `ANTHROPIC_BEDROCK_SERVICE_TIER` | Bedrock service tier (`default`, `flex`, `priority`). |
| `ANTHROPIC_VERTEX_BASE_URL` | Override Vertex AI endpoint URL. |
| `ANTHROPIC_VERTEX_PROJECT_ID` | GCP project ID for Vertex AI. |
| `CLAUDE_CODE_USE_BEDROCK` | Route requests through Bedrock. |
| `CLAUDE_CODE_USE_VERTEX` | Route requests through Vertex. |

## Model configuration

| Variable | Purpose |
|---|---|
| `ANTHROPIC_MODEL` | Name of model setting to use. |
| `ANTHROPIC_SMALL_FAST_MODEL` | Override the small fast model. |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | Custom Haiku model ID. |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | Custom Sonnet model ID. |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | Custom Opus model ID. |
| `ANTHROPIC_CUSTOM_MODEL_OPTION` | Add custom model to `/model` picker. |
| `ANTHROPIC_CUSTOM_MODEL_OPTION_NAME` | Display name for custom model. |
| `ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION` | Display description for custom model. |
| `CLAUDE_CODE_SUBAGENT_MODEL` | Model used by subagents. |

## Bash tool

| Variable | Purpose |
|---|---|
| `BASH_DEFAULT_TIMEOUT_MS` | Default timeout for long-running bash commands. Default: `120000` (2 min). |
| `BASH_MAX_OUTPUT_LENGTH` | Max characters in bash output before output is saved to a file. |
| `BASH_MAX_TIMEOUT_MS` | Maximum timeout the model can set. Default: `600000` (10 min). |

## MCP integration

| Variable | Purpose |
|---|---|
| `MCP_TIMEOUT` | MCP server connect timeout. |
| `MCP_TOOL_TIMEOUT` | Per-tool-call timeout. |
| `MAX_MCP_OUTPUT_TOKENS` | Per-tool output ceiling. Default `25000`; warn at `10000`. **Relevant for OpenCompany** — workflow tools that return large blobs (browser screenshots, file contents) silently hit this. See [`cli_agent_canonical_patterns_rfc.md` §2.9](./cli_agent_canonical_patterns_rfc.md). |
| `ENABLE_TOOL_SEARCH` | Tool deferral control: `true` (default, defer all MCP tools, agent calls `ToolSearch`), `false` (load all upfront), `auto` (load if all fit in 10 % of context), `auto:<N>` (custom %). **OpenCompany sidesteps this** by setting `"alwaysLoad": true` on the `opencompany` mcp-config entry instead — see canonical patterns RFC §2.4 / R2. Requires Sonnet 4 / Opus 4 or later; Haiku has no tool search. |

## Telemetry & disable flags

| Variable | Purpose |
|---|---|
| `DISABLE_TELEMETRY` | Disable telemetry collection. |
| `DISABLE_ERROR_REPORTING` | Disable error reporting. |
| `DISABLE_AUTOUPDATER` | Disable automatic updates. |
| `DO_NOT_TRACK` | Standard opt-out signal. |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | Equivalent to setting multiple disable variables. |
| `CLAUDE_CODE_ENABLE_TELEMETRY` | Enable OpenTelemetry data collection. |
| `DISABLE_BUG_COMMAND` | Disable the `/bug` command. |
| `DISABLE_COST_WARNINGS` | Suppress cost-warning prompts. |
| `DISABLE_NON_ESSENTIAL_MODEL_CALLS` | Skip non-essential model calls (e.g. naming heuristics). |

## Feature disables / behaviour

| Variable | Purpose |
|---|---|
| `CLAUDE_CODE_DISABLE_AGENT_VIEW` | Disable background agents and agent view. |
| `CLAUDE_CODE_DISABLE_ATTACHMENTS` | Disable attachment processing. |
| `CLAUDE_CODE_DISABLE_AUTO_MEMORY` | Disable auto memory creation/loading. |
| `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` | Disable all background task functionality. |
| `CLAUDE_CODE_DISABLE_CRON` | Disable scheduled tasks. |
| `CLAUDE_CODE_DISABLE_FAST_MODE` | Disable fast mode. |
| `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY` | Disable session-quality surveys. |
| `CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING` | Disable `/rewind` functionality. |
| `CLAUDE_CODE_DISABLE_TERMINAL_TITLE` | Don't update the terminal title. |
| `CLAUDE_CODE_DISABLE_THINKING` | Force-disable extended thinking. |
| `CLAUDE_CODE_ENABLE_TASKS` | Enable task tracking in non-interactive mode. |
| `CLAUDE_CODE_EFFORT_LEVEL` | Effort level (`low`, `medium`, `high`, `xhigh`, `max`, `auto`). |
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | Maximum output tokens for requests. |
| `CLAUDE_CODE_MAX_RETRIES` | Retries for failed API requests. Default: `10`. |
| `CLAUDE_CODE_API_KEY_HELPER_TTL_MS` | How long the API-key helper output is cached. |
| `CLAUDE_CODE_IDE_SKIP_AUTO_INSTALL` | Skip auto-install of the IDE extension. |
| `CLAUDE_CODE_SIMPLE` | Sets bare mode (see `--bare`). |
| `CLAUDE_CODE_SKIP_PROMPT_HISTORY` | Don't persist sessions. Same effect as `--no-session-persistence` in any mode. |
| `ENABLE_BACKGROUND_TASKS` | Enable background-task functionality. |
| `MAX_THINKING_TOKENS` | Cap on extended-thinking tokens per turn. |
| `USE_BUILTIN_RIPGREP` | Use the bundled ripgrep binary instead of system rg. |

## Session & debug

| Variable | Purpose |
|---|---|
| `CLAUDE_CODE_SESSION_ID` | Automatically set to current session ID. |
| `CLAUDE_CODE_REMOTE_SESSION_ID` | Set in cloud sessions to current session ID. |
| `CLAUDE_CODE_DEBUG_LOGS_DIR` | Override debug log file path. |
| `CLAUDE_CODE_DEBUG_LOG_LEVEL` | Minimum log level (`verbose`, `debug`, `info`, `warn`, `error`). |

## Config & paths

| Variable | Purpose |
|---|---|
| `CLAUDE_CONFIG_DIR` | Override the root config directory. Defaults to `~/.claude/`. **OpenCompany sets this** to `<DATA_DIR>/claude/` (= `~/.opencompany/claude/` by default) so credentials are project-local and isolated from the user's own `~/.claude/` session — see `nodes/agent/claude_code_agent/_oauth.py:OPENCOMPANY_CLAUDE_DIR` (`= data_path("claude")`). This is the directory under which claude's session JSONL lives at `<CLAUDE_CONFIG_DIR>/projects/<project_key>/<session_id>.jsonl`. The npm-installed CLI binary is separate, in the shared tree at `<DATA_DIR>/packages/node_modules/.bin/claude[.cmd]`. |
| `CLAUDE_REMOTE_CONTROL_SESSION_NAME_PREFIX` | Default prefix for auto-generated Remote Control session names. |

## Timeouts

| Variable | Purpose |
|---|---|
| `API_TIMEOUT_MS` | API request timeout. Default: `600000` (10 min). |
| `CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS` | Stall timeout for background subagents. Default: `600000`. |

## Proxy

| Variable | Purpose |
|---|---|
| `HTTP_PROXY` / `HTTPS_PROXY` | Standard proxy env vars; respected by the CLI. |

---

## Env vars OpenCompany explicitly sets on the spawn env

From [`services/cli_agent/session.py:env()`](../server/services/cli_agent/session.py):

| Variable | Set to | Reason |
|---|---|---|
| `PYTHONUNBUFFERED` | `1` | Line-buffered output (we parse stream-json line by line). |
| `CLAUDE_CONFIG_DIR` | `OPENCOMPANY_CLAUDE_DIR` (claude provider only) | Project-local credential isolation; also where claude writes its session JSONL — load-bearing for the memory bridge (`<key>/projects/<cwd-encoded>/<session_id>.jsonl`). |
| `<provider.ide_lock_env_var>` | path to per-spawn lockfile | VSCode-style IDE auto-discovery. |
| `OPENCOMPANY_PARENT_RUN_ID` | `<workflow_id>:<node_id>:<batch_token[:8]>` | Composio-style parent-run-id for MCP correlation. |
