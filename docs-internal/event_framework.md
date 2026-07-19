# Event Framework (Wave 12)

> **Current architecture:** This document retains the Wave-12 rollout history.
> Controlled deployments now consolidate push registration, event Signals, and
> polling activities inside `WorkflowControlWorkflow`; see
> [Temporal Execution Engine RFC](temporal-execution-engine-rfc.md). Listener
> workflows described in the phase log are legacy compatibility contracts.

Temporal-native event-routing layer for OpenCompany. Implements RFC sections
6.3 (Temporal worker contract) + 6.4 (CloudEvents broadcast contract) from
[plugin_authoring_rfc.md](./ARCHIVE/plugin_authoring_rfc.md).

This doc is the operator + plugin-author reference. The design rationale +
phase plan lives in `~/.claude/plans/properly-fix-the-tech-dreamy-tarjan.md`.

## Status (2026-05-15)

### Shipped

| Phase | State |
|---|---|
| A1-A9 — Temporal primitives + CloudEvents spec compliance | ✅ commit `c3dc85a` (16 tests) |
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
| Canary flag default flip + invariant lock | ✅ this commit (default `True`; `EVENT_FRAMEWORK_ENABLED=false` is the rollback) |
| Wave 13 — EventType SA mismatch fix + canary-only emit path + trigger status lifecycle + polling OOM + template resolution + cancel sweep | ✅ shipped (see "Wave 13 fixes" below) |

### Dropped / Deferred / Pending

| Phase | State |
|---|---|
| D2 — Custom `event_dlq` SQLModel table | ❌ **dropped** (commit `89b15bd`, docs only). Temporal Event History + Visibility queries cover the ops-inspection use case; reinventing them would contradict Wave 12's "Temporal-native, no custom infra" thesis. See § "Failure inspection — no separate DLQ table". |
| D2b — Retire `event_waiter.py` Redis-Streams branch | ✅ shipped as Wave 15.3 (see [TEMPORAL_CLEANUP_AND_RESILIENCE_PLAN.md](./TEMPORAL_CLEANUP_AND_RESILIENCE_PLAN.md)). `event_waiter` is memory-mode-only now; Temporal owns durable delivery. The in-memory collector still backs canvas-Run + non-canary (Twitter) triggers. |
| D4 — Drain remaining dual-emit on message/newsletter/history wire keys | ⏳ pending — paired with FE migration to envelope-aware readers on those channels (`whatsapp_message_received` et al). |
| WorkflowEnvironment integration smoke test | ⏳ pending — full 7-canary in-process Temporal cluster. Existing unit tests + per-canary producer tests + `TestCanaryRegistryCoverage` cover the static surface; the integration smoke would catch real-cluster regressions only. |

**Test surface: 256 passed + 1 xfail** across 18 event-framework test files.

`Settings.event_framework_enabled` gates the new dispatch path. **Default flipped to `True` on 2026-05-15** — the Temporal-Signal consumer fan-out is now production-default. The env var `EVENT_FRAMEWORK_ENABLED=false` is the rollback channel; when set, `services.events.dispatch.emit` reverts to a pass-through no-op and the legacy `event_waiter.dispatch` path keeps working unchanged for non-canary triggers.

### Rollback procedure

If the canary fan-out causes regressions in production:

1. Set `EVENT_FRAMEWORK_ENABLED=false` in `.env` (or the process environment).
2. Restart the server (`npm run start` / `uvicorn` reload). Pydantic Settings re-reads on startup.
3. Confirm pass-through: `dispatch.emit()` logs `event-framework disabled — emit no-op` at DEBUG.

No DB migrations, no schema changes — the rollback is one env var + restart. The legacy `event_waiter` collector/processor keeps trigger nodes firing because plugin producers still call `event_waiter.dispatch(...)` alongside `dispatch.emit(...)` (the dual-dispatch pattern stays for the in-memory canvas-Run path; the Redis-Streams branch itself was retired in Wave 15.3).

Locked by `tests/test_event_framework_phase_a.py::TestEventFrameworkEnabledDefault::test_event_framework_enabled_defaults_true` (source-introspection check that the `Field(default=True, ...)` declaration is present) and `TestCanaryRegistryCoverage::test_seven_canary_types_registered` (every canary plugin opted in via `register_canary_trigger_type`).

## What this framework does

Every inbound event (HTTP webhook, Telegram message, Gmail poll result,
task completion, …) becomes a Temporal Signal delivered to whichever
running workflows are waiting on that event type. Routing happens via
the Temporal Visibility API — workflows tag themselves with custom
Search Attributes at start, and the dispatch helper queries
`ListWorkflows(query="EventType='X' AND ExecutionStatus='Running'")`
to find consumers.

Why Temporal: durability, replay safety, server-side dedup
(`WorkflowIDReusePolicy`), and zero custom infrastructure. The framework
adds ~300 LOC on top of Temporal primitives rather than reinventing an
EventBus + event_log + DLQ table.

