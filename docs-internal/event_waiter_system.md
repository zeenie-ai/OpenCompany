# Event Waiter System

> **Scope note.** This document describes the live in-memory waiter that backs the canvas-Run path. Node authoring happens on the backend: each node is a Python plugin folder under `server/nodes/<category>/<node>/__init__.py` that emits a `NodeSpec`. The frontend reads specs via [client/src/lib/nodeSpec.ts](../client/src/lib/nodeSpec.ts) + [adapters/nodeSpecToDescription.ts](../client/src/adapters/nodeSpecToDescription.ts). See [plugin_system.md](./plugin_system.md) and [server/nodes/README.md](../server/nodes/README.md) for the authoring model.

Trigger nodes in OpenCompany suspend workflow execution until an external event arrives (WhatsApp message, webhook request, Telegram message, chat input, delegated task completion, etc.). The Event Waiter system is the in-memory (`asyncio.Future`) primitive that backs push-based triggers on the canvas-Run path; deployed canary triggers do not use it (see "Deployed Triggers vs Canvas Run"). In a controlled deployment, trigger definitions, push Signals, polling activities, pause state, and queued events live in `WorkflowControlWorkflow`; only a real graph invocation starts `MachinaWorkflow`. `TriggerListenerWorkflow` / `PollingTriggerWorkflow` are legacy replay/compatibility paths. The Redis-Streams backend that previously offered cross-restart waiter persistence was retired because Temporal owns deployed-trigger durability.

Source file: `server/services/event_waiter.py`

## What It Solves

Before this system, each trigger had its own ad-hoc waiting code. That meant:

- No common cancel path
- No common filter semantics
- No way to debug which triggers were active

A single waiter module replaces all of that. Adding a new trigger now means adding one entry to the registry and one filter builder.

## Core Concepts

### Waiter

A `Waiter` is a single subscription:

```python
@dataclass
class Waiter:
    id: str                          # UUID
    node_id: str                     # workflow node waiting
    node_type: str                   # 'whatsappReceive', 'webhookTrigger', ...
    event_type: str                  # 'whatsapp_message_received', ...
    filter_fn: Callable[[Dict], bool]
    future: Optional[asyncio.Future] # resolved by dispatch() on match
    cancelled: bool
    created_at: float
```

### Trigger Registry

Every event-based trigger node type is registered with the event name it listens for. Only the four framework-level triggers (not owned by any plugin domain) are hardcoded in `event_waiter.py`:

```python
TRIGGER_REGISTRY: Dict[str, TriggerConfig] = {
    'start':          TriggerConfig('start',          'deploy_triggered',      'Deploy Start'),
    'webhookTrigger': TriggerConfig('webhookTrigger', 'webhook_received',      'Webhook Request'),
    'chatTrigger':    TriggerConfig('chatTrigger',    'chat_message_received', 'Chat Message'),
    'taskTrigger':    TriggerConfig('taskTrigger',    'task_completed',        'Task Completed'),
}
```

Plugin-owned triggers (`whatsappReceive`, `twitterReceive`, `telegramReceive`, `emailReceive`, `googleGmailReceive`, `discordReceive`, `whatsappBusinessReceive`, ...) are **backfilled** into the same dict by `_auto_populate_from_plugins()` (`event_waiter.py`), which walks every registered `TriggerNode` subclass and reads its `event_type` ClassVar + `display_name`. The backfill runs lazily on first `get_trigger_config` / `build_filter` access (importing the plugin registry at module load would be circular). Hardcoded entries always win, so a plugin upgrade never silently replaces hand-maintained behaviour.

`cronScheduler` is **not** in this registry: it is a Temporal Schedule (created by the deployment manager via `services/temporal/schedules.py`) and does not wait for events.

### Filter Builders

Each trigger type has a filter builder that reads the node's parameters once and returns a closure evaluated per event. Like the registry, only the framework-level builders are hardcoded:

```python
FILTER_BUILDERS = {
    'webhookTrigger': build_webhook_filter,
    'chatTrigger':    build_chat_filter,
    'taskTrigger':    build_task_completed_filter,
}
```

Plugin builders arrive two ways: `_auto_populate_from_plugins()` wraps each `TriggerNode` subclass's `build_filter(params)` method (validated through the plugin's `Params` model), and rich plugin folders register an explicit builder from their `__init__.py` via `event_waiter.register_filter_builder(node_type, fn)` (telegram's `_filters.py:build_telegram_filter`, whatsapp's `_filters.py:build_whatsapp_filter`, ...). `FILTER_BUILDERS` is backed by an `IdempotentRegistry`, so a same-callable re-registration is a no-op and a conflicting one raises.

Filter closures capture parameter values at registration time. For example, `build_whatsapp_filter` (`nodes/whatsapp/_filters.py`) captures `messageTypeFilter`, `sender_filter`, `contact_phone`, `group_id`, `keywords`, `ignore_own`, and `forwarded_filter`, then returns a function that checks each incoming WhatsApp message against those constraints.

## Execution Flow

