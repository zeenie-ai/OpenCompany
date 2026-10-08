# Event Framework and Durable Trigger Queues

> **Current architecture:** This document retains the Wave-12 rollout history.
> Controlled deployments now consolidate push registration, event Signals, and
> polling activities inside `WorkflowControlWorkflow`; see
> [workflow control contract](temporal-workflow-control.md). Listener
> workflows described in the phase log are legacy compatibility contracts.

Temporal-native event-routing layer for OpenCompany. Implements RFC sections
6.3 (Temporal worker contract) + 6.4 (CloudEvents broadcast contract) from
[plugin_authoring_rfc.md](./ARCHIVE/plugin_authoring_rfc.md).

This is the operator and plugin-author reference. The phase log below is
historical; the current control and queue contract is documented here and in
[Temporal architecture](TEMPORAL_ARCHITECTURE.md). The original design is in the
[archived execution RFC](ARCHIVE/temporal-execution-engine-rfc.md).

## Status (2026-05-15)

### Shipped

| Phase | State |
|---|---|
| A1-A9 — Temporal primitives + core CloudEvents envelope compliance | ✅ commit `c3dc85a` (16 tests; legacy extension-name debt documented below) |
| A7 completion — `_pop_matching_event` helper | ✅ commit `0e835e2` (4 tests) |
| B1-B10 — plugin-owned `_events.py` modules (9 plugin folders) | ✅ commits `7e4ff7b` / `c4d9428` / `da63d73` / `de8be88` |
| C1 canary (webhookTrigger) — `TriggerListenerWorkflow` | ✅ commit `c24bc62` (25 tests) |
| C1 rollouts — chat / task / telegram / whatsapp | ✅ commits `850cc9d` / `0d406b9` / `688f686` / `b4db7da` |
| C1 architecture pivot — plugin-self-registered `canary_registry` retires tribal frozenset | ✅ commit `688f686` |
| C2 canary (googleGmailReceive) — `PollingTriggerWorkflow` + `as_poll_activity` | ✅ commit `00dbf10` (10 tests) |
| C3 canary (cronScheduler) — Temporal Schedule + plugin-owned `CronTriggerWorkflow` via `SimplePlugin` | ✅ commit `9314aff` (17 tests) |
| C4 sub-piece A — `social_provider_registry` closes `nodes/social→nodes/whatsapp` | ✅ commit `d1cc33c` (8 tests) |
| C4 sub-piece B — `shutdown_hooks` registry closes 2 reaches in `main.py` lifespan + `IdempotentRegistry` reload-tolerance fix | ✅ commit `4912239` (10 tests) |
| C4 sub-piece C — `service_factories` registry closes `core/container.py` top-level imports | ✅ commit `73c5f08` (9 tests) |
| D1 — Shared `_retry_policies` + `NodeUserError` non-retryable | ✅ commit `751ab94` (11 tests) |
| D3 — Visibility admin WS handlers (`list_canary_listeners` / `list_canary_schedules` / `get_workflow_failure_history`) | ✅ commit `aebfb35` (13 tests) |
| D5 — Auto-gen `DEFAULT_TOOL_NAMES` from `ToolNode` ClassVars | ✅ commits `a01f590` / `07906f0` / `899771c` (78 plugin classes + golden fixture + 3 invariant tests; 75 passed) |
| B11 — FE `plugin_connection_status` envelope handler | ✅ commit `899771c` |
| D4 — Drop legacy `*_status` raw frames (status-only round) | ✅ commit `5ea4e90` (whatsapp/android/telegram status retired; FE consumes typed channel) |
| Canary flag default flip + invariant lock | ✅ shipped (default `True`; the `EVENT_FRAMEWORK_ENABLED=false` rollback no longer works, see "Rollback (known gap)" below) |
| Wave 13 — EventType SA mismatch fix + canary-only emit path + trigger status lifecycle + polling OOM + template resolution + cancel sweep | ✅ shipped (see "Wave 13 fixes" below) |

### Dropped / Deferred / Pending

