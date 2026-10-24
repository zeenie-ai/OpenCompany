# RFC-0004 — Temporal: Using the Python SDK and Platform Correctly and Fully

Status: Draft. Owner decisions recorded in §17 (2026-10-16 and 2026-10-23); Q15 was answered on 2026-10-24 by RFC-0006's T14; Q5, Q9, Q11 and Q13 are still open. The event path is designed in RFC-0005. RFC-0006 corrected this RFC on 2026-10-24 (§22).
Created: 2026-10-16
Scope: `server/services/temporal/`, `server/services/deployment/` (control, recovery, Reset), `server/services/events/dispatch.py`, `server/services/plugin/{base,scaling,polling}.py`, `server/nodes/scheduler/cron_scheduler/`, `server/nodes/workflow/approval_gate/`, `server/routers/webhook.py`, the Temporal CLI packaging (`server/services/temporal/_install.py`, `docker/Dockerfile`, the desktop shell), `server/tests/temporal/`, `.env.template` (`TEMPORAL_*`)
Relationship: builds on [TEMPORAL_ARCHITECTURE.md](./docs-internal/TEMPORAL_ARCHITECTURE.md) and [temporal-workflow-control.md](./docs-internal/temporal-workflow-control.md), which stay normative until a phase here lands. Corrects one CLAUDE.md durability rule (§8.5). Does not change RFC-0002.
Baseline: temporalio 1.30.0 (`server/uv.lock`), Temporal CLI unpinned ("latest" at first boot), repository state of 2026-10-16. Line numbers are as of that date and drift; the few that RFC-0006 updated (§22) are as of commit 4f73cb10 (2026-10-24).

## How to read this

