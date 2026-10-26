# RFC-0007: Approvals the Claude Code and Codex way, on Temporal

- **Status:** Accepted design, 2026-10-25. Checked against the code the same day and revised (§10). The owner's decisions are in §3. Not implemented.
- **Created:** 2026-10-25
- **Scope:**
  - `server/services/approvals/`: what asks, rules, decisions, the card's data, and the wait's activities;
  - `server/services/temporal/`: a new `ApprovalWorkflow`, AgentWorkflow's tool step, MachinaWorkflow's Stop checkpoint for the gate, and the test gates;
  - `server/services/deployment/`: the generation flag, Stop's tree walk, and Reset's cancel;
  - `server/services/chat/`: the chat-run watchdog, and a new message while a call waits;
  - `server/services/cli_agent/` and `server/services/handlers/tools.py`: CLI and in-process drafts;
  - the client: the approval card, the mode menu, and the menu's "Allowed without asking" list (`client/src/features/chat/`).
- **Relationship:**
  - **Amends RFC-0006 §9.6.** `approval_wait_version=1` now also covers tool calls that wait in the turn; `=2` keeps its meaning.
  - **Amends RFC-0005 §17.5.** Its `ApprovalWorkflow` arrives early, for tool calls; Phase 7 moves the gate and reviewed delivery onto it.
  - **Leaves RFC-0006 §24.1 as it is** (D18).
  - **Changes the Phase 1b plan** (§6).
- **Baseline:** f76b1dc3 (2026-10-25). Line numbers are as of that commit. Checked at b3b7cdf3, whose code is the same.
- **Research and check:** in the session scratchpad:
  - `approvals-research/`: five notes and a synthesis:
    - R1: Claude Code;
    - R2: Codex;
    - R3: Temporal human-in-the-loop and other agent frameworks;
    - R4: OpenCompany today and its integration points;
    - R5: classes, modes, rules and the card;
    - `SYNTHESIS.md`.
  - `approvals-check/`: the check against the code, eight reports (C1 to C8, one per area) and `SYNTHESIS.md`.

## 1. Summary

**What Claude Code and Codex do, and what OpenCompany does instead.**
- Claude Code and Codex let known-safe reads run and ask before anything else. A mode sets the baseline, and saved "always allow" rules carve out exceptions.
- A call that asks waits inside the agent's turn. The user allows it once, allows it always, or declines with a note, and the model continues with the real result or the note.
- OpenCompany does the opposite today. A held tool call returns at once with "Not sent yet", the turn goes on, `ApprovedToolCallWorkflow` sends after the owner's Send, and the employee learns the outcome on a later turn.

**This RFC adopts their model and makes the wait durable on Temporal.**
- **Where the wait happens.** In a run that answers the owner in chat, the agent's workflow waits on a small child `ApprovalWorkflow`, outside any admitted action. It holds no worker slot, and Stop still completes. Other runs keep today's drafts (D15).
- **How a decision lands.** The owner's decision is written to the database first, then a nudge goes out. The database row stays the only record.
- **What asks.** One service table of approval classes (read, write, send, money), combined with a per-employee mode, minus "always allow" rules. No plugin changes.
- **How it reaches employees.** Per generation (`approval_wait_version=1`), from the release after the one that ships the code (§7). Running employees keep today's behaviour until their next Start after Reset.

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

### 2.4 OpenCompany today (R4 and the check; file:line at f76b1dc3)

- **The rule:** one Ask first boolean per workflow (`services/approvals/rules.py:1-146`).
- **The specs:** an `ApprovalSpec` on 14 sending plugins (`services/plugin/approval.py:39-123`). It decides which calls send, and renders the card.
- **A held agent tool call:**
  - `tool_calls.check` and `hold` write a `tool_call` row and answer "waiting for the owner" (`tool_calls.py:178-224`, `:289-299`).
  - The turn goes on (`agent_workflow.py:2360-2455`).
  - Send always sets a 5 s Undo window and starts `ApprovedToolCallWorkflow` (`decisions.py:100`; `handlers.py:199-209`, `approved_tool_call_workflow.py:33-80`, `execution.py:58-182`). The node then runs past the check only with `approval_execution` and the claim's token (`tool_calls.py:160-165`, `:183-195`).
  - The employee hears an `[update]` note on its next turn (`execution.py:209-220`).
- **Unspec'd tools run unasked** (`tool_calls.py:196`). That covers shell, Sheets, Tasks and Contacts writes, Twitter, and cloud changes.
- **Graph sends without an Approval node** are never held (`tool_calls.py:196-197`).
- **The `approvalGate` node:**
  - It waits inside a 24-hour activity with unlimited attempts, polling every 10 s (`nodes/workflow/approval_gate/__init__.py:168-277`).
  - It is an admitted action, so Stop stays "pausing" until the owner decides (RFC-0006 §9.6).
  - Reset cancels open rows only through the gate's own reset hook (`:279-297`). That hook runs only when the graph has a gate (`services/deployment/runtime_state.py:38-55`).
- **Chat Stop** in a controlled generation pauses the employee (`services/chat/handlers.py:471-487`). Every generation is controlled since `execution_control_version=1` (`services/deployment/control.py:160`); `request_stop` serves legacy generations only.
- **CLI agents:**
  - Claude Code runs `--permission-mode dontAsk` (`config/ai_cli_providers.json:13`).
  - Their calls to OpenCompany tools are refused while asking first (`check_in_process`, `tool_calls.py:227-251`).
  - The MCP bridge gives a call id only to the Coding Agent, Task Manager and the delegated-task check (`services/cli_agent/workflow_tools.py:337-353`).