| Phase | State |
|---|---|
| D2 — Custom `event_dlq` SQLModel table | ❌ **dropped** (commit `89b15bd`, docs only). Temporal Event History + Visibility queries cover the ops-inspection use case; reinventing them would contradict Wave 12's "Temporal-native, no custom infra" thesis. See § "Failure inspection — no separate DLQ table". |
| D2b — Retire `event_waiter.py` Redis-Streams branch | ✅ shipped as Wave 15.3 (see [TEMPORAL_CLEANUP_AND_RESILIENCE_PLAN.md](./ARCHIVE/TEMPORAL_CLEANUP_AND_RESILIENCE_PLAN.md)). `event_waiter` is memory-mode-only now; Temporal owns durable delivery. The in-memory collector still backs canvas-Run + non-canary (Twitter) triggers. |
| D4 — Drain the last legacy `event_waiter.dispatch` dual-emit | ⏳ pending — only `nodes/twitter/_events.py` remains (twitterReceive is not canary-registered). The whatsapp message/newsletter/history wire keys are now one-way legacy WS frames for FE readers, not dual-emit. |
| WorkflowEnvironment integration smoke test | ⏳ pending — every canary type against an in-process Temporal cluster. Existing unit tests + per-canary producer tests + `TestCanaryRegistryCoverage` cover the static surface; the integration smoke would catch real-cluster regressions only. |

`Settings.event_framework_enabled` gates the new dispatch path. **Default flipped to `True` on 2026-05-15** — the Temporal-Signal consumer fan-out is now production-default. With the flag off, `services.events.dispatch.emit` is a pass-through no-op (logged at DEBUG as `event-framework disabled — emit no-op`), and `DeploymentManager._canary_listener_enabled_for` sends canary trigger types back to the in-process collector (`trigger_manager.setup_event_trigger`).

### Rollback (known gap)

`EVENT_FRAMEWORK_ENABLED=false` is **not** a working rollback any more. The in-process collector fires only on `event_waiter.dispatch(...)`, and most canary producers no longer call it: their `_events.py` calls `dispatch.emit(...)` alone (chat, webhook and email say so in their module docstrings). With the flag off, those deployed triggers never fire, and the in-process WebSocket broadcast that `emit` also performs stops too. The only remaining `event_waiter.dispatch` callers are `nodes/twitter/_events.py`, `StatusBroadcaster.send_custom_event`, and the plugin `WebhookSource` intake in `services/events/webhook.py`. Restoring a rollback would mean a dual-dispatch in `emit` itself; nothing does that today, so leave the flag on.

Locked by `tests/test_event_framework_phase_a.py::TestEventFrameworkEnabledDefault::test_event_framework_enabled_defaults_true` (source-introspection check that the `Field(default=True, ...)` declaration is present) and `TestCanaryRegistryCoverage::test_seven_canary_types_registered`, which asserts that the seven original canary types are registered. It is a floor, not the full list: later plugins register too. For the live set, run `grep -rn "register_canary_trigger_type(" server/nodes`.

## What this framework does

Push producers build a `WorkflowEvent` and call `dispatch.emit`. Routing uses
Temporal Visibility to find running legacy `EventType` consumers and generation
controllers. A controller advertises `ControlEventTypes` and matches accepted
Signals against its registered trigger definitions. Polling controllers instead
call provider Activities and enqueue their returned events; cron uses Temporal
Schedules and the plugin's `CronTriggerWorkflow`.

Visibility is eventually consistent. The dispatch helper logs and suppresses
Temporal connection, lookup and individual Signal failures, and returns the
envelope rather than a delivery receipt. Its return therefore does not prove
that a consumer accepted the event. Once a Signal RPC succeeds, Temporal stores
the Signal durably; successful acceptance still does not acknowledge handler
processing or graph execution. There is no producer outbox in this path.

Controller queues preserve accepted events across Stop, worker restart and
continue-as-new. Deduplication is an application key contract, supplemented by
deterministic child Workflow IDs; Workflow ID policies do not deduplicate
arbitrary Signal payloads. Existing SQL overflow pages and atomic read receipts
are reused for queue spill, without adding an event bus or separate DLQ.

## Architecture

```
Inbound source (FastAPI process)
       ↓
services/events/dispatch.py:emit(event: WorkflowEvent)
       ├─→ Temporal Visibility: legacy listeners + generation controllers
       ├─→ on_event Signal to matching consumers
       │    └─→ controller queue → admitted graph child
       └─→ status_broadcaster.broadcast() — direct in-process WS fan-out
           (skipped with emit(..., broadcast=False))
```

**Private payloads skip the broadcast.** The broadcast reaches every
connected socket. An event whose payload is one owner's content passes
`broadcast=False` and reaches only the signalled workflows: chat does, since
its envelope carries the owner's message text (clients learn of a new
message from the identity-only `chat.updated`).

