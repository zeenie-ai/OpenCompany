# Claude Code: Code Mode (digest)
> **Status: Prototype reference.** External source digest: this file describes Claude Code and the Claude API, not OpenCompany. Paths, names and commands in it belong to those products.

Digest written 2026-11-19. Nothing was run or installed: every statement comes from Anthropic's public documentation pages, fetched that day. A statement marked INFERRED is a conclusion drawn from those pages that none of them states outright. NOT STATED marks something the pages do not say.

Citation shorthand:

- `page: "Permission checks"` names a section of the Code Mode page (source S1).
- `changelog 2.1.328` names a release entry on the changelog page (S2).
- `PTC: "Container lifecycle"` names a section of the Claude API page on programmatic tool calling (S9).

Sections 1 to 8 cover Code Mode in Claude Code. Section 9 covers programmatic tool calling on the Claude API, which is the same idea offered by the same vendor as an API feature. Section 10 sets both beside the two code-mode implementations already digested in [02-pydantic-code-mode.md](02-pydantic-code-mode.md) and [05-deepseek-harness-plugin-architecture.md](05-deepseek-harness-plugin-architecture.md), and beside Claude Code's own dynamic workflows from [01-pydantic-dynamic-workflow.md](01-pydantic-dynamic-workflow.md).

## Sources read

| # | Source | URL | Fetched | Version |
|---|--------|-----|---------|---------|
| S1 | Code Mode page | https://code.claude.com/docs/en/code-mode | 2026-11-19 | live page, Markdown form |
| S2 | Changelog, releases 2.1.323 to 2.1.328 | https://code.claude.com/docs/en/changelog | 2026-11-19 | newest entry 2.1.328, dated 2026-11-19 |
| S3 | Permission modes | https://code.claude.com/docs/en/permission-modes | 2026-11-19 | live page |
| S4 | Auto mode configuration | https://code.claude.com/docs/en/auto-mode-config | 2026-11-19 | live page |
| S5 | Hooks reference | https://code.claude.com/docs/en/hooks | 2026-11-19 | live page |
| S6 | Environment variables | https://code.claude.com/docs/en/env-vars | 2026-11-19 | live page |
| S7 | Settings reference | https://code.claude.com/docs/en/settings-reference | 2026-11-19 | live page |
| S8 | Feature availability | https://code.claude.com/docs/en/feature-availability | 2026-11-19 | live page |
| S9 | Programmatic tool calling (Claude API documentation) | https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling | 2026-11-19 | live page; its metadata says status `stable` |
| S10 | Documentation index | https://code.claude.com/docs/llms.txt | 2026-11-19 | live file |

How each was read:

- S1 was read in full.
- S2 to S8 were searched for the terms "Code Mode", `CODE_MODE`, `REPL` and `poll_event`, and every passage that matched was read.
- S9 was read in part: the introduction, "How programmatic tool calling works", "Core concepts", and everything from "Response format" to the end. Its quick start and its example workflow in several SDK languages were not read.
- These pages were also fetched and searched, and none mentions Code Mode: the tools reference, interactive mode, agent teams, dynamic workflows, sandboxing, "Run agents in parallel", the glossary, "Run Claude Code programmatically", two Agent SDK pages, costs, subagents, the features overview, monitoring, MCP, the CLI reference and two pages on mods.

Not used: a web search on 2026-11-19 also listed third-party articles and an unofficial copy of Claude Code source files. None was opened. No official article about Code Mode other than the pages above was found.

## 1. What it is

Code Mode is an experimental setting of Claude Code, added in v2.1.323 on 2026-11-12 (changelog 2.1.323). The page's own summary: "an experimental feature in which Claude writes JavaScript that makes several tool calls in one step".

- Without it, "Claude asks for tool calls such as Bash, Read, or Edit directly and reads each result before it decides what to do next". With it, "Claude writes a piece of JavaScript that makes the tool calls, and Claude Code runs that code in your session" (page, opening paragraph).
- "One piece of code can run commands in parallel, use one result in the next call, and return the part of the output that Claude needs to read" (page: "How Code Mode works").
- The user still writes an ordinary prompt. The model decides to write code.
- Status, in the page's words: "Code Mode is experimental and off by default. It may change or be removed in any release."