## Architecture

```
Inbound source (FastAPI process)
       ↓
services/events/dispatch.py:emit(event: WorkflowEvent)
       ├─→ Temporal Visibility query: workflows where EventType=event.type
       ├─→ Signal each matching workflow
       └─→ status_broadcaster.broadcast() — direct in-process WS fan-out
```

Worker is embedded in the FastAPI process (`main.py:211-292`
`TemporalWorkerManager.start()` runs as `asyncio.create_task()`). Activities
and the WebSocket connection pool share memory + event loop, so the fan-out
to FE clients is a direct in-process call — no Redis Streams hop required.

## Search Attributes setup

The framework requires 6 custom Search Attributes on the Temporal
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
# ... repeat for the other 5 attributes
```

### Verification

```bash
temporal operator search-attribute list --namespace default
```

Should show the 6 framework attributes alongside Temporal's built-in
default attributes (`WorkflowType`, `WorkflowId`, `ExecutionStatus`, …).

## Temporal contract for plugin authors

| Class attribute | Purpose | Default |
|---|---|---|
| `start_to_close_timeout` | Per-attempt budget (one activity execution) | Kind-base default: ActionNode=10m, TriggerNode=24h, ToolNode=10m |
| `retry_policy: RetryPolicy` | Backoff + max attempts + non-retryable error types | `DEFAULT_RETRY` — 3 attempts, 1-60s exponential. `NodeUserError` is auto-non-retryable. |
| `heartbeat_timeout` | Max idle between `activity.heartbeat()` calls | 2 minutes |
| `task_queue` | Worker pool routing | `TaskQueue.DEFAULT` |

Override only when the kind-base default doesn't fit; an inline comment
explaining why is required (enforced by
`tests/test_plugin_contract.py::TestStartToCloseTimeoutOverridesAreCommented`).

### Worker graceful shutdown

Workers honor SIGTERM with a configurable grace window
(`Settings.temporal_graceful_shutdown_seconds`, default 30s, override via
env `TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS`). Activities mid-flight finish
or hand back to the server for retry instead of being killed mid-call.

## CloudEvents envelope

Every server→FE broadcast wraps `WorkflowEvent` (CloudEvents v1.0;
`services/events/envelope.py`). Spec-compliant ID, source, type, time,
plus OpenCompany extension attributes (`workflow_id`, `trigger_node_id`,
`correlation_id` — kept in snake_case per the documented internal-naming
rationale at `envelope.py:4-12`).

Wire shape: `{"type": "<legacy_wire_key>", "data": <WorkflowEvent JSON>}`.
The outer `type` is what FE switches on; the inner envelope is what
parses for spec-compliant routing + dataschema lookup.

### Plugin-owned event factories

Plugin-specific events (e.g. `com.opencompany.telegram.message.received`)
live in `nodes/<plugin>/_events.py`. Cross-cutting factories
(`credential`, `oauth_completed`, `agent_progress`, `task_completed`,
`workflow_lifecycle`, `deployment_snapshot`, `team_event`,
`node_parameters_updated`) stay in `services/events/envelope.py`.

See RFC §6.4 for the classification rule + the canonical
`telegram/_events.py` example.

## Verification

Each Phase-A milestone has a verification command:

| Phase | Check |
|---|---|
| A1 | `pytest tests/test_plugin_contract.py::TestStartToCloseTimeoutOverridesAreCommented` |
| A2 | `python -c "from services.plugin.scaling import RetryPolicy; assert 'NodeUserError' in RetryPolicy().non_retryable_error_types"` |
| A3 | `python -c "from core.config import Settings; print(Settings().temporal_graceful_shutdown_seconds)"` |
| A4 | After Temporal connect: `temporal operator search-attribute list \| grep -E 'EventType\|EventSource\|EventWorkflowId\|TriggerNodeId\|EventTriggerKind\|EventReceivedAt'` — all 6 lines |

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
- **Temporal Web UI**: http://localhost:8233 — the same data, browsable.

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

- `dispatch.emit(envelope, wire_routing_key=...)` is the single delivery path. It signals legacy `EventType` consumers and running workflow controllers through one Temporal Visibility query, while broadcasting to the frontend on the same wire key. Each controller filters against its durable trigger registry.
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

- Plan: `~/.claude/plans/properly-fix-the-tech-dreamy-tarjan.md`
- RFC: [`plugin_authoring_rfc.md`](./ARCHIVE/plugin_authoring_rfc.md)
- Temporal: [Search Attributes](https://docs.temporal.io/search-attribute) · [Signals](https://docs.temporal.io/develop/python/message-passing) · [Schedules](https://docs.temporal.io/develop/python/schedules) · [Retry Policies](https://docs.temporal.io/encyclopedia/retry-policies)
- CloudEvents: [v1.0 spec](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md)