- **Codex does not run** (C1):
  - `AICliSession` refuses every provider but Claude (`services/cli_agent/session.py:322-329`; `docs-internal/cli_agent_framework.md:635`).
  - Past that refusal:
    - the argv builder raises;
    - the result parser does not read Codex 0.164.0's events;
    - the version is not pinned;
    - an operator's global `codex` would be preferred.
  - It also passes `--ask-for-approval`, which `codex exec` rejects (`openai_codex.py:151-155`).
  - Making Codex run is its own item (D17).

## 3. Owner decisions (2026-10-25)

**Standing direction (the same day):**
- Approvals and safeguards stay their own service.
- Plugins stay untouched, except to self-register into a service registry or to fix their own bug.
- No overengineering, no complexity.
- No key checks in content rules: "the LLM router will handle the api keys". When content rules return, the approval gate calls the approvals service in one line.

### 3.1 The design

| # | Question | Decision |
|---|---|---|
| D1 | Wait inside the turn? | **Yes.** A held call waits in the employee's turn, then runs, and the employee gets the real result. This applies to new generations, in runs that answer the owner in chat (D15). Running employees keep drafts until Reset. |
| D2 | How long may a turn wait? | **Until the draft expires** (7 days). The 24-hour chat-run timeout does not count the wait. |
| D3 | A new owner message while a call waits | **It declines the call and becomes its note** (Claude Code). The employee continues with it. |
| D4 | Undo after Allow | **Dropped.** Allow runs at once. |
| D5 | Modes | **Three, with no "never":** "Before anything outside" (the default), "Before everything", "Only money and deletes". Today's on becomes "Before anything outside", and today's off becomes "Only money and deletes". |
| D6 | Always allow | **Yes**, scoped and removable. It is never offered on a money card; money rules are made in the employee's list (D19). |
| D7 | Tools without a spec | **They ask, with a generic card.** Whether Hire may give them to employees is a later decision (D18). |
| D8 | Stripe | **It asks on a card** instead of being refused. |
| D9 | Claude Code agents | **Their calls to OpenCompany tools are held as drafts.** Claude's built-in tools keep `dontAsk`. |
| D10 | Codex | Fix the flag, pin the version, and isolate the operator's config. **Moved out by D17:** Codex does not run at all, and the item that makes it run includes this. |
| D11 | Web reads | **Fetching a URL asks like a send,** except searches through a search provider. |
| D12 | Graph sends without an Approval node | **Left unheld.** The graph's author adds an Approval node. |

### 3.2 After the check against the code

| # | Question | Decision |
|---|---|---|
| D13 | What chat Stop does to a waiting call | **It pauses the employee; the call keeps waiting.** The card stays. Allow or Decline while paused takes effect after Resume (RFC-0006 T12). |
| D14 | Where a message that declines a call shows (D3) | **On the card, as the decline's note.** No chat bubble: a thread row would break Try again and versions for that turn (C4 ran the branch code). |
| D15 | Which runs wait in the turn | **Only runs that answer the owner in chat.** Schedule, webhook and team-review runs keep today's drafts and `[update]` notes. The conversation store is last-writer-wins, so a days-long wait would overwrite what another firing saved. |
| D16 | The Python, JavaScript and TypeScript code tools | **They ask, like shell** (`send`). Their sandboxes are weak by the code's own comments. Monty stays `read`, except a run that may fetch a URL. |
| D17 | Codex (AP.0) | **Its own item, later,** outside this RFC: a pipe session, its JSON events, the pin, and D10's flag and config isolation (§9, O8). |
| D18 | D7's "Hire may give them" | **Asking only.** Hire's allowlist stays as it is (RFC-0006 §24.1); widening it is a later decision. |
| D19 | Where "Allowed without asking" lives | **In the Ask-first menu,** as "Allowed without asking…", which opens that employee's list. |

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

**The rules of the table:**
- **The safe default.** An unlisted node type is `send`, and so is a value that a mapped field does not list. A new plugin then asks under the default mode and needs no edit. A test checks that every Literal value of a mapped field is listed.
- **Which data it reads.** `class_of` judges the tool's own input when the tool has a separate input model (`tool_input_model()` is not `Params`), and the merged node data otherwise; its caller passes that data. A Browser call without an `operation` runs `snapshot`, not the saved `navigate`.
- **Lists.** A list value takes its worst member's class (montyExecutor's `capabilities`).
- **Calls a spec says reach no one.** When a call's spec covers it and its `when` says the call reaches no one, a `send` call is `write`.
  - This covers calendar changes without invites: under the default mode they run, as they do today with Ask first on.
  - It never lowers `money`. A calendar delete without notices stays a delete; Hire's calendar app turns notices off, so lowering it would let every hired employee delete events unasked, even under "Only money and deletes".
  - So a Dodo `call` asks in every mode, a GET endpoint included: the table cannot see the endpoint. This is a false positive in the safe direction.
- **The class decides; the spec renders.** A plugin's `ApprovalSpec` renders the card: recipient, body, subject, details, Edit fields and outcome labels.
  - The Browser keeps `restrict_while_asking` (§4.3).
  - In v1 the service holds Stripe instead of honouring `refuse_while_asking` (D8). v0 keeps the refusal.