The page names three kinds of task to try it on (page: "When to use Code Mode"):

- "A change that repeats across many files"
- "A search whose results need filtering before Claude reads them"
- "Several independent commands that can run at the same time"

The page's example. The task is to raise a retry limit; the code finds the file, edits it and runs the tests:

```javascript
const files = await $`rg -l "function retryPolicy" src`.text();
const target = files.trim().split("\n")[0];
await edit(target, "maxAttempts: 3", "maxAttempts: 5");
({ edited: target, tests: $`npm test -- retryPolicy`.nothrow() });
```

"The last line returns the file's path and the test output to Claude" (page: "How Code Mode works").

The Code Mode page gives no measurement of tokens, time or quality. The only numbers are on the API page (section 9.5).

## 2. Vocabulary

| Term | Meaning in these sources |
|------|--------------------------|
| Code Mode | A setting of a whole Claude Code session. It is chosen when the session starts and stays with the session (section 7). |
| Code, script | One piece of JavaScript the model submits in one call. The page says "code"; the changelog says "script". |
| `REPL` | The name of the tool that runs the code. Its input field `code` holds the JavaScript (page: "Permission checks"). |
| `$` | The shell helper: a template tag that runs a command (page example). The changelog calls it "Code Mode's `$` shell helper" (changelog 2.1.326). |
| `edit()`, `write()` | Helper functions for changing and writing files (page example; changelog 2.1.328). |
| Permission mode | How Claude Code decides whether a tool call needs the user's approval. Modes named on the page: auto, `bypassPermissions`, and "the modes that ask". |
| Auto mode | The permission mode in which a classifier decides instead of the user (S3). |
| Classifier | Auto mode's reviewer. In Code Mode it reads the code, not the calls. |
| Waiting, background | The two ways a long-running script is handled (section 3.8). |
| Transcript viewer | The view opened with `Ctrl+O`, where each script appears as a collapsed **Code** row. |
| Managed settings | Settings an organization applies to all its users. |
| Native build | The installation type Code Mode requires (page: "Requirements"). |

## 3. How it works

### 3.1 The tool and the script

- There is one tool, named `REPL`, with the script in its `code` input. The page gives the name where it explains how to inspect scripts: "write a `PreToolUse` hook that matches `REPL`, the tool that runs the code, and read the JavaScript from `tool_input.code`" (page: "Permission checks").
- The same name appears in launch options: "add `REPL` to `--tools`, and remove `REPL` from `--disallowedTools` and your `deny` rules" (page: "Code Mode doesn't start").
- The tools reference page does not list `REPL`.
- The language is JavaScript. The example uses top-level `await`. Changelog 2.1.328 fixed Code Mode "refusing valid scripts that start with a `{` block" and "refusing scripts that use both a top-level `await` and a top-level `return`". INFERRED: Claude Code parses or rewrites a script before it runs it, and refuses one it cannot accept.
- Where it runs: "Claude Code runs that code in your session". The requirements add the native build of Claude Code and, on Windows, Git for Windows.
- NOT STATED: whether the JavaScript runs in Claude Code's main process, in a worker or in a sandbox, and what a script can reach besides the helpers (modules, the network, the file API of the runtime).

### 3.2 What a script can call

- **Shell.** `$` runs a command; the example chains `.text()` and `.nothrow()` on it. On Windows, "scripts' shell commands run in Git Bash" (changelog 2.1.328). Changelog 2.1.326: "Changed Code Mode's `$` shell helper to refuse some values with an error; nothing runs". INFERRED: values placed into a command are checked before the command is built.
- **Files.** `edit(path, old, new)` is in the example and `write()` is named in changelog 2.1.328. The same release changed edits: "an edit that changes nothing no longer stops a script, and Claude is told where a failed edit matches or comes closest".
- **MCP servers.** Scripts can call them. Changelog 2.1.328 fixed scripts "failing to call an MCP server that had connected while the previous script was running", and added that "Claude is now told when a server is still connecting and when the list of servers changes".
- **The set follows the agent's permissions.** Changelog 2.1.328 fixed Code Mode "offering `write()` and `edit()` to read-only subagents that can't call them".
- **The model reads descriptions of the helpers.** Changelog 2.1.328: "Improved Code Mode's token use with shorter tool descriptions".
- NOT STATED: the full list of helpers and their signatures. No helper for reading a file is shown.
- Not every call goes through code. The page separates "a call from code" from "any other tool call once Claude has run code in the session" (page: "Tool call refused by a hook"), and dynamic workflows stay a separate tool (section 3.11). Which tools stay direct is NOT STATED.

