# Claude Code headless / print-mode reference (verbatim snapshot)

> **Source:** [code.claude.com/docs/en/headless](https://code.claude.com/docs/en/headless)
> **Fetched:** 2026-05-11
> **Why this lives in-repo:** OpenCompany spawns `claude -p` via
> `services/cli_agent/`. The argv flags this doc describes
> (`--output-format stream-json`, `--include-partial-messages`,
> `--include-hook-events`, `--max-budget-usd`, `--max-turns`,
> `--bare`) and the stream-json event schema it documents are
> load-bearing for our session parser.

---

# Run Claude Code programmatically

> Use the Agent SDK to run Claude Code programmatically from the CLI, Python, or TypeScript.

The Agent SDK gives you the same tools, agent loop, and context management that power Claude Code. It's available as a CLI for scripts and CI/CD, or as Python and TypeScript packages for full programmatic control.

> **Note:** The CLI was previously called "headless mode." The `-p` flag and all CLI options work the same way.

To run Claude Code programmatically from the CLI, pass `-p` with your prompt and any CLI options:

```bash
claude -p "Find and fix the bug in auth.py" --allowedTools "Read,Edit,Bash"
```

This page covers using the Agent SDK via the CLI (`claude -p`). For the Python and TypeScript SDK packages with structured outputs, tool approval callbacks, and native message objects, see the full Agent SDK documentation.

## Basic usage

Add the `-p` (or `--print`) flag to any `claude` command to run it non-interactively. All CLI options work with `-p`, including:

* `--continue` for continuing conversations
* `--allowedTools` for auto-approving tools
* `--output-format` for structured output

This example asks Claude a question about your codebase and prints the response:

```bash
claude -p "What does the auth module do?"
```

### Start faster with bare mode

Add `--bare` to reduce startup time by skipping auto-discovery of hooks, skills, plugins, MCP servers, auto memory, and CLAUDE.md. Without it, `claude -p` loads the same context an interactive session would, including anything configured in the working directory or `~/.claude`.

Bare mode is useful for CI and scripts where you need the same result on every machine. A hook in a teammate's `~/.claude` or an MCP server in the project's `.mcp.json` won't run, because bare mode never reads them. Only flags you pass explicitly take effect.

This example runs a one-off summarize task in bare mode and pre-approves the Read tool so the call completes without a permission prompt:

```bash
claude --bare -p "Summarize this file" --allowedTools "Read"
```

In bare mode Claude has access to the Bash, file read, and file edit tools. Pass any context you need with a flag:

| To load                 | Use                                                     |
| ----------------------- | ------------------------------------------------------- |
| System prompt additions | `--append-system-prompt`, `--append-system-prompt-file` |
| Settings                | `--settings <file-or-json>`                             |
| MCP servers             | `--mcp-config <file-or-json>`                           |
| Custom agents           | `--agents <json>`                                       |
| A plugin                | `--plugin-dir <path>`, `--plugin-url <url>`             |

Bare mode skips OAuth and keychain reads. Anthropic authentication must come from `ANTHROPIC_API_KEY` or an `apiKeyHelper` in the JSON passed to `--settings`. Bedrock, Vertex, and Foundry use their usual provider credentials.

> **Note:** `--bare` is the recommended mode for scripted and SDK calls, and will become the default for `-p` in a future release.

## Examples

These examples highlight common CLI patterns. For CI and other scripted calls, add `--bare` so they don't pick up whatever happens to be configured locally.

### Pipe data through Claude

Non-interactive mode reads stdin, so you can pipe data in and redirect the response out like any other command-line tool.

This example pipes a build log into Claude and writes the explanation to a file:

```bash
cat build-error.txt | claude -p 'concisely explain the root cause of this build error' > output.txt
```

With `--output-format json`, the response payload includes `total_cost_usd` and a per-model cost breakdown, so scripted callers can track spend per invocation without consulting the usage dashboard.

> **Note:** As of Claude Code v2.1.128, piped stdin is capped at 10 MB. If you exceed the cap, Claude Code exits with a clear error and a non-zero status. To work with larger inputs, write the content to a file and reference the file path in your prompt instead of piping it.

### Add Claude to a build script

You can wrap a non-interactive call in a script to use Claude as a project-specific linter or reviewer.

This `package.json` script pipes the diff against `main` into Claude and asks it to report typos. Piping the diff means Claude doesn't need Bash permission to read it, and the escaped double quotes keep the script portable to Windows:

```json
{
  "scripts": {
    "lint:claude": "git diff main | claude -p \"you are a typo linter. for each typo in this diff, report filename:line on one line and the issue on the next. return nothing else.\""
  }
}
```

### Get structured output

Use `--output-format` to control how responses are returned:

* `text` (default): plain text output
* `json`: structured JSON with result, session ID, and metadata
* `stream-json`: newline-delimited JSON for real-time streaming

This example returns a project summary as JSON with session metadata, with the text result in the `result` field:

```bash
claude -p "Summarize this project" --output-format json
```

To get output conforming to a specific schema, use `--output-format json` with `--json-schema` and a JSON Schema definition. The response includes metadata about the request (session ID, usage, etc.) with the structured output in the `structured_output` field.

This example extracts function names and returns them as an array of strings:

```bash
claude -p "Extract the main function names from auth.py" \
  --output-format json \
  --json-schema '{"type":"object","properties":{"functions":{"type":"array","items":{"type":"string"}}},"required":["functions"]}'
```

> **Tip:** Use a tool like jq to parse the response and extract specific fields:
>
> ```bash
> # Extract the text result
> claude -p "Summarize this project" --output-format json | jq -r '.result'
>
> # Extract structured output
> claude -p "Extract function names from auth.py" \
>   --output-format json \
>   --json-schema '{"type":"object","properties":{"functions":{"type":"array","items":{"type":"string"}}},"required":["functions"]}' \
>   | jq '.structured_output'
> ```

### Stream responses

Use `--output-format stream-json` with `--verbose` and `--include-partial-messages` to receive tokens as they're generated. Each line is a JSON object representing an event:

```bash
claude -p "Explain recursion" --output-format stream-json --verbose --include-partial-messages
```

The following example uses jq to filter for text deltas and display just the streaming text. The `-r` flag outputs raw strings (no quotes) and `-j` joins without newlines so tokens stream continuously:

```bash
claude -p "Write a poem" --output-format stream-json --verbose --include-partial-messages | \
  jq -rj 'select(.type == "stream_event" and .event.delta.type? == "text_delta") | .event.delta.text'
```

### Stream-json event types

#### `system/api_retry`

When an API request fails with a retryable error, Claude Code emits a `system/api_retry` event before retrying. You can use this to surface retry progress or implement custom backoff logic.

| Field            | Type            | Description                                                                                                                                                           |
| ---------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `type`           | `"system"`      | message type                                                                                                                                                          |
| `subtype`        | `"api_retry"`   | identifies this as a retry event                                                                                                                                      |
| `attempt`        | integer         | current attempt number, starting at 1                                                                                                                                 |
| `max_retries`    | integer         | total retries permitted                                                                                                                                               |
| `retry_delay_ms` | integer         | milliseconds until the next attempt                                                                                                                                   |
| `error_status`   | integer or null | HTTP status code, or `null` for connection errors with no HTTP response                                                                                               |
| `error`          | string          | error category: `authentication_failed`, `oauth_org_not_allowed`, `billing_error`, `rate_limit`, `invalid_request`, `server_error`, `max_output_tokens`, or `unknown` |
| `uuid`           | string          | unique event identifier                                                                                                                                               |
| `session_id`     | string          | session the event belongs to                                                                                                                                          |

#### `system/init`

The `system/init` event reports session metadata including the model, tools, MCP servers, and loaded plugins. It is the first event in the stream unless `CLAUDE_CODE_SYNC_PLUGIN_INSTALL` is set, in which case `plugin_install` events precede it. Use the plugin fields to fail CI when a plugin did not load:

| Field           | Type  | Description                                                                                                                                                                                                                                                                                  |
| --------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `plugins`       | array | plugins that loaded successfully, each with `name` and `path`                                                                                                                                                                                                                                |
| `plugin_errors` | array | plugin load-time errors, each with `plugin`, `type`, and `message`. Includes unsatisfied dependency versions and `--plugin-dir` load failures such as a missing path or invalid archive. Affected plugins are demoted and absent from `plugins`. The key is omitted when there are no errors |

#### `system/plugin_install`

When `CLAUDE_CODE_SYNC_PLUGIN_INSTALL` is set, Claude Code emits `system/plugin_install` events while marketplace plugins install before the first turn. Use these to surface install progress in your own UI.

| Field        | Type                                                     | Description                                                                                                    |
| ------------ | -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `type`       | `"system"`                                               | message type                                                                                                   |
| `subtype`    | `"plugin_install"`                                       | identifies this as a plugin install event                                                                      |
| `status`     | `"started"`, `"installed"`, `"failed"`, or `"completed"` | `started` and `completed` bracket the overall install; `installed` and `failed` report individual marketplaces |
| `name`       | string, optional                                         | marketplace name, present on `installed` and `failed`                                                          |
| `error`      | string, optional                                         | failure message, present on `failed`                                                                           |
| `uuid`       | string                                                   | unique event identifier                                                                                        |
| `session_id` | string                                                   | session the event belongs to                                                                                   |

For programmatic streaming with callbacks and message objects, see *Stream responses in real-time* in the Agent SDK documentation.

### Auto-approve tools

Use `--allowedTools` to let Claude use certain tools without prompting. This example runs a test suite and fixes failures, allowing Claude to execute Bash commands and read/edit files without asking for permission:

```bash
claude -p "Run the test suite and fix any failures" \
  --allowedTools "Bash,Read,Edit"
```

To set a baseline for the whole session instead of listing individual tools, pass a permission mode. `dontAsk` denies anything not in your `permissions.allow` rules or the read-only command set, which is useful for locked-down CI runs. `acceptEdits` lets Claude write files without prompting and also auto-approves common filesystem commands such as `mkdir`, `touch`, `mv`, and `cp`. Other shell commands and network requests still need an `--allowedTools` entry or a `permissions.allow` rule, otherwise the run aborts when one is attempted:

```bash
claude -p "Apply the lint fixes" --permission-mode acceptEdits
```

### Create a commit

This example reviews staged changes and creates a commit with an appropriate message:

```bash
claude -p "Look at my staged changes and create an appropriate commit" \
  --allowedTools "Bash(git diff *),Bash(git log *),Bash(git status *),Bash(git commit *)"
```

The `--allowedTools` flag uses permission rule syntax. The trailing ` *` enables prefix matching, so `Bash(git diff *)` allows any command starting with `git diff`. The space before `*` is important: without it, `Bash(git diff*)` would also match `git diff-index`.

> **Note:** User-invoked skills like `/commit` and built-in commands are only available in interactive mode. In `-p` mode, describe the task you want to accomplish instead.

### Customize the system prompt

Use `--append-system-prompt` to add instructions while keeping Claude Code's default behavior. This example pipes a PR diff to Claude and instructs it to review for security vulnerabilities:

```bash
gh pr diff "$1" | claude -p \
  --append-system-prompt "You are a security engineer. Review for vulnerabilities." \
  --output-format json
```

See *system prompt flags* in the CLI reference for more options including `--system-prompt` to fully replace the default prompt.

### Continue conversations

Use `--continue` to continue the most recent conversation, or `--resume` with a session ID to continue a specific conversation. This example runs a review, then sends follow-up prompts:

```bash
# First request
claude -p "Review this codebase for performance issues"

# Continue the most recent conversation
claude -p "Now focus on the database queries" --continue
claude -p "Generate a summary of all issues found" --continue
```

If you're running multiple conversations, capture the session ID to resume a specific one:

```bash
session_id=$(claude -p "Start a review" --output-format json | jq -r '.session_id')
claude -p "Continue that review" --resume "$session_id"
```

## Next steps

* Agent SDK quickstart: build your first agent with Python or TypeScript
* CLI reference: all CLI flags and options — see [`claude_code_cli_reference.md`](./claude_code_cli_reference.md)
* GitHub Actions: use the Agent SDK in GitHub workflows
* GitLab CI/CD: use the Agent SDK in GitLab pipelines

---

## What OpenCompany reads from this stream

`services/cli_agent/session.py:_consume_stdout` parses every line of
`--output-format stream-json` output. The event types we touch:

| Event | What we do with it |
|---|---|
| `type: "system"` `subtype: "init"` | Log tools count + mcp_servers list. First evidence the spawn is healthy. |
| `type: "assistant"` `content: [text]` | Log a 300-char preview as `[CC-Agent stream] assistant.text`. |
| `type: "assistant"` `content: [tool_use]` | Log tool name + input keys as `assistant->tool_use`. |
| `type: "tool_use"` (top-level) | Log name + input keys. |
| `type: "tool_result"` | Log `is_error` + 300-char content preview. |
| `type: "hook"` | Log hook event name (requires `--include-hook-events`). |
| `type: "result"` | Final summary — drives `SessionResult` (`session_id`, `total_cost_usd`, `duration_ms`, `num_turns`, `usage`). |

`--include-partial-messages` surfaces deltas to the Terminal panel via
`broadcast_terminal_log`; we don't aggregate them into messages
because the `result` event carries the final response anyway.

`--max-budget-usd` is set from `ClaudeTaskSpec.max_budget_usd` (default
`5.0` USD) so we cap per-task spend.

`--max-turns` is set from `ClaudeTaskSpec.max_turns` (default `10`).

We do NOT use `--bare` — auto-discovery of hooks/skills/MCP/auto-memory
is what makes the workflow-tool bridge work. CI use cases that want
deterministic startup can flip it on per task in the future.
