# Webhook Response (`webhookResponse`)

| Field | Value |
|------|-------|
| **Category** | workflow / utility |
| **Backend handler** | Plugin [`server/nodes/utility/webhook_response/__init__.py`](../../../server/nodes/utility/webhook_response/__init__.py) (`WebhookResponseNode`); dispatch via `BaseNode.execute()` + the `@Operation("respond")` method (body inlined from the deleted `handlers/http.py`). |
| **Tests** | [`server/tests/nodes/test_workflow_triggers.py`](../../../server/tests/nodes/test_workflow_triggers.py) |
| **Skill (if any)** | none |
| **Dual-purpose tool** | no |

## Purpose

Sends a custom HTTP response back to the caller of a companion
[`webhookTrigger`](./webhookTrigger.md) whose `responseMode` is set to
`responseNode`. The trigger parks the incoming request on a future inside
`routers/webhook.py`; this node resolves that future with the configured
status code, body, and content type. Without a `webhookResponse` node in
the workflow, a `responseNode`-mode trigger hangs until the request times
out.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | yes (semantically) | Upstream node outputs are surfaced as `ctx.raw["connected_outputs"]` and made available for template substitution / default body. |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `status_code` | number | `200` | no | - | HTTP status code. Validated `100..599` (Pydantic `ge=100, le=599`). |
| `body` | any | `None` | no | - | Response body. When a string, supports `{{input.<field>}}` and `{{<nodeType>.<field>}}` template substitutions using connected upstream outputs. Non-string values are JSON-serialized. |
| `headers` | object | `{}` | no | - | Header dict (currently not forwarded by `resolve_webhook_response`). |
| `content_type` | string | `application/json` | no | - | Free-form content type string. |

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | The respond op's return payload (see below); typically terminates a response branch. |

### Handler return payload

```ts
{
  sent: true;
  statusCode: number;
  contentType: string;
  bodyLength: number;
}
```

Wrapped in the standard envelope.

## Logic Flow

```mermaid
flowchart TD
  A[BaseNode.execute -> respond op] --> B[outputs = ctx.raw connected_outputs]
  B --> D[body = params.body]
  D --> F{body is str AND outputs?}
  F -- yes --> G[For each node_type, output in outputs:<br/>replace '{{input.key}}' and '{{node_type.key}}' with str value]
  F -- no --> H
  G --> H{body falsy AND outputs present?}
  H -- yes --> I[body = json.dumps first output]
  H -- no --> J
  I --> J[resolve_webhook_response node_id, status_code, body_text, content_type]
  J --> K[Return sent / statusCode / contentType / bodyLength]
```

## Decision Logic

- **Template resolution**: only applied when `body` is a non-empty string AND
  there are connected outputs. Two template formats are supported:
  - `{{input.<key>}}` - pulls from any connected node's output dict
  - `{{<nodeType>.<key>}}` - pulls from a specific connected node type
- **Empty body fallback**: if `body` is falsy AND at least one
  upstream node has output, the op JSON-serialises the FIRST output
  (iteration order of a Python dict keyed by node type) and uses that as
  the body. If there are no upstream outputs the body stays empty.
- **resolve_webhook_response**: imported lazily from `routers.webhook`;
  writes the response dict into the pending-future map so the originating
  request can return.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none directly from the handler.
- **External API calls**: none. Instead, the handler resolves an
  in-process `asyncio.Future` owned by `routers.webhook` which in turn
  completes the pending HTTP response.
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: none.
- **Services**: `routers.webhook.resolve_webhook_response`.
- **Python packages**: `json` (stdlib).
- **Environment variables**: none.

## Edge cases & known limits

- `resolve_webhook_response` is looked up by `node_id`. If no matching
  webhookTrigger is pending (e.g. this node runs in a workflow without an
  upstream `webhookTrigger`, or the trigger uses `response_mode=immediate`),
  `resolve_webhook_response` simply no-ops - the op still returns
  `success=True`. There is no warning that the response was dropped.
- Template substitution uses plain `str.replace`; there is no escaping.
  Binary / non-stringifiable values will render as their Python `repr`.
- The "first connected output" fallback depends on dict iteration order
  (insertion order in CPython 3.7+) - which in turn depends on edge order.
  If multiple upstream nodes are connected, the output picked is not
  explicitly controlled by the user.
- `status_code` is validated by Pydantic (`ge=100, le=599`); an out-of-range
  value fails validation and produces an error envelope before the op runs.
- `webhookResponse` reads `ctx.raw["connected_outputs"]` — the upstream
  outputs are surfaced into the node context by the executor for this plugin.

## Related

- **Companion node**: [`webhookTrigger`](./webhookTrigger.md) -
  `webhookResponse` is only useful when the trigger's `responseMode` is
  `responseNode`.
- **Sibling triggers**: [`webhookTrigger`](./webhookTrigger.md),
  [`chatTrigger`](./chatTrigger.md).
- **Architecture docs**: [Event Waiter System](../../event_waiter_system.md)