### 3.3 What comes back, and what the user sees

- The value of the last expression goes back to the model. In the example the last line is an object whose `tests` field holds a command that was not awaited, and the page says the test output is returned. INFERRED: a pending command inside the returned value is awaited first.
- Long command output is cut. Changelog 2.1.328 fixed scripts "not being told when a failing command's long output was cut". The size is NOT STATED.
- The user sees the calls, not the code: "In the conversation, you see the tool calls, such as the edit's diff, and not the code." The code is in the transcript viewer (page: "How Code Mode works").
- Since 2.1.328 Claude Code shows "the command a script is running, and its output, while it runs" (changelog 2.1.328).

### 3.4 State between scripts

- Changelog 2.1.328: "Fixed Code Mode silently using `undefined` for top-level variables that an earlier failed script never set". INFERRED: top-level variables of one script are visible to later scripts of the session, which fits the tool's name.
- One reply can run several scripts: the same release fixed a loss of state "when resuming a session in which one reply ran several scripts".

### 3.5 Permission checks on each call

The script is checked call by call with the user's existing rules:

- "When Claude runs code, each tool call in it goes through your `deny` and `ask` permission rules and your `PreToolUse` hooks under the tool's usual name, such as `Bash`" (page: "Permission checks").
- In the modes that ask, "the code starts without a review, and you're prompted for each tool call that would prompt you in that mode".
- The command text a rule or hook sees can differ from what the model wrote: "In Code Mode, a `git push` in a folder named `app` can reach your hook as `cd 'app' && git push`."
- Changelog 2.1.326: "Improved permission checks in Code Mode; more sandboxed commands now ask or are refused".

### 3.6 Review of the whole script

In auto mode a second, coarser check is added, and it replaces the classifier's usual call-by-call review:

- "the classifier reviews each piece of code before it runs, not each tool call. It doesn't see values computed at run time, such as a path taken from search output, so keep rules or hooks for calls you must control" (page: "Permission checks").
- S3 says the same from the other side: "the code that Claude writes goes to the classifier once before it runs, and the tool calls inside it don't. You can still block a call with a `deny` rule or require a prompt with an `ask` rule".
- S4: "the classifier applies `hard_deny` and `soft_deny` to each piece of code as a whole, before the code runs. To block a specific tool call that the code makes, use a `permissions.deny` rule."
- S4 on the setting that sends every shell command to the classifier: "In a Code Mode session, the classifier reviews the code that runs the commands, even with this setting on."
- A user can add a check of the code itself with the `PreToolUse` hook on `REPL` (section 3.1).

### 3.7 Hooks

- **A hook that gives no decision refuses a call from code.** The page lists two errors, `the hook failed` and `the hook did not finish`, and says they "mean that a `PreToolUse` hook didn't give a decision, which refuses the call even when `onFailure` is unset" (page: "Tool call refused by a hook"). Outside Code Mode the hooks reference says a timed-out `PreToolUse` hook "doesn't block the tool call" (S5).
- **Hooks on script output.** The page says that when code can run in the background, `UserPromptSubmit` hooks "also run on code output", with `source` set to `poll_event` in the hook's input (page: "Code output withheld by a hook"). Changelog 2.1.328, released on the day of this digest, reverses that: "Changed Code Mode to no longer run UserPromptSubmit hooks, managed ones included, on scripts' output; to redact that output, use a PostToolUse hook with `updatedToolOutput`". The page and the hooks reference still describe the earlier behaviour.

### 3.8 Waiting and background