**Workflow-scoped delivery** (core dispatch rule, July 2026): an envelope
whose `workflow_id` field is set by its producer's call site reaches ONLY
that workflow's consumers — `_signal_running_consumers` appends
`AND EventWorkflowId='<id>'` to both the listener clause and the controller
clause of the Visibility query (every consumer shape carries that Search
Attribute). Unscoped envelopes (telegram / webhook / gmail — genuinely
broadcast semantics) are unchanged. First producer: chat — the panel is
bound to one workflow (`session_id` IS its workflow id), and without the
scope one workflow's chat message fired every deployed workflow's
chatTrigger. The scoping DECISION lives in the core call site
(`services/chat/handlers.py:handle_send_chat_message`; the legacy
`"default"` session stays unscoped) and the narrowing in core dispatch —
event factories (the chat one is `services/chat/events.py`, source
`opencompany://services/chat`) only plumb the field of their own wire
shape. The chat message is emitted with `broadcast=False` (it carries the
owner's text), and its CloudEvent id is the chat run it started, so the
listener's child run id is predictable; chat run events (`chat_run_event`)
never ride `emit` or the broadcast at all: `services/chat/hub.py` delivers
them only to sockets subscribed to the session
([chat_protocol.md](./chat_protocol.md)).

Worker is embedded in the FastAPI process (the `main.py` lifespan schedules
`run_temporal_lifecycle` from `services/temporal/lifecycle.py` as one
`asyncio.create_task()` named `temporal-init`; that module owns the connect loop and the
`TemporalWorkerManager` / `TemporalWorkerPool` start). Activities
and the WebSocket connection pool share memory + event loop, so the fan-out
to FE clients is a direct in-process call — no Redis Streams hop required.

## Search Attributes setup

The framework requires 7 custom Search Attributes on the Temporal
namespace. Registration is **idempotent + automatic on Temporal client
connect** (`services/temporal/client.py:TemporalClientWrapper.connect`):

| Attribute | Type | Used for |
|---|---|---|
| `EventType` | KEYWORD | Visibility query — find consumers by CloudEvents type |
| `EventSource` | KEYWORD | Routing when same type arrives from multiple sources |
| `EventWorkflowId` | KEYWORD | Scope events to a OpenCompany workflow_id |
| `TriggerNodeId` | KEYWORD | Per-trigger event-history queries |
| `EventTriggerKind` | KEYWORD | Coarse classification (webhook / polling / …) |
| `EventReceivedAt` | DATETIME | Time-range queries |
| `ControlEventTypes` | KEYWORD_LIST | The push event types a `WorkflowControlWorkflow` currently has triggers for (upserted as triggers register); `dispatch.emit` skips controllers whose list does not contain the event type. Absent on pre-upgrade histories = match-all. |

Declarations live in
[`services/temporal/search_attributes.py:EVENT_SEARCH_ATTRIBUTES`](../server/services/temporal/search_attributes.py).
Single source of truth — add an entry to that list and registration
picks it up next connect.

### Manual registration (if needed)

If the auto-registration on connect fails (e.g. permissions on a managed
Temporal Cloud namespace), register manually via the Temporal CLI:

```bash
temporal operator search-attribute create \
  --namespace default \
  --name EventType \
  --type Keyword
# ... repeat for the other 6 attributes (ControlEventTypes is --type KeywordList)
```

### Verification

```bash
temporal operator search-attribute list --namespace default
```

Should show the 7 framework attributes alongside Temporal's built-in
default attributes (`WorkflowType`, `WorkflowId`, `ExecutionStatus`, …).

## Temporal contract for plugin authors

### Event keys and accumulator behavior

The controller borrows durable buffering, stable keys and continuation-state
carry from Temporal's [Event Accumulator pattern](https://docs.temporal.io/design-patterns/event-accumulator).
The complete inactivity-window batching pattern is unsuitable here: triggers
process individual events immediately when admission is open. Stop holds the
queue; Resume admits it in FIFO order without replaying completed graph work.

New version 1 histories use `controller-event-accumulator-v1`. Pending keys are
protected separately from the bounded recent-completion cache, including an
event currently being dispatched. Restored overflow pages retain their earlier
FIFO position over a same-key live redelivery. Continue-as-new rechecks arrivals
after every spill and after message handlers finish, then carries or spills the
accepted tail. Reset's close flag prevents rollover from reopening the controller.

Use stable source identities for redelivery of the same occurrence:

| Producer | Event ID |
|---|---|
| Telegram | `telegram:{chat_id}:{message_id}` |
| Discord gateway message | `discord:message:{message_id}` |
| Discord interaction | `discord:interaction:{interaction_id}` |
| WhatsApp | `whatsapp:{direction}:{chat_id_or_sender}:{message_id}` |
| Chat | Tracked run ID, otherwise saved message ID |

A missing component retains a fresh generated ID rather than collapsing
unrelated messages. Provider event factories own these mappings; do not generate
random IDs inside a Workflow. Dedup is bounded, not a permanent occurrence ledger.
An already-started deterministic child is a successful duplicate admission;
transient child-start failures keep the original event pending for retry.

Signal-With-Start is appropriate when creating a collector is part of the
producer contract. Here Start owns generation creation and execution-root
membership. Recreating a lost version 1 controller via Signal-With-Start would
produce an empty registry, so producers signal the existing controller and its
loss fails closed. Legacy deployment recovery remains separate.

See [workflow messaging](temporal-workflow-control.md#workflow-messaging-review)
for completed control Updates and read-only status Queries. The Signal queue
and controller rollover tests are listed in the
[verification guide](temporal-workflow-control.md#verification-and-operations).

### Activity policy and Stop boundaries

| Class attribute | Purpose | Default |
|---|---|---|
| `start_to_close_timeout` | Per-attempt budget (one activity execution) | Kind-base default: ActionNode=10m, TriggerNode=24h, ToolNode=10m |
| `retry_policy: RetryPolicy` | Backoff + max attempts + non-retryable error types | `DEFAULT_RETRY` — 3 attempts, 1-60s exponential. `NodeUserError` is auto-non-retryable. |
| `heartbeat_timeout` | Max idle between `activity.heartbeat()` calls | 2 minutes |
| `task_queue` | Worker pool routing | `TaskQueue.DEFAULT` |

Override only when the kind-base default doesn't fit; an inline comment
explaining why is required (enforced by
`tests/test_plugin_contract.py::TestStartToCloseTimeoutOverridesAreCommented`).

Ordinary agent tool dispatch carries the resolved plugin's timeout, heartbeat,
retry and task-queue policy in prepared tool bindings. Routing also respects the
run's frozen worker-pool setting. Node and tool operations remain regular
Activities. Stop closes admission for the next operation and lets admitted
operations finish under their existing retry policies, including bookkeeping.
Resume consumes recorded results and continues from the next pending action.

Heartbeats track liveness, enable cancellation delivery and can record real
recoverable progress after failure. A status string cannot checkpoint an opaque
HTTP request, subprocess or provider session. Do not add a generic heartbeat
cursor or cancel the current tool to implement Stop. See the
[node authoring contract](node_creation.md) and
[long-running Activity guidance](https://docs.temporal.io/design-patterns/long-running-activity).

### Worker graceful shutdown

Workers honor SIGTERM with a configurable grace window
(`Settings.temporal_graceful_shutdown_seconds`, default 30s, override via
env `TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS`). Activities mid-flight finish
or hand back to the server for retry instead of being killed mid-call.

## CloudEvents envelope

Domain/lifecycle broadcasts wrap `WorkflowEvent` (CloudEvents v1.0;
`services/events/envelope.py`). High-frequency state projections such as
`node_status`, output, variables, and log streams remain the explicitly tested
telemetry carve-out; they are reconnect snapshots, not lifecycle occurrences.

New envelopes use the required `specversion`, `id`, `source`, and `type`
attributes, a reverse-DNS `type`, and an exact entity `subject`. Application
scope such as `workflow_id`, `execution_id`, and `root_execution_id` belongs
inside `data`. CloudEvents requires extension-attribute names to contain only
lowercase ASCII letters and digits, so the historical top-level
`workflow_id`, `trigger_node_id`, and `correlation_id` fields are compatibility
debt and must not be copied into new contracts.

Wire shape: `{"type": "<legacy_wire_key>", "data": <WorkflowEvent JSON>}`.
The outer `type` is what FE switches on; the inner envelope carries the
CloudEvents routing identity and dataschema lookup.

### Agent capability occurrences

Skill and tool activity uses the `agent_capability` WebSocket route with a
complete CloudEvent inside `data`:

- `source = opencompany://services/agent`;
- `type = com.opencompany.agent.(skill|tool).<state>`;
- `subject = data.agent_node_id`, the exact invoking agent;
- `data.target_node_id`, when present, is the exact connected Master Skill or
  tool node;
- Temporal occurrences use a deterministic `id`; consumers deduplicate on the
  CloudEvents `(source, id)` identity pair.

The browser rejects envelopes whose type/state/subject disagree, ignores late
events from an archived root execution, and updates only the stated agent and
target nodes. Prompts, tool arguments, instruction/resource bodies, raw
results, secrets, and raw exceptions are forbidden from the payload.

Capability telemetry broadcasts directly to WebSocket clients through
`StatusBroadcaster.broadcast_agent_capability`. It does not use
`services.events.dispatch.emit`: that path performs Temporal Visibility lookup
and signals trigger consumers, while capability activity is an observation of
an execution already in progress. The paired raw `node_status` frame is only a
latest-state/reconnect projection; the CloudEvent is the canonical occurrence.

### Plugin-owned event factories

Plugin-specific events (e.g. `com.opencompany.telegram.message.received`)
live in `nodes/<plugin>/_events.py`. Cross-cutting factories
(`credential`, `oauth_completed`, `agent_progress`, `agent_capability`, `task_completed`,
`workflow_lifecycle`, `deployment_snapshot`, `team_event`,
`node_parameters_updated`) stay in `services/events/envelope.py`. A core
service that owns its event keeps the factory beside it, so core code never
imports a plugin's `_events.py`: `services/chat_thread.py` (`chat_updated`)
and `services/workflow_ops.py` (`workflow_ops_applied`, sent by
`broadcast_workflow_ops`).

See RFC §6.4 for the classification rule + the canonical
`telegram/_events.py` example.

### UI-only lifecycle events broadcast directly, never through `emit`

`dispatch.emit` exists to reach **Temporal consumers**: it runs a Visibility
`ListWorkflowExecutions` query to find running listeners, then broadcasts
in-process as a side effect. That query is only worth paying for when some node
type registered the event via `register_canary_trigger_type`.

The Context conversation event (`context.updated`, in
`nodes/context/_events.py`) and the Memory mutation event (`memory.updated`,
in `nodes/tool/simple_memory/_events.py`) have no canary consumer, so the
query is guaranteed to match nothing — once per save/mutation. They call
`get_status_broadcaster().broadcast({...})` directly instead, which is the same
pattern `nodes/telegram/_events.py` uses for status. The CloudEvents envelope,
`source`, `type`, `subject` and `data` are identical either way, so the wire
contract the frontend sees does not change. Normal mode's two events follow
the same rule: `employee_lifecycle` (`com.opencompany.employee.*`, in
`services/employees/events.py`) and `approval_lifecycle`
(`com.opencompany.approval.*`, in `services/approvals/events.py`,
identity only). See [normal_mode.md](./normal_mode.md#wire-contract). So do
the Browser plugin's `browser_updated`, `browser_profiles_updated` and
`browser_runtime` (`com.opencompany.browser.*`, in `nodes/browser/_events.py`;
identity and state only, never a URL, a page title or cookie data).

Two core events follow it too. `chat.updated` (`com.opencompany.chat.updated`,
in `services/chat_thread.py`) goes out after every chat row insert and clear,
with data `{workflow_id, session_id, role}` (`role` null for a clear,
`workflow_id` null for session `"default"`): identity only, so Home's thread
and the editor's chat pane refetch through `get_chat_messages`. The
workflow-ops push `workflow_ops_apply` (`com.opencompany.workflow.ops.applied`,
built and sent by `services/workflow_ops.broadcast_workflow_ops`) is the one
whose frame is the event's flat data,
`{workflow_id, caller_node_id, operations, persisted?}`, the shape
`useWorkflowOpsListener` reads.
`persisted: true` marks a batch the server already saved
(`services/workflow_storage/mutate.apply_graph_additions`: Turn on Talk, the
Agent Builder); its ops carry the server's ids and full parameter rows, and
editors adopt them without saving. (The Vertex managed agent's cloud-tool
nodes still send their own frame of the same shape, never persisted.) See
[workflow_ops_protocol.md](./workflow_ops_protocol.md#persisted-batches).

Rule of thumb: **if no `register_canary_trigger_type` call names your event
type, broadcast it directly.** Reach for `emit` only when a Temporal workflow
has to receive it. `tests/nodes/test_context_events.py` locks this for Context
by parsing the module's imports (AST, not grep, so the docstring can explain
the reasoning without tripping the assertion).

### Context events are emitted at the persistence boundary

`save_conversation` (`services/agent_context/conversation.py`) is the one
place every Context writer passes through — the in-process agent loop, the
Temporal LLM activity, and the specialized-provider bridge all persist
through it. So the "conversation advanced" notification is emitted there
rather than at each call site, via a fanout registry in
`services/agent_context/listeners.py`:

```python
register_conversation_listener(async_fn)   # nodes/context/__init__.py
await notify_conversation_saved(workflow_id=..., generation=..., agent_node_id=..., message_count=...)
```

Same shape as the plugin registries in `plugin_system.md`, and for the same
reason: the store must never import `nodes/`. A new writer gets live updates for
free, and no caller carries broadcast code.

Two properties are load-bearing and have tests in
`tests/services/agent_context/test_conversation.py`:

- **After commit.** The notification fires only after the durable upsert, so
  it can never be observed ahead of the state it describes.
- **A listener can never fail a save.** `notify_conversation_saved` swallows
  and logs at WARNING (visible at the default log level — a silently failing
  listener is how "the panel never updates live" becomes undiagnosable).
  Saves run inside the Temporal LLM activity's post-send window, after the
  provider has been called and billed — a throwing listener there would fail
  a run over a UI notification.

Clears are deliberately **not** routed through the store listener: the
clear handler and the Context node's Reset hook dispatch `context.updated`
themselves (a delete is not a save, and only the caller knows it happened).

## Verification

Each Phase-A milestone has a verification command:

| Phase | Check |
|---|---|
| A1 | `pytest tests/test_plugin_contract.py::TestStartToCloseTimeoutOverridesAreCommented` |
| A2 | `python -c "from services.plugin.scaling import RetryPolicy; assert 'NodeUserError' in RetryPolicy().non_retryable_error_types"` |
| A3 | `python -c "from core.config import Settings; print(Settings().temporal_graceful_shutdown_seconds)"` |
| A4 | After Temporal connect: `temporal operator search-attribute list \| grep -E 'EventType\|EventSource\|EventWorkflowId\|TriggerNodeId\|EventTriggerKind\|EventReceivedAt\|ControlEventTypes'` — all 7 lines |

Full test surface landed in Phase A9. Phase B (plugin `_events.py`
modules), Phase C (Temporal trigger-waiter migration), and Phase D
(admin handlers; the custom DLQ table was dropped — see the status
table and the section below) built on this foundation.

## Failure inspection — no separate DLQ table

When a canary listener / polling cycle / cron firing fails after its
`RetryPolicy` is exhausted, Temporal's own primitives are the ops
inspection surface:

- **Visibility list**: `client.list_workflows(query="ExecutionStatus='Failed' AND EventWorkflowId='<deployment_workflow_id>'")` returns every failed run for a deployment. The same Search Attributes the cancel sweep uses for cleanup (per `services/temporal/search_attributes.py`) make this query work.
- **Failure detail**: `client.get_workflow_history(workflow_id, run_id)` returns the full Event History, including the `ActivityTaskFailed` event's error message + stacktrace + each retry attempt timestamp.
- **Temporal Web UI**: `http://localhost:<TEMPORAL_UI_PORT>` — the same data, browsable.

This is why Wave 12 explicitly does NOT add a custom `event_dlq` SQLModel table. Doing so would reinvent the Temporal primitives the rest of the framework was built AROUND, not against. The pre-Temporal `services/execution/models.py::DLQEntry` for the legacy `WorkflowExecutor` is a separate concern and stays where it is.

Wave 12 D3 (shipped — see the status table) added thin WS handlers (`list_canary_listeners` / `list_canary_schedules` / `get_workflow_failure_history`) that wrap these Visibility queries for the FE admin surface, so operators can inspect failed runs without leaving the OpenCompany UI.

## Wave 13 fixes

Six load-bearing corrections to the Wave 12 canary path. All locked by regression tests.

### 1. `EventType` Search Attribute must equal the producer's `event.type`

Pre-fix the deployment manager registered the **legacy snake_case** `event_type` from `TriggerConfig` (e.g. `"chat_message_received"`) as the `EventType` SA on every canary listener. But `dispatch.emit` Visibility-queries by `event.type` from the CloudEvents envelope (reverse-DNS, e.g. `"com.opencompany.chat.message.received"`). The strings never matched — the listener started OK, never reacted to incoming events.

Fix:
- `register_canary_trigger_type(node_type, cloudevent_type)` now requires the CloudEvents type as a second arg ([canary_registry.py](../server/services/deployment/canary_registry.py)). Re-registering with a diverging `cloudevent_type` raises `ValueError` so plugin upgrades that change the envelope shape surface loudly.
- `cloudevent_type_for(node_type)` lookup, used by `DeploymentManager._start_canary_listener` for the `EventType` SA value (was `config.event_type`).
- `WorkflowEvent.task_completed` factory unified to single type `com.opencompany.agent.task.completed` (was `.succeeded` / `.failed` split — broke single-SA listener matching).

Locked by `TestCloudEventTypeMatchesSearchAttribute` in [`test_canary_registry.py`](../server/tests/test_canary_registry.py).

### 2. Plugin `_events.py` is canary-only — no more dual-emit

`event_waiter.dispatch` / `broadcaster.send_custom_event` calls inside canary-registered plugin `_events.py` files (chat / webhook / task / telegram / email) had zero consumers in canary-on mode — the deployment manager skips `setup_event_trigger` when `is_canary_trigger_type(...)` is True, so no legacy waiter ever registered. Removed.

- `dispatch.emit(envelope, wire_routing_key=...)` is the single delivery path. It signals legacy `EventType` consumers and running workflow controllers through one Temporal Visibility query, while broadcasting to the frontend on the same wire key. Each controller filters against its durable trigger registry — note this is a match on the **CloudEvents type**, not on the trigger node's own parameters.

- **The trigger node's own filter is applied in `TriggerListenerWorkflow._spawn_child_run`**, gated on the `machina-trigger-listener-node-filter` patch. That method is the single choke point: `WorkflowControlWorkflow._spawn_push_run` constructs a `TriggerListenerWorkflow` purely to call it, so both the controller path and the legacy listener path go through one branch. The predicate runs in `evaluate_trigger_filter_activity` rather than inline, because filter builders live in plugin folders and reach imports the workflow sandbox forbids; the recorded boolean keeps replay deterministic. It receives the CloudEvents **`data` member, not the envelope** (`event_waiter.dispatch` unpacks to `(event_type, data)` and hands filters the inner payload), and fails **open** — an unknown node type, malformed payload or raising builder all admit the event, because over-firing is recoverable while a dropped trigger event looks like a broken product.

  Before this, `filter_params` was carried in `listener_data` and never read, so the `EventType` Search Attribute was the only narrowing and every event of the right type spawned a run: a `webhookTrigger` bound to `/a` fired on a POST to `/b` (all webhooks share one CloudEvents type and are unscoped), a `taskTrigger` watching one agent fired on every task, and a `whatsappReceive` scoped to one group fired on every message. The canvas-Run path applied these filters, so Run and deploy disagreed about what the same node does.

  `listener_data["filter_params"]` is the only possible source: the graph snapshot carries no parameters (`node.data` holds just the label, and `load_persisted_workflow_graph_activity` returns nodes and edges only), so the hot graph re-read cannot supply them. Editing a deployed trigger's filter therefore takes effect on **re-deploy** — consistent with the trigger node itself, which is pre-executed with the event as its output and never re-reads its parameters at run time.
- For workflow-control generations, trigger definitions, push-event signals,
  and polling activities live directly in the generation's
  `WorkflowControlWorkflow`. There are no separate listener workflow runs;
  only an actual graph execution is started as a child. Standalone
  `TriggerListenerWorkflow` / `PollingTriggerWorkflow` starts remain only as a
  compatibility path for legacy deployments without a controller.
- `google/_events.py:dispatch_gmail_received` deleted. Controlled Gmail polling runs as an activity inside the controller; `PollingTriggerWorkflow` remains the legacy compatibility implementation.
- `whatsapp/_events.py` kept the legacy raw frame on `whatsapp_message_received` for received messages because the FE message-list handler reads `data.sender` (legacy shape) directly — drop blocked on FE migration (D4 follow-up). The duplicate typed-envelope sibling on the same wire key was dropped.
- `twitter/_events.py` keeps both paths — twitter is the only deferred canary plugin (needs PollingTriggerNode subclass refactor first).

### 3. Trigger node status pulse on every firing

Pre-fix the canary listener broadcast `waiting` once at deploy and stayed there forever — when an event fired, FE saw downstream nodes light up but no visual signal on the trigger node itself. The legacy `services/deployment/triggers.py` collector/processor did `waiting → idle (Graph executing...) → waiting` per event; canary skipped this.

Fix: `broadcast_trigger_status_activity` ([activities.py](../server/services/temporal/activities.py)). The shared trigger-run helper used by the controller and legacy `TriggerListenerWorkflow` calls it before + after each child spawn:
- Before: `status="idle"` with `message="Graph executing..."` data
- After: `status="waiting"` with the next-event message

The controller's polling path and legacy `PollingTriggerWorkflow` use the same status lifecycle. Pause overrides the armed `waiting` projection with an explicit paused state and Resume rearms it.

### 4. Polling `seen` set OOM leak

Both polling paths grew `seen: Set[str]` unboundedly:
- `as_poll_activity` returned `seen_ids: list(prior_seen | current)` every cycle — Temporal payload paid for the union forever.
- `_build_poll_coroutine` called `seen.add(msg_id)` per emit with no eviction.

At Gmail's ~100 msgs/day / 60s poll, this hit ~36K entries / ~1.4MB just for IDs in a year. Fix: rebase `seen = current` at end of every cycle in both paths ([polling.py](../server/services/plugin/polling.py)). Items the provider no longer reports drop out. Bounded by the provider's natural window size.

Semantic note: if a filtered item disappears then reappears (e.g. user marks an email unread again under `is:unread`), it re-emits. That's the correct semantic for visibility-filtered providers; the old unbounded set suppressed legitimate re-emits.

### 5. Template resolution against trigger output (canary path)

Pre-fix the canary path's pre-executed trigger output never reached the workflow output store. `MachinaWorkflow.run` set `outputs[node_id]` in workflow memory only — never persisted. `ParameterResolver._gather_connected_outputs` reads from the store, so `{{triggerNode.field}}` templates in downstream nodes resolved to empty.

The legacy `DeploymentManager._execute_from_trigger` called `_store_output(trigger_node_id, "output_0", trigger_output)` explicitly. Canary skipped it.

Fix: new `store_node_output_activity` ([activities.py](../server/services/temporal/activities.py)). `MachinaWorkflow.run`'s pre-executed loop schedules it for every firing trigger (skips non-firing siblings with `_trigger_output={"not_triggered": True}`). Writes to `output_main` + `output_top` + `output_0` so any downstream edge handle resolves.

### 6. `DeploymentManager.cancel` full status sweep

Pre-fix cancel reset only cron + listener trigger nodes to `idle`. Downstream agents/tools/actions that were mid-execute when cancel hit stayed in `executing` forever on FE. Toolbar Start/Stop indicator also stayed at `executing=True` because no terminal `executing=False` workflow_status broadcast was emitted.

Fix ([manager.py](../server/services/deployment/manager.py) `DeploymentManager.cancel`):
- `await broadcaster._clear_stuck_node_statuses(workflow_id, include_waiting=True)` — sweeps every node still broadcast as `executing`/`waiting`. The delegation guard inside `_clear_stuck_node_statuses` still protects in-flight fire-and-forget child agents.
- `await broadcaster.update_workflow_status(executing=False, workflow_id=...)` — terminal toolbar state. Avoids relying on `workflow_run_ended`'s counter (can race against in-flight `workflow_run_started` callers).

Locked by `TestCancelSweepsStuckNodeStatuses` in [`test_deployment_canary_listener.py`](../server/tests/test_deployment_canary_listener.py).

## References

- Current contract: [Workflow control](temporal-workflow-control.md), including
  admission/drain acknowledgements, compatibility gates and verification.
- RFC: [`plugin_authoring_rfc.md`](./ARCHIVE/plugin_authoring_rfc.md)
- Temporal: [Messaging patterns](https://docs.temporal.io/design-patterns/workflow-messaging-patterns) · [Python messages](https://docs.temporal.io/develop/python/workflows/message-passing) · [Event Accumulator](https://docs.temporal.io/design-patterns/event-accumulator) · [Search Attributes](https://docs.temporal.io/search-attribute) · [Schedules](https://docs.temporal.io/develop/python/schedules) · [Retry Policies](https://docs.temporal.io/encyclopedia/retry-policies)
- CloudEvents: [v1.0.2 spec](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md)
