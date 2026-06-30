# Start (`start`)

| Field | Value |
|------|-------|
| **Category** | workflow |
| **Backend handler** | Plugin [`server/nodes/workflow/start/__init__.py`](../../../server/nodes/workflow/start/__init__.py) (`StartNode`); dispatch via `BaseNode.execute()` + the `@Operation("emit")` method. |
| **Tests** | [`server/tests/nodes/test_workflow_triggers.py`](../../../server/tests/nodes/test_workflow_triggers.py) |
| **Skill (if any)** | none |
| **Dual-purpose tool** | no |

## Purpose

Manual workflow entry point. Emits a user-authored JSON payload as the
workflow's initial data. Every workflow that is not kicked off by an event
trigger (`webhookTrigger`, `chatTrigger`, `cronScheduler`, etc.) starts from a
`start` node. Unlike the event triggers in this category, `start` does not
register an event waiter - it completes immediately with the parsed initial
data.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| (none) | - | - | `start` has no inputs - it is the entry point |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `initial_data` | any (JSON string or value) | `None` | no | - | Surfaced as the node output. If a JSON string, it is parsed (parse failure -> `{}`); if `None` -> `{}`; otherwise returned as-is. |

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | The parsed JSON value of `initialData` (or `{}` if invalid). |

### Output payload

```ts
// initial_data resolved:
//   None      -> {}
//   JSON str  -> parsed value (invalid -> {})
//   any other -> returned as-is
unknown
```

Wrapped in the standard envelope: `{ success: true, result: <payload>, node_id, node_type: "start" }`.

## Logic Flow

```mermaid
flowchart TD
  A[BaseNode.execute -> emit op] --> B[raw = params.initial_data]
  B --> C{raw is None?}
  C -- yes --> E[return {}]
  C -- no --> D{raw is str?}
  D -- yes --> F{json.loads?}
  F -- ok --> G[return parsed]
  F -- Exception --> E
  D -- no --> H[return raw as-is]
```

## Decision Logic

- **Validation**: none. `None` `initial_data` returns `{}`.
- **Branches**: `None` -> `{}`; string -> `json.loads` (failure -> `{}`);
  any other value returned unchanged.
- **Fallbacks**: JSON decode errors are swallowed and produce `{}`.
- **Error paths**: the op does not raise; it always returns a
  `success=True` envelope. A malformed `initial_data` string is **not** reported
  as an error.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none (the caller emits `executing` / `success` via
  `StatusBroadcaster`; the handler itself is silent).
- **External API calls**: none.
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: none.
- **Services**: none.
- **Python packages**: `json` (stdlib).
- **Environment variables**: none.

## Edge cases & known limits

- `start` always returns `success=True` regardless of `initial_data` content.
  Invalid JSON in a string is silently coerced to `{}` - users get no warning
  their data was dropped.
- `start` is a plain `ActionNode` (group `workflow`), not an event trigger -
  it registers no event waiter and completes immediately. It carries
  `ui_hints` `hideInputSection` / `hideOutputSection` / `hasInitialDataBlob`.
- The payload is returned as-is for non-string values; no validation, schema
  enforcement, or nested template resolution is performed by the op.

## Related

- **Skills using this as a tool**: none.
- **Other nodes that consume this output**: any downstream node - typical
  pattern is `start -> aiAgent` or `start -> httpRequest` in a hand-run
  workflow.
- **Architecture docs**: [Workflow Schema](../../workflow-schema.md), [Execution Engine Design](../../DESIGN.md)
- **Sibling triggers**: [`cronScheduler`](./cronScheduler.md), [`timer`](./timer.md), [`webhookTrigger`](./webhookTrigger.md), [`chatTrigger`](./chatTrigger.md), [`taskTrigger`](./taskTrigger.md), [`webhookResponse`](./webhookResponse.md)