- **Waiting.** "Claude answers after the code finishes, and the code stops after ten minutes, including time you spend at permission prompts" (page: "Long-running code").
- **Background.** "you can keep talking to Claude, and the code can run for up to two hours". The condition: "For background code, start your session in auto or `bypassPermissions` mode."
- Changelog 2.1.328 fixed Code Mode "stopping a script when a permission prompt outside auto or bypass mode, a plan approval or a question stayed open for ten minutes". The page text does not reflect this yet.
- Changelog 2.1.328 also removed "a 0.75 second pause after most scripts" in auto and bypass-permissions modes, and tells the model to start dev servers "as background tasks that show in `/tasks`".

### 3.9 More than one script at a time

In auto mode the model can start a script while an earlier one still runs (page: "Permission prompt with a countdown"):

- "When Claude starts more code in auto mode while earlier code is still running, the earlier code loses its classifier review. Claude Code then asks you about the earlier code's remaining calls that would need a review."
- With no IDE connected the prompt carries a countdown. The page's example reads "Claude Code will automatically block this request in 1:59, to avoid holding up an unattended session".
- "Answer before the countdown ends. Otherwise that call is refused, and later calls that would show a countdown are refused without a prompt."
- Changelog 2.1.327 fixed "a running Code Mode script in auto mode getting permission prompts and denials once Claude started a second script that auto mode also allowed". How that fix changes the behaviour the page describes is NOT STATED.

### 3.10 What the model is told when something goes wrong

Several changelog entries are about the text the model receives, not about the runtime:

- which tool calls were still running when a script failed (changelog 2.1.328);
- where a failed edit "matches or comes closest" (changelog 2.1.328);
- that a failing command's long output was cut (changelog 2.1.328);
- that a script was cut off, when the session is resumed (changelog 2.1.328);
- the error text of a refused call (page: "Tool call refused by a hook").

### 3.11 Subagents, teams and dynamic workflows

- Subagents run scripts too: the changelog mentions "read-only subagents" (2.1.328) and "background subagents started in Code Mode" (2.1.326).
- Agent teams need v2.1.326 or later. "Teammates run in Code Mode in the default in-process display mode, and start without it in split panes" (page: "Requirements").
- Dynamic workflows remain a separate tool inside a Code Mode session. Changelog 2.1.328 fixed "Claude in Code Mode not being told to run workflows only when you ask, or the workflow size you set in `/config`".

## 4. Configuration surface

- **The switch** is the environment variable `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE`: `1` starts sessions in Code Mode, `0` keeps it off (S6).
- **Where it counts.** "You can turn on Code Mode with `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE` from your shell, user settings, or a `--settings` file, and not from managed, project, or local settings. With a `0` in any of the first three or in managed settings, Code Mode stays off whatever you set elsewhere" (S7). The page gives the reason for project files: "Claude Code ignores this variable in a project's settings files, so a repository you clone can't turn on Code Mode for you."
- **Confirmation.** "Claude Code asks you to confirm before your first Code Mode session." After the user selects "Yes, turn on Code Mode", Claude Code restarts and shows this line (page: "Try Code Mode"):

```text
Code Mode (experimental) is on · this session can be resumed only with Code Mode on
```

- **Organization control.** A `0` in managed settings blocks it for everyone. "You can't turn on Code Mode for your users from managed settings" (page: "Block Code Mode for your organization").
- **Launch options.** Code Mode does not start with `--restricted`, with a `--tools` list that leaves out `REPL`, or with `REPL` in `--disallowedTools` or a `deny` rule (page: "Code Mode doesn't start").
- **Diagnosis.** When Code Mode does not start, a line near the top of the session and `/status` give the reason.

## 5. Limits and failure handling

| Limit | Value | Source |
|-------|-------|--------|
| A script the model waits for | "stops after ten minutes" | page: "Long-running code" |
| A script in the background | "up to two hours"; only in a session started in auto or `bypassPermissions` mode | page: "Long-running code" |
| An unanswered prompt for an earlier script in auto mode | A countdown, 1:59 in the page's example; then the call is refused, and later such calls are refused without a prompt | page: "Permission prompt with a countdown" |
| Tool calls per script | NOT STATED | |
| Calls running at once | NOT STATED | |
| Size of output returned | Long output of a failing command is cut; the size is NOT STATED | changelog 2.1.328 |
| A `PreToolUse` hook that fails or times out | The call from code is refused | page: "Tool call refused by a hook" |

