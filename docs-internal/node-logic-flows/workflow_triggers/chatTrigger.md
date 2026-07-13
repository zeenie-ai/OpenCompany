# Chat Trigger (`chatTrigger`)

| Field | Value |
|------|-------|
| **Category** | workflow / trigger / utility |
| **Backend handler** | Plugin [`server/nodes/trigger/chat_trigger/__init__.py`](../../../server/nodes/trigger/chat_trigger/__init__.py) (`ChatTriggerNode`); dispatch via `BaseNode.execute()`. The base `TriggerNode.execute` handles the event-waiter wait — the `@Operation("wait")` body is a stub. The generic legacy path is [`server/services/handlers/triggers.py::handle_trigger_node`](../../../server/services/handlers/triggers.py) (reachable only for the lone deferred-canary trigger, not chatTrigger). |
| **Tests** | [`server/tests/nodes/test_workflow_triggers.py`](../../../server/tests/nodes/test_workflow_triggers.py) |
| **Skill (if any)** | none |
| **Dual-purpose tool** | no |

## Purpose

Fires when the user sends a message from the Console Panel chat tab. The
producer [`server/nodes/trigger/chat_trigger/_events.py`](../../../server/nodes/trigger/chat_trigger/_events.py)
emits a CloudEvents `WorkflowEvent` (`type: com.opencompany.chat.message.received`)
via `dispatch.emit`. `chatTrigger` is canary-registered
(`register_canary_trigger_type`), so `DeploymentManager` starts a
`TriggerListenerWorkflow` for it; the listener receives the event via Temporal
Signal and spawns a child `MachinaWorkflow` per matching event. Any `chatTrigger`
node whose `session_id` matches (or is `'default'`) receives the event and emits
it as output. This is the primary way a user feeds an interactive prompt into an
`aiAgent` or `chatAgent`.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| (none) | - | - | Trigger nodes have no inputs. |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `session_id` | string | `default` | no | - | Matches the `session_id` on the incoming chat event. If set to `default`, the filter accepts every event. Otherwise it only accepts events with the same `session_id`. |
| `placeholder` | string | `Type a message...` | no | - | Frontend display only - not used by the handler. |

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | The chat event payload (see below). |

### Output payload

The exact shape depends on how the dispatcher in
`routers/websocket.py::send_chat_message` builds the event, but the
documented fields surfaced to downstream nodes are:

```ts
{
  message: string;
  timestamp: string;   // ISO 8601
  session_id: string;
  node_id?: string;    // Optional - set when the client targets a specific chatTrigger
}
```

Wrapped in the standard envelope.

## Logic Flow

```mermaid
flowchart TD
  P[chat tab sends message] --> Q[_events.py dispatch.emit<br/>WorkflowEvent com.opencompany.chat.message.received]
  Q --> R[TriggerListenerWorkflow receives via Temporal Signal]
  R --> S[ChatTriggerNode.build_filter:<br/>if session_id != 'default' require exact match]
  S -- match --> T[spawn child MachinaWorkflow<br/>trigger pre-executed with event payload]
  S -- no match --> R
```

## Decision Logic

- **Filter** (`ChatTriggerNode.build_filter`):
  ```python
  if session_id and session_id != 'default':
      return event.get('session_id') == session_id
  return True
  ```
  So `session_id='default'` (the frontend default) is a wildcard - the
  trigger fires for every chat message regardless of which session the user
  is in.
- **Cancellation**: yields `success=False, error="Cancelled by user"`.

## Side Effects

- **Database writes**: none in the trigger plugin itself. (The chat message
  is separately persisted by `send_chat_message` via `database.add_chat_message`.)
- **Broadcasts**: the producer emits a CloudEvents `WorkflowEvent` via
  `dispatch.emit` (Temporal Signal fan-out + in-process WS broadcast). The
  `TriggerListenerWorkflow` emits firing-pulse status via
  `broadcast_trigger_status_activity` before/after each child spawn.
- **External API calls**: none.
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: none.
- **Services**: `services.event_waiter`, `services.status_broadcaster`.
- **Python packages**: stdlib only.
- **Environment variables**: none.

## Edge cases & known limits

- `session_id='default'` behaves as a wildcard; setting a unique session ID
  per trigger is the only way to scope messages to a specific node.
- When multiple `chatTrigger` nodes exist with the same session ID, all of
  them fire for a matching message.
- The handler has no timeout; it waits forever until an event arrives or
  the run is cancelled.
- The chat message's persistence to the DB happens in the WebSocket handler
  BEFORE the event is dispatched, so even if no `chatTrigger` is waiting
  the message is still stored in `chat_messages`.

## Related

- **Skills using this as a tool**: none.
- **Sibling triggers**: [`webhookTrigger`](./webhookTrigger.md),
  [`taskTrigger`](./taskTrigger.md).
- **Architecture docs**: [Event Waiter System](../../event_waiter_system.md),
  [Status Broadcaster](../../status_broadcaster.md)
