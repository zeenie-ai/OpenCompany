# Event Framework (Wave 12)

Temporal-native event-routing layer for MachinaOs. Implements RFC sections
6.3 (Temporal worker contract) + 6.4 (CloudEvents broadcast contract) from
[plugin_authoring_rfc.md](./plugin_authoring_rfc.md).

This doc is the operator + plugin-author reference. The design rationale +
phase plan lives in `~/.claude/plans/properly-fix-the-tech-dreamy-tarjan.md`.

## Status (2026-05-14)

| Phase | State |
|---|---|
| A1-A9 — Temporal primitives + CloudEvents spec compliance | ✅ shipped (commit `c3dc85a`) |
| B1-B10 — plugin-owned `_events.py` modules (9 plugin folders) | ✅ shipped (commits `7e4ff7b`, `c4d9428`, `da63d73`, `de8be88`) |
| B11 — FE handler migration to envelope-aware readers | ⏳ deferred to FE session |
| C1 canary (webhookTrigger) — `TriggerListenerWorkflow` + Visibility-API registry | ✅ shipped 2026-05-14 (25 tests) |
| C1 rollout (chat / task / telegram / whatsapp) — plugin-self-registered via `canary_registry` | ✅ shipped 2026-05-14 |
| C2 canary (googleGmailReceive) — `PollingTriggerWorkflow` + `as_poll_activity` per-cycle activity | ✅ shipped 2026-05-15 (10 tests) |
| C3 canary (cronScheduler) — Temporal Schedule + plugin-owned `CronTriggerWorkflow` via `SimplePlugin` | ✅ shipped 2026-05-15 (17 tests) |
| D1 — Shared `_retry_policies` + `NodeUserError` non-retryable on workflow callsites | ✅ shipped 2026-05-15 (11 tests) |
| D2 — Custom `event_dlq` SQLModel table | ❌ dropped 2026-05-15 — Temporal Event History + Visibility queries cover the ops-inspection use case; no separate table needed (matches the plan's "What Temporal eliminates" rationale) |
| D2b — Retire `event_waiter.py` Redis-Streams branch | ⏸ deferred — gated on `event_framework_enabled` flipping to default-on |
| D3 — Visibility admin WS handlers (`list_canary_listeners` / `list_canary_schedules` / `get_workflow_failure_history`) | ✅ shipped 2026-05-15 (13 tests) |
| D4 — Drain `_LEGACY_RAW_DICT_BROADCASTS` | ⏸ blocked on B11 FE migration |
| D5 — Auto-gen `DEFAULT_TOOL_NAMES` from `ToolNode` ClassVars | ⏳ pending — large-scope refactor |
| C2 — Polling triggers as long-lived workflows | ⏳ pending |
| C3 — APScheduler → Temporal Schedules | ⏳ pending |
| C4 — Close cross-plugin `_service` reaches (4 sites) | ⏳ pending |
| D1-D5 — Visibility / retry / DLQ / drain / Y5 | ⏳ pending |

`Settings.event_framework_enabled` gates the new dispatch path (default off in Phase A). When off, `services.events.dispatch.emit` is a no-op pass-through; plugin emitters still call `status_broadcaster.broadcast(...)` directly, so the FE fan-out is unchanged. Turn on per-callsite for opt-in dogfooding without flipping the flag globally.

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
| `EventWorkflowId` | KEYWORD | Scope events to a MachinaOs workflow_id |
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
plus MachinaOs extension attributes (`workflow_id`, `trigger_node_id`,
`correlation_id` — kept in snake_case per the documented internal-naming
rationale at `envelope.py:4-12`).

Wire shape: `{"type": "<legacy_wire_key>", "data": <WorkflowEvent JSON>}`.
The outer `type` is what FE switches on; the inner envelope is what
parses for spec-compliant routing + dataschema lookup.

### Plugin-owned event factories

Plugin-specific events (e.g. `com.machinaos.telegram.message.received`)
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

Full test surface lands in Phase A9. Phase B (plugin `_events.py`
modules) + Phase C (Temporal trigger-waiter migration) + Phase D
(admin handlers + DLQ) build on this foundation.

## Failure inspection — no separate DLQ table

When a canary listener / polling cycle / cron firing fails after its
`RetryPolicy` is exhausted, Temporal's own primitives are the ops
inspection surface:

- **Visibility list**: `client.list_workflows(query="ExecutionStatus='Failed' AND EventWorkflowId='<deployment_workflow_id>'")` returns every failed run for a deployment. The same Search Attributes the cancel sweep uses for cleanup (per `services/temporal/search_attributes.py`) make this query work.
- **Failure detail**: `client.get_workflow_history(workflow_id, run_id)` returns the full Event History, including the `ActivityTaskFailed` event's error message + stacktrace + each retry attempt timestamp.
- **Temporal Web UI**: http://localhost:8233 — the same data, browsable.

This is why Wave 12 explicitly does NOT add a custom `event_dlq` SQLModel table. Doing so would reinvent the Temporal primitives the rest of the framework was built AROUND, not against. The pre-Temporal `services/execution/models.py::DLQEntry` for the legacy `WorkflowExecutor` is a separate concern and stays where it is.

Wave 12 D3 (pending) adds thin WS handlers that wrap these Visibility queries for the FE admin surface, so operators can inspect failed runs without leaving the MachinaOs UI.

## References

- Plan: `~/.claude/plans/properly-fix-the-tech-dreamy-tarjan.md`
- RFC: [`plugin_authoring_rfc.md`](./plugin_authoring_rfc.md)
- Temporal: [Search Attributes](https://docs.temporal.io/search-attribute) · [Signals](https://docs.temporal.io/develop/python/message-passing) · [Schedules](https://docs.temporal.io/develop/python/schedules) · [Retry Policies](https://docs.temporal.io/encyclopedia/retry-policies)
- CloudEvents: [v1.0 spec](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md)