What a failure looks like to the model is in section 3.10. The page describes no automatic retry of a script.

## 6. Requirements and availability

From the page ("Requirements"):

- **Version:** Claude Code v2.1.323 or later.
- **Account:** "a Claude subscription or Console account. You can't use Code Mode through a gateway, Amazon Bedrock, Claude Platform on AWS, Google Cloud's Agent Platform, Microsoft Foundry, or Claude Models on Google Cloud (Anthropic managed)".
- **Organization:** not one that uses the HIPAA configuration or zero data retention.
- **Model:** "Claude Opus 5.5, Sonnet 5.5, Haiku 5.5, or Fable 5.2".
- **Surface:** "an interactive `claude` session in a terminal. Code Mode doesn't work with `-p`, the Agent SDK, the VS Code panel, the Desktop app, or cloud sessions".
- **Installation:** the native build. **Windows:** Git for Windows.
- **Settings that block it:** "an API key helper, OpenTelemetry export, or `DISABLE_TELEMETRY`".

Reasons the page lists for Code Mode not starting, shortened (page: "Code Mode doesn't start"):

| The reason shown includes | Cause |
|---------------------------|-------|
| `it isn't open to your account in this version` or `it isn't open to your account yet` | An old version, or the account is not yet included |
| `it only works directly with the Anthropic API` | Requests go through a cloud provider, a gateway or a custom `ANTHROPIC_BASE_URL` |
| `it doesn't work with an API key helper` | `apiKeyHelper`, `ANTHROPIC_AUTH_TOKEN`, or an `Authorization` or `X-Api-Key` header in `ANTHROPIC_CUSTOM_HEADERS` |
| `it only works with Opus 5.5, Sonnet 5.5 and later models` | Another model |
| `it doesn't work in background jobs` | Not started in the foreground, or started with `--remote-control` |
| `it needs settings that Claude Code downloads from Anthropic` | Feature-flag fetching is off, for example through `DISABLE_TELEMETRY` |
| `OpenTelemetry is on` | OpenTelemetry export is enabled |
| `it doesn't yet work with the plugin` | A named plugin is enabled |
| `it can't be used together with agent teams yet` | Agent teams on a version before v2.1.326 |
| `this launch's tool options` | `--restricted`, or `REPL` missing from the allowed tools |
| `couldn't save your answer` | `~/.claude.json` is not writable |
| `your organization's settings don't allow it` | The managed setting |

The page's table also has a row for an installed mod; changelog 2.1.327 removed that refusal.

Other limits the page lists ("Code Mode limitations"): Remote Control, Claude in Chrome and computer use cannot be used; the session cannot move to the Desktop app or become a background session; `/fork` cannot start a background copy.

Two statements on other pages bear on how it is built:

- The feature availability page lists Code Mode in a table introduced with "These features work in the local CLI but depend on a server-side capability that not every provider exposes" (S8). The row says yes, experimental, for a Claude subscription and the Anthropic Console, and no for Amazon Bedrock, Claude Platform on AWS, both Google Cloud offerings and Microsoft Foundry.
- The environment variables page lists turning on Code Mode among the things that cannot be done while feature-flag fetching is off (S6).

NOT STATED: what the server-side capability is, and why only four models are accepted.

## 7. Resume and durability

- A session keeps the mode it started with. "With Code Mode off, Claude Code refuses to resume the session. Sessions that you started without Code Mode resume without Code Mode, even with the variable set" (page: "Try Code Mode"). The limitation list says a Code Mode session can be resumed "only in the terminal, with Code Mode on".
- A script does not survive the end of the session. Changelog 2.1.325: "Fixed Claude starting work by itself when you resumed a session that ended during a Code Mode script; it now waits for your next prompt". Changelog 2.1.328 added that the model is told the script was cut off.
- The pages describe no way to continue a script from the point where it stopped, no saved result per call, and no ban on the clock or on random numbers inside a script. Claude Code's dynamic workflows have all three ([01-pydantic-dynamic-workflow.md](01-pydantic-dynamic-workflow.md), section 8.3).
- When an organization blocks Code Mode, "Users who are in a Code Mode session stay in it until they exit or restart Claude Code", and cannot resume their Code Mode sessions afterwards.