**The starting table.** AP.1 implements it, and its test checks every name and value against the registry (157 types at f76b1dc3).
- **read**
  - **Triggers and agents.**
    - The 15 triggers; they never reach the check.
    - The 16 agents: aiAgent, chatAgent and the 14 specialized agents. Each of an agent's own tool calls is checked.
  - **Chat models and Monty.**
    - The 13 chat models.
    - montyExecutor, unless its `capabilities` include `http_get` (then `send`) or `workspace_write` (then `write`).
  - **The 7 Android monitors:** batteryMonitor, networkMonitor, systemInfo, location, appList, environmentalSensors, motionDetection.
  - **Search and provider reads:**
    - braveSearch, duckduckgoSearch, perplexitySearch, serperSearch;
    - twitterSearch, twitterUser;
    - gmaps_create, gmaps_locations, gmaps_nearby_places;
    - detectLanguage, translateText, transliterateText, speechToText;
    - embeddingGenerator, visionAnalyze, textGenerator.
  - **Local reads and computation:** calculatorTool, chatHistory, console, context, currentTimeTool, documentParser, employeeJobSource, fileHandler, fileRead, fsSearch, gallery, masterSkill, proxyStatus, socialReceive, start, teamMonitor, textChunker.
  - **Waits, the ask itself, and answers.**
    - Waits: timer, cronScheduler.
    - The ask itself: approvalGate.
    - Answers to the owner or the caller: chatReply, chatUi, webhookResponse.
  - **The employee's team:** taskManager, every operation. A held Task Manager preflight would be appended as "Not sent yet" before the loop (`agent_workflow.py:1984-1989`). Each teammate's own calls are checked.
  - **Lookups inside write tools:**
    - simpleMemory `recall`/`list`/`get`;
    - agentBuilder `inspect_canvas`/`inspect_node`/`search_docs`/`read_doc`.
  - **The read operations of operation-mapped nodes:**
    - Gmail `search`/`read`; MS Mail `read`/`search`/`list_attachments`/`download_attachments`;
    - Calendar and Tasks `list`; Contacts `list`/`search`/`get`; Drive `download`/`list`; Sheets `read`;
    - emailRead `list`/`search`/`read`/`folders`;
    - discordAction's list, get and download operations;
    - whatsappDb's lookups and `newsletter_live_updates`;
    - whatsappBusinessMedia `get_url`/`download`; whatsappBusinessSend `list_templates`;
    - the reads of aws, cloudflare, gcloud, github, vercel and dodo;
    - processManager `list`/`get_output`;
    - proxyConfig `list_providers`/`get_stats`/`list_routing_rules`/`test_provider`;
    - vectorStore `query`; dataSource `list`/`read`/`search`/`metadata`; tikhubAction `list_endpoints`/`account`;
    - the Browser's observations and `request_user`.
- **write**
  - **The employee's own work:**
    - agentBuilder's changes, and `plan_update`, which counts as the change it plans; canvas, employeeJob, fileModify, writeTodos, textToSpeech;
    - simpleMemory `remember`/`update`/`forget`;
    - vectorStore `store`/`delete`;
    - dataSource `write`/`append`/`copy_to_workspace`;
    - githubAction `repo_clone`, gcloudAction `set_project`, processManager `stop`/`restart`;
    - montyExecutor with `workspace_write`.
  - **The owner's phone:** the 9 Android automation nodes.
  - **The Browser's `close`:** node-only; agents cannot call it.
- **send** (the default)
  - **The plain sends:**
    - whatsappSend, telegramSend, discordSend, emailSend, socialSend, chatSend;
    - twitterSend's `tweet`/`reply`/`retweet`/`quote`/`like`/`unlike`.
  - **The send operations of mapped nodes:**
    - Gmail `send`; MS Mail `send`/`reply`;
    - Calendar `create`/`update`; Contacts `create`/`update`; Tasks `create`/`complete`/`update`; Drive `upload`/`share`; Sheets `write`/`append`; emailRead `move`;
    - Discord's changes; whatsappDb's channel changes; WhatsApp Business sends and media `upload`;
    - GitHub PRs and issues;
    - cloud starts, stops, uploads and deploys (aws, gcloud, cloudflare DNS create and edit, vercel `deploy`);
    - Dodo's checkout, customer and portal-link operations;
    - proxyConfig's changes, which are install-wide and take a host the model chooses.
  - **Commands on the computer:** shell; processManager `start`/`send_input`; pythonExecutor, javascriptExecutor, typescriptExecutor (D16).
  - **Tools that drive apps:** mobile_use_agent, android_tool.
  - **The CLI agents:** claude_code_agent, codex_agent, rlm_agent.
  - **mcpConnector.** A binding with `mcp_ask: false` counts as a matching rule (§4.3).
  - **URL fetches, under D11:**
    - httpRequest and proxyRequest GET, POST, PUT and PATCH;
    - crawleeScraper, httpScraper, fileDownloader;
    - tikhubAction `fetch_url` and `call`;
    - montyExecutor with `http_get`;
    - the Browser's `navigate`, `tabs` and site actions (§4.3 says which are held).
- **money**
  - **Money:**
    - stripeAction, every command;
    - Dodo `create_refund`/`cancel_subscription`/`change_subscription_plan`/`call` (any endpoint: the table cannot see which);
    - apifyActor, a paid run.
  - **Deletes:**
    - Calendar, Contacts, Tasks and emailRead `delete`;
    - emailRead `flag`, which can mark a message deleted;
    - twitterSend `delete`; whatsappBusinessMedia `delete`; discordAction `delete_message`;
    - aws `s3_delete`, gcloud `storage_rm`, cloudflare `dns_record_delete`;
    - httpRequest and proxyRequest DELETE.
  - **Passthroughs that can spend or delete:** aws `call`; cloudflare, gcloud, github, vercel and discord `custom`.

`repeats.py` stays separate. It answers "may a broken-off attempt run again", which differs from "may it run unasked" (for example `fileModify` `edit`, or Dodo `cancel_subscription`). One cross-check test is enough: every class-level `read` entry is in `REPEATABLE`, except the triggers (never a tool call) and the agents (a second run repeats every tool call).

### 4.2 Modes (D5)

| Mode | Asks for | Like |
|---|---|---|
| **Before anything outside** (default) | send, money | Claude Code acceptEdits; Codex Auto |
| **Before everything** | write, send, money | Claude Code default (Manual); Codex Read-only |
| **Only money and deletes** | money | Claude Code auto; Codex on-request with the network on |

