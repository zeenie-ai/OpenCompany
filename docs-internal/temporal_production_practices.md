# Temporal production practices

This note compares OpenCompany's Temporal execution with the production practices in Pydantic AI's Temporal guide, and says what to adopt. It comes from a read-only review on 2026-11-11:
- nine reviews of the code areas;
- one cross-check against the RFCs and the release plan;
- one fact sheet on the external mechanisms the guide relies on.

Nothing in the note is built yet. The fixes are queued for v0.3.3.

Sources:
- Pydantic AI, [Durable Execution with Temporal](https://pydantic.dev/docs/ai/integrations/durable_execution/temporal/), and the [Temporal durability section of Code Mode](https://pydantic.dev/docs/ai/harness/code-mode/#temporal-durability).
- temporalio SDK 1.34.0 and the pinned Temporal CLI 1.9.1 (server 1.32.0), read in source.
- pydantic-ai 2.67.0, read only to see how the guide's own library implements a practice.

Read it with [TEMPORAL_ARCHITECTURE.md](TEMPORAL_ARCHITECTURE.md), [temporal-workflow-control.md](temporal-workflow-control.md) and [agent_context_flow.md](agent_context_flow.md). The decisions it builds on are in [RFC-0004](../RFC-0004-TEMPORAL-SDK-FEATURE-ADOPTION.md), [RFC-0005](../RFC-0005-EVENT-OS-ON-TEMPORAL.md), [RFC-0006](../RFC-0006-TEMPORAL-DESIGN-PATTERNS.md) and [RFC-0007](../RFC-0007-APPROVALS-ON-TEMPORAL.md). Where a document and the code differ, the code wins.

## Decisions in this pass

The owner decided these on 2026-11-11:
- **Practices only.** The native LLM layer and the agent loop stay. OpenCompany takes no pydantic-ai dependency and builds no Code Mode feature, and every change below uses Temporal SDK features it already uses.
- **Release.** Every fix in this note goes to v0.3.3. v0.3.2 ships as planned.
- **Python code node.** User code runs in a worker thread with its declared timeout (item 2).
- **Edge conditions.** They are evaluated in an activity, behind a dormant patch marker (item 9).
- **Conversations.** Each firing is relieved before its first step, under `context_pressure_version` 2, for new generations only (item 17).

## Reading the items

Each item gives the problem, who can hit it, the plainest change and its size. Two labels follow:
- **Marker.** Whether the change alters which commands a workflow emits. Such a change needs a `workflow.patched(...)` marker, which ships dormant for one release through `server/services/temporal/patches.py`, or a generation flag frozen at Start. A change to activity, client or configuration code needs neither.
- **Shape.** *Today* is the desktop and single-process install on the built-in dev server with SQLite. *Hosted* is a per-owner instance on the open-source server with its own PostgreSQL. *Both* is both.

Items are grouped by the owner's four problem areas: runs that wedge or hang; retries, rate limits and cost; long-lived agents; live streaming. Anchors marked "(not re-read)" were verified by one reviewer and not re-read for this note.

## What already matches the guide

| Guide practice | OpenCompany |
|---|---|
| Workflow code only coordinates; model calls, tool calls and I/O run in activities | All 15 workflow types. No clock, `uuid4`, randomness or I/O in workflow modules. |
| Stable, unique type names; a changed command sequence needs a patch | The golden identity test locks workflow types, activity names, queues, message handlers and patch markers on every CI run (`server/tests/temporal/test_workflow_identity_golden.py`). New markers ship dormant for one release. |
| Ids derived from the workflow run | Activity and child ids come from workflow ids, iterations and call ids. |
| Models and credentials resolved on the worker | Provider keys are resolved inside the LLM step (`server/services/temporal/agent_activities.py:565`). No key or client object appears in a step's input or output. |
| One retry owner | The agent LLM step sends one HTTP request per Temporal attempt: SDK retries 0, one inner attempt, no transport retries (`agent_activities.py:566-596`). |
| Non-retryable errors classified in the activity; typed failures reach workflow code | `LLMError.<category>` is raised with `non_retryable` and `next_retry_delay` (`agent_activities.py:246-307`), and workflow code reads the category. |
| Streaming inside the model activity, idempotent delivery, no deltas in history | Each attempt streams its own segment, a failed attempt's segment is withdrawn, and the client drops duplicates (`server/services/chat/stream.py`). |
| Continue-as-new at a safe point, carrying usage | The server's suggestion comes first, at a turn boundary with nothing in flight. Usage and the iteration count are carried. The carried input is measured with the workflow's own converter and refused cleanly above 1,900,000 bytes (`server/services/temporal/agent_workflow.py:3212-3219`). |
| An oversized activity result fails at once | `PayloadsTooLarge` is non-retryable (`server/services/temporal/_retry_policies.py:52-58`). |
| Replay-safe tracing | `TracingInterceptor` is registered once on the client, and replay creates no spans. |
| A client reattaches through an ownership check | `chat_subscribe` compares the session's owner with the socket's principal. |

## Decided against, and why the reasons still hold

| Guide practice | Decision | Why it still holds | Reopen when |
|---|---|---|---|
| External Storage on the data converter: the guide's "reliable fix" for oversized inputs | Not adopted (RFC-0004 §12.3) | Public preview through SDK 1.36. SDK 1.34 ships only an S3 driver, and the local-disk driver in Temporal's docs is a development sample. Temporal never deletes stored payloads, while OpenCompany runs never time out. | It is GA, and a driver OpenCompany owns (local disk, or the instance's PostgreSQL) is deleted together with Reset (open question 3) |
| A claim-check payload codec | Not adopted (RFC-0006) | A claim must outlive every history that refers to it. | Same as External Storage |
| Transcripts passed by reference | T6: transcripts stay in history | They are bounded instead, by the byte budget in `server/services/temporal/agent_context_pressure.py`. Items 3 and 17 close the two paths that are still unbounded. | Not planned |
| Workflow Streams for live events | Not adopted (RFC-0004 §15) | Experimental since SDK 1.27. Every publish batch and subscriber poll lands in history, and a run takes at most 2,000 Updates. | It leaves experimental status |
| The Pydantic data converter | Plain dicts (RFC-0006) | Every payload is a JSON-native dict, so the converter adds nothing. | Not planned |
| Worker Versioning | Not adopted (RFC-0004) | One process is upgraded in place, so old builds cannot keep polling. | A hosted shape with several worker processes |
| `workflow_failure_exception_types` | Not configured. Bugs stay "stuck and visible" (RFC-0006; Q5 open). | The option is marked experimental. A broad entry would fail every open run on an incompatible upgrade. A narrow list would not: the SDK fails a run on non-determinism only when a listed type is a superclass of `NondeterminismError`. | Q5 is closed |
| Sandboxed workflows and a determinism lint | All 15 types run with `sandboxed=False`; the lint (1b.40) was dropped | Importing `services.temporal` has side effects. The recorded reason for dropping the lint (the replay gate covers it) is weaker than stated; see rule 1. | New workflow types |
| A timeout on child workflows | None (the months-long contract in `CLAUDE.md`) | Timeout timers keep running through a cooperative pause. | Not planned |
| SDK metrics export | Phase 8 | No exporter has been chosen. | An exporter is chosen |

## Recommended changes (v0.3.3)

### Runs that wedge or hang

**1. Events run twice after a rename, an Apply and a restart.**
*Shape: Both. Marker: none.*
- **How it happens.** Start records the slug that names a generation's listeners and Schedules (`server/services/deployment/control.py:165`). Apply rebuilds the control row's snapshot from the saved workflow (`server/services/employees/safe_apply.py:122`) and writes it over the old one (`:294`), so `workflow_slug` is lost.
- **What breaks.** At the next restart the re-arm names listeners by the current slug (`server/services/deployment/manager.py:228`, `:719-734`), while the controller keeps its Start-time ids.
  - Each trigger then has two specs, and each event starts two runs, so replies and sends can go out twice.
  - A mail trigger runs two poll loops under one activity id, which can wedge the controller (not re-read).
- **Who.** An owner who renames an employee, then applies a change or turns on Talk. The desktop app restarts on every launch.
- **Change.** Apply copies `workflow_slug` from the previous snapshot, one line. A renamed trigger label needs one of two further changes (not re-read):
  - the re-arm reuses the controller's listener id per node;
  - or the node-id-based ids of RFC-0004 M2.

**2. The Python code node freezes the backend.**
*Shape: Both, Dev mode only (the node is not on the hire allowlist). Marker: none.*
- **How it happens.** `exec()` runs user and model code directly on the backend's event loop (`server/nodes/code/python_executor/__init__.py:101`). The node's own `timeout` (default 30 s, `server/nodes/code/_base.py:19`) is never read.
- **What breaks.** While a script runs, every request, broadcast and activity heartbeat stops. A script that runs past the 2-minute heartbeat timeout fails every in-flight activity, and is itself retried up to three times.
- **Change (decided).** Run the code with `asyncio.to_thread` under `asyncio.wait_for` and the declared timeout, about 6 lines.
  - Trade-off: a runaway loop keeps one thread busy until the process restarts, but the app stays responsive.
  - The Monty executor in `server/nodes/code/monty_executor/` remains the option with hard time and memory limits.
- **Guide rule.** An activity that can block the event loop must not depend on heartbeats.

**3. An oversized input fails the workflow task forever.**
*Shape: Both. Marker: none, if the threshold is the server's limit itself.*
- **The failure mode.** This is the guide's warning, word for word. An activity's input is encoded in workflow code, so an oversized one fails the workflow task, not the activity, and no retry policy bounds it. The run stays Running. Home shows only the `stuck_runs` line, after five failed tasks.
- **Agent step.** Nothing measures the input before the LLM step is scheduled. When an agent has no prompt, the whole upstream output becomes the prompt (`server/services/temporal/agent_activities.py:1727-1734`), and each message's text rides twice in the step input. A scraped page or a parsed document of about 1 MB is enough.
- **Graph node.** A node's input carries the trigger output four times: once in `inputs` and once in the trigger node inside `nodes` (`server/services/temporal/workflow.py:733`, `:739`). Each of those holds the data twice, beside its envelope (`server/services/temporal/trigger_listener_workflow.py:398-401`). A polled item near 0.5 MB therefore wedges its run, although the poll cap is 512,000 bytes.
- **Change.** Before scheduling, measure the input with `workflow.payload_converter()`, as the continuation guard does (`agent_workflow.py:3212-3213`). Over the server's limit, end the step or node with the existing clean failure, naming the largest message.
  - With the threshold at the limit itself, every recorded history stays under it, so replay never takes the new branch.
  - v0.3.2 item 3, which cuts an oversized polled item, must measure the item as the converter writes it (escaped JSON, rule 4) and leave room for the four copies.

**4. A failed controller looks running.**
*Shape: Both. Marker: none.*
- **How it happens.** The status query passes no `reject_condition` (`server/services/deployment/handlers.py:757`), so a controller that failed, for example on `ControllerContinuationTooLarge`, still answers it.
- **What breaks.** Dispatch targets running controllers only, so that employee's events are dropped while webhooks answer 200. `stuck_runs` counts running workflows only, so the failure is invisible until the next Stop or Resume.
- **Change.** Query with `reject_condition=QueryRejectCondition.NOT_OPEN`, and treat a rejection as a missing controller, so the existing missing-controller policy applies. About 4 lines.

**5. Coding Agent turns fail permanently on a restart.**
*Shape: Both, on Linux and macOS. Marker: none.*
- **How it happens.** On SIGTERM or SIGINT the Pi host interrupts its turn and writes an `aborted` frame (`server/pi/src/index.ts:67-72`, `:108-109`). The driver has no case for that frame, so it raises `PiProtocolConflict` (`server/services/temporal/pi_activities.py:317-318`), which is non-retryable (`server/services/temporal/pi_workflow.py:193`).
- **Who.** A systemd restart of a deployed VM signals every process in the unit at once.
- **Change.** Map `aborted` to the retryable `PiTransportFailure`. Two lines.

**6. A Coding Agent host that cannot start retries forever.**
*Shape: Both. Marker: none.*
- **How it happens.** A host that exits before its first frame raises the retryable `PiTransportFailure` (`pi_activities.py:239-240`). Its attempts are unlimited, and `coding.pi_drive_turn` is exempt from the stuck-retry alert (`server/services/temporal/_interceptors.py:50`).
- **Change.** Raise the existing non-retryable `PiRuntimeMissing` (`pi_activities.py:203`) when the host exits before any frame.
- **Trade-off.** A host that crashes at launch for a passing reason then fails the run instead of retrying.

**7. Stuck runs are invisible on a server the backend does not own.**
*Shape: Hosted. Marker: none.*
- **How it happens.** The `stuck_runs` count runs only inside the dev-server watchdog (`server/services/temporal/lifecycle.py:133-135`, `:289-291`).
- **Change.** When the server is not owned, run the existing `_check_stuck_runs` in its own loop, about 8 lines.
- **Also needed.** The hosted server's dynamic config needs `system.numConsecutiveWorkflowTaskProblemsToTriggerSearchAttribute: 5`. Today only the dev server gets it (`server/config/temporal_cli.json`).

**8. A pool queue can stop being polled silently.**
*Shape: Both. Marker: none.*
- **How it happens.** After a crash, `TemporalWorkerPool._run_queue_worker` rebuilds its worker outside any `try` (`server/services/temporal/worker.py`, the `_build_queue_worker` call). An exception there ends the queue's only supervisor, and `stop()` swallows it.
- **What breaks.** Activities routed to that queue then wait, with nothing on Home.
- **Change.** Guard the rebuild as `TemporalWorkerManager._run_worker` guards its own.

**9. Edge-condition regexes run in workflow code.**
*Shape: Both. Marker: a dormant patch.*
- **How it happens.** The "Matches Regex" operator runs the owner's pattern with `re.search` inside workflow code (`server/services/execution/conditions.py:222`). The text it searches can come from outside, in a webhook body or an email.
- **What breaks.** A backtracking pattern holds the process past the SDK's 2-second deadlock check, and stalls it again after every restart.
- **Change (decided).** Evaluate conditions in a short activity whose result Temporal records, behind a new dormant patch marker. One extra activity per conditional edge.

**10. The Timer node sleeps inside its activity.**
*Shape: Both. The Timer is on the hire allowlist. Marker: depends on the design.*
- **How it happens.** `server/nodes/scheduler/timer/__init__.py` sleeps inside the activity (not re-read).
- **What breaks.**
  - A wait longer than the 24-hour activity limit never fires.
  - A restart starts the wait again from zero.
  - Stop waits for the whole sleep.
- **Status.** Already on the plan's Later menu. It is listed here because Home hires can reach it.

### Retries, rate limits and cost

**11. A provider's Retry-After is never capped.**
*Shape: Both. Marker: none.*
- **How it happens.** The hint becomes the next attempt's delay unchanged (`server/services/temporal/agent_activities.py:265-289`), and so does the in-process loop's (`server/services/agent_runtime.py:163-173`, not re-read). The parser accepts any delay up to ten thousand years (`server/services/llm/protocol.py:815-827`).
- **Why OpenCompany must cap it.** The Temporal server does not cap an activity's `next_retry_delay` at the policy's `maximum_interval`. The guide's cap is pydantic-ai's own code.
- **What breaks.** A controlled Stop waits for the step for as long as the provider asked.
- **Change.** `min(hint, LLM_STEP_RETRY.maximum_interval)`, which is 5 minutes, keeping the raw hint in the error details. The owner decided this cap in RFC-0004 §13.3.

**12. A mid-stream provider overload fails the run.**
*Shape: Both. Marker: none.*
- **How it happens.** Anthropic always streams (`server/services/llm/providers/anthropic.py:138-191`). Its `overloaded_error` and `api_error` events arrive after HTTP 200, and the SDK raises them with that 200 response. The classifier has no rule for status 200 and returns `UNKNOWN` (`server/services/llm/protocol.py:965-1035`), which is never retried (`:555-561`). OpenAI chat-completions stream errors end the same way.
- **Who.** Any employee on Anthropic during a capacity incident.
- **Change.** In `_classify_error`, map the error types `overloaded_error`, `api_error` and `server_error` to `SERVER`. About 3 lines and two tests; the same idea as `_RESPONSE_ERROR_STATUS` for OpenAI Responses (`server/services/llm/providers/openai.py:33-34`).

**13. Deployed employees' token usage is not recorded.**
*Shape: Both. Marker: none. Already queued as v0.3.3 item 6.*
- **How it happens.** LLM steps on Temporal write no usage row. Only the summarizer's call is saved (`server/services/compaction.py:217`).
- **Change.** Key each row by the Temporal workflow id, run id and activity id, in the existing `execution_id` column, and replace the row on retry. A retried attempt, or a gateway cache replay at no cost, is then not counted twice.

**14. Errors that need the owner lose their message.**
*Shape: Both. Marker: none.*
- **How it happens** (not re-read):
  - The parent keeps details only for `LLMError` causes (`server/services/temporal/workflow.py:1109-1126`).
  - The agent's tool arm shows the model the outer error text (`agent_workflow.py:2815`).
  - `ConversationTooLarge`'s "Clear the conversation" advice and an oversized tool result therefore read as generic failures, and the model may repeat a call at cost.
  - An oversized step result is reported as "Retry the run or check server logs" (`agent_workflow.py:1440-1444`), which invites paying for the same answer again.
- **Change.** Keep the innermost `ApplicationError`'s message and type in both places, and name `PayloadsTooLarge` in the step's failure text. About 10 lines.

**15. A permanent error classified as retryable retries without bound.**
*Shape: Both. Marker: none. No change proposed yet.*
- **Two cases.** A daily cap answered as a plain 429, and a request that always outlives its in-attempt deadline. The step's policy has no attempt limit.
- **With item 11.** The wait becomes 5 minutes per attempt, and a refused request costs nothing. The owner sees a stuck-retry line on Home after attempt 8.
- **Status.** The step budget with parking (T18) is the recorded remedy, and it has no release. Open question 1.

**16. A cancel reaches an in-flight request up to 48 seconds late.**
*Shape: Both. Marker: none. Optional.*
- **How it happens.** A cancel is delivered on a heartbeat response. The SDK throttles heartbeats to `min(0.8 × heartbeat_timeout, 60 s)`, which is 48 s for the LLM step.
- **Who.** A Reset and the chat watchdog's cancel wait that long while the request keeps streaming. Chat Stop is not affected: it checks the run every second.
- **Change.** One Worker option, `max_heartbeat_throttle_interval`, for example 10 s, at the cost of more heartbeat calls.

### Long-lived agents

**17. Conversations whose firings use no tools never compact.**
*Shape: Both. Marker: `context_pressure_version` 2, recorded by `agent.prepare_payload`.*
- **How it happens.** A final answer leaves the loop (`server/services/temporal/agent_workflow.py:1568-1570`) before the relief and compaction block (`:2884`). A chat employee that answers without tools therefore never clears or summarizes its stored conversation.
- **What breaks.** Every firing fails once the conversation outgrows the model's context or the 1 MB load cap, until the owner clears the Context.
- **Change (decided).** Run the existing relief once before each firing's first step, with the new user message as the latest turn. It uses the same clearing, summarizer view and `agent.compact_context`. About 30 lines, a version constant, a test and a recorded scenario.
- **Who gets it.** New generations only. Current employees need Reset and Start.

**18. Reasoning text accumulates without bound.**
*Shape: Both. Marker: none.*
- **How it happens.** Every step's reasoning is appended to `thinking_accumulated` (`agent_workflow.py:1493-1505`, not re-read). It rides the continuation and the result and `store_output` inputs.
- **What breaks.** A long run on a model that returns full reasoning text fails as `AgentContinuationTooLarge`, with an unrelated hint.
- **Change.** Keep the last part after each append, for example the last 64,000 characters. Two lines.

**19. Non-English text is stored 2 to 3 times larger than its UTF-8.**
*Shape: Both. Marker: none for the change proposed.*
- **How it happens.** The default converter calls `json.dumps` with `ensure_ascii` left on (`temporalio/converter/_payload_converter.py:793-795`). Every non-ASCII character becomes a 6-byte `\uXXXX` escape.
- **Effects.**
  - Byte budgets hold 2 to 3 times fewer characters of Hindi, Chinese or Russian context.
  - The node-result guard measures UTF-8 with orjson (`server/services/plugin/base.py:1139`), so its readable error fires late or not at all for such text.
- **Change.** Measure node results with `json.dumps`, as the converter does. One line.
- **Open question 4.** Writing raw UTF-8 instead would halve those payloads. The byte budgets would have to change with it, under a new `context_pressure_version`.

**20. A delegated lead gives up its own permit after about three hours.**
*Shape: Both, in teams. Marker: none.*
- **How it happens.** A lead that yielded its permit reacquires it under the 3-attempt `AGENT_ACTIVITY_RETRY` (`agent_workflow.py:2861-2873`, not re-read). Every other permit wait uses the unlimited `PERMIT_WAIT_RETRY`.
- **Change.** Use `PERMIT_WAIT_RETRY` there. One line.

### Live streaming and reconnect

Streaming matches the guide. Text is streamed from inside the model activity and is never recorded in history. Each attempt has its own segment, and a failed attempt's segment is withdrawn. A refreshed client rebuilds the live run from the hub's snapshot. The one gap in this area is how late a cancel reaches a request (item 16).

### Approvals

Applies to both shapes; none of these needs a marker.

| # | Problem | Change |
|---|---|---|
| 21 | The hold key is `workflow_id:execution_id:tool_call_id` (`server/services/approvals/tool_calls.py:138-139`), and every firing of a generation shares the `execution_id`. A provider that sends empty or repeated call ids makes a later firing reuse an earlier firing's draft; the fallback id is a position (`agent_workflow.py:2357-2359`). OpenAI, Anthropic and Gemini ids are unique. | Add the calling agent's Temporal workflow id to the key. One line. |
| 22 | An approved draft runs under a 10-minute limit whatever its node declares (`server/services/temporal/approved_tool_call_workflow.py`, not re-read). Some tools declare up to 3 hours. | Use the node's declared timeout. Needed before AP.7 widens what is held. |
| 23 | In local mode at most 50 drafts can wait at once, because of the `triggers-event` queue's slot count in `server/services/temporal/worker.py` (not re-read). Later gates get no card. | Raise the slot count (configuration). |

### The safety net

Test code only; none of these needs a marker.

| # | Gap | Change |
|---|---|---|
| 24 | The recorder's fake `agent.prepare_payload` omits `context_pressure_version` (`server/tests/temporal/record_fixtures.py:182-202`), which production records (`agent_activities.py:1873`). Every recorded agent history therefore replays the legacy branch (`agent_workflow.py:1251`), and none schedules compaction or a tool refresh. | At the next release cut, give the fake payload production's version keys, and add compaction, tool-refresh and `ApprovalWorkflow` scenarios. |
| 25 | The golden inventories activities, children and patches, but not timers, `continue_as_new`, search-attribute upserts or calls with keyword arguments only (`server/tests/temporal/workflow_identities.py`, not re-read). | Add those command kinds, then regenerate the golden from the release commit. |
| 26 | "Stuck and visible" has never been observed on a real stuck run. | One test on the pinned server that lowers `limit.blobSize.error` through `dev_server_extra_args` and checks that the `stuck_runs` count rises. |
| 27 | A plugin's retry policy is validated only when workflow code schedules it. | Build each declared policy in the existing plugin contract test. |

### For the hosted plan

These matter once a per-owner instance runs the open-source server on PostgreSQL (not re-read):
- **Dev-server ownership.** It is inferred from a loopback address (`server/services/temporal/lifecycle.py:57-61`). The planned tier L server binds to localhost, so the backend would treat it as a dev server of its own. An explicit ownership setting is about 4 lines.
- **The second worker process.** The standalone worker requires `DISTRIBUTED_MODE`, which refuses a loopback Temporal address. It also runs no per-queue pools, so it cannot be T8's second process as planned.
- **Worker identities.** They are the same in every process (`opencompany-<queue>-<mode>`), so the Temporal UI cannot tell processes apart.
- **Tool parameter rows.** Raw rows can enter history through `agent.refresh_tools`. Running the existing `sanitize_runtime_payload` over them keeps secret-shaped keys out of a history that lives in PostgreSQL.

## Rules worth keeping

1. **Replay validation does not prove determinism.** On replay Temporal compares only a command's kind, type and id (RFC-0006 rule 6). Changed arguments, and branches no recorded history takes, pass the replay gate. The guide says the same: replay validation is not a substitute for keeping workflow-side code deterministic.
2. **Open runs can hold old payload keys.** Never rename or remove an activity input or result key that an open run may hold, and read a new key with `.get()`. An activity retried after an upgrade receives the input recorded before it.
3. **Measure inputs that external data can make large.** An input over the payload limit fails the workflow task, not the activity, and no retry policy bounds it. Wherever external data can make an input large, measure it in workflow code with the workflow's own payload converter before scheduling, and end the run cleanly over the limit.
4. **Measure sizes as the converter writes them.** That means escaped JSON, where a non-ASCII character costs 6 bytes.
5. **Code that can block the event loop never runs on it.** On the shared loop one blocked activity stops every activity's heartbeats.
6. **Cap provider hints in OpenCompany's own code.** The server does not cap `next_retry_delay`.
7. **The suggest-continue-as-new thresholds are configuration, not patches.** They are the per-namespace `limit.historyCount.suggestContinueAsNew` and `limit.historySize.suggestContinueAsNew`, and the suggestion is recorded with each workflow task. On the dev server they go in `server/config/temporal_cli.json`; on the hosted server, in its dynamic config file.

## Open questions for the owner

1. **Daily caps.** A daily cap answered as a plain 429 (OpenRouter free models, Groq daily tokens) can either keep retrying every 5 minutes after item 11, or fail with "resume after the quota resets", as Gemini's daily quota does. Which?
2. **Stop during a retry wait.** May a controlled Stop end an LLM step that is only waiting between retry attempts? This is T18's parking, which has no release.
3. **External Storage on the hosted shape.** Does the reopen condition (GA plus a fitting driver) apply there too? May OpenCompany own a small driver, on local disk or the instance's PostgreSQL, that Reset deletes?
4. **The converter.** Keep escaping non-ASCII text, or write raw UTF-8 on the hosted plan, with new byte budgets?
5. **What AP.7 holds.** After AP.7, should Android, Mobile Agent, Claude Code, Codex and RLM calls in non-chat runs be held as drafts? If so, item 22 lands first.
6. **The recorder.** Should its fake payload follow production's version keys (item 24)? That changes what "the corpus passes" means for agent code.
7. **Q5 and Q13.** Close them as decisions, keeping the working defaults "bugs stay stuck and visible" and "no payload codec"?
8. **Decisions with no release.** These lost their release when Phases 2 to 10 became a menu:
   - Q4: failed runs end as Failed workflows.
   - Q6: a total budget per LLM step.
   - T14: replay open runs at upgrade.
   - M2: node-id-based listener ids.
   - The boot registration check.

   Keep them as undated decisions, or drop them?