## 8. Release history in the first week

| Release | Date | Code Mode entries |
|---------|------|-------------------|
| 2.1.323 | 2026-11-12 | Added, as "an experimental opt-in" |
| 2.1.325 | 2026-11-13 | 3 fixes: scripts ignoring an entered worktree and writing `~` paths into the project; text typed with a Yes at a permission dialog not reaching the model; the model starting work by itself after a resume |
| 2.1.326 | 2026-11-16 | 7: support with agent teams; `$` refuses some values; stricter permission checks; clearer messages when it cannot start; 3 fixes about skills, background subagents and mods |
| 2.1.327 | 2026-11-17 | 3: no longer refuses to start when a mod hooks tool calls; 2 fixes (prompts and denials for a running script after a second starts; a restart problem on Linux when turning it on) |
| 2.1.328 | 2026-11-19 | 22: no `UserPromptSubmit` hooks on script output; 8 improvements (speed, prompt caching with MCP servers, shorter tool descriptions, live command output, edit behaviour, failure reports, dev servers, Windows shell guidance); 13 fixes |

That is 35 entries in four releases over the seven days after the feature shipped.

## 9. The API counterpart: programmatic tool calling

The Claude API offers the same idea as a feature any application can use. The facts below are from S9.

### 9.1 What it is

"Programmatic tool calling allows Claude to write code that calls your tools programmatically within a code execution container, rather than requiring round trips through the model for each tool call."

- The page's metadata gives its status as `stable`.
- The code is Python and runs in a container Anthropic hosts.
- Platforms: the Claude API, Claude Platform on AWS, Claude Models on Google Cloud (Anthropic managed) and Microsoft Foundry (hosted on Anthropic). It is not available in Managed Agents.
- Models: eighteen model ids are listed, from Claude Sonnet 4.5 and Opus 4.5 up to Fable 5.2. "Claude Haiku 4.5 accepts the `code_execution_20260120` and later tool versions but doesn't support programmatic tool calling."
- Not eligible for zero data retention: "Built on code execution containers; data retained up to 30 days."

### 9.2 How a call travels

The page's five steps (PTC: "How programmatic tool calling works"):

1. "Claude writes Python code that calls the tool as a function."
2. "Claude runs this code in a sandboxed container through code execution."
3. "When a tool function is called, code execution pauses and the API returns a `tool_use` block."
4. "You provide the tool result, and code execution continues (intermediate results are not loaded into Claude's context window)."
5. "Once all code execution completes, Claude receives the final output and continues working on the task."

Details:

- Tools are "async Python functions, so Claude can run them in parallel with `asyncio.gather`. Each function takes a single dict of arguments and returns a string".
- Each tool definition says who may call it through `allowed_callers`: `["direct"]`, `["code_execution_20260120"]`, or both. The page advises choosing one. It also warns that the field "is not a hard API-level block on direct calls" and adds: "Do not rely on `allowed_callers` as a security boundary."
- Every `tool_use` block carries a `caller` field. For a call from code it holds the type `code_execution_20260120` and a `tool_id`, the id of the code execution run that made the call.
- So the application still executes every tool call itself, one `tool_use` block at a time. What changes is that the result goes back to the running code and not into the model's context.

### 9.3 Container and time limits

- "A new container is created for each request unless you reuse an existing one." While a call from code waits for its result, the container id must be sent back with the request.
- "Containers expire 30 days after creation. After about 5 minutes of inactivity a container is checkpointed, and sending a request with its ID inside the 30-day window restores it."
- A waiting call has its own, shorter limit: "the pending call times out after about 4 minutes and raises a `TimeoutError` inside the code". The page's example error names 270 seconds.

### 9.4 Restrictions