```
Canvas Run (or a trigger type deployed without a canary registration)
        |
        v
Trigger node encountered in execution layer
        |
        v
event_waiter.register(node_type, node_id, params) -> Waiter
        |
        v
event_waiter.wait_for_event(waiter)  (suspends)
                    |
        External event arrives (WhatsApp RPC, Telegram long-polling, webhook HTTP request, ...)
                    |
                    v
        event_waiter.dispatch(event_type, data)  (sync; also accepts a WorkflowEvent)
                    |
                    v
        For each Waiter with matching event_type:
            if waiter.filter_fn(data):
                waiter.future.set_result(data)
                    |
                    v
Workflow resumes, trigger node completes, downstream nodes execute
```

## Backend

Single in-memory backend using `asyncio.Future` with a module-level `_waiters` dict.

- `register()` creates an `asyncio.Future` and stores it in `_waiters[waiter.id]`.
- `wait_for_event()` awaits the future.
- `dispatch()` iterates `_waiters`, runs `filter_fn(data)` on each, calls `future.set_result(data)` on matches.

`capture_main_loop()` (called during app startup in `main.py`) stores the main event loop so future thread-context callers can hop onto it via `asyncio.run_coroutine_threadsafe`.

Durability note: waiter state does NOT survive a process restart — that is by design. Controlled deployed triggers get restart durability from `WorkflowControlWorkflow`; cron uses Temporal Schedules. The event waiter only backs interactive canvas-Run waits and legacy uncontrolled deployments, where a dead process means the canvas session is gone. (The Redis-Streams backend that previously covered this was retired in Wave 15.3.)

## Deployed Triggers vs Canvas Run

When a workflow is deployed, a trigger type registered with `register_canary_trigger_type` (live list: `rg 'register_canary_trigger_type\(' server/nodes`) does not use this module. It runs under the Temporal controller, and its producer delivers events with `services.events.dispatch.emit`. The event waiter is involved only for canvas Run and for trigger types that are not canary-registered:

| Trigger Type | Deployed | Canvas Run |
|---|---|---|
| `whatsappReceive`, `telegramReceive`, `chatTrigger`, `webhookTrigger`, `taskTrigger`, `discordReceive`, `discordInteraction` | Canary push, delivered by `dispatch.emit` | `event_waiter` waiter; see the known gap below |
| `whatsappBusinessReceive`, `whatsappBusinessStatus` | Canary push, delivered by `dispatch.emit` | `event_waiter` waiter, resolved by the `WebhookSource` intake in `services/events/webhook.py` |
| `googleGmailReceive`, `emailReceive`, `msMailReceive` | Canary polling: the controller runs the plugin's `poll.{node_type}.v{version}` activity | The node polls from its own `execute`; no waiter |
| `twitterReceive` | Not canary-registered. It is in `POLLING_TRIGGER_TYPES` but registers no polling factory, so deploy falls back to an in-process `event_waiter` collector | `event_waiter` waiter; see the known gap below |
| `cronScheduler` | Temporal Schedule (`services/temporal/schedules.py`) | Not a waiter |

Controlled polling triggers are registered with `WorkflowControlWorkflow`, which starts a graph only for deduplicated new events. Legacy uncontrolled polling still uses the deployment compatibility layer. See [Workflow control](./temporal-workflow-control.md) and [Temporal Architecture](./TEMPORAL_ARCHITECTURE.md) for the live deployment architecture; the [execution-engine RFC](ARCHIVE/temporal-execution-engine-rfc.md) is historical.

### Cooperative Stop and durable controller queues

For new generations (`execution_control_version=1`), Stop closes admission
before another polling fetch or graph child start and drains already-admitted
business work through result bookkeeping. It does not cancel an Activity or
an event waiter. Accepted push Signals remain queued while stopped; Resume
releases the next pending action in the same execution. Polling results that
finish during Stop update their seen-ID baseline and preserve new events
before the fetch leaves the drain count. Sleeps between fetches and control
waits do not count as admitted business work.

