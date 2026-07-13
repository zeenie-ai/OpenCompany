# Claude Code permission modes reference (verbatim snapshot)

> **Source:** [code.claude.com/docs/en/permission-modes](https://code.claude.com/docs/en/permission-modes)
> **Fetched:** 2026-05-11
> **Why this lives in-repo:** OpenCompany's claude_code_agent defaults to
> `--permission-mode acceptEdits` and exposes the full enum
> (`default`, `acceptEdits`, `plan`, `auto`, `dontAsk`,
> `bypassPermissions`) on `ClaudeTaskSpec.permission_mode`. See
> `services/cli_agent/types.py` and
> `server/nodes/agent/claude_code_agent/_provider.py`.

---

# Choose a permission mode

> Control whether Claude asks before editing files or running commands. Cycle modes with Shift+Tab in the CLI or use the mode selector in VS Code, Desktop, and claude.ai.

When Claude wants to edit a file, run a shell command, or make a network request, it pauses and asks you to approve the action. Permission modes control how often that pause happens. The mode you pick shapes the flow of a session: default mode has you review each action as it comes, while looser modes let Claude work in longer uninterrupted stretches and report back when done. Pick more oversight for sensitive work, or fewer interruptions when you trust the direction.

## Available modes

Each mode makes a different tradeoff between convenience and oversight. The table below shows what Claude can do without a permission prompt in each mode.

| Mode                                                                | What runs without asking                                                               | Best for                                |
| :------------------------------------------------------------------ | :------------------------------------------------------------------------------------- | :-------------------------------------- |
| `default`                                                           | Reads only                                                                             | Getting started, sensitive work         |
| `acceptEdits`                                                       | Reads, file edits, and common filesystem commands (`mkdir`, `touch`, `mv`, `cp`, etc.) | Iterating on code you're reviewing      |
| `plan`                                                              | Reads only                                                                             | Exploring a codebase before changing it |
| `auto`                                                              | Everything, with background safety checks                                              | Long tasks, reducing prompt fatigue     |
| `dontAsk`                                                           | Only pre-approved tools                                                                | Locked-down CI and scripts              |
| `bypassPermissions`                                                 | Everything                                                                             | Isolated containers and VMs only        |

In every mode except `bypassPermissions`, writes to protected paths are never auto-approved, guarding repository state and Claude's own configuration against accidental corruption.

Modes set the baseline. Layer permission rules on top to pre-approve or block specific tools in any mode except `bypassPermissions`, which skips the permission layer entirely.

## Switch permission modes

You can switch modes mid-session, at startup, or as a persistent default. The mode is set through these controls, not by asking Claude in chat.

### CLI

**During a session:** press `Shift+Tab` to cycle `default` → `acceptEdits` → `plan`. The current mode appears in the status bar. Not every mode is in the default cycle:

* `auto`: appears when your account meets the auto-mode requirements; cycling to auto shows an opt-in prompt until you accept it, or select **No, don't ask again** to remove auto from the cycle.
* `bypassPermissions`: appears after you start with `--permission-mode bypassPermissions`, `--dangerously-skip-permissions`, or `--allow-dangerously-skip-permissions`; the `--allow-` variant adds the mode to the cycle without activating it.
* `dontAsk`: never appears in the cycle; set it with `--permission-mode dontAsk`.

Enabled optional modes slot in after `plan`, with `bypassPermissions` first and `auto` last. If you have both enabled, you will cycle through `bypassPermissions` on the way to `auto`.

**At startup:** pass the mode as a flag.

```bash
claude --permission-mode plan
```

**As a default:** set `defaultMode` in settings.

```json
{
  "permissions": {
    "defaultMode": "acceptEdits"
  }
}
```

The same `--permission-mode` flag works with `-p` for non-interactive runs.

### VS Code / JetBrains / Desktop / Web

See the upstream page for IDE-specific UI controls. In Remote Control sessions on the user's local machine the available modes are Ask permissions, Auto accept edits, and Plan mode (Auto and Bypass not available). In cloud claude.ai/code sessions only Auto accept edits and Plan mode are available.

## Auto-approve file edits with `acceptEdits` mode

`acceptEdits` mode lets Claude create and edit files in your working directory without prompting. The status bar shows `⏵⏵ accept edits on` while this mode is active.

In addition to file edits, `acceptEdits` mode auto-approves common filesystem Bash commands: `mkdir`, `touch`, `rm`, `rmdir`, `mv`, `cp`, and `sed`. These commands are also auto-approved when prefixed with safe environment variables such as `LANG=C` or `NO_COLOR=1`, or process wrappers such as `timeout`, `nice`, or `nohup`. Like file edits, auto-approval applies only to paths inside your working directory or `additionalDirectories`. Paths outside that scope, writes to protected paths, and all other Bash commands still prompt.

When the PowerShell tool is enabled, `acceptEdits` mode also auto-approves `Set-Content`, `Add-Content`, `Clear-Content`, and `Remove-Item` on in-scope paths, along with their common aliases. The same scope and protected-path rules apply.

Use `acceptEdits` when you want to review changes in your editor or via `git diff` after the fact rather than approving each edit inline. Press `Shift+Tab` once from default mode to enter it, or start with it directly:

```bash
claude --permission-mode acceptEdits
```

## Analyze before you edit with `plan` mode

Plan mode tells Claude to research and propose changes without making them. Claude reads files, runs shell commands to explore, and writes a plan, but does not edit your source. Permission prompts still apply the same as default mode.

Enter plan mode by pressing `Shift+Tab` or prefixing a single prompt with `/plan`. You can also start in plan mode from the CLI:

```bash
claude --permission-mode plan
```

Press `Shift+Tab` again to leave plan mode without approving a plan.

### Review and approve a plan

When the plan is ready, Claude presents it and asks how to proceed. From that prompt you can:

* Approve and start in auto mode
* Approve and accept edits
* Approve and review each edit manually
* Keep planning with feedback
* Refine with Ultraplan for browser-based review

Approving a plan exits plan mode and switches the session to the permission mode each approve option describes, so Claude starts editing. To plan again, cycle back to plan mode with `Shift+Tab`, or prefix your next prompt with `/plan`.

Press `Ctrl+G` to open the proposed plan in your default text editor and edit it directly before Claude proceeds. When `showClearContextOnPlanAccept` is enabled, each approve option also offers to clear the planning context first.

Accepting a plan also names the session from the plan content automatically, unless you've already set a name with `--name` or `/rename`.

### Set plan mode as the default

To make plan mode the default for a project, set `defaultMode` in `.claude/settings.json`:

```json
{
  "permissions": {
    "defaultMode": "plan"
  }
}
```

## Eliminate prompts with `auto` mode

> **Note:** Auto mode requires Claude Code v2.1.83 or later.

Auto mode lets Claude execute without permission prompts. A separate classifier model reviews actions before they run, blocking anything that escalates beyond your request, targets unrecognized infrastructure, or appears driven by hostile content Claude read.

Auto mode also instructs Claude to execute immediately and minimize clarifying questions. To get that behavior while keeping permission prompts, set the Proactive output style instead.

> **Warning:** Auto mode is a research preview. It reduces prompts but does not guarantee safety. Use it for tasks where you trust the general direction, not as a replacement for review on sensitive operations.

Auto mode is available only when your account meets all of these requirements:

* **Plan**: Max, Team, Enterprise, or API. Not available on Pro.
* **Admin**: on Team and Enterprise, an admin must enable it in Claude Code admin settings before users can turn it on. Admins can also lock it off by setting `permissions.disableAutoMode` to `"disable"` in managed settings.
* **Model**: Claude Sonnet 4.6, Opus 4.6, or Opus 4.7 on Team, Enterprise, and API plans; Claude Opus 4.7 only on Max plans. Other models, including Haiku and claude-3 models, are not supported.
* **Provider**: Anthropic API only. Not available on Bedrock, Vertex, or Foundry.

If Claude Code reports auto mode as unavailable, one of these requirements is unmet; this is not a transient outage. A separate message that names a model and says auto mode "cannot determine the safety" of an action is a transient classifier outage.

### What the classifier blocks by default

The classifier trusts your working directory and your repo's configured remotes. Everything else is treated as external until you configure trusted infrastructure.

**Blocked by default:**

* Downloading and executing code, like `curl | bash`
* Sending sensitive data to external endpoints
* Production deploys and migrations
* Mass deletion on cloud storage
* Granting IAM or repo permissions
* Modifying shared infrastructure
* Irreversibly destroying files that existed before the session
* Force push, or pushing directly to `main`

**Allowed by default:**

* Local file operations in your working directory
* Installing dependencies declared in your lock files or manifests
* Reading `.env` and sending credentials to their matching API
* Read-only HTTP requests
* Pushing to the branch you started on or one Claude created

Sandbox network access requests are routed through the classifier rather than allowed by default. Run `claude auto-mode defaults` to see the full rule lists.

### Boundaries you state in conversation

The classifier treats boundaries you state in the conversation as a block signal. If you tell Claude "don't push" or "wait until I review before deploying", the classifier blocks matching actions even when the default rules would allow them. A boundary stays in force until you lift it in a later message. Claude's own judgment that a condition was met does not lift it.

Boundaries are not stored as rules. The classifier re-reads them from the transcript on each check, so a boundary can be lost if context compaction removes the message that stated it. For a hard guarantee, add a deny rule instead.

### When auto mode falls back

Each denied action shows a notification and appears in `/permissions` under the Recently denied tab, where you can press `r` to retry it with a manual approval.

If the classifier blocks an action 3 times in a row or 20 times total, auto mode pauses and Claude Code resumes prompting. Approving the prompted action resumes auto mode. These thresholds are not configurable. Any allowed action resets the consecutive counter, while the total counter persists for the session and resets only when its own limit triggers a fallback.

In non-interactive mode with the `-p` flag, repeated blocks abort the session since there is no user to prompt.

Repeated blocks usually mean the classifier is missing context about your infrastructure. Use `/feedback` to report false positives, or have an administrator configure trusted infrastructure.

### How the classifier evaluates actions

Each action goes through a fixed decision order. The first matching step wins:

1. Actions matching your allow or deny rules resolve immediately.
2. Read-only actions and file edits in your working directory are auto-approved, except writes to protected paths.
3. Everything else goes to the classifier.
4. If the classifier blocks, Claude receives the reason and tries an alternative.

On entering auto mode, broad allow rules that grant arbitrary code execution are dropped:

* Blanket `Bash(*)` or `PowerShell(*)`
* Wildcarded interpreters like `Bash(python*)`
* Package-manager run commands
* `Agent` allow rules

Narrow rules like `Bash(npm test)` carry over. Dropped rules are restored when you leave auto mode.

The classifier sees user messages, tool calls, and your CLAUDE.md content. Tool results are stripped, so hostile content in a file or web page cannot manipulate it directly. A separate server-side probe scans incoming tool results and flags suspicious content before Claude reads it.

### How auto mode handles subagents

The classifier checks subagent work at three points:

1. Before a subagent starts, the delegated task description is evaluated, so a dangerous-looking task is blocked at spawn time.
2. While the subagent runs, each of its actions goes through the classifier with the same rules as the parent session, and any `permissionMode` in the subagent's frontmatter is ignored.
3. When the subagent finishes, the classifier reviews its full action history; if that return check flags a concern, a security warning is prepended to the subagent's results.

### Cost and latency

The classifier runs on a server-configured model that is independent of your `/model` selection, so switching models does not change classifier availability. Classifier calls count toward your token usage. Each check sends a portion of the transcript plus the pending action, adding a round-trip before execution. Reads and working-directory edits outside protected paths skip the classifier, so the overhead comes mainly from shell commands and network operations.

## Allow only pre-approved tools with `dontAsk` mode

`dontAsk` mode auto-denies every tool call that would otherwise prompt. Only actions matching your `permissions.allow` rules and read-only Bash commands can execute; explicit `ask` rules are denied rather than prompting. This makes the mode fully non-interactive for CI pipelines or restricted environments where you pre-define exactly what Claude may do.

Set it at startup with the flag:

```bash
claude --permission-mode dontAsk
```

## Skip all checks with `bypassPermissions` mode

`bypassPermissions` mode disables permission prompts and safety checks so tool calls execute immediately. As of v2.1.126 this includes writes to protected paths, which earlier versions still prompted for. Removals targeting the filesystem root or home directory, such as `rm -rf /` and `rm -rf ~`, still prompt as a circuit breaker against model error. Only use this mode in isolated environments like containers, VMs, or dev containers without internet access, where Claude Code cannot damage your host system.

You cannot enter `bypassPermissions` from a session that was started without one of the enabling flags; restart with one to enable it:

```bash
claude --permission-mode bypassPermissions
```

The `--dangerously-skip-permissions` flag is equivalent.

On Linux and macOS, Claude Code refuses to start in this mode when running as root or under `sudo`:

```text
--dangerously-skip-permissions cannot be used with root/sudo privileges for security reasons
```

The check is skipped automatically inside a recognized sandbox. To run autonomously in a container, use the dev container configuration, which runs Claude Code as a non-root user.

> **Warning:** `bypassPermissions` offers no protection against prompt injection or unintended actions. For background safety checks without prompts, use auto mode instead. Administrators can block this mode by setting `permissions.disableBypassPermissionsMode` to `"disable"` in managed settings.

## Protected paths

Writes to a small set of paths are never auto-approved, in every mode except `bypassPermissions`. This prevents accidental corruption of repository state and Claude's own configuration. In `default`, `acceptEdits`, and `plan` these writes prompt; in `auto` they route to the classifier; in `dontAsk` they are denied; in `bypassPermissions` they are allowed.

**Protected directories:**

* `.git`
* `.vscode`
* `.idea`
* `.husky`
* `.claude`, except for `.claude/commands`, `.claude/agents`, `.claude/skills`, and `.claude/worktrees` where Claude routinely creates content

**Protected files:**

* `.gitconfig`, `.gitmodules`
* `.bashrc`, `.bash_profile`, `.zshrc`, `.zprofile`, `.profile`
* `.ripgreprc`
* `.mcp.json`, `.claude.json`

## See also

* Permissions: allow, ask, and deny rules; managed policies
* Configure auto mode: tell the classifier which infrastructure your organization trusts
* Hooks: custom permission logic via `PreToolUse` and `PermissionRequest` hooks
* Ultraplan: run plan mode in a Claude Code on the web session with browser-based review
* Security: safeguards and best practices
* Sandboxing: filesystem and network isolation for Bash commands
* Non-interactive mode: run Claude Code with the `-p` flag — see [`claude_code_headless_reference.md`](./claude_code_headless_reference.md)

---

## What OpenCompany uses

| Mode | Where | Notes |
|---|---|---|
| `acceptEdits` | Default on `ClaudeTaskSpec.permission_mode` | Auto-approves file edits + safe filesystem commands inside cwd. cwd is `repo_root` for memory-bound runs (so claude can edit the actual repo) and the per-task worktree otherwise (isolated). |
| `plan` | User-selectable per task | When the user wants research without edits. |
| `bypassPermissions` | Available but not default | For sandboxed CI scenarios. Not recommended on a user's real repo. |
| `dontAsk` | Available but not default | For locked-down CI; agent must have `permissions.allow` rules pre-configured. |
| `auto` | Available but not default | Requires Sonnet 4.6 / Opus 4.6+ and Anthropic API plan; classifier model + token cost overhead. |