- Not supported with it: tools with `strict: true`, forcing a tool through `tool_choice`, and `disable_parallel_tool_use: true`.
- Cannot be called from code: tools of an MCP connector, and the computer use and browser use toolsets.
- A custom tool whose input schema refers to itself cannot be enabled for it.
- The reply to a waiting call "must contain only `tool_result` blocks", and each result must be text.
- "Each tool call from code execution counts toward those limits individually" (rate limits).
- A warning about results: "They can contain any content, including code snippets or executable commands that might be processed by the execution environment."

### 9.5 Measurements

These are the only measurements in the sources of this file (PTC, introduction and "Token efficiency"):

- On the agentic search benchmarks BrowseComp and DeepSearchQA, adding it "improved performance by an average of 11 percent while using 24 percent fewer input tokens".
- "On a 75-tool project-management agent benchmark, enabling programmatic tool calling reduced billed input tokens by roughly 38 percent with no change in task accuracy."
- On tau-squared-bench, "where each turn makes one or two sequential tool calls, programmatic tool calling left scores unchanged and cost roughly 8 percent more. Sequential single-call workflows do not benefit."
- "Across production API traffic, requests whose `tools` array contains 10 to 49 tool definitions see typical token savings of 20 to 40 percent".

The page's own guidance on fit (PTC: "When to use programmatic calling"):

- Strong: fan-out across many items; large results that can be filtered before they reach the model; agentic search and retrieval.
- Weak: "Strictly sequential workflows where each call depends on Claude reasoning over the previous result"; a few calls with small results; "Tools that require immediate user feedback between calls".

Billing: "Tool results from programmatic calls do not count toward your input/output token usage. Only the final code execution result and Claude's response count."

### 9.6 Anthropic's three shapes of the pattern

The page ends by calling the feature "a generalizable pattern that can also be implemented on your own infrastructure" and compares three ways to build it (PTC: "Alternative implementations"):

| Shape | How | The page's advantages | The page's disadvantages |
|-------|-----|-----------------------|--------------------------|
| Client-side direct execution | The application gives the model a code tool and runs the code locally, where the functions are defined | "Minimal re-architecting of your application"; full control | "Runs untrusted code outside of a sandbox"; "Tool calls can be vectors for code injection" |
| Self-managed sandboxed execution | The same, but the code runs in a sandboxed container, and tool calls need "a protocol for running tool calls outside the sandbox" | Safe on the application's own infrastructure; full control | "Complex to build and maintain"; "Requires managing both infrastructure and inter-process communication" |
| Anthropic-managed execution | The API feature of this section | "Safe and secure by default"; "no infrastructure to run" | Only on supported platforms and models |

Which of these shapes Code Mode in Claude Code has is NOT STATED: the code runs on the user's machine, in the session, and the pages do not say whether a sandbox surrounds it.

## 10. Side by side, facts only

### 10.1 Four code-mode implementations

The Pydantic and DeepSeek columns come from [02-pydantic-code-mode.md](02-pydantic-code-mode.md) and section 7.5 of [05-deepseek-harness-plugin-architecture.md](05-deepseek-harness-plugin-architecture.md).