The versioned controller uses Event Accumulator queue/deduplication/carry
practices without inactivity batching. Pending deduplication keys remain
protected independently of the bounded recent-key window, overflow pages
restore queued keys, and Continue-As-New fences include events delivered
during spill awaits. This applies after Temporal accepts the Signal;
`dispatch.emit` still relies on eventually consistent Visibility and logs or
suppresses delivery failures, so it is not a durable producer outbox.
Same-label/type trigger Workflow-ID collisions remain a separate known limit.
See [Node creation: event contract](./node_creation.md#event-identity-and-delivery-contract)
and [Temporal Event Accumulator](https://docs.temporal.io/design-patterns/event-accumulator).

Events use Signals; acknowledged control and root membership use completed
Updates; status uses read-only Queries. A Signal response means receipt into
Temporal history, not handler completion. Those messages never operate on the
process-local `_waiters` map. See [Temporal workflow messaging](https://docs.temporal.io/design-patterns/workflow-messaging-patterns).

### Known gap: canvas Run on canary push triggers

Pressing Run on the push triggers in the first table row registers a waiter that no producer resolves. Their producers call only `dispatch.emit`, which signals Temporal consumers and broadcasts to WebSocket clients but never calls `event_waiter.dispatch`. `StatusBroadcaster.send_custom_event`, which does dispatch to waiters, has no production caller. The node stays in `waiting` until the user cancels it. Deployed runs of the same triggers are unaffected.

### Known gap: `twitterReceive` never fires

Nothing produces Twitter trigger events. `dispatch_twitter_event_received` in `nodes/twitter/_events.py` has no caller, and `TwitterReceiveNode` is a plain `TriggerNode`, not a `PollingTriggerNode`, so it registers no polling factory. Canvas Run and a deployment both wait on an `event_waiter` future that is never resolved; on deploy, `DeploymentManager._setup_event_trigger` also logs `No polling factory registered for trigger`. The `_events.py` module docstring describes the missing `PollingTriggerNode` refactor.

## Cancellation

Users can cancel a waiting trigger from the UI (Cancel button on the trigger node). The path:

1. Frontend sends `cancel_event_wait` WebSocket message with `waiter_id` or `node_id`.
2. `handle_cancel_event_wait()` in `server/routers/websocket.py` calls either `event_waiter.cancel(waiter_id)` or `event_waiter.cancel_for_node(node_id)`.
3. `cancel()` sets `w.cancelled = True` and calls `future.cancel()`.
4. The suspended `wait_for_event()` raises `asyncio.CancelledError`, which bubbles up through the node executor.

This explicit waiter cancellation is distinct from a controlled generation's
Stop. It ends the interactive wait; it cannot provide durable Resume after a
process restart. Use generation `pause_workflow` / `resume_workflow` for the
cooperative Temporal contract.

## Debugging

`event_waiter.get_active_waiters()` returns a list of all currently active waiters with age and done/cancelled status. The `get_active_waiters` WebSocket handler exposes this to the frontend for the active-triggers debug view. (`get_backend_mode()` still exists for compatibility but always returns `"memory"` — the Redis-Streams backend was retired in Wave 15.3.)

```python
{
    "id": "uuid",
    "node_id": "whatsappReceive-1",
    "node_type": "whatsappReceive",
    "event_type": "whatsapp_message_received",
    "done": False,
    "cancelled": False,
    "age_seconds": 142.3,
    "mode": "memory"  # always "memory" since Wave 15.3
}
```

## Adding a New Trigger Type

1. **Declare the plugin** as a `TriggerNode` subclass in its own folder, `server/nodes/<group>/mqtt_trigger/__init__.py`, with `group = ('mqtt', 'trigger')` and an `event_type` ClassVar. `_auto_populate_from_plugins()` backfills `TRIGGER_REGISTRY` from that ClassVar — do not hand-edit `event_waiter.py` (only the four framework-level triggers live there).

   ```python
   class MqttTriggerNode(TriggerNode):
       type = "mqttTrigger"
       display_name = "MQTT Message"
       group = ("mqtt", "trigger")
       event_type = "mqtt_message_received"
       Params = MqttTriggerParams
       Output = MqttTriggerOutput
   ```

2. **Build a filter** closure from the validated `Params`. Either implement `build_filter` on the class (auto-registered) or, for a self-contained plugin folder, put it in `_filters.py` and register it from `__init__.py`:

   ```python
   def build_mqtt_filter(params: Dict) -> Callable[[Dict], bool]:
       topic = params.get('topic', '')
       qos = params.get('qos', 0)

       def matches(m: Dict) -> bool:
           if topic and m.get('topic') != topic:
               return False
           if qos and m.get('qos') != qos:
               return False
           return True
       return matches

   # nodes/mqtt/__init__.py
   from services.event_waiter import register_filter_builder
   register_filter_builder('mqttTrigger', build_mqtt_filter)
   ```

3. **Dispatch events** from the external service. For a canary (Temporal-routed) trigger, register the CloudEvents type with `register_canary_trigger_type(node_type, cloudevent_type)` and emit through `services.events.dispatch.emit(envelope, wire_routing_key=...)`. `emit` does not reach this module, so canvas Run resolves only if the producer also calls `event_waiter.dispatch` (see the known gap above):

   ```python
   from services import event_waiter
   event_waiter.dispatch('mqtt_message_received', {'topic': ..., 'payload': ...})
   ```

4. **Add the trigger to `WORKFLOW_TRIGGER_TYPES`** in `server/constants.py`. Omitting it is a silent failure — `find_trigger_nodes` filters on that set, so deploy ignores the node with no warning.

5. **Declare the output shape** as the plugin's `Output` Pydantic model (auto-registered into `NODE_OUTPUT_SCHEMAS`), or call `services.node_output_schemas.register_output_schema(node_type, Model)` from `__init__.py` when the shape is not auto-derivable. The frontend reads it from `GET /api/schemas/nodes/<type>/spec.json` — there is no frontend file to edit.

No changes are needed in the execution engine, cancel path, deployment manager, or any frontend file.

## Related Docs

- [DESIGN.md](DESIGN.md) - how triggers fit into execution
- [workflow-schema.md](workflow-schema.md) - trigger node catalog
- [TEMPORAL_ARCHITECTURE.md](TEMPORAL_ARCHITECTURE.md) - how deployed triggers get durability from Temporal