- **No "never" mode.** A rule lets a money tool run unasked.
- **Where the mode lives** (O3, settled).
  - It is a nullable `mode` column on `workflow_rules`, added through the database's column-add migration. SQLite adds it on its own; PostgreSQL needs the documented manual `ALTER` (`core/database.py:211-225`).
  - NULL follows `ask_first`: on is "outside", off is "money_only".
  - `set_mode` writes `mode` and the matching `ask_first` in one compare-and-swap, so v0 generations keep reading `ask_first`. "Outside" and "everything" write on; "money_only" writes off.
- **No rule row.** A workflow without one, such as one built in the editor, asks nothing, as today.
- **Scope.** The mode applies to v1 generations. v0 keeps Ask first on/off and the 14 specs (§4.10).

### 4.3 The decision order

In a v1 generation, for every caller: agent tool calls, CLI calls and in-process calls. The harness enforces it, never the model, and it fails closed.
1. **Read** runs.
2. **A matching "always allow" rule,** or an mcpConnector binding with `mcp_ask: false`, runs. The call is recorded as `approved_by: auto`.
3. **Otherwise, if the mode asks for the call's class,** it is held.
   - **The Browser:**
     - A Browser call is held only when it navigates: a `url` is set, in `navigate` or in `tabs`.
     - Its other site actions run read-only through `restrict_while_asking`, as today, and hand changes to the owner through `request_user`.
   - **Wait or draft:**
     - The call waits in its turn (§4.6) only when its caller marked it, with `approval_wait: true` in the activity payload.
     - Only AgentWorkflow's v1 tool step marks calls, and only for the agent that answers a chat run (D15).
     - Every other held call is a draft, as today. That covers CLI agents' OpenCompany tool calls (D9), in-process agents, teammates, team jobs, Workspace tasks, manual Runs, and runs that answer no chat run.
4. **Otherwise** it runs, and in a chat it is recorded as `approved_by: auto`, as today.

`tool_calls.check` stays the one place this is decided (`tool_calls.py:178`). `check_in_process` follows the same order in v1. A v0 generation keeps today's check unchanged. The generation flag reaches both (§4.6).

### 4.4 Always-allow rules (D6)

