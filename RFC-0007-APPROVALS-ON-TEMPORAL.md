# RFC-0007: Approvals the Claude Code and Codex way, on Temporal

- **Status:** Accepted design, 2026-10-25. The owner's decisions are in §3. Not implemented.
- **Created:** 2026-10-25
- **Scope:**
  - `server/services/approvals/` (what asks, rules, decisions, the card's data);
  - `server/services/temporal/` (a new `ApprovalWorkflow`, AgentWorkflow's tool step, MachinaWorkflow's Stop checkpoint for the gate);
  - `server/services/chat/` (the chat-run watchdog, Stop, a new message while a call waits);
  - `server/services/deployment/control.py` (the generation flag);
  - `server/services/cli_agent/` (the Codex launch);
  - the client's approval card and settings (`client/src/features/chat/approval/`, Home).
- **Relationship:**
  - **Amends RFC-0006 §9.6.** `approval_wait_version=1` now also covers tool calls that wait in the turn; `=2` keeps its meaning.
  - **Amends RFC-0005 §17.5.** Its `ApprovalWorkflow` arrives early, for tool calls; Phase 7 moves the gate and reviewed delivery onto it.
  - **Changes the Phase 1b plan** (§6).
- **Baseline:** HEAD f76b1dc3 (2026-10-25). Line numbers are as of that commit.
- **Research:** five notes and a synthesis in the session scratchpad, `approvals-research/`:
  - R1: Claude Code;
  - R2: Codex;
  - R3: Temporal human-in-the-loop and other agent frameworks;
  - R4: OpenCompany today and its integration points;
  - R5: classes, modes, rules and the card;
  - `SYNTHESIS.md`.

## 1. Summary

**What Claude Code and Codex do, and what OpenCompany does instead.**
- Claude Code and Codex let known-safe reads run and ask before anything else. A mode sets the baseline, and saved "always allow" rules carve out exceptions.
- A call that asks waits inside the agent's turn. The user allows it once, allows it always, or declines with a note, and the model continues with the real result or the note.
- OpenCompany does the opposite today. A held tool call returns at once with "Not sent yet", the turn goes on, `ApprovedToolCallWorkflow` sends after the owner's Send, and the employee learns the outcome on a later turn.

**This RFC adopts their model and makes the wait durable on Temporal.**
- The agent's workflow waits on a small child `ApprovalWorkflow`, outside any admitted action, so it holds no worker slot and Stop still completes.
- The owner's decision is a database write first, then a nudge; the database row stays the only record.
- **What asks** comes from one service table of approval classes (read, write, send, money) combined with a per-employee mode. No plugin changes.
- **It reaches employees per generation** (`approval_wait_version=1`). Running employees keep today's behaviour until their next Start after Reset.

## 2. Background

### 2.1 Claude Code (R1)

- **Modes:** default (Manual), acceptEdits, plan, auto, dontAsk, bypassPermissions.
  - Reads run without asking; edits, shell and network ask unless a rule allows them.
  - A short list always asks.
- **Rules:**
  - checked deny, then ask, then allow; the first category that matches wins, whatever its specificity;
  - specifiers such as `Bash(npm test *)` and `WebFetch(domain:…)`;
  - "Yes, and don't ask again" saves a rule, offered only when the prompt can show everything the rule allows;
  - rules are listed and removed in `/permissions`.
- **Decisions:**
  - allow;
  - allow with changes (`updatedInput`);
  - allow and remember (`updatedPermissions`);
  - deny with a message: the model reads it and continues;
  - deny with interrupt: the turn stops.
- **Waiting:**
  - `canUseTool` and `--permission-prompt-tool` wait for as long as the process lives, which is not durable.
  - The durable option is the `defer` hook: the turn ends, and `--resume` re-asks. It handles one tool call per turn only.
  - Claude Code's advice is never to leave a process waiting for a person.

### 2.2 Codex (R2)

- **Two settings decide when Codex asks:** a sandbox (read-only, workspace-write, danger-full-access) and an approval policy (on-request, never, a granular object; `untrusted` is retired).
- **What is checked:** execpolicy prefix rules, plus a short "dangerous" list.
- **`ReviewDecision`:**
  - Approved;
  - ApprovedForSession, cached in memory and keyed on the exact action;
  - standing amendments, written to rules and config files;
  - Denied: the model reads the reason and the turn continues;
  - Abort: the turn stops for commands and patches, while for an MCP call only that call is skipped.
- **Waiting:** in memory with no timeout. A cleared wait counts as Abort, and it is lost when the process restarts.
- **`codex exec` never asks anyone.** It forces `never` and answers every approval request with an error.

### 2.3 Other agent frameworks (R3)

- **They all pause inside the turn and resume with the real result:**
  - the OpenAI Agents SDK (`needs_approval`, interruptions, `RunState`);
  - LangGraph (`interrupt`, `Command(resume)`);
  - Strands, Google ADK and Deep Agents.
- **On Temporal,** each maps the pause onto `workflow.wait_condition` with a durable timeout, plus a Signal or an Update for the decision.
- **A decision that needs an immediate answer** should not travel as a bare Signal. Here the answer comes from the database compare-and-swap (§4.6), so the workflow needs only a nudge.
- **MCP elicitation is not durable.** Answer `decline` until MCP calls can be suspended.

### 2.4 OpenCompany today (R4; file:line at f76b1dc3)

- **The rule:** one Ask first boolean per workflow (`services/approvals/rules.py:1-146`).
- **The specs:** an `ApprovalSpec` on 14 sending plugins (`services/plugin/approval.py:39-123`). It decides which calls send, and renders the card.
- **A held agent tool call:**
  - `tool_calls.check` and `hold` write a `tool_call` row and answer "waiting for the owner" (`tool_calls.py:178-224`, `:289-299`);
  - the turn goes on (`agent_workflow.py:2360-2455`);
  - Send starts `ApprovedToolCallWorkflow`: 5 s Undo, `SEND_ONCE`, then the outcome (`approved_tool_call_workflow.py:33-80`, `execution.py:58-182`);
  - the employee hears an `[update]` note on its next turn (`execution.py:209-220`).
- **Unspec'd tools run unasked.** That covers shell, Sheets, Tasks and Contacts writes, Twitter, and cloud changes.
- **Graph sends without an Approval node** are never held (`tool_calls.py:196-197`).
- **The `approvalGate` node** waits inside a 24-hour activity with unlimited attempts, polling every 10 s (`nodes/workflow/approval_gate/__init__.py:168-277`). It is an admitted action, so Stop stays "pausing" until the owner decides (RFC-0006 §9.6).
- **CLI agents:**
  - their own prompts are off: Claude Code runs `--permission-mode dontAsk` (`config/ai_cli_providers.json:13`), and Codex runs `exec … --ask-for-approval never`;
  - their calls to OpenCompany tools are refused while asking first (`check_in_process`, `tool_calls.py:227-251`).
- **A live bug (R2; found in the source, not run).**
  - `codex exec` does not accept `--ask-for-approval` in current Codex or in 0.164.0, yet OpenCompany always passes it (`services/cli_agent/providers/openai_codex.py:145-155`; the test asserts it at `tests/services/cli_agent/test_providers.py:470-474`).
  - Codex is unpinned (no `package_version`, so `bun x` takes npm latest), so every Codex run should fail with a usage error.
  - The operator's `~/.codex` config, rules and reviewer also apply to employees' runs.

## 3. Owner decisions (2026-10-25)

**Standing direction (the same day):**
- Approvals and safeguards stay their own service.
- Plugins stay untouched, except to self-register into a service registry or to fix their own bug.
- No overengineering, no complexity.
- No key checks in content rules: "the LLM router will handle the api keys". When content rules return, the approval gate calls the approvals service in one line.

| # | Question | Decision |
|---|---|---|
| D1 | Wait inside the turn? | **Yes.** A held call waits in the employee's turn, then runs, and the employee gets the real result. This applies to new generations; running employees keep drafts until Reset. |
| D2 | How long may a turn wait? | **Until the draft expires** (7 days). The 24-hour chat-run timeout does not count the wait. |
| D3 | A new owner message while a call waits | **It declines the call and becomes its note** (Claude Code). The employee continues with it. |
| D4 | Undo after Allow | **Dropped.** Allow runs at once. |
| D5 | Modes | **Three, with no "never":** "Before anything outside" (the default), "Before everything", "Only money and deletes". Today's on becomes "Before anything outside", and today's off becomes "Only money and deletes". |
| D6 | Always allow | **Yes**, scoped and removable. It is never offered on a money card; money rules are made in settings. |
| D7 | Tools without a spec | **They ask, with a generic card,** and Hire may give them to employees. |
| D8 | Stripe | **It asks on a card** instead of being refused. |
| D9 | Claude Code agents | **Their calls to OpenCompany tools are held as drafts.** Claude's built-in tools keep `dontAsk`. |
| D10 | Codex | **Fix the flag, pin the version, and isolate the operator's config.** |
| D11 | Web reads | **Fetching a URL asks like a send,** except searches through a search provider. |
| D12 | Graph sends without an Approval node | **Left unheld.** The graph's author adds an Approval node. |

## 4. Design

### 4.1 Approval classes (`services/approvals/classes.py`, new)

The ladder is the one Hire already uses: `SIDE_EFFECTS = ("read", "write", "send", "money")` (`services/employees/apps.py:43`).
- **read:** looks things up. It never asks.
- **write:** works on its own files, notes, team, or the owner's phone.
- **send:** reaches people or services. That means messages, posts, invites, deploys, cloud changes, commands on the computer and, under D11, fetching a URL.
- **money:** spends money or deletes something.

The table has the same shape as `repeats.py`:
- `CLASSES: {node_type: class}`;
- `CLASSES_WHEN: {node_type: (field, {value: class})}`;
- `class_of(node_cls, data, *, tool_call)`, which reads an unset field from the model's default, as `repeatable()` does.

An unlisted node type is `send`. A new plugin then asks under the default mode and needs no edit. The class decides; a plugin's `ApprovalSpec` only renders the card (recipient, body, subject, details, Edit fields, outcome labels) and keeps `restrict_while_asking` for the Browser. The service no longer honours `refuse_while_asking`: Stripe is held instead (D8).

**The starting table.** AP.1 implements it; the test checks every name and value.
- **read**
  - The 15 triggers. They never reach the check.
  - The 13 chat models and the 4 code executors.
  - The 7 Android monitors: batteryMonitor, networkMonitor, systemInfo, location, appList, environmentalSensors, motionDetection.
  - **Search and provider reads:**
    - braveSearch, duckduckgoSearch, perplexitySearch, serperSearch;
    - twitterSearch, twitterUser;
    - gmaps_create, gmaps_locations, gmaps_nearby_places;
    - detectLanguage, translateText, transliterateText, speechToText;
    - embeddingGenerator, visionAnalyze, textGenerator.
  - **Local reads and computation:** calculatorTool, chatHistory, console, context, currentTimeTool, documentParser, employeeJobSource, fileHandler, fileRead, fsSearch, gallery, masterSkill, proxyStatus, socialReceive, start, teamMonitor, textChunker.
  - **Waits:** timer, cronScheduler.
  - **The ask itself:** approvalGate.
  - **Answers to the owner or the caller:** chatReply, chatUi, webhookResponse.
  - **The 16 agents** (aiAgent, chatAgent and the 14 specialized agents). Each of their own tool calls is checked.
  - **The read operations of operation-mapped nodes:**
    - Gmail `search`/`read`; MS Mail `read`/`search`/`list_attachments`/`download_attachments`;
    - Calendar and Tasks `list`; Contacts `list`/`search`/`get`; Drive `download`/`list`; Sheets `read`;
    - emailRead `list`/`search`/`read`/`folders`;
    - discordAction's list, get and download operations; whatsappDb's reads;
    - whatsappBusinessMedia `get_url`/`download`; whatsappBusinessSend `list_templates`;
    - the reads of aws, cloudflare, gcloud, github, vercel, dodo, proxyConfig and processManager;
    - vectorStore `query`, dataSource's reads, tikhubAction `list_endpoints`/`account`;
    - the Browser's observations and `request_user`.
- **write**
  - agentBuilder, canvas, employeeJob, taskManager, writeTodos, simpleMemory, textToSpeech;
  - vectorStore `store`/`delete`; proxyConfig's changes;
  - fileModify `write`/`edit`; dataSource `write`/`append`/`copy_to_workspace`;
  - githubAction `repo_clone`, gcloudAction `set_project`, processManager `stop`/`restart`;
  - the 9 Android automation nodes;
  - the Browser's `close`.
- **send** (the default)
  - **The plain sends:** whatsappSend, telegramSend, discordSend, emailSend, twitterSend, socialSend, chatSend.
  - **The send operations of mapped nodes:**
    - Gmail `send`; MS Mail `send`/`reply`;
    - calendar, contacts, tasks, Drive and Sheets writes; emailRead `move`/`flag`;
    - Discord's changes; whatsappDb's channel changes; WhatsApp Business sends and media changes;
    - GitHub PRs and issues;
    - cloud starts, stops, uploads and deploys;
    - Dodo's checkout, customer and portal-link operations.
  - **Commands on the computer:** shell; processManager `start`/`send_input`.
  - **Tools that drive apps:** the mobile agents (mobile_use_agent, android_tool).
  - **The CLI agents:** claude_code_agent, codex_agent, rlm_agent.
  - **mcpConnector.** Its per-tool Ask first stays and acts as a rule.
  - **URL fetches, under D11:**
    - httpRequest and proxyRequest GET, POST, PUT and PATCH;
    - httpScraper, crawleeScraper, fileDownloader;
    - tikhubAction `fetch_url` and `call`;
    - the Browser's `navigate` and `tabs` (see O1).
- **money**
  - **Money:** stripeAction (every command); Dodo `create_refund`/`cancel_subscription`/`change_subscription_plan`/`call`; apifyActor (a paid run).
  - **Deletes:**
    - calendar, contacts, tasks and email `delete`; discordAction `delete_message`;
    - aws `s3_delete`, gcloud `storage_rm`, cloudflare `dns_record_delete`;
    - httpRequest and proxyRequest DELETE.
  - **Passthroughs that can spend or delete:** aws `call`; cloudflare, gcloud, github, vercel and discord `custom`.

`repeats.py` stays separate. It answers "may a broken-off attempt run again", which differs from "may it run unasked" (for example `fileModify` `edit`, or Dodo `cancel_subscription`). One cross-check test is enough: every class-level `read` entry in `classes.py` is in `REPEATABLE`.

### 4.2 Modes (D5)

| Mode | Asks for | Like |
|---|---|---|
| **Before anything outside** (default) | send, money | Claude Code acceptEdits; Codex Auto |
| **Before everything** | write, send, money | Claude Code default (Manual); Codex Read-only |
| **Only money and deletes** | money | Claude Code auto; Codex on-request with the network on |

- **No "never" mode.** A rule lets a money tool run unasked.
- **Where the mode lives.** It is stored with the workflow's rule (`workflow_rules`), next to `ask_first`, which old generations keep reading: on maps to "outside", off to "money_only". AP.2 picks the storage, following the database's column-add migration.
- **No rule row.** A workflow without one, such as one built in the editor, asks nothing, as today.

### 4.3 The decision order

The harness enforces it, never the model, and it fails closed:
1. **Read** runs.
2. **A matching "always allow" rule** runs, and the call is recorded as `approved_by: auto`.
3. **Otherwise, if the mode asks for the call's class,** it is held.
   - In a v1 generation the turn waits (§4.6).
   - Anywhere else it is a draft, as today: a v0 generation, a CLI agent's OpenCompany tool call (D9), or an in-process agent.
4. **Otherwise** it runs, and in a chat it is recorded as `approved_by: auto`, as today.

The Browser's `restrict_while_asking` still applies to its site actions, which run read-only and hand changes to the owner through `request_user`. `tool_calls.check` stays the one place this is decided (`tool_calls.py:178`); `check_in_process` follows the same order.

### 4.4 Always-allow rules (D6)

- **Key:** the workflow (the employee), the node type, and one scope:
  - a spec'd send: its recipient (the spec's `recipient` fields, normalised);
  - a URL fetch: the host (httpRequest, proxyRequest, httpScraper, crawleeScraper, fileDownloader, the Browser's navigation);
  - an operation-mapped node: the operation, for example "Google Calendar: create events";
  - otherwise the whole tool.
- **Offered** on the card only when it can name everything the rule covers (Claude Code's rule), and never on a money card. Money rules are added in settings.
- **Storage:** a small `approval_allows` table with `id`, `workflow_id`, `node_type`, `operation`, `scope_kind`, `scope_value`, `label`, `created_at` and `created_by`. `tool_calls.check` reads it.
- **Lifetime:** Reset keeps rules, because they are the owner's settings. Deleting the employee deletes them through the existing workflow-deleted hook.
- **Revocation:** a settings list, "Allowed without asking", with Remove on each line. A removed rule applies from the next call.
- **Not adopted:** deny rules, session-scoped approvals, and rule files.

### 4.5 The card and the decisions

- **Allow** (once). A spec'd message still offers Edit first.
- **Always allow {scope}:** allows this call and saves the rule. A one-line confirmation names the rule.
- **Decline,** with an optional note: "Tell {name} what to do instead". The model reads an error-style result: "The owner declined this; nothing happened. Their note: …. Don't try to get the same outcome another way."
- **A new chat message from the owner while a call waits** declines that call, with the message as its note (D3). The employee continues with it, and no second run starts.
- **Chat Stop** cancels the run's open approvals, and the turn ends as stopped.
- **No Undo after Allow** (D4).
- **A tool without a spec** gets a generic card (D7):
  - the title "{name} wants to use {Tool}";
  - up to six argument lines, each cut at 300 characters, with secret-named keys removed (`sanitize_runtime_payload`);
  - a class pill: "Reaches out", "Money" or "Deletes";
  - no Edit.
- **1b.1's outcome cards** are not requests and never offer Always:
  - "May have sent", with Send again after a confirmation;
  - "May have happened", with Dismiss.
- **The wording** follows R5 §5:
  - the mode menu "Ask me first: Before anything outside / Before everything / Only for money and deletes";
  - the settings list and its empty state;
  - the money footer "Money and deletes always ask unless you allow them in {name}'s settings."

### 4.6 The wait on Temporal (D1, D2)

This applies only to generations whose `resource_manifest` has `approval_wait_version: 1`, written by `begin_generation` (`services/deployment/control.py:160`). The flag is threaded the way `execution_control_version` is:
- the listener arguments (`services/deployment/manager.py:960`, `:1124`);
- the controller's run input;
- MachinaWorkflow's agent child context, as `pi_harness_version` is (`workflow.py:762-763`);
- the node activity context, so `tool_calls.check` sees it.

**1. Hold.** The tool activity holds the call as today and returns the held marker (`approval_id`, `status: waiting_for_owner`). The row is the only record.

**2. Wait** (`agent_workflow.py:2360-2391`, v1 branch only):
- On a held result, end the tool action (`end_action`).
- Start the child `ApprovalWorkflow`, id `approval-<approval_id>`, inside `begin_child_start` / `end_child_start`.
- Await it **outside any admitted action**, so Stop's checkpoint completes (T12). The model does not see "Not sent yet".

**3. `ApprovalWorkflow`** (new, `services/temporal/approval_workflow.py`, about 60 lines, `sandboxed` per RFC-0006 §17.18):
- `run(ref)` reads the row through `approvals.read`, a new 30 s activity on `BOOKKEEPING_RETRY`.
- It loops on `workflow.wait_condition(lambda: self._nudged, timeout=max(expires_at - now, 1 s))`. After each nudge or the deadline it re-reads, and returns once the row is decided or final:
  - `approved{always?}`;
  - `declined{note}`;
  - `expired` (fail closed: nothing runs);
  - `cancelled`.
- Its one message is the Signal `nudge()`, with no payload. Duplicates are harmless, and nothing needs validating.
- Every wait has a timeout: switching between `timeout=None` and a timeout is a nondeterministic change. A cancel propagates; never swallow `CancelledError`.

**4. Decide.**
- `decide_approval` (`services/approvals/handlers.py:162-217`) keeps the database compare-and-swap (`decisions.apply`) and its immediate answer.
- After the commit it nudges `approval-<id>`, best-effort. This is T10's shape: the intent first, delivery after.
- The approvals watchdog round re-nudges rows that were decided more than 10 s ago and are still unconsumed.
- A nudge to a closed workflow is logged and dropped.

**5. Run** (back in AgentWorkflow):
- **Approved:**
  - wait until resumed (`_wait_until_resumed()`), then `begin_action()`;
  - run the three steps extracted from `ApprovedToolCallWorkflow` as one helper: `approvals.claim_send`, then the node activity with `SEND_ONCE`, then `approvals.record_outcome`. Activity ids derive from the approval id;
  - append the real result to the transcript, and continue the loop;
  - a break-off reads "may or may not have happened" (1b.1's redo guard).
- **Declined:** an error-style result with the owner's note.
- **Expired:** an error-style result: "The owner did not answer in time; nothing happened."
- **Cancelled:** the turn ends as stopped.

**6. Interactions.**
- **Stop and Resume:** the wait is not an action, so Stop completes. A decision taken while stopped is consumed after Resume.
- **Reset:** terminates the run and its child, and the gate's reset hook cancels the open rows (`approval_gate/__init__.py:279-297`, `store.cancel_open`).
- **Continue-as-new:** AgentWorkflow continues as new only between iterations (`agent_workflow.py:3000-3055`), never during a tool batch. The wait lives in the child.
- **Parallel calls:** tool calls already run in model order, so a second held call waits behind the first, as both CLIs prompt one at a time.
- **Limits:** one child per wait, under the 2,000 pending-children limit. Nothing grows with waiting time, and payloads carry ids only.

**7. Replay.**
- `ApprovalWorkflow` is a new type: register it in `_framework_workflows` and record it in the golden identity file.
- The shared types branch only on the frozen flag, never on `workflow.patched` for old generations (RFC-0005 rules 2 and 3).
- The next release's corpus records five scenarios: approve, decline, expire, decide-while-stopped, and cancel while waiting.

### 4.7 Chat runs (D2, D3)

- **The watchdog's 24-hour timeout** (`ledger.py:763-767`) and 1b.14's cancel skip a run that has an open approval row for its `run_id`.
- **Chat Stop** (`request_stop`) cancels the run's open approvals with `store.cancel_open(run_ids=[run_id])`, the helper branch edits already use (`chat/branches.py:774`), and then nudges them.
- **A new owner message while a run waits on an approval.**
  - `send_chat_message` declines that run's open approvals, with the message text as the note, and nudges them.
  - The message is saved in the thread and does not start a second run; today a live run answers `run_in_progress`.
  - The waiting run continues with the decline.
  - AP.4c defines the exact reply fields.

### 4.8 Graph runs (D12)

- Under `approval_wait_version=1`, MachinaWorkflow no longer counts the `approvalGate` activity in the Stop checkpoint (RFC-0006 §9.6 item 3). The node after it runs only after Resume, through its own admission.
- Graph sends without an Approval node stay unheld (D12). Hired employees always get the Approval node.
- RFC-0005 Phase 7 (`approval_wait_version=2`) moves the gate and reviewed delivery onto the same `ApprovalWorkflow`.

### 4.9 CLI and in-process agents (D9, D10)

- **`check_in_process` holds a draft instead of refusing.** It writes the `tool_call` row, keyed by the call id: for MCP calls, `mcp:` plus a digest, `workflow_tools.py:347-351`; AP.2c checks that it stays stable when the call is retried. It answers "waiting for the owner".
  - The owner's Allow starts `ApprovedToolCallWorkflow`, which runs the node's own activity; the CLI is not involved.
  - The employee hears the outcome as an `[update]` note.
  - A CLI session cannot wait durably, so this path stays a draft.
- **Claude Code's built-in tools keep `dontAsk`.** Answering Claude Code's own permission prompts through cards (stdio `can_use_tool`) is not adopted: it blocks the CLI session, and a restart loses it.
- **Codex** (AP.0):
  - drop `--ask-for-approval` from the `exec` argv. Keep `CodexTaskSpec.ask_for_approval` accepted but without effect, because saved task lists reject unknown fields;
  - pin `package_version` in `config/ai_cli_providers.json`;
  - add `--ignore-user-config --ignore-rules` once their names are checked against the pinned version;
  - update `test_providers.py:470-474`.

### 4.10 What stays and what retires

| | v0 generations, CLI and in-process drafts | v1 generations, agent tool calls |
|---|---|---|
| What asks | Ask first on/off and the 14 specs | the mode and the class table, minus rules |
| A held call | a draft; "waiting for the owner"; `ApprovedToolCallWorkflow` after Send (5 s Undo); `[update]` note | waits in the turn; runs after Allow; the real result |
| Retry of a failed send | `ApprovedToolCallWorkflow` | `ApprovedToolCallWorkflow` (unchanged) |
| The gate | an activity that holds Stop | an activity outside the Stop checkpoint (Phase 7: the child) |

### 4.11 Not adopted, and why

- **The settings-file scope stack, Bash command-string rules and compound-command splitting:** OpenCompany's tools are typed, with operations.
- **The auto-mode classifier and Codex's AI reviewer:** an extra model on every call; owners decide.
- **User hooks and OS sandboxes:** OpenCompany is a product; containment is `resolve_within` and netpolicy.
- **Deny rules and session approvals:** Decline covers one call, and a months-long employee has no clear session.
- **Codex's own prompts** (they need `app-server` and a live process); **MCP elicitation** (answer `decline`).

## 5. Amendments

- **RFC-0006 §9.6 and the §22.5 flag table.** `approval_wait_version=1` widens to two things: graph approval gates no longer hold Stop, **and** agent tool calls wait in the turn on `ApprovalWorkflow`, with the modes, classes and rules of this RFC. `=2` stays: the gate and reviewed delivery become `ApprovalWorkflow` children (Phase 7).
- **RFC-0005 §17.5.** The `ApprovalWorkflow` arrives before Phase 7, for tool calls, in its nudge-and-re-read form (§4.6). Phase 7 keeps the gate, reviewed delivery and the outbox. T10's deliverer reduces to the nudge plus the re-nudge backstop.

## 6. Changes to the Phase 1b plan

- **1b.1b and 1b.1c (the redo guard, the unknown cards):** unchanged. They come first; a blocking approved run uses them.
- **1b.2:** unchanged. The approval wait's cancel must propagate.
- **1b.5 (sends wait while stopped):** shrinks to v0 rows and Retry. A v1 run waits for Resume in the workflow.
- **1b.9, 1b.10:** unchanged.
- **1b.14 (cancel a chat run past 24 h):** skips a run waiting on an approval.
- **The plan's open Ask-first gaps** close for v1: Dev-graph sends without a spec, and specless tools.

## 7. Implementation plan

One commit per step, each with its tests. After every commit, run the replay corpus and the golden test.

| Step | What | Main files | Replay |
|---|---|---|---|
| AP.0 | Codex launch fix (D10): drop the flag, pin the version, isolate the config | `services/cli_agent/providers/openai_codex.py`, `types.py`, `config/ai_cli_providers.json`, `tests/services/cli_agent/test_providers.py` | none |
| AP.1 | The class table (§4.1) and its test (names, values, the cross-check with `repeats.py`) | `services/approvals/classes.py`, `tests/services/approvals/test_classes.py` | none |
| AP.2a | Modes and the class-based check, v1 only. The mode is stored on `workflow_rules`; `check` is mode × class; generic rows for specless tools; `refuse_while_asking` holds. | `services/approvals/rules.py`, `tool_calls.py`, `handlers.py`, `core/database.py` | none (activity code) |
| AP.2b | Always-allow rules and the decline note: the `approval_allows` table, `send{always}`, `discard{note}`, WS commands to list and remove rules | `models/approvals.py`, `services/approvals/*`, `core/database.py` | none |
| AP.2c | `check_in_process` holds drafts instead of refusing (D9) | `services/approvals/tool_calls.py`, `services/handlers/tools.py`, `services/cli_agent/workflow_tools.py` | none |
| AP.3 | Client: the mode menu, Allow / Always allow / Decline with a note, the generic card, "Allowed without asking", the wording | `client/src/features/chat/approval/*`, `data/approvals.ts`, Home settings | none |
| AP.4a | `ApprovalWorkflow` and `approvals.read`; the nudge after decide; the re-nudge backstop; golden | `services/temporal/approval_workflow.py`, `services/approvals/activities.py`, `handlers.py`, `reconcile.py`, `worker.py` | new type |
| AP.4b | AgentWorkflow's v1 wait and run (§4.6), the extracted claim/run/record helper, and the flag threading | `services/temporal/agent_workflow.py`, `approved_tool_call_workflow.py`, `services/deployment/control.py`, `manager.py`, `workflow.py` | per-generation flag |
| AP.4c | Chat: the watchdog exemption, Stop cancels open approvals, a new message declines a waiting call | `services/chat/ledger.py`, `handlers.py` | none |
| AP.5 | The approvalGate is left out of the Stop checkpoint under v1 | `services/temporal/workflow.py` | per-generation flag |
| AP.6 | Docs: `chat_protocol.md` Approvals, `normal_mode.md` (Ask first becomes the mode), `TEMPORAL_ARCHITECTURE.md`, the CLAUDE.md rows | docs | none |

**Order:**
- AP.0 first: it is a live bug, and independent.
- Then 1b.1c, 1b.1b and 1b.2.
- Then AP.1 to AP.6.
- Then the rest of Phase 1b batch A.

**The corpus scenarios** (§4.6, item 7) are recorded at the next release cut.

## 8. Acceptance

- In a v1 generation:
  - a held send waits, Allow runs it, and the model's next step sees the real result;
  - Decline gives the model the note, and nothing runs;
  - expiry runs nothing.
- Stop completes while a call waits. A decision taken while stopped runs only after Resume.
- A new owner message declines the waiting call and does not start a second run. Chat Stop ends the turn.
- An "always allow" rule skips the next matching call and is listed and removable. No rule is offered on a money card.
- A specless tool asks with a generic card that shows no secret-named key.
- Under the default mode, a URL fetch asks, and a search does not.
- v0 generations behave exactly as today. The replay corpus and golden pass.
- A Codex employee run starts, with no usage error, on the pinned version.

## 9. Open items, to confirm while implementing

- **O1. The Browser's navigation under D11.** Navigating to a site asks like a fetch, with "Always allow this site" rules; site actions keep their read-only restriction. This may ask often until rules accumulate. Confirm with the owner in AP.2a.
- **O2. TikHub `fetch_url`** sends a share URL to TikHub, which fetches it. It is classed `send` under D11; confirm.
- **O3. Mode storage:** a column on `workflow_rules` through the existing column-add migration, or a small table. Decide in AP.2a.
- **O4.** Check that the MCP call id's digest stays stable when a CLI agent retries a held call (AP.2c).
- **O5.** The exact `send_chat_message` reply fields when a message declines a waiting call (AP.4c).
