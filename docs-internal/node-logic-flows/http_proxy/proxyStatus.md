# Proxy Status (`proxyStatus`)

| Field | Value |
|------|-------|
| **Category** | proxy / tool |
| **Backend handler** | [`server/nodes/proxy/proxy_status/__init__.py`](../../../server/nodes/proxy/proxy_status/__init__.py) — `ProxyStatusNode.status` (dispatched via `BaseNode.execute()` + `@Operation("status")`; the legacy `handlers/proxy.py` was deleted in Wave 11.D.3) |
| **Tests** | [`server/tests/nodes/test_http_proxy.py`](../../../server/tests/nodes/test_http_proxy.py) |
| **Skill (if any)** | [`server/skills/web_agent/proxy-config-skill/SKILL.md`](../../../server/skills/web_agent/proxy-config-skill/SKILL.md) |
| **Dual-purpose tool** | yes - tool name `proxy_status` (`readonly = True`, `usable_as_tool = True`) |

## Purpose

Read-only snapshot of the `ProxyService` runtime state: per-provider health
stats (score, success rate, latency, bytes transferred) and aggregate stats.
Used by the dashboard and by AI agents that need to reason about which
provider to target.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream trigger |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `provider_name` | string | `""` | no | - | Declared field; the op currently **ignores** it and always returns all providers |

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Status envelope (see below) |

### Output payload

```ts
{
  enabled: boolean;            // proxy_svc.is_enabled()
  providers: Array<ProviderStats>;  // [] when disabled
  stats: Record<string, any>;  // {} when disabled
}
```

`ProviderStats` is produced by `ProviderStats.model_dump()` and includes
`name`, `enabled`, `priority`, `score`, `success_rate`, `avg_latency_ms`,
`total_requests`, `total_bytes`, etc. (see `services/proxy/models.py`).

## Logic Flow

```mermaid
flowchart TD
  A[ProxyStatusNode.status] --> B{proxy_svc and<br/>proxy_svc.is_enabled?}
  B -- no --> C[return enabled:false providers:[] stats:{}]
  B -- yes --> D[providers = proxy_svc.get_providers -> model_dump each]
  D --> E[stats = proxy_svc.get_stats]
  E --> F[return enabled:true providers stats]
```

## Decision Logic

- **Validation**: none - no required params.
- **Branches**: service-disabled short-circuit returns `enabled=false` with empty collections (still a success envelope from `BaseNode.execute()`).
- **Fallbacks**: `provider_name` is ignored - the op always returns the full list.
- **Error paths**: the op has no try/except; any exception propagates to `BaseNode.execute()`, which produces the error envelope.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none.
- **External API calls**: none.
- **File I/O**: none.
- **Subprocess**: none.
- **In-memory reads**: `ProxyService._providers` and rolling history deques.

## External Dependencies

- **Credentials**: none.
- **Services**: `ProxyService` (optional; handler tolerates disabled state).
- **Python packages**: none.
- **Environment variables**: none.

## Edge cases & known limits

- Even when proxy is disabled, the op returns a success envelope with `enabled=false` and empty collections - callers need to inspect `result.enabled` rather than `success`.
- `provider_name` is a declared field but never applied in the op; filtering must happen in the consumer.
- `ProviderStats.model_dump()` exposes every field including computed `score`; the shape is governed by `services/proxy/models.py::ProviderStats`.
- No locking around the read: if another request is mutating `ProxyService._providers` (e.g. `reload_providers` mid-flight), the returned list may reflect a partial state.

## Related

- **Skills using this as a tool**: [`proxy-config-skill/SKILL.md`](../../../server/skills/web_agent/proxy-config-skill/SKILL.md)
- **Companion nodes**: [`proxyConfig`](./proxyConfig.md), [`proxyRequest`](./proxyRequest.md), [`httpRequest`](./httpRequest.md)
- **Architecture docs**: [Proxy Service](../../proxy_service.md)