- **Key:** the workflow (the employee), the node type, the operation, and one scope:
  - **A spec'd send** whose spec renders a recipient for the call: the recipient, normalised.
  - **A URL fetch an agent can call:** the host. This covers httpRequest, proxyRequest, crawleeScraper and the Browser's navigation.
    - httpRequest and proxyRequest also store the method as the operation: "Always allow GET requests to {host}". A DELETE is money and never gets a card rule.
    - A crawl stays on its start host (crawlee's default, `same-hostname`).
  - **An operation-mapped node:** the operation, for example "Google Calendar: create events". twitterSend is mapped on `action`.
  - **Otherwise the whole tool,** for example "Always allow Shell".
- **Offered** on the card only when it can name everything the rule covers (Claude Code's rule), and never on a money card. Money rules are switched on in the employee's list (D19).
- **Storage:** a small `approval_allows` table with `id`, `workflow_id`, `node_type`, `operation`, `scope_kind`, `scope_value`, `label`, `created_at` and `created_by`.
  - `tool_calls.check` reads it.
  - It is a new table, so `create_all` makes it on both databases.
- **Applies** in v1 generations only, to waits and drafts alike.
- **Lifetime:** Reset keeps rules, because they are the owner's settings. Deleting the employee deletes them through the approvals `on_workflow_deleted` hook (`services/approvals/handlers.py:285-289`).
- **Revocation:** the Ask-first menu's "Allowed without asking…" (D19) lists the rules, each with Remove, plus a switch for each of the employee's money tools. A removed rule applies from the next call.
- **Not adopted:** deny rules, session-scoped approvals, and rule files.

### 4.5 The card and the decisions

- **Allow** (once). A spec'd message still offers Edit first.
- **Always allow {scope}:** allows this call and saves the rule. A one-line confirmation names the rule.
- **Decline,** with an optional note: "Tell {name} what to do instead".
  - The model reads an error-style result: "The owner declined this; nothing happened."
  - With a note, it adds "Their note: …". Without one, it adds "Don't try to get the same outcome another way."
- **A new chat message from the owner while a call waits** declines that call (D3).
  - It shows on the card as "You replied: …", with no chat bubble (D14).
  - The model reads: "The owner sent a message instead of answering; nothing happened. Their message: …"
  - The employee continues in the same turn, and no second run starts (§4.7).
- **Chat Stop** pauses the employee (D13). The card stays, and a decision taken while paused takes effect after Resume.
- **No Undo after Allow, and no Restore after Decline,** for a call that waits in its turn (D4). Drafts keep both, as today.
- **A tool without a spec** gets a generic card (D7):
  - the title "{name} wants to use {Tool}";
  - up to six argument lines, each cut at 300 characters;
  - secret-named keys removed by `sanitize_runtime_payload`, which also drops `authorization`, `proxyauthorization` and `cookie`;
  - no Edit.
- **A class pill** on every request card: "Makes changes" (write), "Reaches out" (send), "Money or deletes" (money).
- **Keys:** the chat-wide Ctrl/Cmd+Enter never allows a money card, and the note box keeps Ctrl/Cmd+Enter to itself.
- **1b.1's outcome cards** are not requests and never offer Always:
  - "May have sent", for a spec'd send other than the Browser, with Send again after a confirmation;
  - "May have happened", with Dismiss.
- **The summary** the server sends gains four fields:
  - `approval_class`;
  - `always`: its label and scope, or null;
  - `waits`: the call waits in its turn;
  - `generic`.
- **The wording** follows R5 §5:
  - the mode menu "Ask me first: Before anything outside / Before everything / Only for money and deletes";
  - the menu's "Allowed without asking…" and the list's empty state;
  - the money footer "Money and deletes always ask unless you allow them under Allowed without asking."

### 4.6 The wait on Temporal (D1, D2, D15)

**The flag.** `begin_generation` writes `approval_wait_version: 1` into `resource_manifest` (`services/deployment/control.py:160`). The write is AP.7, the last step (§7). The flag follows `pi_harness_version`'s path (C3 §3.4):
- `freeze_generation_harness` copies it into the payload and into `_temporal_routing_v1`. It is called at three sites: `manager.py:949-951`, `manager.py:1117-1119` and `temporal/executor.py:166-167`.
- `_frozen_routing_from_input` keeps it (`workflow.py:175-195`).
- MachinaWorkflow sets it on the agent and node contexts after `workflow.py:762-763`.
- `_INHERITED_SCOPE_KEYS` (`agent_workflow.py:295-325`) carries it into tool payloads, the Task Manager preflight, delegated children and continue-as-new.
- The MCP bridge entry carries it beside `pi_harness_version` (`services/cli_agent/workflow_tools.py:335`), and so does the in-process tool path.
- `serialize_control` exposes it to the client (`control.py:47-71`).

Team jobs, Workspace tasks and manual Runs do not carry it, so their calls stay v0.

**1. Hold.**
- The tool activity holds the call.
- When the payload carries `approval_wait: true`, the row is written with `runtime="turn"`, using the existing column (`models/approvals.py:60-61`). Otherwise it is a draft row, `"temporal"` or `"local"`, as today.
- The activity returns the held marker: `approval_id` and `status: waiting_for_owner` (`tool_calls.py:143-157`).
- The row is the only record.

**2. Wait,** in AgentWorkflow's tool step.
- Tool calls run one after another, in model order (`agent_workflow.py:2035`). Each call is scheduled at `:2363-2369` and its result appended at `:2449-2455`.
- On a held result for a call it marked, the step:
  - ends the tool action (`end_action`);
  - starts the child `ApprovalWorkflow`, id `approval-<approval_id>`, inside `child_start()`, with `ParentClosePolicy.TERMINATE`, `ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED` and `WorkflowIDReusePolicy.ALLOW_DUPLICATE`;
  - awaits it **outside any admitted action**, as delegations already do (`:2261-2262`). Stop's checkpoint then completes (T12).
- The model never sees "Not sent yet".

**3. `ApprovalWorkflow`** (new, `services/temporal/approval_workflow.py`, about 60 lines).
- **It runs with `sandboxed=False`,** like all 14 existing types. The package has import side effects, and a sandboxed type fails worker validation.
- **`run(ref)` loops:**
  - It clears the nudge flag, then reads the row through `approvals.read`, a new 30 s activity on `BOOKKEEPING_RETRY`.
    - That activity applies the row's expiry first (`decisions.expire_if_due`), so nothing spins at the deadline.
    - When it returns a final answer, it stamps `consumed_at`.
  - Once the row is no longer pending, it returns one of `approved{revision}`, `declined{note}`, `expired` or `cancelled`.
  - Otherwise it waits with `workflow.wait_condition(lambda: self._nudged, timeout=max(expires_at - now, 1 s))`, then reads again.
- **Its one message is the Signal `nudge()`,** with no payload. Duplicates are harmless, and nothing needs validating.
- **Every wait has a timeout.** Switching a wait between `timeout=None` and a timeout is a nondeterministic change.
- **A cancel propagates.** On a cancel, the child marks the row `cancelled` through a bookkeeping activity, then re-raises.
- **Stop's tree walk skips it.**
  - `transition_generation` sends `set_control_state` and `wait_for_checkpoint` Updates to every pending child (`services/deployment/execution_control.py:103-152`).
  - The approval child admits no action and has neither handler. Both walks therefore skip children whose type is `ApprovalWorkflow`.

**4. Decide.**
- **The compare-and-swap stays.** `decide_approval` (`services/approvals/handlers.py:162-217`) keeps the database compare-and-swap (`decisions.apply`) and its immediate answer.
- **A turn row gets none of the draft handling:**
  - no Undo grace (`decisions.py:100`);
  - no `ApprovedToolCallWorkflow` (`handlers.py:199-209`);
  - no `[update]` note (`:197-198`);
  - no Restore.

  `recover_sends` (`reconcile.py:83-91`) and 1b.5 leave turn rows to their turn.
- **The nudge is an approval listener** (`services/approvals/__init__.py:37`). Every site that already calls `notify_approval_changed` therefore nudges: decide, branch moves, Clear, the gate's reset, and expiry. It is best-effort; a nudge to a closed workflow is logged and dropped.
- **The backstop:**
  - The chat watchdog round (`services/chat/watchdog.py:116-125`) calls `reconcile`.
  - `reconcile` re-nudges turn rows that were decided more than 10 s ago, are not consumed, and belong to a running generation.

**5. Run** (back in AgentWorkflow).
- **Approved:**
  - Wait until resumed, then `begin_action()`.
  - Run one helper, shared with `ApprovedToolCallWorkflow`: `approvals.claim_send` (with the child's `revision`), then the call's own activity, then `approvals.record_outcome`.
    - AgentWorkflow passes the call's own activity name and `tool_payload`, overlaid with the claim's `node_data`, `tool_args` and `approval_execution {approval_id, claim_token}`. `check` requires that last field (`tool_calls.py:160-165`).
    - It runs with `SEND_ONCE`, and its activity ids derive from the approval id.
    - `ApprovedToolCallWorkflow` passes no ids, so its recorded commands do not change.
    - A refused claim reads "not run".
    - A stopped chat run's empty success is not recorded as sent.
    - `record_outcome` writes no `[update]` note for a turn row, and is shielded from a cancel.
    - A break-off answers "may or may not have happened" from the helper itself, because the single attempt never reaches 1b.1's redo guard.
  - Append the real result to the transcript, and continue the loop.
- **Declined:** an error-style result, worded as in §4.5.
- **Expired:** an error-style result: "The owner did not answer in time; nothing happened."
- **Cancelled:** the turn ends without running the call. AgentWorkflow ends its loop itself.

**6. Interactions.**
- **Stop and Resume:** the wait is not an action, so Stop completes. A decision taken while stopped is consumed after Resume.
- **Reset:** terminates the run and its child. The approvals service cancels the generation's open turn rows from the Reset path itself, because the gate's hook runs only when the graph has a gate (`runtime_state.py:38-55`).
- **Continue-as-new:** AgentWorkflow continues as new only between iterations (`agent_workflow.py:3000-3055`), never during a tool batch. The wait lives in the child.
- **Other firings of the same agent** see the waiting call closed as "did not finish" (`services/agent_context/conversation.py:254-286`). D15 keeps waits to chat runs, which the chat lane serialises. A team review during a chat wait can still be overwritten when the waiting turn saves (O7).
- **Task Manager:** taskManager is `read`, so a preflight never holds. A waiting teammate keeps its subagent permit.
- **Limits:**
  - one child per wait, under the 2,000 pending-children limit;
  - nothing grows with waiting time, and payloads carry ids only;
  - the wait keys by the approval id, because `call_key` is not unique when a provider repeats a tool id (C3 P14).

**7. Replay and the test gates.**
- **The new type:** register `ApprovalWorkflow` in `framework_workflows` (`services/temporal/workflow_registry.py:20-25`). Record it in the golden identity file: `workflow_types`, its identities, and `signal:nudge`.
- **Two test gates change,** in tests and data only:
  - `test_replay_corpus.py:69` requires N-1 histories only for the types the previous release had.
  - `test_workflow_identity_golden.py:26-35` accepts a reviewed entry that names a new type or a generation flag in place of a `workflow.patched` marker.
- **1b.42 runs before AP.4a,** because it rewrites the golden with no patch entries (`tests/temporal/record_fixtures.py:1365-1374`).
- **The shared types branch only on the frozen flag,** never on `workflow.patched` for old generations (RFC-0005 rules 2 and 3).
- **The corpus scenarios:** the release that ships AP.1-AP.6 records five: approve, decline, expire, decide-while-stopped, and cancel while waiting. C3 §3.6 lists what each one needs.

### 4.7 Chat runs (D2, D3, D13, D14)

- **The 24-hour timeout** (`services/chat/ledger.py:763-767`), in a v1 generation:
  - A run is not timed out while a pending turn row carries its `run_id`.
  - The window restarts at the owner's latest decision on the run's rows. That is `decided_at`, ignoring `approved_by == "auto"`, the same way the window restarts at Resume (`:744`, `:763-765`).
  - The closure check stays (`:768-778`).
  - One store helper, `run_wait`, answers both questions.
  - 1b.14's cancel goes inside the same branch, after AP.4c.
- **A closed run:** when the watchdog ends a v1 run whose workflow closed (`:768-778`), it cancels the run's pending rows.
- **Chat Stop** is the employee's pause (D13; `services/chat/handlers.py:471-487`). Nothing in chat changes, and `request_stop` stays legacy-only.
- **A new owner message while a run waits** (D3, D14):
  - It is handled in the `RunInProgress` arm (`handlers.py:318-319`). Three conditions must all hold:
    - the lane run's generation is v1;
    - the message is plain text, with no button press and no attachments;
    - the run has pending turn rows.

    Then `send_chat_message` declines those rows, with the text as the note and the decision key `chat:<client_message_uid>`.
  - It saves no chat row and starts no run.
  - A resend with the same `client_message_id` finds the decision by its key and gets the same answer.
  - Otherwise, or when the decline loses the race to the card, it answers `run_in_progress`, as today.
  - The answer is `{success: true, message_id: null, run_id: null, declined: {run_id, approval_ids}, delivery: "now" | "queued"}`. O5 is settled.
- **The client** (AP.3):
  - The composer sends ("Reply") while a v1 lane run waits on a pending call. Today `busy` covers any lane run (`client/src/features/chat/ChatPane.tsx:166`), and the button shows Stop (`Composer.tsx:191-219`).
  - A card links to its run's turn by `approval.runId`, so it survives a restart. The hub keeps its activities in memory only (`services/chat/reducer.py:59-83`).
  - The client fetches the open rows as well as the newest 100 (`data/approvals.ts:172`).
  - A waiting turn reads "Waiting for your OK", and its step renders from the approval's state; the step's id is the `toolCallId`.

### 4.8 Graph runs (D12)

- Under `approval_wait_version=1`, MachinaWorkflow no longer counts the `approvalGate` activity in the Stop checkpoint (`services/temporal/workflow.py:865-868`; RFC-0006 §9.6 item 3). The node after it runs only after Resume, through its own admission.
- Graph sends without an Approval node stay unheld (D12). A hired employee's app replies go through an Approval node (builder v3).
- RFC-0005 Phase 7 (`approval_wait_version=2`) moves the gate and reviewed delivery onto the same `ApprovalWorkflow`.

### 4.9 CLI and in-process agents (D9)

- **In v1, `check_in_process` holds a draft instead of refusing** (called at `services/handlers/tools.py:237-251`).
  - **The row's node id:** the row carries the bridge entry's `node_id`, so Send can run it.
  - **The row's key is the call id.**
    - The bridge computes `mcp:` plus a digest for every MCP tool call, not only for the three types it covers today (`services/cli_agent/workflow_tools.py:337-353`).
    - A held call without a call id is refused.
    - A retried call is a new request and gets a new row. O4 is settled.
  - **The answer:** "waiting for the owner". The owner's Allow starts `ApprovedToolCallWorkflow`, which runs the node's own activity; the CLI is not involved.
  - **The outcome:** the CLI agent hears it on its next turn. The context bridge (`services/cli_agent/context_bridge.py`) claims the `[update]` notes, which only `agent_activities.py:1762-1771` claims today.
- **v0 generations keep the refusal.**
- **Claude Code's built-in tools keep `dontAsk`.** Answering Claude Code's own permission prompts through cards (stdio `can_use_tool`) is not adopted: it blocks the CLI session, and a restart loses it.
- **Codex is out of this RFC** (D17; §9, O8).

### 4.10 What stays and what retires

| | v0 generations | v1 generations |
|---|---|---|
| What asks | Ask first on/off and the 14 specs | the mode and the class table, minus rules |
| A held call of the agent answering a chat run | a draft; "waiting for the owner"; `ApprovedToolCallWorkflow` after Send (5 s Undo); `[update]` note | waits in the turn; runs after Allow; the real result |
| Any other held call (other runs, teammates, CLI, in-process) | a draft, or refused (CLI, Stripe) | a draft (D9, D15) |
| Retry of a failed send | `ApprovedToolCallWorkflow` | `ApprovedToolCallWorkflow` (unchanged) |
| The gate | an activity that holds Stop | an activity outside the Stop checkpoint (Phase 7: the child) |

### 4.11 Not adopted, and why

- **The settings-file scope stack, Bash command-string rules and compound-command splitting:** OpenCompany's tools are typed, with operations.
- **The auto-mode classifier and Codex's AI reviewer:** an extra model on every call; owners decide.
- **User hooks and OS sandboxes:** OpenCompany is a product; containment is `resolve_within` and netpolicy.
- **Deny rules and session approvals:** Decline covers one call, and a months-long employee has no clear session.
- **Codex's own prompts** (they need `app-server` and a live process); **MCP elicitation** (answer `decline`).

## 5. Amendments

- **RFC-0006 §9.6 and the §22.5 flag table.** `approval_wait_version=1` widens to two things:
  - graph approval gates no longer hold Stop;
  - **and** agent tool calls in runs that answer the owner in chat wait in the turn on `ApprovalWorkflow`, with the modes, classes and rules of this RFC.

  `=2` stays: the gate and reviewed delivery become `ApprovalWorkflow` children (Phase 7).
- **RFC-0005 §17.5.**
  - The `ApprovalWorkflow` arrives before Phase 7, for tool calls, in its nudge-and-re-read form (§4.6).
  - Phase 7 keeps the gate, reviewed delivery and the outbox.
  - For approval decisions, T10's deliverer reduces to the nudge plus the re-nudge backstop. Stop, Resume, Interrupt, Start and Apply keep Phase 4's deliverer.
- **The patch rule.**
  - Old: "a change to which workflow commands run needs a `workflow.patched` marker".
  - New: "it needs a marker, or, for new generations only, a generation flag frozen at Start" (RFC-0005 §24.1 rule 2).
  - The plan and CLAUDE.md:759 take this wording (AP.6).
- **RFC-0006 §24.1 stands** (D18): shell and code tools stay off Hire's allowlist.

## 6. Changes to the Phase 1b plan

- **1b.1b, 1b.1c and 1b.2** come first; a blocking approved run uses them. The check corrected them (C8):
  - the Browser gets "May have happened" (`not spec.restrict_while_asking`);
  - `record_unknown` skips `approval_execution` runs;
  - effect rows use the `e:` key prefix;
  - when a worker shuts down in the middle of a call that is not safe to repeat, the call records `unknown` and answers at once;
  - the Coding Agent's tools record a card;
  - API keys stay injected before template resolution (`node_executor.py:291-292`).
- **1b.5 (sends wait while stopped):** shrinks to v0 rows and Retry, and leaves turn rows to their turn.
- **1b.9, 1b.10:** unchanged.
- **1b.14 (cancel a chat run past 24 h):** after AP.4c, inside the restarted window (§4.7).
- **1b.31:** its sanitizer change (`authorization`, `proxyauthorization`, `cookie`) moves to AP.2a.
- **1b.42:** moves to just before AP.4a (§4.6 item 7).
- **The plan's open Ask-first gaps:** the gap of specless tools closes for v1 (D7). Dev-graph sends without an Approval node stay unheld (D12).

## 7. Implementation plan

One commit per step, each with its tests. After every commit, run the replay corpus and the golden test.

| Step | What | Main files | Replay |
|---|---|---|---|
| AP.1 | The class table (§4.1) and its test: names, values, completeness, the cross-check with `repeats.py`, `class_of` cases | `services/approvals/classes.py`, `tests/services/approvals/test_classes.py` | none |
| AP.2a | Modes and the class-based check, v1 only: the mode column and `set_mode`; `check` is mode × class, with the Browser rule; generic rows for specless tools; Stripe held; the sanitizer keys | `models/approvals.py`, `services/approvals/rules.py`, `tool_calls.py`, `handlers.py`, `services/workflow_sanitizer.py`, `core/database.py` | none (activity code) |
| AP.2b | Rules, turn rows and the decline note: the `approval_allows` table, the note column, `send{always}`, `discard{note}`, the turn rows' decision rules (§4.6 item 4), WS commands to list and remove rules, rules deleted with the employee | `models/approvals.py`, `services/approvals/*`, `core/database.py` | none |
| AP.2c | CLI and in-process drafts (D9): a call id for every MCP call, the node id, notes claimed by the context bridge | `services/approvals/tool_calls.py`, `services/handlers/tools.py`, `services/cli_agent/workflow_tools.py`, `context_bridge.py` | none |
| AP.3 | Client: the mode menu and its "Allowed without asking…" list; Allow / Always allow / Decline with a note; the generic card; the pills; Reply while a call waits; cards linked by run; the waiting turn; the wording | `client/src/features/chat/*` (approval, composer, thread, data), Home | none |
| AP.4a | `ApprovalWorkflow`, `approvals.read`, the nudge listener and the backstop; Stop's tree walk skips the child; the two test gates; the golden | `services/temporal/approval_workflow.py`, `workflow_registry.py`, `worker.py`, `services/approvals/activities.py`, `reconcile.py`, `__init__.py`, `services/chat/watchdog.py`, `services/deployment/execution_control.py`, `tests/temporal/test_replay_corpus.py`, `test_workflow_identity_golden.py`, `fixtures/workflow_identities.json` | new type |
| AP.4b | AgentWorkflow's wait and run (§4.6), the shared claim/run/record helper, the flag's threading (without the write), Reset's cancel | `services/temporal/agent_workflow.py`, `approved_tool_call_workflow.py`, `workflow.py`, `coding_agent_runtime.py`, `executor.py`, `services/deployment/manager.py`, `handlers.py`, `services/approvals/execution.py` | per-generation flag |
| AP.4c | Chat: the watchdog window, the closed-run cleanup, a new message that declines a waiting call | `services/chat/ledger.py`, `handlers.py`, `services/approvals/store.py`, `decisions.py` | none |
| AP.5 | The approvalGate leaves the Stop checkpoint under v1 | `services/temporal/workflow.py` | per-generation flag |
| AP.6 | Docs: `chat_protocol.md` (Approvals), `normal_mode.md` (Ask first becomes the mode), `TEMPORAL_ARCHITECTURE.md`, the CLAUDE.md rows, the patch-rule sentence | docs | none |
| AP.7 | Turn it on: `begin_generation` writes `approval_wait_version: 1` | `services/deployment/control.py` | the flag |

**Order:**
1. 1b.1c, 1b.1b, 1b.2.
2. AP.1 to AP.6, with 1b.42 just before AP.4a, and AP.4c before 1b.14.
3. The rest of Phase 1b batch A.
4. AP.7, after the release that ships AP.1-AP.6. Per-generation flags are read one release before any release writes them (RFC-0005 §24.3), so a rollback still replays.

**The corpus scenarios** (§4.6, item 7) are recorded at the release that ships AP.1-AP.6.

## 8. Acceptance

- In a v1 generation, in a run that answers a chat run:
  - a held send waits, Allow runs it, and the model's next step sees the real result;
  - Decline gives the model the note, or "don't work around it", and nothing runs;
  - expiry runs nothing;
  - a new owner message declines the waiting call, shows on its card, and starts no second run.
- Stop completes while a call waits, and the card stays. A decision taken while stopped runs only after Resume.
- A schedule or webhook run in a v1 generation gets a draft, as today.
- A run that waited more than 24 hours is not timed out after the owner decides.
- An "always allow" rule skips the next matching call, and is listed and removable from the Ask-first menu. No rule is offered on a money card.
- A specless tool asks with a generic card that shows no secret-named key, `Authorization` or `Cookie`.
- Under the default mode, a URL fetch asks, and so does a Python, JavaScript or TypeScript run. A search does not ask.
- A CLI agent's held OpenCompany tool call becomes a draft with its own row, and the agent hears the outcome on its next turn.
- v0 generations behave exactly as today. The replay corpus and the golden pass, with their reviewed entries.

## 9. Open items

- **O1. The Browser's navigation (D11).** Settled by §4.3: navigating asks, with "Always allow this site" rules, and site actions run read-only. It may ask often until rules build up.
- **O2. TikHub `fetch_url`** is `send` under D11. Settled.
- **O3. Mode storage:** a column. Settled (§4.2).
- **O4. The MCP call id:** a retry gets a new row. Settled (§4.9).
- **O5. The reply fields:** settled (§4.7).
- **O6. A hard crash on a call's last or only attempt** leaves no card (1b.1, C8). Accepted.
- **O7. A team review during a chat wait** can be overwritten when the waiting turn saves (§4.6 item 6). Accepted until firings of one conversation are serialised.
- **O8. Codex** (D17), a separate item. It needs:
  - a pipe-based session with stdin closed (DEVNULL);
  - a parser for Codex 0.164.0's JSON events;
  - the pin, through the shared packages tree;
  - the argv `exec --json --ignore-user-config --ignore-rules --model … --sandbox … -- PROMPT`;
  - tests with a fake codex.

  The check's report C1 has the details.

## 10. The check against the code (2026-10-25)

**Method.** Eight read-only checks compared this RFC with the code at b3b7cdf3, one area each:
- Codex;
- the approvals service;
- the Temporal side;
- chat;
- the client;
- the class table, checked against the live registry;
- the documents;
- 1b.1b and 1b.2.

The coordinator re-read the central findings in the code. About 25 corrections went into §2.4 and §4 to §8.

**Four findings would have broken at once:**
- Stop's tree walk would have sent control Updates to the approval child.
- A sandboxed `ApprovalWorkflow` fails worker validation.
- The two test gates have no way to accept a new type or a flag-gated branch.
- Send would have started a second sender for a row its turn claims. Turn rows fix this.

**Two premises were wrong:**
- AP.0 rested on one: Codex never starts, so it moved out (D17).
- §4.7 rested on another: chat Stop is a pause in controlled generations (D13).

**Where the reports are:** the session scratchpad's `approvals-check/` folder.