- **Owner:** §1, §3 (decisions) and §17 (questions only you can answer).
- **Reviewer:** §4 (what we use and what we don't) and §5 (defects). Each defect gives the evidence, the Temporal rule it breaks, the fix, and whether the fix changes workflow commands (and so needs a patch gate).
- **Implementer:** §6 to §15 by area, §16 (phases) and §18 (acceptance gates).

Every defect in §5 was found by one audit pass and re-checked by an independent verification pass that tried to refute it against the code, the installed SDK source and the docs. Corrections from verification are folded in. Statements that rest on the Temporal server or CLI source rather than the docs say so.

Text corrected by RFC-0006 on 2026-10-24 (§22) cites RFC-0006 sections, RFC-0006's owner decisions T1-T20 (RFC-0006 §3.1), and items of RFC-0005 Phase 1b by id, such as 1b.9 (listed in RFC-0006 §22).

## 1. Summary

OpenCompany uses Temporal more deeply than most applications: a DAG orchestrator, an LLM agent loop, a per-deployment controller entity that continues-as-new, Updates with validators, a cooperative pause state machine, typed search attributes, Schedules, one worker per task queue, and patch-gated changes with replay tests. Much of this follows Temporal's documented patterns (§4.1).

The audit found three groups of problems.

1. **The platform under it is the development server.** Every install runs `temporal server start-dev` on a SQLite file, downloaded as "latest" on first boot with no version pin or checksum. Temporal documents both as development-only; a SQLite database created by one server version is never migrated by a later one; closed workflows are deleted after 24 hours; and no SDK metrics are exported, so none of Temporal's documented alerts (stuck workflow tasks, nondeterminism, oversized messages) can exist. (§5 H4, H5, §6, §7)
2. **Temporal's semantics expose real defects.** Every backend restart fires each deployment's Start node again. A large webhook can wedge a deployment's controller. Inbound events are routed through eventually consistent Visibility and silently dropped. Node activities swallow cancellation and are recorded as completed failures. The LLM step and the legacy tool path retry without limit. Cron's overlap policy never applies to the graph run. Activity type names are computed from live plugin code at replay time, three patch markers were deleted without the deprecation step, and CI never replays histories recorded by a previous release. (§5)
3. **Features that fix current pain are unused:** addressing Schedules and controllers by their known ids, `schedule_to_close` and `schedule_to_start` timeouts, the BENIGN error category, user metadata in the Web UI, `TemporalReportedProblems`, task queue priority, queue-wide rate limits, time-skipping tests, a replay corpus, and (in preview) External Storage. (§4.2)

The RFC makes 18 decisions in five phases. Phase 0 fixes defects without changing workflow commands; Phase 1 builds the safety net (pinned server, SDK 1.34, metrics, replay gate); Phase 2 makes the command-changing fixes behind patch gates; Phase 3 adopts features; Phase 4 settles the production platform. Some choices are the owner's (§17), chiefly the upgrade contract (must runs that are open or paused survive an upgrade?) and which server durable installs run on.

## 2. Method and sources

- **Docs.** 282 pages of docs.temporal.io, downloaded as markdown on 2026-10-16 and read in full: the whole Python SDK guide (57 pages), the concept pages (83), cross-SDK worker performance and priority/fairness (12), best practices (9), design patterns (46), self-hosting and worker deployments (24), troubleshooting, references and guides (23), the CLI reference (9), and the AI cookbook and Python integrations (29).
- **Releases.** temporalio release notes 1.25.0 to 1.34.0; Temporal CLI v1.3 to v1.9.2 and server 1.27 to 1.32; the installed 1.30.0 SDK source and the 1.34.0 sdist; the CLI and server repositories at exact tags for dev-server behavior the docs don't cover.
- **Code.** Seven read-only audits of `server/` (core workflows, agent workflows, activities, workers and runtime, triggers and dispatch, client-side control, tests and docs and patches), then five verification passes.
- **Not done.** Nothing was run against a live server. The effect of a newer dev server opening an older SQLite file is derived from source, not reproduced.

## 3. Decisions

**[Proposed]** is the author's recommendation, open to review. **[Owner]** needs the owner's call; §17 lists the options and a recommendation.

| # | Decision | Status |
|---|---|---|
| D1 | Pin the Temporal CLI per OpenCompany release (exact version and per-platform SHA-256, versioned path); record the server version beside each `temporal.db`; delete a database created by a different server minor and start empty (owner, 2026-10-23; RFC-0005 E16) | Done (RFC-0005 Phase 1) |
| D2 | How an install moves an existing `temporal.db` to a newer server | Owner: it does not; the database is deleted (Q3) |
| D3 | Which server durable installs run on: pinned dev server with SQLite, PostgreSQL, or Temporal Cloud | Owner |
| D4 | Set namespace retention and the needed dynamic config explicitly instead of relying on dev-server defaults | Proposed |
| D5 | Upgrade temporalio 1.30.0 to 1.34.0, behind the replay gate of D10 | Proposed |
| D6 | Export SDK metrics, turn on `TemporalReportedProblems`, alert on Temporal's documented set | Proposed |
| D7 | Bound inbound events at the edge; pass graphs, parameter snapshots and large events by reference | Proposed |
| D8 | Route events and manage Schedules by their known ids from the app database; Visibility only for operations | Proposed (the delivery guarantee is Owner) |
| D9 | Make cancellation real: re-raise it in activities, stop swallowing it in workflow loops, cancel before terminating | Proposed |
| D10 | Upgrade contract and versioning: replay corpus gate, startup verification, patch lifecycle, dispatch frozen into run input | Owner (the contract); Proposed (the mechanics) |
| D11 | An explicit retry policy on every activity, and a time budget on every paid or side-effecting one (AG9) | Proposed (the LLM budget values are Owner) |
| D12 | What a failed run and a bug in workflow code look like in Temporal | Owner |
| D13 | Human waits (approvals, reviewed delivery) move from polling activities to workflow messages and timers | Owner |
| D14 | Label work in the Web UI; classify expected failures as BENIGN | Proposed |
| D15 | Worker topology: protect the API event loop, make the standalone worker register what the embedded one does, queue-wide rate limits, priority, a registration check at boot | Proposed (which topologies are supported is Owner) |
| D16 | Testing: replay corpus, time skipping, activity environment, determinism lint, golden identity test | Proposed |
| D17 | Features evaluated and not adopted, each with the condition that would reopen it | Proposed |
| D18 | Fix the security defects found on the way (webhook authentication, event scoping, secrets in history) ahead of and outside the phases | Proposed |

## 4. What OpenCompany uses today

### 4.1 Used, and used the documented way

| Feature | Where | Note |
|---|---|---|
| Updates with validators for control | `services/temporal/workflow.py:249-263`, `workflow_control_workflow.py:106-200` | Revisioned state; stale requests rejected before they reach history; deterministic Update ids `control:{rev}:...` (`services/deployment/execution_control.py`) |
| `@workflow.init` where handlers read input | MachinaWorkflow, controller, EmployeeJob, WorkspaceTaskController | Handlers can run before `run()`; the docs require this |
| Continue-as-new only from the main loop, after `all_handlers_finished` | `workflow_control_workflow.py:605-716` | Matches "don't call Continue-as-New from the handlers" |
| Deterministic "first completed" wait | `workflow.py:1101-1133` | Walks an insertion-ordered dict; never `asyncio.wait`, whose set results the SDK calls non-deterministic |
| Typed search attributes | `executor.py:186-193`, `workflow_control_workflow.py:742-749` | The dict API is deprecated; not used |
| Schedules, not Cron Jobs | `services/temporal/schedules.py` | "We recommend using Schedules instead of Cron Jobs" |
| No workflow execution or run timeouts | `workflow.py:148-155` | "We generally do not recommend setting Workflow Timeouts" |
| Deterministic ids, ABANDON and `ALLOW_DUPLICATE_FAILED_ONLY` for trigger runs | `trigger_listener_workflow.py:412-445` | The event id is the idempotency key |
| Update-with-Start with `USE_EXISTING` | `services/node_invocations.py:47-58` | Workspace tasks |
| At-most-once for approved sends | `approved_tool_call_workflow.py:30-80`, `services/approvals/tool_calls.py:367-379` | `maximum_attempts=1` plus a claim token |
| Durable timer for the 5 s Undo window | `approved_tool_call_workflow.py:41-46` | `workflow.now()` arithmetic, tested with time skipping |
| LLM errors classified at the activity boundary | `agent_activities.py:246-303` | Category decides `non_retryable`; Retry-After becomes `next_retry_delay`; provider SDK retries off |
| Token streaming outside history | `services/chat/stream.py`, `services/chat/hub.py` | Per-attempt segments; the shape Temporal's AI demo uses |
| Interceptors | `client.py:92-97` | `TracingInterceptor` registered once on the client; workers inherit it |
| Worker rebuilt on every crash restart | `services/temporal/lifecycle.py`, `worker.py` | A Worker object is single-use |
| Poller autoscaling; resource-based tuner for I/O-bound LLM work | `worker.py:538-571` | Needs server 1.28 or later (see H4) |
| Replay tests | 18 files in `server/tests/temporal/` | Including synthetic pre-patch histories and `max_cached_workflows=0` |
| Media by reference, byte budgets | `services/media/`, `agent_context_pressure.py`, `plugin/base.py:1015-1038` | Keeps results under the 2 MB payload limit |

### 4.2 Not used, and relevant

| Feature | What it gives | Availability | Where it fits | Phase |
|---|---|---|---|---|
| `schedule_to_close_timeout` | A total time budget across retries | GA | LLM step, legacy tools, polls | 0 |
| `schedule_to_start_timeout` | Detects that a routed worker is gone | GA | Browser owner queues, Pi driver queue | 0 |
| Handles by known id | Describe, signal, pause or delete without Visibility | GA | `dispatch.emit`, Schedule management | 0-1 |
| SDK metrics (Prometheus, OpenTelemetry or `MetricBuffer`) | Alerts and tuning | GA | `Runtime` | 1 |
| `TemporalReportedProblems` | Marks executions stuck in workflow-task failures | Server 1.30.1+, dynamic config | Boot reconcile, health view | 1 |
| Time skipping, `ActivityEnvironment`, replay corpus | The documented test toolkit | GA | `server/tests/temporal/` | 1 |
| `activity.cancellation_details()`, `activity.is_worker_shutdown()` | Tell pause, reset and shutdown apart | SDK 1.12 / 1.18 | The per-type activity wrapper | 0-2 |
| `failure_exception_types` | Choose between "fail" and "stuck until fixed" for bugs | Experimental | Every workflow | 2 |
| `workflow.info().get_current_history_size()` | Byte-based rollover | GA | A backstop only. The server suggests continue-as-new at 4,096 events or 4 MiB, and every rollover check reads the suggestion first (RFC-0006 §8.3) | Later, only if a namespace raises those limits |
| `first_execution_run_id` on handles | Server-enforced run-chain identity | GA | Control plane | 2 |
| `ApplicationErrorCategory.BENIGN` | Expected failures logged at DEBUG, kept out of failure metrics and error spans | SDK 1.12 | LLM retries, permit waits | 3 |
| `static_summary`, `static_details`, `summary=`, `set_current_details` | Readable rows and timelines in the Web UI | SDK 1.9, UI 2.34.6 | Every start, LLM and tool activities, waits | 3 |
| Heartbeat details | Resume long work from a checkpoint | GA | Browser, coding, polls | 3 |
| Task queue Priority | Chat runs ahead of background work | Server GA 1.31 | Chat runs, agent steps | 3 |
| Fairness keys | Per-employee or per-user fairness | Server GA 1.31 plus `matching.enableFairness` | Multi-employee installs | The flag in RFC-0005 Phase 1 (T17); per-employee keys in RFC-0005 Phases 4-5 |
| `max_task_queue_activities_per_second`, `temporal task-queue config` | Queue-wide rate limits, changeable at runtime | Config API server 1.29 | Only a queue with one downstream and one fixed limit. Not LLM steps: providers limit per credential, so per-credential buckets replace per-provider LLM queues with queue-wide caps (RFC-0006 §13.2) | 3, where it fits |
| `start_delay` | A delay with no workflow code running | GA | No current use. Not the Undo window: an approval must accept Updates during it, and a delayed start runs no handler. Never on ids addressed by Signal-With-Start, which bypasses the delay (RFC-0006 §9.4) | Not adopted |
| Eager workflow start | Lower start latency when starter and worker share a process | Server 1.29; labelled preview | Not applicable: chat runs start as children or by Signal-With-Start, and neither can be eager (RFC-0006 §14.4) | Not adopted |
| Activity operations (pause, reset, update options) | Operator recovery without restarting | Preview; server 1.28 | Runbook | 3 |
| External Storage | Built-in claim check for large payloads | Preview; SDK 1.25 | Transcripts, events | 4 (evaluate) |
| Payload codec and codec server | Encryption or compression of history | GA | Docker and VM installs | 4 (Owner) |
| Standalone Activities | Durable single jobs without a workflow | GA in server 1.32 and SDK 1.34.0; SDK 1.30.0 already ships the client API, marked experimental | Not direct node runs (§9.6). A possible use is the owner's Retry of an `unknown` send (RFC-0005 E12; RFC-0006 §19.10) | 4 (evaluate) |
| `temporalio.envconfig`, TLS, API keys | Connect to a remote cluster or Temporal Cloud | GA | `DISTRIBUTED_MODE` | 4 (Owner) |

## 5. Defects and risks

Ranked. "Replay impact" says whether the fix changes the commands a workflow emits. If it does, the fix needs a patch gate or another versioning approach (§8.5).

### Critical

**C1. One large webhook can wedge a deployment's controller, or be lost silently.**
- *What happens.* The generic webhook route reads the whole body with no limit (`server/routers/webhook.py:78`) and stores a JSON body twice, raw and parsed (`:93-94`), along with every request header (`:91`). Webhook events are not scoped to a workflow, so every controller with a webhook trigger receives the signal. When the controller spawns the run it builds `{**data, "_event_envelope": event}` (`trigger_listener_workflow.py:392-395`), four copies of the body, and adds the run graph and the deployment's full parameter snapshot to the child's start input (`:446-449`, `:489-490`). Nothing checks the size before `start_child_workflow`. The outcome depends on the body size:
  - Roughly 0.5-1 MB (less with a large parameter snapshot): the signal fits under 2 MB but the child-start command does not. When the server reports its payload limits, SDK 1.30 fails the controller's workflow task with `PAYLOADS_TOO_LARGE` (`temporalio/worker/_worker.py:769-779`, `_workflow.py:487-500`) and the workflow stays open; every retry rebuilds the same command, so the deployment processes no further events until Reset. When the server does not report limits, the server closes the controller instead. Which happens depends on the unpinned dev-server version.
  - Above about 1 MB: the signal itself is rejected; the webhook still answers 200 and the event is lost.
  - Roughly 0.25-0.5 MB: the per-firing MachinaWorkflow wedges instead, because its activity inputs carry the trigger output twice.
- *Rule.* 2 MB per payload, 4 MB per gRPC request. With Python SDK 1.23+ an oversized payload fails the workflow task and "The Workflow is not terminated and remains open, so you can deploy a fix and allow the Workflow to continue" (https://docs.temporal.io/troubleshooting/blob-size-limit-error).
- *Fix (D7).* Cap request bodies at the edge (HTTP 413); keep one copy of the body; pass bodies over a threshold by reference (a workspace `FileRef`); check the child input size in the controller and dead-letter the event instead of starting. *Replay impact:* smaller payloads change no commands; the in-workflow size check does (patch gate).

**C2. An upgrade can strand open runs, and nothing would notice.**
- *What happens.* There are 19 patch markers at 24 call sites and no `workflow.deprecate_patch`. Three markers were deleted outright (`agent-delegation-acquire-cancellation-v1`, `agent-tool-call-identity-v2`, `agent-task-manager-delegation-v1`); the SDK's core fails replay of any history holding a marker the code no longer calls ("Non-deprecated patch marker encountered", `workflow_machines.rs:1771-1782`, read from the core source). Activity type names `node.{type}.v{version}`, their queues and retry policies are resolved from the live plugin registry during replay (`workflow.py:1053-1080`), and a `workflow.patched` call is itself conditional on a plugin attribute (`:856`); a plugin `version` bump changes the scheduled activity type for every open run. The agent child id format changed without a patch. CI replays almost only histories that the current code just produced: two fixtures exist and one is orphaned, the four replay tests that need a real CLI never run in CI, there is no verification at startup, and the app does not surface nondeterminism errors.
- *Rule.* "Fail CI if any error is encountered during replay" (https://docs.temporal.io/develop/python/best-practices/testing-suite); replay recent histories before deploying (https://docs.temporal.io/develop/safe-deployments); patch, then deprecate, then remove after the old runs are gone (https://docs.temporal.io/develop/python/workflows/versioning).
- *Fix (D10).* The owner sets the upgrade contract; the mechanics follow in §8.6. *Replay impact:* none for the tests and the startup check; freezing dispatch into run input is designed to produce today's commands.

**C3 (security). Webhook trigger authentication is never enforced, and its secret is written to history.**
- *What happens.* The webhook trigger offers `authentication: header` with `header_name` and `header_value` (`server/nodes/trigger/webhook_trigger/__init__.py:43-59`). No code reads them: both filters compare only the path (`:90-99`, `server/services/event_waiter.py:171-189`), `/webhook/` is a public path (`server/middleware/auth.py:34`), and the default empty path matches every webhook. The secret reaches history through the raw filter parameters and the parameter snapshot (`services/deployment/manager.py:584`, `:881`), and every request header is stored (`routers/webhook.py:91`); history is not encrypted.
- *Fix (D18).* Enforce the header in the router before `emit` with a constant-time compare; require a non-empty path; keep secrets and raw headers out of workflow inputs. This does not wait for the phases.

**C4. Every backend restart fires each deployment's Start node again.**
- *What happens.* Boot reconcile re-arms running and paused generations by replaying the user-Start deploy path from the generation snapshot (`services/temporal/lifecycle.py:125` → `reconcile_active_controls_on_boot`, `services/deployment/handlers.py:1596-1617` → `_rearm_generation`, `:1767-1835` → `handle_deploy_workflow`, `:1822`). That path fires every `start` node (`services/deployment/manager.py:277-282`, `_fire_start_trigger` at `:546-560`) as a new MachinaWorkflow run with a random id, `<slug>-<uuid8>`, and no id reuse or conflict policy (`services/temporal/executor.py:127`, `:181-194`). Nothing marks the call as a re-arm. So after every boot, clean or not, a start-node graph runs again and repeats its side effects. A paused generation queues one more held run per restart, and all of them execute on Resume. After an unclean shutdown the re-arm runs before the recovery pause, so the duplicate starts while the generation is still running. temporal-workflow-control.md:653-656 calls re-arm "idempotent"; the mechanisms it lists do not cover start nodes.
- *Rule.* The workflow id is the idempotency key of a start; a deterministic id with `WorkflowIDReusePolicy.REJECT_DUPLICATE` rejects a second start (https://docs.temporal.io/workflow-execution/workflowid-runid).
- *Fix.* Give the start-node run a deterministic per-generation id (for example `<slug>-start-g<generation>`) with `REJECT_DUPLICATE`, and skip start nodes when re-arming. The owner confirms the intended behavior (§17 Q16). *Replay impact:* none (client side).

### High

**H1. Inbound events are routed through Visibility and dropped silently.**
- *What happens.* `dispatch.emit` runs one Visibility List query per event and signals each match by workflow id (`services/events/dispatch.py:53-55`, `:132-158`, `:250-257`). The event is dropped, with only a log line, when Temporal is not connected (`:125-129`), the query fails (`:162-164`), it returns nothing (`:185-190`), or a signal fails (`:195-206`). Producers still answer 200 (`routers/webhook.py:69-76`, `:99-106`). A controller that has just started, has a newly registered event type, or is between two runs of a continue-as-new may not be in the index yet. A controller without push triggers never sets `ControlEventTypes`, and dispatch treats it as matching every event (`dispatch.py:177-183`, `:216-247`; `workflow_control_workflow.py:330-334`, `:739-740`).
- *Rule.* Visibility is "eventually consistent", "an empty result is a valid response", and is "intended for operational discovery rather than a critical path for application logic. ... To coordinate between workflows, use signals, updates, or child workflows rather than reading Visibility" (https://docs.temporal.io/visibility).
- *Fix (D8).* Route from the app database, which already holds every live generation and its deterministic controller id (`workflow-control-<id>-g<gen>`); signal by id with retries; tell the producer when delivery fails (5xx for webhooks, which providers retry). *Replay impact:* none (client side).

**H2. Node activities swallow cancellation and are recorded as completed failures.**
- *What happens.* `NodeExecutor` turns `asyncio.CancelledError` into `{success: False, error: "Cancelled"}` (`services/node_executor.py:231-239`) for every node type except `browser_agent`, `mobile_use_agent` and the Android tool. The per-type wrapper returns that dict (`plugin/base.py:1313-1388`), and the SDK records a normal return as Completed even after a cancel request (`temporalio/worker/_activity.py:358-363`). MachinaWorkflow then counts the node as failed, and a trigger-started run adds one to the deployment's pause-on-failure streak. It happens when a graceful backend shutdown outlasts `TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS` (10 s on desktop, 30 s elsewhere) and when agent tool calls are cancelled with their workflow; with activity pause (§9.5) it would happen on every pause.
- *Rule.* "For the Activity to appear canceled the exception needs to be re-raised" (https://docs.temporal.io/develop/python/workflows/cancellation).
- *Fix (D9).* On the Temporal path re-raise; turn a worker-shutdown cancellation into a retryable `ApplicationError`, as `NodeWaitInterrupted` already does (`plugin/base.py:1347-1358`). *Replay impact:* none.

**H3. Several activities retry without limit.**
- *The LLM step:* 10 min start-to-close, 1 min heartbeat, `LLM_STEP_RETRY` with `maximum_attempts=0` and 5 s to 5 min backoff; only `NodeUserError` and `InvalidEvent` are non-retryable (`agent_workflow.py:90`, `:98`, `:3168-3173`; `_retry_policies.py:94-100`). Any other exception (a missing key, a parsing bug after the call) and every timeout retry forever, so a turn that needs more than 10 minutes, such as a long output from a local model on a CPU, is cut off and re-billed indefinitely.
- *The legacy tool path* (`agent_workflow.py:108-141`): no retry policy (the server default is unlimited) and a fixed 10 min timeout, ignoring the plugin's declared policy. It serves manual canvas runs and generations admitted before execution control; `claude_code_agent`, `codex_agent` and `rlm_agent` declare `maximum_attempts=1` (`nodes/agent/claude_code_agent/__init__.py:251-254`) but on this path are re-run indefinitely, so a Claude Code session longer than 10 minutes never finishes.
- *Activities with no policy at all* (so unlimited): `store_node_output_activity` (`workflow.py:618-626`) and `load_persisted_workflow_graph_activity` (`trigger_listener_workflow.py:358-362`).
- *An oversized result* fails with `PayloadSizeError`, which Temporal makes retryable on purpose and which is not in `NON_RETRYABLE_ERROR_TYPES` (`_retry_policies.py:52-55`); under `LLM_STEP_RETRY` it repeats forever.
- *Rule.* The default retry policy is unlimited; bound total time with `schedule_to_close` rather than attempts (https://docs.temporal.io/encyclopedia/retry-policies); "Infinite retries without circuit breaking" is listed as an anti-pattern (https://docs.temporal.io/best-practices/error-handling).
- *Fix (D11, §9.2).* *Replay impact:* none; retry policies and timeouts are not compared on replay (§8.5).

**H4. Durable state sits on the development server.**
- *What happens.* `_install.py` downloads `https://temporal.download/cli/archive/latest` (`:46`), caches it under a fixed name (`:121`) with `known_hash=None` (`:124-136`), so the first boot fixes the version forever and nothing verifies the bytes; `company clean` keeps it and the Docker image copies it into the volume once (`docker/Dockerfile:39-43`, `:117`). Installs run whatever server their first boot downloaded. `start-dev` creates the SQLite schema only when the file is new and keeps no schema version; between server 1.27 and 1.31 the schema grew from 33 to 37 tables, and a database created by 1.27 or earlier cannot load any workflow when opened by 1.28 or later (missing `chasm_node_maps`; read from the CLI and server source). The namespace gets 24-hour retention (CLI source), while `.env.template:147-149` promises that "Temporal UI keeps showing every workflow that ever ran". `--metrics-port 0` opens `/metrics` on a random port; the comment at `_runtime.py:124` says it disables it.
- *Rule.* "The development server is not intended for production use. It skips certain HTTP security checks" (https://docs.temporal.io/cli/command-reference/server); SQLite persistence "is meant only for development and testing, not production usage" (https://docs.temporal.io/temporal-service/persistence); a file-based SQLite Visibility schema cannot be upgraded, "you must delete the database and create it again" (https://docs.temporal.io/self-hosted-guide/visibility/sqlite); upgrade one minor version at a time (https://docs.temporal.io/self-hosted-guide/upgrade-server).
- *Fix.* D1 to D4 (§6).

**H5. There is no way to see that Temporal work is stuck.**
- *What happens.* The runtime exports no metrics: `Runtime(telemetry=TelemetryConfig(logging=LoggingConfig(filter="ERROR")), worker_heartbeat_interval=None)` (`client.py:76-83`, `worker.py:125-136`). The log filter also hides Core's WARN lines, and nothing forwards Core logs into the app's logging. No workflow sets `failure_exception_types`, so a bug or a nondeterminism error leaves the execution Running with workflow-task failures retried forever, and nothing reads `TemporalReportedProblems`.
- *Rule.* Start alerting with zero pollers, `NonDeterminismError`, `GrpcMessageTooLarge`, workflow-task schedule-to-start latency and `RESOURCE_EXHAUSTED` (https://docs.temporal.io/best-practices/worker-alerting); a non-determinism failure leaves executions "in Running status the whole time" (https://docs.temporal.io/encyclopedia/detecting-workflow-failures).
- *Fix.* D6 (§7).

**H6. Cron's overlap policy never applies to the graph run.**
- *What happens.* The Schedule uses `ScheduleOverlapPolicy.SKIP` (`schedules.py:150`, `:226-229`), but the workflow it starts, `CronTriggerWorkflow`, starts the graph run as an ABANDON child and returns as soon as the start is acknowledged (`nodes/scheduler/cron_scheduler/_workflow.py:176-198`). SKIP only covers that short-lived workflow, so slow runs pile up without limit. The comment at `schedules.py:187-189` says the opposite. Schedule action ids also carry a literal `{{.ScheduledStartTime}}` (`schedules.py:65-71`), and the graph run's id uses `workflow.now()` rather than the scheduled time.
- *Rule.* SKIP discards an action only while "a previously started Workflow Execution is still running" (https://docs.temporal.io/schedule); "The Action's timestamp is appended to the Workflow Id" (same page); no templating is documented.
- *Fix.* Either await the child in `CronTriggerWorkflow` or let the Schedule start the run directly and read `TemporalScheduledStartTime`; the owner chooses the overlap behavior (§17). *Replay impact:* yes (patch gate or a new workflow type).

**H7. The documented worker-pool rollback strands runs, and the standalone worker registers different code.**
- *What happens.* `.env.template:100-107` presents `TEMPORAL_WORKER_POOL_ENABLED=false` as a rollback. Routing is frozen into each run's input (`executor.py:29-48`) and into the controller's trigger specs (`manager.py:886-887`), so MachinaWorkflow keeps routing node activities to plugin queues (`workflow.py:556`, `:863`, `:1078`) that nothing polls once the flag is off (`lifecycle.py:226-229`; the plugins' default queue is `machina-default`). With no `schedule_to_start` timeout those activities wait forever; only cron Schedules are refreshed in place on reboot. The standalone worker (`worker.py:792-947`) registers no `plugins=` (so no `CronTriggerWorkflow`), no `poll.*` activities and no pool queues, hard-codes namespace `default` and queue `machina-tasks`, and without `DISTRIBUTED_MODE` imports no plugins at all, so its workflow code schedules `execute_node_activity` where the embedded worker schedules `node.<type>.v<n>`; a workflow task that moves between the two fails replay.
- *Rule.* "All Workers polling the same Task Queue must register the same Workflow Types and Activity Types" (https://docs.temporal.io/task-queue).
- *Fix (D15).* Correct the rollback text. Always start the worker pool, so the queues that frozen runs target are always polled; the flag selects only the routing frozen into new runs (RFC-0006 §15.2). Build every worker's registration from one function; refuse to start a standalone worker that differs.

**H8. Workflow loops swallow cancellation.**
- *What happens.* When awaited activity work is cancelled, the awaiting workflow code receives `ActivityError` whose cause is `temporalio.exceptions.CancelledError`; with the default `TRY_CANCEL`, core resolves the activity at once (`temporalio/worker/_workflow_instance.py:1897-1937`). `ActivityError` is an ordinary `Exception` (`temporalio/exceptions.py:13`, `:288`), so `except Exception` catches it. The SDK's helper `is_cancelled_exception` (`temporalio/exceptions.py:459`) is used in five places in the repo, none of them these:
  - Legacy polling listener: a cancel during the poll activity is swallowed and polling continues (`polling_trigger_workflow.py:193`); a cancel during the graph lookup is swallowed and the child run is still started (`:297`). Legacy deployments stop listeners with `cancel()` (`services/deployment/manager.py:1159-1162`).
  - Legacy trigger listener: swallowed, and the listener keeps listening; after a cancel during the graph lookup or the idle broadcast, the child is still started (`trigger_listener_workflow.py:276` and the handlers inside it).
  - Controller poll tasks (`workflow_control_workflow.py:849-866`), cancelled by `task.cancel()` from reset, `replace_graph` or continue-as-new: the loop catches `asyncio.CancelledError`, but the cancelled activity arrives as `ActivityError` and falls into `except Exception`. A trigger retired by Apply can keep polling, and during continue-as-new the `asyncio.gather` at `:615` can wait on the still-running task and stall the rollover; execution control narrows this window without closing it.
- *Rule.* Cancellation must be re-raised to take effect; `temporalio.exceptions.is_cancelled_exception(e)` tells it apart from other failures.
- *Fix (D9).* Re-raise when `is_cancelled_exception(e)`; check the retired flag after every await in poll loops; the determinism lint (§8.1) keeps it fixed. *Replay impact:* yes, for runs whose history recorded a swallowed cancel (patch gate).

**H9. Boot recovery can stall, and one transient error can fail a generation for good.**
- *What happens.* After an unclean shutdown, with the default `WORKFLOW_CONTROL_CRASH_RECOVERY=pause`, the serial boot loop awaits each recovery pause in turn (`handlers.py:1614-1617` → `handle_pause_workflow` → the shielded drain at `:2307-2308`). The drain sends `wait_for_checkpoint` Updates with no client deadline (`services/deployment/execution_control.py:145-169`), and the SDK polls until the Update has an outcome (`temporalio/client/_workflow.py:1953-1976`). Admitted actions include 24 h node activities and LLM steps that retry without limit, so recovery can wait for hours; nothing bounds the loop, and the dev-server watchdog starts only after it returns (`lifecycle.py:125-127`). Separately, a single `NOT_FOUND`, or any error whose text contains "not found" or "already completed", counts as "controller missing" (`handlers.py:736-748`, `:903-912`; `services/deployment/execution_control.py:63-76`) and moves a v1 generation to the terminal `failed` state (`handlers.py:834-841`), which only Reset and Start clear. The code never tells a missing workflow from a missing namespace, although the SDK exposes `RPCError.grpc_status` and both failure detail types.
- *Rule.* Give Update calls a client timeout; with no worker polling, the client keeps retrying (https://docs.temporal.io/design-patterns/early-return). `DescribeWorkflowExecution`, not Visibility, answers whether an execution exists (https://docs.temporal.io/visibility).
- *Fix.* Run recovery pauses as background transitions, as the boot loop already does for pausing and resuming rows (`handlers.py:1600-1606`); start the watchdog first; give every Update a deadline; decode `NotFoundFailure` against `NamespaceNotFoundFailure` and require two Describe confirmations before failing a generation. *Replay impact:* none.

**H10. The controller's continue-as-new input carries the whole graph once per trigger, and an oversized carry fails the deployment.**
- *What happens.* Each trigger spec embeds the nodes, edges and parameter snapshot (`manager.py:874-889`; rewritten by `replace_graph`, `workflow_control_workflow.py:303-306`) and is carried across continue-as-new (`:684-688`). Above 1.9 MB the controller of a versioned generation (`execution_control_version=1`, every generation started on current code) raises a non-retryable `ControllerContinuationTooLarge` (`:705-711`), which fails the controller and then the generation (`controller_execution_missing`); only Reset recovers it. Boot recovery and Resume rebuild a controller with the same specs (`USE_EXISTING`) only for unversioned generations, where the same oversized carry breaks the next rollover again (RFC-0006 §8.2). RFC-0005's supervisor is instead rebuilt in place from the database, as T20 (RFC-0006 §3.1) decides. Triggers are added after the start by unacknowledged `register_trigger` signals, one full graph copy each, resent on every boot (`manager.py:945-956`).
- *Rule.* "Pass minimal state"; "Exceeding the 2 MB payload limit on Continue-As-New input ... Use external storage" (https://docs.temporal.io/design-patterns/continue-as-new).
- *Fix (D7).* Carry trigger metadata and a generation reference; load the graph in activities; pass triggers in the start input. *Replay impact:* gate on a carried version so that only post-rollover runs change.

**H11 (security). Task-completion events are not scoped to their workflow and can cross users.**
- *What happens.* `_broadcast_task_event` sets `data.workflow_id` but not the envelope's `workflow_id` (`server/nodes/agent/_events.py:109-134`), so dispatch adds no workflow scope, and the filter that runs (`event_waiter.py:214-259`) matches everything with default parameters. A Task Completed trigger in another workflow, or another user's workflow in `AUTH_MODE=multi`, fires with this task's result as its input.
- *Fix (D18).* Set `envelope.workflow_id`. *Replay impact:* none.

**H12. Cleanup is skipped on failure, cancellation and Reset, and Reset misses Pi inline runners.**
- *What happens.* Agent children start with the default parent close policy, TERMINATE (`workflow.py:820-828`; `employee_job_workflow.py:117`, `:120`; `agent_workflow.py:593-597` for the teammate child of `DelegatedTaskWorkflow`, `:1728-1732` for direct delegates). When an uncontrolled run fails, MachinaWorkflow stops without cancelling its running handles (`workflow.py:905-908`); the children are then terminated by policy. Reset terminates every execution it finds instead of cancelling it (`services/deployment/handlers.py:992-994`, `:2591`), and termination never reaches workflow code. That skips `DelegatedTaskWorkflow`'s delegation cancellation and permit release (`agent_workflow.py:627-725`), AgentWorkflow's `employee.job.failed` and browser-claim release (`:834-856`) and delegation cleanup (`:1506-1554`), EmployeeJobWorkflow's failure marker and MachinaWorkflow's unregister. Reset compensates browser claims and Workspace tasks, but it releases a browser claim only while the terminated execution is still retained (24 h on the dev server) and only for its own generation. A manual canvas Run's claim, or one older than retention, is never released: it keeps the profile `BrowserBusy` for every other task and defers a deleted employee's profile cleanup (RFC-0006 §6.2). Nothing sweeps team tasks, subagent permits or employee jobs; those leftovers stay "active" without blocking the next generation. This fits the open "Reset leaves team tasks and permits" bug. Separately, `PiInlineDelegationWorkflow` is started from a tool handler with no search attributes (`services/handlers/tools.py:378-386`, `pi_inline_delegation.py:219-226`), so it is its own root and neither Reset query finds it (`handlers.py:844-900`). It registers in the controller's `live_roots`, which Reset never reads, so inline Claude Code or RLM delegations to a Coding Agent survive Reset and workflow deletion.
- *Rule.* "In most cases, canceling is preferable because it allows the Workflow to finish gracefully" (https://docs.temporal.io/develop/python/workflows/cancellation); "Prefer Cancellation" (https://docs.temporal.io/encyclopedia/workflow/cancellation-and-termination).
- *Fix (D9).* `REQUEST_CANCEL` with `WAIT_CANCELLATION_COMPLETED` for agent children; cancel and drain on cancellation, and on failure start nothing new while running nodes finish (§8.4); Reset cancels, waits a bounded grace period, then terminates stragglers, acts on `live_roots`, and closes team tasks, employee jobs and permits (§8.4); start the Pi runner with `EventWorkflowId` like its siblings. *Replay impact:* the parent close policy is not compared on replay (§8.5); the new cancel commands in MachinaWorkflow are (patch gate); Reset and the Pi start options are client side.

### Medium

**M1. All activities run on the API server's event loop.** The 48 `@activity.defn` functions are `async def`, no worker passes `activity_executor`, and the embedded workers run on uvicorn's loop (`main.py:393`). A blocking call in any of about 150 plugins stalls HTTP, WebSocket, polling and heartbeats; one known case is `json.dumps` of transcripts up to about 1 MB on every LLM step (`agent_activities.py:668`). Docs: "By default, Activities should be synchronous rather than asynchronous"; blocking an async activity "blocks your event loop and the rest of Temporal" (https://docs.temporal.io/develop/python/best-practices/python-sdk-sync-vs-async); don't deploy workers "side-by-side with other application code" (https://docs.temporal.io/develop/safe-deployments). Fix in §11.1.

**M2. Schedules are found through Visibility, and ids built from labels collide.** Pause, delete, Apply and Reset find Schedules with `list_schedules` (`schedules.py:337-355`, `:398-409`; `services/employees/safe_apply.py:85`, `:108`, `:177-200`; `handlers.py:2561`; `manager.py:997`), although the ids are deterministic; a Schedule created moments earlier can be missed while strict mode reports success. Listener and Schedule ids come from slugged labels, so "Orders 1" and "Orders-1" collide: the second push trigger is silently ignored (`workflow_control_workflow.py:325-329`) and the second cron node overwrites the first Schedule (`schedules.py:254-295`). Fix: address Schedules by id; derive ids from immutable node ids (a one-time migration, §17).

**M3. Polling triggers lose events.** An id whose detail fetch fails is still recorded as seen and never emitted; `post_emit` (for example Gmail mark-as-read) runs inside the poll activity before the workflow has recorded the event (`services/plugin/polling.py:207-270`), so a lost attempt hides the message from the `is:unread` query; a failed child start is marked seen and never retried (`polling_trigger_workflow.py:203-227`). The poll activity has no heartbeat timeout and reuses one activity id every cycle (`workflow_control_workflow.py:809`, `:834-838`). Fix in §10.5.

**M4. Human waits hold worker slots for days.** The approval gate polls the database every 10 s inside an activity with a 24 h start-to-close and unlimited retries (`nodes/workflow/approval_gate/__init__.py:48`, `:181-185`, `:235-277`); reviewed employee delivery waits up to 31 days in an activity at three sites (`employee.job.deliver`, `employee-reviewed-delivery`, `employee.job.resolve_delivery`), and a retry within the 2-minute delivery lease completes without waiting and strands the draft; permit waits poll every second. Pause checkpoints wait for them. Temporal's pattern is a Signal or Update plus `workflow.wait_condition(..., timeout=...)` (https://docs.temporal.io/design-patterns/approval). D13 (Owner). The interim fix is item 1b.9 of RFC-0005 Phase 1b: a retried delivery re-attaches to its own lease. RFC-0005 Phase 7's ApprovalWorkflow then replaces all three waits (RFC-0006 §9.6, §22).

**M5. Failures don't look like failures.** MachinaWorkflow and AgentWorkflow complete with `success: False` (`workflow.py:913-930`), so Temporal status, Visibility and metrics never show a failed run. Without `failure_exception_types`, an unexpected exception in workflow code retries the workflow task forever; a malformed `register_trigger` signal does that to a controller (`workflow_control_workflow.py:325-334`), and a signal handler that raises any other exception is "stuck" by design (https://docs.temporal.io/handling-messages). D12 (Owner).

**M6. Inputs grow with the graph.** Every node activity and agent child input carries the full node list, edges, parameter snapshot and upstream inputs (`workflow.py:708-771`). All ready nodes are scheduled in one workflow task, and a task whose commands exceed the 4 MB gRPC limit terminates the execution ("Terminated Executions do not retry", https://docs.temporal.io/troubleshooting/blob-size-limit-error). The run result returns every output (`:924-930`). Fix (D7): pass references; the activity already loads parameters from the database.

**M7. A failed search-attribute registration is reported as harmless.** Registration catches the error and returns a status that the caller ignores (`search_attributes.py:175-183`, `client.py:126-137`), but every start sets custom search attributes, which the server rejects with "search attribute ... is not defined". Fix: fail loudly; pass `--search-attribute` when the dev server creates the namespace.

**M8. Routed queues strand work silently.** Browser owner queues (`services/browser_owners.py:46-47`; `agent_workflow.py:144-155`; `workflow.py:788-792`) and the Pi driver queue check owner liveness only when work is bound (`browser_owners.py:60-68`). An owner that dies afterwards strands its tasks silently. The docs set `schedule_to_start_timeout` on worker-specific queues and retry the sequence elsewhere (https://docs.temporal.io/design-patterns/worker-specific-taskqueue), but that fits neither queue: a browser profile never moves to another owner, and parked parents make a backlog on the Pi driver queue normal. Fix: no schedule-to-start timeout on these queues; monitor them and raise an attention item (RFC-0006 §15.2).

**M9. LLM calls bypass the queue built for them.** The LLM and compaction steps run on `machina-tasks` (`agent_workflow.py:3168-3173`), not `ai-heavy` as `agent_activities.py:12-13` says, so the ai-heavy tuner and rate limit never apply, and they share 100 slots with 31-day deliveries. Rate limits are per worker only (`worker.py:430-440`, `:584`). Fix in §11.3.

**M10. The determinism sandbox is off everywhere.** All 14 workflow definitions pass `sandboxed=False`. Edge conditions run a user-supplied regular expression on the workflow thread and log through structlog rather than `workflow.logger` (`services/execution/conditions.py:30-32`, `:217-223`); a catastrophic pattern would block the thread past the SDK's deadlock detector. Docs: "Skipping Workflow Sandboxing results in a lack of determinism checks" (https://docs.temporal.io/develop/python/best-practices/python-sdk-sandbox). Fix in §8.1.

**M11. AgentWorkflow history grows quickly and its continue-as-new input carries dead weight.** Each LLM step input records the whole transcript, the tool definitions and the system prompt (about 12 + 18k events per turn with k tools); with a transcript near its byte budget, history passes 10 MB in about 25 turns. Continue-as-new happens only at a clean turn end, and its input carries the stale seed conversation (up to 1 MB), duplicated tools and parameter snapshot, and unbounded thinking text, which can exceed 1.9 MB and raise `AgentContinuationTooLarge` (`agent_workflow.py:2989-3014`). Fix in §13.4.

**M12. Boot recovery is not safe with several backends.** The crash marker is one key in the shared cache table, read and overwritten at every boot and stamped clean by every graceful shutdown (`handlers.py:1452`, `:1481-1508`, `:2835`), and every backend with Temporal enabled reconciles every active row (`main.py:379-396`, `lifecycle.py:125`, `core/database.py:3105-3120`). `services/deployment` never consults `DISTRIBUTED_MODE` or a replica id. A replica that boots while another runs reads "running", treats it as a crash and pauses every running generation; one replica's clean stop can hide another's crash; every replica re-arms every generation (C4). Revision checks keep the pauses themselves from conflicting. Fix with a per-replica marker and one elected reconciler, if multi-backend installs are supported (§17 Q14).

### Low

- **L1.** Dead code: `emit_event_activity` (`activities.py:389`), `WSConnectionPool` (`ws_client.py`), the "heartbeat on every non-matching broadcast" branch (`activities.py:275-276`; `/ws/internal` is never a broadcast target), unused helpers in `agent_activities.py:2599-2612`, and the `EventSource` and `EventReceivedAt` search attributes, registered and never set.
- **L2.** Update calls with no client timeout (`node_invocations.py:52`, `handlers.py:708`); with no worker polling, the client waits indefinitely.
- **L3.** Pi Updates without validators (`pi_workflow.py:79-107`); Pi signal handlers that run activities (`pi_control.py:36-55`); the Workspace controller rejects inside Update handlers instead of validators, so rejected calls still cost history and the per-run Update quota (`workspace_tasks_workflow.py:82-148`).
- **L4.** Three LLM call sites keep their SDK's own retries under Temporal's (`nodes/vision/vision_analyze/__init__.py:310`, `:348`, `:399`; `services/memory/vector_store.py:239`; `nodes/translate/_providers/llm.py:168`).
- **L5.** `WebhookSource` puts every event on an unbounded queue that nothing drains (`services/events/webhook.py:121`, `push.py:27-31`).
- **L6.** The `temporal_start` and `temporal_stop` WebSocket commands have no owner check (`services/temporal/_handlers.py:35-55`); with login off (the default) any page on the machine that passes the Origin check can stop the engine.
- **L7.** Worker identities are not unique across hosts; `worker.py:290` imports the private `load_default_build_id`.
- **L8.** `_get_node_inputs` and the dependency map iterate sets (`workflow.py:940`, `:1192-1194`). Only in-memory key order varies: the default JSON converter writes payloads with `sort_keys=True` (`temporalio/converter/_payload_converter.py:639-643`), so neither commands nor payload bytes change. Iterate lists anyway, to keep the rule simple.
- **L9.** The execution-control Updates carry no explicit id (`execution_control_activities.py:17-27`); their handlers are idempotent, so this is hygiene.

## 6. Platform: server, persistence and versions

### 6.1 What runs today

| Item | Today |
|---|---|
| Python SDK | temporalio 1.30.0 (2026-07-02) in `server/uv.lock`; floor `>=1.25.0` in `server/pyproject.toml:170` |
| Latest SDK | 1.34.0 (2026-09-30) |
| Dev server | `temporal server start-dev` spawned by `services/temporal/_runtime.py:137-159` with `--port --ui-port --db-filename --metrics-port 0 --log-level warn --namespace` and three SQLite pragmas |
| CLI version | "latest" at first boot, cached forever; today that resolves to v1.9.1 (server 1.32.0, UI 2.54.1). v1.9.2 is a pre-release build with a server release candidate |
| Retention | 24 hours (CLI source); never changed by the app |
| Dynamic config | None passed |
| Metrics | SDK: none. Server: `/metrics` on a random port, never scraped |

### 6.2 The SQLite schema hazard

The CLI calls `SetupSchema` only when the database file does not exist, in every release from v1.3.0 to v1.9.1; the script uses plain `CREATE TABLE` and keeps no schema version table. The schema changed between server minors:

| Server | Tables | New |
|---|---|---|
| 1.27 | 33 | |
| 1.28 | 34 | `chasm_node_maps` |
| 1.29 | 36 | `tasks_v2`, `task_queues_v2` |
| 1.31 | 37 | `current_chasm_executions` (schema v1.11) |

A database created by 1.27 or earlier, opened by 1.28 or later, cannot load any workflow. One created by 1.30 or earlier, opened by 1.31 or 1.32, fails only for newer features such as standalone activities. Opening a newer database with an older server is unsupported. (CLI and server source at release tags; not reproduced.)

Today no install changes server version by itself, because the cached binary is never replaced. The hazard appears the day the version is pinned or bumped (D1), and on any machine where the cache is deleted but the database is kept.

### 6.3 Pinning (D1)

- Pin `version = 1.9.1` (server 1.32.0) and the per-platform SHA-256 from that release's `checksums.txt` in a committed file. The versioned URL is `https://temporal.download/assets/temporalio/cli/releases/download/v1.9.1/temporal_cli_1.9.1_<os>_<arch>.<zip|tar.gz>`; it returns the same bytes and ETag as the GitHub asset and `checksums.txt` sits beside it. This URL shape is observed behavior, not documented; the documented fallback is the GitHub release asset.
- Download with `pooch.retrieve(known_hash="sha256:...")` into a versioned directory and file name, so an upgrade keeps the old binary for rollback.
- Write `temporal.db.server.json` (server version, CLI version, creation time) when the database is created, and refuse to start the dev server on a different server **minor**; patch releases of the same minor are allowed, so security fixes can ship. *Owner, 2026-10-23:* a different minor, or a database without the marker, deletes the database instead of refusing, and the install starts empty (RFC-0005 E16).
- Apply the same pin to the Docker image (replace the copy-once volume step with a versioned path) and the desktop bundle.
- Record the pin in `cli/` predeploy checks so CI downloads the same binary.

### 6.4 Moving an existing database (D2, Owner)

| Option | How | Cost |
|---|---|---|
| A. Keep the binary that made the database | Store each database's server version; run that binary; offer an explicit, staged upgrade one minor at a time on a copy of the file | Most code; old servers keep running on old installs and miss security fixes |
| B. Recreate the Temporal database and re-arm from app state | Pause deployments, stop the server, move `temporal.db` aside, start the pinned server, run the existing boot reconcile (`reconcile_active_controls_on_boot`), which re-arms generations from their stored graph snapshots | Loses in-flight runs and history (closed history is already deleted after 24 h); needs a "your running workflows will restart" notice |
| C. Move durable installs to PostgreSQL | The documented production store with a schema tool (`temporal-sql-tool`) | Larger footprint; see D3 |

Recommendation: B for desktop installs, because their state of record is the app database and runs are already paused on every unclean restart; C, or B with a pinned server, for Docker and VM installs, chosen under D3. Option B depends on the upgrade contract (D10): if open runs must survive upgrades, B is allowed only between releases that declare it.

### 6.5 Which server durable installs run on (D3, Owner)

| Option | For | Notes |
|---|---|---|
| Pinned dev server with SQLite (D1, D4) | Desktop; small single-user installs | Documented as development-only: single writer, one history shard, no authorization, no schema upgrades |
| PostgreSQL-backed Temporal server | Docker and cloud VM installs meant to run for months | Production-grade store with schema upgrades; Temporal publishes server images and Helm charts; adds a database and a process to supervise |
| Temporal Cloud | Hosted, multi-tenant or paid installs | Needs TLS or API keys and `envconfig` in `client.py` (not supported today); payload codec strongly advised |

### 6.6 Retention and dev-server flags (D4)

- Set retention explicitly (recommendation: 7 days) after the namespace exists, with the operator service (`UpdateNamespace`) or `temporal operator namespace update --retention`, and correct `.env.template:147-149`. Retention applies only to executions that close afterwards.
- Pass `--search-attribute Name=Type` for the seven custom attributes; it applies only when the namespace is created, so keep the operator-API registration for existing databases (M7).
- Pass explicit `--dynamic-config-value` entries for what the app relies on, instead of each CLI version's defaults:
  - `system.numConsecutiveWorkflowTaskProblemsToTriggerSearchAttribute=5` (D6; passed in Phase 1b with the SDK metrics, RFC-0006 1b.38)
  - `frontend.WorkerHeartbeatsEnabled=true`, `frontend.ListWorkersEnabled=true` if worker heartbeating is turned on
  - `matching.enableFairness=true` and one task-queue partition (`matching.numTaskqueueReadPartitions=1`, `matching.numTaskqueueWritePartitions=1`), shipped in the fresh-start release because partitions can be lowered safely only on a new database (T17, RFC-0006 §3.1, §13.4)
  - not `history.enableStandaloneActivityOperatorCommands`: the dev server forces it on in code, so the desktop and hosted tier S do not pass it (RFC-0006 §19.10)
- Bind the server and UI to loopback explicitly (`--ip 127.0.0.1`, `--ui-ip 127.0.0.1`); consider `--headless` on Docker and VM installs, where the Web UI shows every payload in plain text; `--ui-disable-news-fetch` on all installs.
- Fix the `--metrics-port` comment. Name both `--metrics-port` and `--http-port` with `*_PORT` variables in `.env.template`: today the dev server opens both on random free ports, and `services/netpolicy.py` protects only named ports. Pin the metrics port on loopback, because telemetry will scrape it (1b.33; RFC-0006 §14.1, §17.8).

### 6.7 SDK upgrade 1.30.0 to 1.34.0 (D5)

No API the repo uses was removed (checked by grep and an AST diff); core dependencies are unchanged (`nexus-rpc==1.4.0`). What changes:

- `payload_limits` moved from `DataConverter` to `Client.connect(payload_limits=PayloadLimitsConfig(...))` and `PayloadSizeWarning` was removed (1.31); `services/media/limits.py:14` names the removed class.
- From 1.33, calling continue-as-new from an Update handler fails the workflow task (the repo never does).
- Fixes that matter here: cancellations no longer swallowed (1.31); try-cancel child and duplicate activity-cancel nondeterminism fixed (1.32, 1.33); shutdown drains completions (1.33). 1.33 also stops sticky pollers starving new workflows for up to about 60 s, but only when `max_cached_workflows` is smaller than the workflow-task slot count (10). OpenCompany's caches of 50, 100 and 500 are unaffected, so that fix is no reason to upgrade (RFC-0006 §14.1, §17.9).
- New and useful: `workflow.uuid7`, replay-safe OpenTelemetry providers (1.32); `ActivityHandle` operator commands and an OpenTelemetry metrics exporter (1.33); `WorkflowAlreadyStartedError.first_run_id` (1.34).
- Gate: record histories with 1.30.0 before the bump (D16) and replay them with 1.34.0.

### 6.8 Security posture of the dev server

The dev server has no authorizer ("allows every API request"), and its Web UI shows every payload. It must stay on loopback, which needs the explicit flags above. The `temporal_start` and `temporal_stop` WebSocket commands need an owner check (L6). Encrypting payloads with a codec (§12.4) is an owner decision for Docker and VM installs.

## 7. Observability and operations (D6, D14)

### 7.1 Metrics and alerts

Create the runtime with metrics before any other Temporal object: `Runtime(telemetry=TelemetryConfig(metrics=PrometheusConfig(bind_address="127.0.0.1:<port>"), global_tags={...}))` for Docker and VM installs, and `MetricBuffer` drained into the app's health view on desktop, where nothing scrapes. Keep tag values low-cardinality (no workflow ids).

Start with Temporal's documented set (https://docs.temporal.io/best-practices/worker-alerting), plus two of ours:

| Alert | Signal |
|---|---|
| No pollers on a queue with demand | `num_pollers` = 0 while tasks are scheduled |
| Nondeterminism | `temporal_workflow_task_execution_failed{failure_reason="NonDeterminismError"}` |
| Oversized task | `failure_reason` `GrpcMessageTooLarge` or `PayloadsTooLarge` |
| Workflow tasks waiting | workflow-task schedule-to-start p99 above 5 s |
| Server pushback | `RESOURCE_EXHAUSTED` on start, signal or update |
| Retry storm (ours) | rate of attempts above N on `LLM_STEP_RETRY`, compaction, browser cleanup, controller queue paging and compensation policies, counted with `activity.metric_meter()` in the existing interceptor. Permit waits and the planned poll retries of T7 (RFC-0006 §3.1) are excluded, because retrying is their purpose (RFC-0006 §11.7) |
| Stuck executions (ours) | count of `TemporalReportedProblems IS NOT NULL` |

Use the rate of attempts, not cumulative counts, and do not use `worker_task_slots_available` on the resource-tuned queues (the docs say it is not meaningful there).

### 7.2 Stuck executions

Set `system.numConsecutiveWorkflowTaskProblemsToTriggerSearchAttribute` (server 1.30.1+). The server then sets `TemporalReportedProblems` after five consecutive workflow-task failures. Boot reconcile and the deployment status view query `TemporalReportedProblems IN ('category=WorkflowTaskFailed','category=WorkflowTaskTimedOut')` and show "needs attention" instead of "running".

### 7.3 Labels in the Web UI (D14)

- `static_summary` (one line, 200 bytes) and `static_details` (Markdown, 20 KB) on every start: employee, deployment, generation, trigger; never prompts or secrets.
- `summary=` on LLM steps (model, turn), tool activities (tool name), timers and child starts.
- `workflow.set_current_details()` while a run waits (approval, permit, pause); it is served by a query and adds no history events.
- Adding `summary=` to an activity already in history needs no patch: on replay the SDK's core compares only an activity's id and type (§8.5; RFC-0006 §5 rule 6). The replay corpus stays the check.

### 7.4 Logs and expected failures

Replace `ExpectedLLMRetryFilter` (`core/logging.py:256-283`) with `ApplicationError(..., category=ApplicationErrorCategory.BENIGN)` for expected outcomes (provider rate limits, permit waits). BENIGN only changes logging level, failure metrics and span status; retries are unchanged. Forward Core logs at WARN through `LoggingConfig(filter=TelemetryFilter(core_level="WARN", other_level="ERROR"), forwarding=LogForwardingConfig(logger=...))`.

### 7.5 Tracing

`TracingInterceptor` is correct but "the earlier integration": workflow spans have no duration, and runs started by a Schedule get no workflow span unless `always_create_workflow_spans=True`. `OpenTelemetryPlugin` with `create_tracer_provider()` gives real durations (experimental; register on the client only). Spans are recorded but dropped today: `core/tracing.py` installs no exporter unless console spans are on. Choose an exporter before investing here.

### 7.6 Operator runbook (CLI)

Add a runbook page to docs-internal covering, with the pinned CLI:

- `temporal workflow describe | show --reverse | trace | stack` to see why a run is stuck.
- `temporal workflow reset --type LastWorkflowTask` after shipping a fix for a workflow-task failure (resetting a closed run "is not idempotent").
- `temporal activity pause | unpause | reset | update-options` for a misbehaving activity, once H2 is fixed (preview; server 1.28+).
- `temporal task-queue describe` for backlog, and `temporal task-queue config set --queue-rps-limit` for a provider outage.
- `temporal schedule describe` for missed actions (`schedule_missed_catchup_window`).

## 8. Workflow design (D9, D10, D12)

### 8.1 Determinism and the sandbox

Today's workflow code avoids every non-deterministic call the docs list: no system time, randomness, `uuid4`, environment reads or I/O, and no `asyncio.wait` or set iteration that drives commands. The exceptions are the edge-condition helper (M10) and code constants and the live plugin registry deciding commands (C2).

With the sandbox off, nothing enforces this. Recommendation:

1. A lint test that walks every module defining a `@workflow.defn` class and the helpers it imports, and fails on `datetime.now`, `time.time`, `random`, `uuid.uuid4`, `os.environ`, `Settings()`, `asyncio.wait`, `asyncio.as_completed`, `print`, stdlib or structlog loggers, and `except Exception` around awaited activities or children unless the block re-raises cancellation. It also fails on cleanup that awaits a workflow operation in `finally`, `except Exception` or `except BaseException` without first re-raising a workflow bug (`is_workflow_bug`, the bug guard of 1b.13). Every current exit-cleanup block re-raises only after its cleanup, so the cancellation rule alone misses them (RFC-0006 §18.2).
2. Sandbox the small, pure workflows first (ApprovedToolCallWorkflow, NodeInvocationWorkflow, EmployeeJobDeliveryWorkflow, CronTriggerWorkflow), with `workflow.unsafe.imports_passed_through()` around imports and `SandboxRestrictions.default.with_passthrough_modules(...)` for app packages. The large workflows stay unsandboxed until their module imports are free of side effects (`services/temporal/__init__.py` registers handlers on import).
3. Optionally replace the hand-rolled `_wait_any_complete` with `workflow.wait(..., return_when=asyncio.FIRST_COMPLETED)`, which returns ordered lists; this changes no commands.

### 8.2 Messaging

The control plane is in good shape (§4.1). Changes:

- Turn signals that need an answer into Updates with validators: `register_trigger` (today a poison signal can wedge the controller, M5), and the Pi `pause` and `resume` handlers, which run activities.
- Add validators to the Pi Updates and move the Workspace controller's rejections into validators (L3).
- Give every Update call a client deadline with `asyncio.timeout` around the whole call (L2). `rpc_timeout` bounds each request only: after the Update is accepted, `execute_update` waits for the outcome without it (RFC-0006 §7.3). Give a stable `id=` where the call is retried (L9), except Workspace submissions, which keep random ids: their checks can fail after acceptance, and a stable id would return that cached failure to every retry.
- Count Signals and Updates per run, not only events: limits are 10,000 signals, 2,000 total and 10 in-flight Updates per run. Each fired run costs the controller two Updates and a signal.

### 8.3 Continue-as-new

- Controller: carry metadata, not graphs (H10); keep continuing-as-new only from the main loop. A byte check with `get_current_history_size()` adds little, because the server suggests continue-as-new at 4,096 events or 4 MiB and every rollover check reads the suggestion first; keep one only as a backstop, if a namespace raises those limits (RFC-0006 §8.3).
- An idle controller (no events, polls, firings or Updates) never rolls over, so it never picks up new code and its markers can never be retired. Busy versioned controllers do roll over: each cron firing sends four Updates, and Update handlers request a rollover under pressure (RFC-0006 §8.3). So roll over periodically as well (for example after 7 days or N patch changes); the docs suggest periodic continue-as-new so "you're running only a couple of versions". RFC-0006 §8.3 does this with a rollover request to every live controller at the first boot of each release.
- AgentWorkflow: trim the carried input (M11, §13.4).

### 8.4 Children and cancellation (D9)

- Agent children: `parent_close_policy=ParentClosePolicy.REQUEST_CANCEL`, `cancellation_type=ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED`.
- MachinaWorkflow: on cancellation, cancel every handle in `running` and drain them within a bound, then finish. On failure, start nothing new and let running nodes finish, as controlled runs already do (RFC-0006 §6.3).
- Workflow loops: never let `except Exception` swallow cancellation; check `is_cancelled_exception(e)` and re-raise (H8).
- Reset: signal, cancel, wait a bounded grace period (for example 30 s), then terminate what is left; keep the existing database sweeps as the backstop. No sweep exists for team tasks, employee jobs or permits, so Reset must close them (1b.21; RFC-0006 §10.2).

### 8.5 What needs a patch (corrects CLAUDE.md)

CLAUDE.md says "Removing/adding start options or retry policies on recorded commands requires a `workflow.patched(...)` guard". That is stricter than Temporal needs. On replay, the SDK's core always checks the kind and order of commands: each recorded event must match the next command the code emits (`workflow_machines.rs:928-1000`). Among command attributes it compares only an activity's id and type (`activity_state_machine.rs:371-396`) and a child workflow's id and type (`child_workflow_state_machine.rs:233-262`), and only for histories that recorded the `IdAndTypeDeterminismChecks` flag, which current core records on every workflow task (`internal_flags.rs:20-23`, `:98-109`; the Rust sources ship in the temporalio 1.30.0 wheel). Task queues, timeouts, retry policies, inputs, headers, cancellation types, parent close policies and id reuse policies are never compared. The docs agree: it is "not safe to change the types or IDs of Child Workflows or Activities", while changing their options does not cause nondeterminism errors (https://docs.temporal.io/workflow-definition). Cancellation types need care although they are not compared: they decide whether a cancel emits a command and when the workflow sees the outcome, and core raises a nondeterminism error when a non-ABANDON activity or child records a start or completion after its cancel. Here core is stricter than that page. So changing a cancellation type needs a patch whenever a replayed history can contain a cancel of that activity or child; passing today's default explicitly changes nothing (RFC-0006 §5 rule 6, §6.2). One more exception is documented for Python, Java and Go: changing a timer's duration from or to zero is non-deterministic (https://docs.temporal.io/workflow-definition).

The working rule becomes:

- **Needs a patch gate:** adding, removing or reordering commands (activities, children, timers, signals to external workflows, search-attribute upserts, markers); changing an activity type name or activity id; changing a child workflow type or id; changing whether a `workflow.patched` call happens; changing a cancellation type when a replayed history can contain a cancel of that activity or child.
- **Does not need a patch, but needs the replay corpus to pass:** timeouts, retry policies, task queues, parent close policies, `summary=`, a cancellation type change when no replayed history can contain a cancel of that activity or child (or today's default passed explicitly), input and result payload shapes (the code must still accept old shapes).

### 8.6 Patch lifecycle and the replay corpus (D10)

- **Freeze dispatch.** Record each node's activity name, queue, timeouts and retry policy in the run input when the run is admitted, as agent tools already do (`_tool_activity_policy`), and keep registering old `node.X.vN` names for one release after a version bump. Old inputs fall back to the registry, so today's commands are unchanged.
- **Corpus.** Record one history per workflow type and per important path (manual run, trigger run, agent turn with tools, delegation, controller rollover, cron firing, approval, workspace task, Pi run) with each release, redacted, under `server/tests/temporal/fixtures/<release>/`. CI replays every fixture of the previous release (and the one before, if the owner wants N-2) with `Replayer(workflows=<the production list>)`, built from the same function the worker uses, and fails on any error.
- **Golden identity test.** A test lists the activity type names, child id formats and patch markers each workflow can emit and fails when one changes without a patch entry.
- **Startup verification** (Owner, §17): on upgrade, fetch the histories of open executions (`list_workflows(...).map_histories()`) and replay them before starting workers. On failure, either refuse to start workers, or start them and mark the affected generations "needs Reset".
- **Retirement.** Keep a table of markers in TEMPORAL_ARCHITECTURE.md with the release that added each one. A marker becomes `deprecate_patch` once no supported upgrade source can hold a run that predates it, and is removed one release after that. Never delete a marker in one step again.

### 8.7 Failure semantics (D12, Owner)

Two questions:

1. When a run fails (a node failed, an agent gave up), should the Temporal workflow end Failed, so that Temporal status, Visibility (`ExecutionStatus='Failed'`) and metrics show it, or stay Completed with `success: False` and a `RunOutcome` search attribute?
2. When workflow code hits a bug, should the run stay stuck until a fixed release replays it (Temporal's default, now visible through §7.2), or fail fast (`failure_exception_types=[Exception]`), losing the run but not the deployment?

Recommendation: fail graph and agent runs as Failed after their bookkeeping (a patch-gated final command), and keep bugs stuck but visible, because a fixed release resumes them without losing state. That holds only with the bug guard (1b.13): exit cleanup must re-raise a workflow bug first, or the cleanup records commands and marks rows failed, and the fixed release then fails replay (RFC-0006 §18.2).

## 9. Activities (D11)

### 9.1 Async activities on the API loop

See M1 and §11.1. Short term: a lint that flags known blocking calls (`requests`, `time.sleep`, `subprocess.run`, `sqlite3`, file I/O without `to_thread`) inside `async def` activities and plugin operations, and move CPU work such as transcript encoding into `asyncio.to_thread`.

### 9.2 Timeouts and retries

| Activity | Today | Proposed |
|---|---|---|
| LLM step | 10 min attempt, unlimited, 5 s to 5 min | Attempts of 10 min (cloud) and 30 min (local); a `schedule_to_close` budget per step (Q6) of 15 min (cloud) and 45 min (local). Expiry parks the turn instead of failing it, so the budget ships with parking in RFC-0005 Phase 5, for new generations only, as T11 and T18 (RFC-0006 §3.1) decide (values: RFC-0006 §11.3). Non-provider exceptions non-retryable; `PayloadSizeError` non-retryable; BENIGN for rate limits |
| Compaction | 3 attempts | Same policy as the LLM step, so a 429 burst does not end the run |
| Legacy agent tools | 10 min, no policy (unlimited) | The plugin's declared policy and timeout on every path |
| Node activities | 24 h attempt, 2 min heartbeat, plugin policy | The plugin's declared timeout; heartbeats that report progress, not only liveness; `schedule_to_close` per plugin |
| Store output, load graph | No policy (unlimited) | A declared unlimited bookkeeping policy (`BOOKKEEPING_RETRY`): both are idempotent database calls (RFC-0006 §11.1) |
| Poll activities | `max(30, 4 x interval)` s, no heartbeat timeout | Heartbeat timeout; per-cycle activity id (patch) |
| Owner-routed activities | No `schedule_to_start` | Still none, on owner queues or the Pi driver queue (M8); monitor them and raise an attention item (RFC-0006 §15.2) |
| Own-permit re-acquire | 3 attempts of 1 h | `PERMIT_WAIT_RETRY` |
| Employee delivery | Three 31-day activity sites, 3 attempts each; a retry within the 2-minute delivery lease strands the draft (M4) | Interim: a retry re-attaches to its own lease, with unlimited retries (1b.9). Then a workflow wait (D13): RFC-0005 Phase 7's ApprovalWorkflow (RFC-0006 §9.6) |

Add `PayloadSizeError` to `NON_RETRYABLE_ERROR_TYPES`. This holds for SDK 1.30.0; 1.34.0 removes that Python type, so recheck after the upgrade (RFC-0006 §16.3). Plugin exceptions are converted to returned results inside the activity (`plugin/base.py:683-761`), so plugin retry policies never see an HTTP 5xx or 429. The owner decides whether graph nodes retry transient errors (§17); if so, plugins raise a typed retryable error and keep returning envelopes for user errors.

### 9.3 Cancellation and heartbeats

Fix H2. Use `activity.cancellation_details()` to tell pause, reset, timeout and worker shutdown apart; re-raise in each case. For long activities, heartbeat a small checkpoint (`activity.heartbeat(details)`) and read `activity.info().heartbeat_details` on retry, as the browser and coding agents could.

### 9.4 Idempotency

Activities run at least once. Graph-node sends (Telegram, WhatsApp, email, HTTP POST) have no idempotency key or resend guard. Agent tool sends refuse a second attempt only for plugins with an approval spec whose `sends()` counts the call and that are not `restrict_while_asking`, and MCP tools only while their per-tool Ask is on; other side-effecting tools run again (RFC-0006 §11.2). T13 (RFC-0006 §3.1) extends the guard to every side effect, graph nodes included: a call that is not idempotent and broke off is recorded `unknown` instead of being run again (1b.1). The owner chooses at-most-once or at-least-once with provider idempotency keys (§17). Keys must not use the run id, which changes across continue-as-new and workflow retries; derive them from the workflow id and an explicit activity id that is unique across the continue-as-new chain. The SDK's default activity id is a per-run counter that restarts after continue-as-new (RFC-0006 §17.11).

### 9.5 Activity operations

Pause, unpause, reset and update-options let an operator stop a runaway activity without restarting anything (preview; server 1.28+; raw gRPC from Python for workflow activities). They are safe only after H2: today a paused node activity would report a completed failure. Document them in the runbook (§7.6); no product UI.

### 9.6 Standalone Activities

GA in server 1.32 and SDK 1.34.0 (SDK 1.30.0 already ships the client API, marked experimental): a client-started durable activity with its own id space and no workflow. It does not fit single-node direct runs: the Workspace task is a child of its controller, and a workflow cannot start a standalone activity; the agent branch is a child workflow; and the status writes after a failure need a workflow. Nor does it fit agents, controllers or chat runs, and Schedules cannot start it. The rule: keep a workflow whenever something must run after the activity fails. A possible use is the owner's Retry of an `unknown` send (RFC-0005 E12). Evaluate in Phase 4 (RFC-0006 §19.10).

## 10. Events, triggers and Schedules (D7, D8)

### 10.1 Routing without Visibility

1. The producer looks up live generations that subscribe to the event type in the app database (the control rows already hold the deterministic controller id and the graph snapshot).
2. It signals `workflow-control-<id>-g<gen>` by id, without a run id, with a few retries and backoff. A plain signal, not Signal-with-Start: a controller's existence is lifecycle state.
3. If no live generation subscribes, the event is recorded as "no consumer". If delivery fails, webhooks answer 5xx so the provider retries; chat marks the run `not_delivered`, as it does today.
4. Visibility stays for operations: reconcile jobs, status views, cleanup sweeps.

### 10.2 Limits at the edge and references

- A configurable body limit on `/webhook/*` (HTTP 413), one stored copy of the body, and a byte budget in `emit`.
- Bodies over a threshold become a workspace `FileRef`, like media.
- Trigger runs receive the graph by generation reference; node activities load parameters from the database, as they already do.

### 10.3 Cron

The owner chooses the overlap behavior (§17). For "skip while a run is going", let the Schedule start a workflow that awaits the run, or start the run directly; for "allow overlap", say so in the comment and set `ScheduleOverlapPolicy.ALLOW_ALL`. Drop the literal `{{.ScheduledStartTime}}` from action ids (Temporal appends the timestamp) and use the scheduled time, not `workflow.now()`, in the run id. Consider `jitter` for many crons on one minute, `pause_on_failure`, and alerts on `schedule_missed_catchup_window`.

### 10.4 Schedules by id

Compute Schedule ids from the generation snapshot and use `client.get_schedule_handle(id)` for describe, pause, update and delete; keep the `list_schedules` sweep only as a fallback that reports what it finds.

### 10.5 Polling triggers

- Return only the ids that were emitted or deliberately skipped.
- Move `post_emit` (mark-as-read) into its own activity after the workflow has recorded the event (patch gate).
- Retry a failed child start instead of marking it seen.
- Heartbeat timeout and per-cycle activity ids (patch gate).
- For inboxes polled every 30-60 s, the docs' "infrequent polling" shape (an activity that fails with a BENIGN "not ready" error and retries on a fixed interval) records no history per quiet cycle; today each quiet cycle costs about 11 events. T7 (RFC-0006 §3.1) adopts this shape for RFC-0005 Phase 3: one small workflow per poll trigger runs a single activity, which stores new items and its cursor in one transaction and ends with a BENIGN error, so Temporal retries it after the interval. The measurement becomes Phase 3's acceptance test (RFC-0006 §9.2).

## 11. Workers and performance (D15)

### 11.1 Topology and the event loop

| Profile | Recommendation |
|---|---|
| Desktop | Keep the embedded worker: the desktop stays one process. Protect the loop (§9.1), size the graceful shutdown inside the shell's deadline, and keep cancellation correct |
| Hosted tier S (container or VM, RFC-0005 §20) | Run Temporal workers in a second process, apart from uvicorn, as T8 (RFC-0006 §3.1) decides. First route the activities bound to the backend process to a queue only that process polls, and build every Worker with one builder (RFC-0006 §15.2, §15.3). Temporal recommends workers apart from application code; one more process also gives a second core |
| Hosted tier L | As RFC-0005 §20 sets it: an API process with the relay, a workflow worker and activity workers per class, on the instance's own open-source server, with unique identities and the same registration everywhere |

### 11.2 One registration function

Build the list of workflows, activities and plugins in one function used by the embedded manager, the pool, the standalone worker and the test `Replayer`. At boot, assert that every activity name a workflow can schedule is registered on the queue it targets: an unregistered activity fails as "Not Found", which Temporal retries, so it loops silently today.

### 11.3 LLM providers, rate limits and priority

- Put LLM and compaction steps on class queues with fixed slots and explicit routes, and no queue-wide cap. Providers limit requests and tokens per credential, which no queue-wide cap can express, so per-credential buckets in the app database enforce provider limits (RFC-0005 Phase 5; RFC-0006 §13.2). Use `max_task_queue_activities_per_second` (changeable at runtime with `temporal task-queue config set`, server 1.29+) only on a queue with one downstream and one fixed limit, and never on `machina-tasks`, where it turns off eager activity dispatch. A per-worker limit multiplies with worker count.
- Priority keys follow RFC-0005 §13 and RFC-0006 §13.3: every top-level workflow start sets an explicit key, and an unkeyed start runs at 3, RFC-0005's internal class (server 1.31+; Priority does not preempt running tasks). Fairness keys are per employee (RFC-0005 §13.2); `matching.enableFairness` ships with the fresh-start release (T17, RFC-0006 §3.1).

### 11.4 Tuning

- Sticky cache: local mode keeps 50 workflows (Docker 100, VMs 500). SDK 1.33's fix for sticky-poller stalls applies only to caches smaller than the workflow-task slot count (10), so it changes nothing here. Measure cache misses and forced evictions with long-lived controllers, and later with RFC-0005's resident entities, before keeping 50 (RFC-0006 §15.1).
- The docs recommend fixed-size slot suppliers "for most workloads" and resource-based ones for I/O-bound work. ai-heavy fits the latter; the browser queue, whose tasks use a lot of memory each, is the docs' counter-example. Use a fixed-size supplier there.

### 11.5 Shutdown

Workers are stopped by cancelling their run task; `Worker.shutdown()` is never called, and the SDK discourages cancel-based shutdown. Call `shutdown()` with the graceful timeout and await it. The worst case on desktop (about 35 s before the clean-shutdown marker, against a 65 s deadline) fits; keep the comments in `core/desktop.py:64-71` and `cli/_common.py:90-93` in step, including the Pi driver pool stop.

## 12. Data and payloads (D7)

### 12.1 Limits

| Limit | Warn | Hard | On breach |
|---|---|---|---|
| One payload | 512 KB (256 KB on some pages) | 2 MB | Workflow task fails, workflow stays open (SDK 1.23+) |
| One gRPC request (all commands of one workflow task) | | 4 MB | Execution terminated, no retry |
| History events | 10,240 | 51,200 | Terminated |
| History size | 10 MB | 50 MB | Terminated |
| Pending activities, children, signals sent, cancels | | 2,000 each (keep under 500) | Command fails, task retried |
| Signals received per run | | 10,000 | Further signals rejected |
| Updates per run | | 10 in flight, 2,000 total | |
| Search attribute value | | 2 KB | Workflow task fails |

### 12.2 By reference

Graphs, parameter snapshots and large events already exist in the app database or the workspace. Pass ids (generation, node, file) and load inside activities. This removes most of H10 and M6 without a new storage system. Agent transcripts are the exception: they stay in history, bounded by the byte budget, compaction and continue-as-new, as T6 (RFC-0006 §3.1) decides; §13.4 handles M11.

### 12.3 External Storage

External Storage is the SDK's built-in claim check (`ExternalStorage(drivers=[S3StorageDriver(...)], payload_size_threshold=256 * 1024)` on the data converter). It names AI agent conversations as a use case, but it is Public Preview with two breaking changes since 1.25, Temporal never deletes stored payloads ("If your Workflows run indefinitely ... there is no finite TTL that guarantees safety", which collides with our no-run-timeout policy), and the only desktop-fit driver is a local-disk driver documented as development-only. Do not adopt now; reopen when it is GA and a driver fits the desktop.

### 12.4 Codecs and encryption (Owner)

History in `temporal.db` holds prompts, transcripts, emails and error text in plain text, and the Web UI shows them. A `PayloadCodec` (AES-GCM, key id in the payload metadata) with `DefaultFailureConverterWithEncodedAttributes`, plus a codec server inside the backend for the Web UI, would encrypt them. Keys must live as long as the longest-running workflow, months, not only the retention window. Search attributes are never encoded: never put secrets or personal data in them. Recommendation: encrypt on Docker and VM installs; leave desktop installs, where the file sits beside the app database, unencrypted.

## 13. AI agents on Temporal

### 13.1 Our loop against Temporal's integrations

Temporal's Python integrations (OpenAI Agents, Strands, Deep Agents, Google ADK and GenAI, LangGraph) all run the framework's loop inside the workflow and make each model call one activity carrying the whole transcript; tools and MCP calls are activities; SDK client retries are turned off. OpenCompany's AgentWorkflow does the same with its own provider-neutral loop, plus a conversation store, cooperative stop, approvals and a token-streaming path. Keep the native loop; adopting a framework plugin would put a foreign loop, mostly in preview, into the sandbox-less workflow code.

### 13.2 Streaming

Every integration streams through Workflow Streams (Public Preview, SDK 1.27): batched signals in and Update long-polls out, all recorded in history and limited by the 10,000-signal and 2,000-Update caps. Our activity-to-WebSocket path records nothing in history and handles retried attempts with per-attempt segments; Temporal's own AI demo uses the same shape. Keep it. Use queries and `set_current_details` for coarse progress.

### 13.3 LLM retries

Already right: errors are classified, Retry-After drives `next_retry_delay`, provider retries are off. To add:

- The budget from §9.2, which ships with parking in RFC-0005 Phase 5.
- Read `x-should-retry` and decide 409: both the OpenAI and Anthropic SDKs honour that header and retry 409 (RFC-0006 §16.5).
- Honour Retry-After for at most 5 minutes per retry, keeping the provider's raw hint for the UI (RFC-0006 §16.5). A delay longer than the remaining step budget parks the turn (RFC-0005 Phase 5; RFC-0006 §11.5). There is no 60 s cap like the cookbook's.
- An OpenRouter HTTP 200 whose body is an error is classified as a non-retryable protocol error with a "check the base URL" message (`services/llm/providers/openai.py:142-172`); classify it by the inner code instead. (LLM-layer finding, not Temporal.)
- Turn off SDK retries at the three call sites in L4 only after Q9: until graph nodes retry transient errors through Temporal, one 429 would fail the node.

### 13.4 History growth

- Keep transcripts in history: they travel in LLM-step inputs, bounded by the byte budget, compaction and continue-as-new, as T6 (RFC-0006 §3.1) decides. Passing them by reference is not planned.
- Trim the continue-as-new input: drop the seed conversation, memory content and parameter snapshot, and keep `tools`, which the resumed run reads. Continue-as-new input is not compared on replay, so this needs no patch; it is item 1b.19 of RFC-0005 Phase 1b (RFC-0006 §16.8).
- No byte-based rollover check: every rollover check reads `is_continue_as_new_suggested()` first, and the server suggests continue-as-new at 4,096 events or 4 MiB.

### 13.5 Human in the loop

The cookbook's shape is a signal or Update that sets state, plus `workflow.wait_condition(..., timeout=...)`. Use an Update with a validator when the approver needs an immediate "accepted / too late / already decided". This is D13; it also applies to the Undo window, where an Undo Update can wake the 5 s wait early.

## 14. Testing (D16)

- **Replay corpus and CI gate** (§8.6).
- **Run the CLI-gated tests in CI** with the pinned CLI and full git history, on Linux at least; Windows and macOS if CI time allows, since the desktop targets them.
- **Time skipping** (`WorkflowEnvironment.start_time_skipping()`) for timers, catch-up windows, backoff and approvals; never-run paths such as TriggerListenerWorkflow, PollingTriggerWorkflow, EmployeeJobDeliveryWorkflow and real Schedules.
- **`ActivityEnvironment`** with `on_heartbeat`, `cancel()` and `worker_shutdown()` for the per-type wrapper (would have caught H2).
- **A visibility query contract test**: every query string the repo issues runs against the pinned server (server 1.32 changed the query converter; 1.33 removes the old one).
- **The determinism lint** (§8.1) and the **golden identity test** (§8.6).
- Workflow logic is mostly tested by monkeypatching `temporalio.workflow`; keep those tests, but every workflow type needs at least one real-SDK test.

## 15. Evaluated and not adopted (D17)

| Feature | Why not now | Reopen when |
|---|---|---|
| Nexus | On self-hosted Temporal the Nexus registry is per cluster, so with one Temporal per owner Nexus could only join namespaces of one instance, which task queues and priority already separate (RFC-0006 §19.1) | An instance must call another Temporal cluster, and external-URL endpoints have left pre-release |
| Serverless workers | Require Worker Versioning and Temporal-invoked compute | A Cloud-hosted tier |
| Worker Versioning | Needs old builds to keep polling until drained; a single-process desktop cannot | Distributed installs with side-by-side deploys; then PINNED for short runs and upgrade-on-continue-as-new for entities |
| Workflow Pause | Pre-release; server 1.30+ plus a flag; no Python API; rejects Updates and Queries while paused, which the controller needs | GA with Update support |
| Workflow Streams for tokens | Every chunk lands in history | Never for tokens; maybe for coarse progress |
| Framework plugins (OpenAI Agents, Strands, ADK, LangGraph) | Foreign loops, mostly preview, tight upstream pins | Never as the core loop |
| External Storage | Preview; no deletion; no desktop driver | GA with a fitting driver |
| Dual Visibility | A migration tool for real servers | A move to a production server |
| Local activities | Reopened on 2026-10-24: progress broadcasts cost 205 ms before an agent's first LLM step and are 35-52% of an AgentWorkflow history, so they become best-effort local activities for new generations (RFC-0006 §14.2). Never for LLM steps, tools or sends; other steps only after measurement (whole-task re-runs; signals wait) | Reopened |
| Standalone Nexus Operations, NexGen | Pre-release | |

## 16. Phases

**Phase 0: defects that change no workflow command.**
1. Security (D18): enforce webhook authentication and a non-empty path; scope task-completion events; keep secrets and raw headers out of workflow inputs; owner check on `temporal_start` and `temporal_stop`.
2. Restart safety (C4, H9): start-node runs get a deterministic per-generation id with `REJECT_DUPLICATE` and are skipped on re-arm; recovery pauses run in the background with Update deadlines; `NOT_FOUND` is decoded before a generation is failed.
3. Edge limits: webhook body cap and one stored copy.
4. Retries and timeouts (D11): an explicit policy on every activity (unlimited only where AG9 allows it), `PayloadSizeError` non-retryable, the legacy tool path uses declared policies. The LLM step budget ships with parking in RFC-0005 Phase 5 (§9.2). Routed queues get monitoring, not `schedule_to_start` (M8).
5. Cancellation (H2, H12): re-raise in node activities; Reset also acts on `live_roots`; the Pi inline runner is started with `EventWorkflowId`.
6. Schedules by id; search-attribute registration fails loudly.
7. Worker parity: one registration function; refuse a divergent standalone worker; correct the pool-flag rollback text.
8. Docs and comments listed in §20.

**Phase 1: the safety net.**
1. D1 and D4: pin the CLI with checksums, database marker, retention, explicit flags and dynamic config.
2. D5: SDK 1.34.0, after recording 1.30.0 fixtures.
3. D6: metrics, `TemporalReportedProblems`, the alert table, "needs attention" in the status view.
4. D16: replay corpus and CI gate, golden identity test, CLI-gated tests in CI, time-skipping and activity-environment tests, query contract test, determinism lint.
5. D8 client side: route events from the app database.

**Phase 2: fixes that change commands, behind patch gates.**
1. Freeze dispatch into run input.
2. Controller: size check and dead-letter, slim carry, triggers in the start input, periodic rollover.
3. Agent children `REQUEST_CANCEL`, cancel-and-drain on cancellation and one failure policy (§8.4), workflow loops re-raise cancellation; Reset cancels before terminating.
4. Cron overlap (owner choice).
5. Polling: `post_emit` after record, per-cycle ids.
6. D12 and D13 per the owner's answers.
7. AgentWorkflow: nothing remains here. Transcripts stay in history, as T6 (RFC-0006 §3.1) decides, and the trimmed continue-as-new input needs no patch, so it moved to RFC-0005 Phase 1b as item 1b.19 (§13.4).

**Phase 3: features.** Web UI labels; BENIGN; LLM class queues with fixed slots (§11.3); Priority for chat; heartbeat checkpoints; the sandbox for small workflows; the operator runbook with activity operations. There is no eager-start experiment for chat runs: they start as children or by Signal-With-Start, and neither can be eager (§4.2).

**Phase 4: production platform (Owner).** D2 and D3; Temporal Cloud and TLS support; payload codec; Worker Versioning for distributed installs; Standalone Activities and External Storage evaluations.

## 17. Owner decisions

The owner answered on 2026-10-16. Further answers came on 2026-10-23 while designing [RFC-0005](./RFC-0005-EVENT-OS-ON-TEMPORAL.md), which implements this RFC's event-path decisions. Answers recorded here are decisions. For questions still open, the recommendation is the working assumption.

| # | Question | Options | Recommendation | Answer |
|---|---|---|---|---|
| Q1 | Upgrade contract (D10): must runs that are open or paused survive an upgrade to the next release? | Always (N-1 replay gate) / Within a release line; major releases may require Reset / Any release may require Reset | Always, for N-1; a release that cannot honour it must say so and use D2 option B | Always, for N-1 (2026-10-16) |
| Q2 | Server for durable installs (D3) | Pinned dev server with SQLite everywhere / PostgreSQL for Docker and VM / also support Temporal Cloud | Pinned dev server on desktop; PostgreSQL for Docker and VM; Cloud later | 2026-10-16: pinned dev server with SQLite. Superseded on 2026-10-23 (RFC-0005 E1-E3): one instance per owner with free Temporal only, no Temporal Cloud. The dev server runs up to about 100 employees; above that, a hosted instance runs the open-source server on its own PostgreSQL. Hosted instances use PostgreSQL for the app database |
| Q3 | Moving an existing database (D2) | Keep its binary / recreate and re-arm / PostgreSQL | Recreate and re-arm on desktop, with notice | Superseded on 2026-10-23 (RFC-0005 E16). Every install moves to the pinned server, and nothing is migrated: a database without a marker or from another server minor is deleted together with the old CLI, the install starts empty, and owners Reset and Start their employees. Before that, on 2026-10-16: keep the install's current server; moving is opt-in. RFC-0005 §20 defines the dev-server-to-full-server move as a re-arm in the maintenance window |
| Q4 | Failed runs (D12) | Temporal Failed / Completed plus `RunOutcome` | Temporal Failed | Temporal Failed (2026-10-16) |
| Q5 | Bugs in workflow code (D12) | Stuck and visible until a fix / fail fast | Stuck and visible | Open |
| Q6 | LLM step budget (D11) | A total budget per step (for example 30 min) / unlimited with alerts / unlimited for outages, capped for timeouts | A total budget, with longer attempts for local models | A total budget per step (2026-10-16) |
| Q7 | Event delivery (D8) | Guaranteed (5xx and retries) / best effort without Visibility / keep today's | Guaranteed | Guaranteed (2026-10-16); designed in RFC-0005 §8-§9 |
| Q8 | Graph-node sends (§9.4) | At-least-once with idempotency keys / at-most-once / per plugin | At-least-once with keys where the provider supports them, at-most-once otherwise | As recommended. Where a provider has no idempotency key, an uncertain send is shown as `unknown` with Retry and is never resent automatically (2026-10-23, RFC-0005 E12) |
| Q9 | Transient errors in graph nodes (§9.2) | Retried by Temporal / returned as failures (today) | Retried, for plugins marked safe | Open |
| Q10 | Human waits (D13) | Workflow messages and timers / keep activities | Workflow messages and timers | Update plus durable timer (2026-10-16); RFC-0005 §17.5 |
| Q11 | Cron overlap (§10.3) | Skip while a run is going / allow overlap / buffer one | Skip | Open. Cron stays on Temporal Schedules (2026-10-23, RFC-0005 E5); RFC-0005 defaults overlap to skip |
| Q12 | Preview features in shipped builds | Behind a flag, off by default / where they solve a problem / GA only | Behind a flag, off by default | Behind a flag, off by default (2026-10-16) |
| Q13 | History encryption and retention (§12.4, D4) | Encrypt on Docker and VM, 7 days / no encryption, 7 days / other | Encrypt on Docker and VM, 7 days | Open. Telemetry is metrics and errors only (2026-10-23, RFC-0005 E14) |
| Q14 | Supported topologies (D15) | Embedded only / plus a separate worker process / plus multi-backend distributed mode | Embedded plus a separate worker process; distributed later | One instance per owner (2026-10-23, RFC-0005 §20). The desktop stays one process; hosted tier S runs Temporal workers in a second process, as T8 (RFC-0006 §3.1) decided on 2026-10-24, replacing the 2026-10-23 answer that kept small hosted instances embedded; tier L follows RFC-0005 §20 |
| Q15 | Startup verification of open runs (§8.6) | Refuse to start workers on failure / start and mark "needs Reset" / none | Start and mark "needs Reset" | Start anyway and mark the affected generations "needs Reset"; a failed readiness check still rolls back (2026-10-24, T14 in RFC-0006 §3.1) |
| Q16 | Start nodes (C4): when does a deployment's Start node run? | Once per generation / again after every restart (today) | Once per generation | Once per generation (approved with RFC-0005 Phase 0.4, 2026-10-23) |

## 18. Acceptance gates

| Gate | Check |
|---|---|
| AG1 | The CLI download URL names an exact version and `known_hash` is set; a test fails on "latest" |
| AG2 | Starting with a database created by another server minor, or by no recorded server, deletes it and starts empty (owner, 2026-10-23) |
| AG3 | Retention is set to the configured value after namespace creation (integration test) |
| AG4 | A metrics endpoint or buffer exists; the alert table's signals are emitted in a test run |
| AG5 | `TemporalReportedProblems` turns a failing workflow task into "needs attention" in the status view |
| AG6 | CI replays every fixture of the previous release with the production workflow list and fails on any error |
| AG7 | The golden identity test fails when an activity name, child id format or marker changes without a patch entry |
| AG8 | The determinism lint passes for every workflow module |
| AG9 | Every activity call site has an explicit retry policy. No policy on a paid or side-effecting call is unlimited without a time budget; the LLM step and compaction are the one declared exception in generations without RFC-0005 Phase 5's step budget (`scheduler_version=1`), as T18 (RFC-0006 §3.1) decides. Every unlimited policy (bookkeeping, compensations, queue paging, permit waits, RFC-0006 T7's poll activities, sends and deliveries made at-most-once by their claim) is declared and idempotent, and all but the polls and permit waits, whose retries are planned, are covered by the retry alert (§7.1; RFC-0006 §11.1) |
| AG10 | A cancelled node activity is recorded as Cancelled (ActivityEnvironment test) |
| AG11 | A webhook without the configured header gets 401; one over the size cap gets 413 |
| AG12 | An event accepted 10 ms after the generation's Start committed is delivered to its controller once the generation runs (no Visibility on the path). "Started" means the Start commit (RFC-0006 §7.2) |
| AG13 | A slow cron run causes the next tick to be skipped (or, if the owner chooses overlap, the policy says so) |
| AG14 | A standalone worker with a different registration refuses to start |
| AG15 | Every query string in the repo runs against the pinned server (contract test) |
| AG16 | The upgrade from the previous release keeps a paused generation resumable (end-to-end, with the pinned CLI) |
| AG17 | Restarting the backend twice starts no second start-node run for a running or paused generation |
| AG18 | Reset cancels, then terminates, every execution of the generation, including Pi inline runners reached through `live_roots` |

## 19. What this RFC does not change

- The plugin model, the WebSocket protocol, the chat protocol and RFC-0002's conversation store.
- The cooperative pause design; native Workflow Pause stays unused (§15).
- The decision to keep workflow execution and run timeouts unset.
- The provider-neutral agent loop (§13.1).

## 20. Docs and comments to correct

| Where | Today says | Should say |
|---|---|---|
| CLAUDE.md, durability item 2 | Option changes need a patch | §8.5 rule |
| CLAUDE.md, Temporal heartbeats note | Heartbeats on every non-matching broadcast | That branch is dead; the 30 s receive timeout is the heartbeat |
| CLAUDE.md, cron naming row | `<slug>-<label>-<ScheduledStartTime>` | The real ids (H6) |
| CLAUDE.md and `services/deployment/handlers.py:1520` | Native pause needs server 1.28+ | Server 1.30+ plus `frontend.WorkflowPauseEnabled` |
| `.env.template:100-107` | The pool flag rolls back cleanly | It affects new runs only |
| `.env.template:147-149` | Every workflow stays visible | Closed workflows are deleted after the retention period |
| `.env.template:212-216`, `core/config.py:179-183` | `EVENT_FRAMEWORK_ENABLED=false` rolls back | It no longer does (event_framework.md:54-56) |
| `services/temporal/_runtime.py:124` | `--metrics-port 0` disables metrics | It picks a random port |
| `services/temporal/schedules.py:187-189` | SKIP drops a firing while the run is going | See H6 |
| `services/temporal/agent_activities.py:12-13` | LLM step lives on ai-heavy | It runs on `machina-tasks` |
| `services/temporal/agent_workflow.py:2445-2447` | One-shot retry | Unlimited |
| `services/temporal/client.py:126-129` | Search attributes fall back to broadcast-only | Starts fail without them |
| `services/media/limits.py:14` | `PayloadSizeWarning` | Removed in SDK 1.31 |
| TEMPORAL_ARCHITECTURE.md:962 | 9 live markers | 19, with a table (§8.6) |
| TEMPORAL_ARCHITECTURE.md:771, temporal-workflow-control.md:622-623 | Fixtures cover graph, agent and invocation histories | Two agent fixtures, one orphaned |
| temporal-workflow-control.md:653-656 | Boot re-arm is idempotent | Not for start nodes (C4) |
| event_framework.md:439 | The filter activity exists because of the sandbox | Every workflow is unsandboxed |
| `server/routers/webhook.py:8-9` | Plugins pull from `source.emit()` | Nothing does (L5) |

## 21. References

Python SDK guide: https://docs.temporal.io/develop/python (workflows, activities, workers, client, testing-suite, python-sdk-sandbox, python-sdk-sync-vs-async, error-handling, message-passing, continue-as-new, child-workflows, cancellation, schedules, versioning, workflow-streams, data-conversion, data-encryption, external-storage, observability, enriching-ui, interceptors, standalone-activities, integrations).
Concepts: https://docs.temporal.io/visibility, /search-attribute, /schedule, /patching, /workflow-execution/limits, /encyclopedia/retry-policies, /encyclopedia/detecting-workflow-failures, /encyclopedia/workflow/cancellation-and-termination, /encyclopedia/workflow/workflow-pause, /activity-operations, /standalone-activity, /external-storage, /worker-versioning, /task-queue, /handling-messages.
Operations: https://docs.temporal.io/troubleshooting/blob-size-limit-error, /troubleshooting/execution-failures, /troubleshooting/schedule-missed-actions, /references/sdk-metrics, /references/dynamic-configuration, /best-practices/worker-alerting, /best-practices/worker, /develop/worker-performance, /develop/task-queue-priority-fairness, /develop/safe-deployments.
Self-hosting: https://docs.temporal.io/temporal-service/persistence, /self-hosted-guide/visibility/sqlite, /self-hosted-guide/upgrade-server, /self-hosted-guide/embedded-server, /cli/command-reference/server.
Design patterns: https://docs.temporal.io/design-patterns (approval, entity-workflow, continue-as-new, signal-with-start, early-return, worker-specific-taskqueue, polling, downstream-rate-limiting, priority-task-queues, fairness).
AI: https://docs.temporal.io/ai, /ai/cookbook, /develop/python/integrations.
Releases: https://github.com/temporalio/sdk-python/releases (1.25.0 to 1.34.0), https://github.com/temporalio/cli/releases, https://github.com/temporalio/temporal/releases.

## 22. Corrections log

**2026-10-24, RFC-0006**

- Header and "How to read this": the status line notes this correction; updated line numbers are as of commit 4f73cb10; ids such as 1b.9 name items of RFC-0005 Phase 1b.
- §3 D11: a time budget on every paid or side-effecting activity, not on every activity (AG9).
- §4.2: `get_current_history_size()` is a backstop only; `start_delay` does not fit the Undo window; queue-wide caps fit only a single-downstream queue; eager start is not applicable; Standalone Activities do not fit direct node runs.
- §5 H4, M9 and §20: line references updated to 4f73cb10.
- §5 H7: the worker pool always starts, and the flag selects only the routing of new runs.
- §5 H10: a failed controller fails a versioned generation and only Reset recovers it; the `USE_EXISTING` rebuild is for unversioned generations; RFC-0005's supervisor is rebuilt in place (T20).
- §5 H12: Reset releases a browser claim only within retention and for its own generation; the fix follows §8.4.
- §5 M4: three 31-day delivery sites, the stranded draft, interim fix 1b.9, then RFC-0005 Phase 7's ApprovalWorkflow.
- §5 M8: no schedule-to-start on owner queues or the Pi driver queue; monitor them and raise an attention item.
- §6.6: name the HTTP and metrics ports, pin a loopback metrics port, drop the standalone-activity flag on the dev server, and pass the `TemporalReportedProblems` value with the SDK metrics (1b.38).
- §6.7, §11.4: SDK 1.33's sticky-poller fix does not apply to OpenCompany's caches, so it is no reason to upgrade.
- §7.1: the retry-storm alert covers compaction, paging and compensations, and excludes permit waits and planned poll retries.
- §7.3: adding `summary=` needs no patch.
- §8.1: the lint also requires re-raising a workflow bug before exit cleanup.
- §8.2: `asyncio.timeout` bounds the whole Update call; Workspace submissions keep random ids.
- §8.3: idle controllers never roll over but busy versioned ones do; the history-size check is a backstop; rollover at each release's first boot.
- §8.4: cancel and drain on cancellation only; on failure nothing new starts; Reset closes team tasks, employee jobs and permits (1b.21).
- §8.5: a cancellation type change can need a patch; the zero-duration timer rule is on the workflow-definition page.
- §8.7: "stuck but visible" holds only with the bug guard (1b.13).
- §9.2: LLM step defaults and their phase (T11, T18); a declared bookkeeping policy; no schedule-to-start on routed queues; employee delivery; `PayloadSizeError` after SDK 1.34.0.
- §9.4: which tool sends refuse a second attempt; T13 extends the guard; idempotency keys use chain-unique activity ids.
- §9.6: Standalone Activities do not fit direct node runs; keep a workflow whenever something must run after the activity fails.
- §10.5: T7 adopts the infrequent-polling shape for RFC-0005 Phase 3.
- §11.1, §17 Q14: the desktop stays one process; hosted tier S runs workers in a second process (T8); tier L follows RFC-0005 §20.
- §11.3, §13.3: class queues with fixed slots and per-credential buckets; priority keys per RFC-0005 §13; Retry-After bounded by the step budget; `x-should-retry` and 409; SDK retries off only after Q9.
- §12.2, §13.4, §16 Phase 2 item 7: transcripts stay in history (T6); the carry trim keeps `tools` and moved to 1b.19; no byte-based rollover check.
- §15: local activities reopened; a new reason and reopen condition for Nexus.
- §16 Phases 0, 2 and 3: aligned with the corrections above; the eager-start experiment is dropped.
- §18 AG9, AG12: unlimited policies reworded, with the T18 exception; "started" means the Start commit.
- §4.2, §6.6, §11.3: `matching.enableFairness` and one task-queue partition ship with the fresh-start release (T17).
- §13.3: Retry-After is honoured for at most 5 minutes per retry; a longer wait than the step budget parks the turn.
- §17 Q15: answered by T14 (start anyway, mark "needs Reset"; a failed readiness check still rolls back).
- Each change, with its evidence, is listed in RFC-0006 §21.