| Aspect | Claude Code Code Mode | Claude API programmatic tool calling | Pydantic AI Harness Code Mode | DeepSeek Harness PTC |
|---|---|---|---|---|
| Status | Experimental, off by default, since 2026-11-12 | Stable | Alpha package; its docs call the capabilities meant for production | The executor ships disabled in the base bundle |
| Script language | JavaScript | Python | Python, the Monty subset | TypeScript |
| The tool | `REPL`, input `code` | The `code_execution` server tool | `run_code(code, restart)` | `run_code(code, description, timeoutMs, sandbox_permissions)` |
| Where the code runs | In the user's session, on their machine | A container hosted by Anthropic | The Monty sandbox, in a worker process or on a remote server | A fresh Node process under the session's file sandbox policy |
| How a tool is called | Helpers: `$`, `edit()`, `write()`; MCP servers; the rest NOT STATED | One async function per tool, taking one dict and returning a string | One async function per tool, keyword-only, with typed signatures | `await tools.name(args)` |
| Which tools | NOT STATED | Each tool opts in through `allowed_callers` | A selector, all eligible tools by default; never another code tool | The tool registry |
| What returns to the model | The last expression | What the code prints | The last expression, plus printed text | What the program prints and returns |
| State between scripts | Top-level variables persist (INFERRED) | The container, when its id is reused | The interpreter session, within one agent run | Only with an opt-in retained session |
| Check on each call | The user's `deny` and `ask` rules and `PreToolUse` hooks, under the tool's usual name | The application runs each call and applies its own checks | The agent's normal tool pipeline, hooks and approval rules included | Each call re-enters the normal tool pipeline, with its own approvals and logging |
| A call that needs a person | Prompted while the script waits; refused when nobody answers a countdown | The result must arrive in about 4 minutes, or the code gets a `TimeoutError` | Resolved while the snippet waits, or the call fails | Denied calls reject inside the program |
| Check on the whole script | Auto mode's classifier, once before the run; a hook on `REPL` | None described | A type check of the first snippet | None described |
| Time limit | 10 minutes waiting, 2 hours in the background | About 4 minutes per waiting call; the container lives 30 days | 30 seconds of computation per snippet | 120 seconds by default, 600 at most |
| Call budget | NOT STATED | Ordinary rate limits, counted per call | 100 calls per `run_code` call | 1,024 per minute by default; 10 at once |
| Surviving a restart | No: a cut-off script is reported, not continued | The container's state is checkpointed and can be restored for 30 days; a waiting call still times out after about 4 minutes | No, alone; under Temporal the snippet is run again on replay | No |
| Published measurements | None | Yes (section 9.5) | None | None |

### 10.2 Code Mode beside dynamic workflows, inside Claude Code

Claude Code now has two features in which the model writes JavaScript. The workflow column is from [01-pydantic-dynamic-workflow.md](01-pydantic-dynamic-workflow.md), section 8.3.

| Aspect | Code Mode | Dynamic workflows |
|---|---|---|
| The functions | The session's tools | `agent()`, `parallel()`, `pipeline()`: subagents |
| What the script can touch | Shell commands and files, through helpers | "No direct filesystem or shell access from the workflow itself"; no module loading |
| Relation to the caller | The model waits, up to ten minutes; background only in auto or bypass mode | Launched in the background; the result arrives later |
| Limits | Time: ten minutes or two hours | Count: 16 at once, 1,000 agents per run, 4,096 items per fan-out |
| After an interruption | The script is reported as cut off | Saved results are replayed in the order agents start |
| Clock and randomness | NOT STATED | They throw |
| How it is switched on | An environment variable per user, with a confirmation | The model is told to run one only when asked |
| The script afterwards | Visible in the transcript viewer | Saved as a file that can be edited, rerun and kept as a command |

## 11. Maturity, versions, caveats

- The feature was seven days old when this digest was written. The page's own words: "It may change or be removed in any release."
- The changelog carries 35 Code Mode entries in the four releases after the first (section 8).
- On the day of the fetch the page and the changelog disagreed on two points: hooks on script output (section 3.7) and the ten-minute stop while a prompt is open (section 3.8). The changelog entry is the newer statement in both cases.
- Code Mode is unavailable on every programmatic surface the page names: `-p`, the Agent SDK, the VS Code panel, the Desktop app and cloud sessions.
- An organization can block it and cannot force it on.
- The API feature of section 9 is a separate product with its own status. Nothing in the sources says Code Mode is built on it.

## 12. What could not be confirmed

- Nothing was run. No Claude Code session was started in Code Mode.
- The description of the `REPL` tool that the model reads, the list of helper functions and their signatures. They are not on any public page that was found.
- Where the JavaScript runs and what isolates it (section 3.1).
- Any limit on the number of calls, on calls running at once, or on output size.
- What the "server-side capability" behind Code Mode is (section 6).
- Whether Code Mode changes token use, speed or result quality, and by how much. The Code Mode page gives no numbers; the numbers in section 9.5 belong to the API feature.
- Whether a script may read the clock or random numbers.
- The S9 quick start and example workflow were not read, so details that appear only there are missing from section 9.
