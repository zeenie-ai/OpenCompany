# Proxy Config (`proxyConfig`)

| Field | Value |
|------|-------|
| **Category** | proxy / tool (dual-purpose) |
| **Backend handler** | [`server/nodes/proxy/proxy_config/__init__.py`](../../../server/nodes/proxy/proxy_config/__init__.py) — `ProxyConfigNode.dispatch` (via `BaseNode.execute()` + `@Operation("dispatch")`) calls the module-level `execute_proxy_config(...)` 10-op dispatcher. The AI-tool fast path in [`server/services/handlers/tools.py`](../../../server/services/handlers/tools.py) imports the same `execute_proxy_config`. The legacy `handlers/proxy.py` was deleted in Wave 11. |
| **Tests** | [`server/tests/nodes/test_http_proxy.py`](../../../server/tests/nodes/test_http_proxy.py) |
| **Skill (if any)** | [`server/skills/web_agent/proxy-config-skill/SKILL.md`](../../../server/skills/web_agent/proxy-config-skill/SKILL.md) |
| **Dual-purpose tool** | yes - tool name `proxy_config` (`destructive = True`, `usable_as_tool = True`) |

## Purpose

Admin-style CRUD node for managing proxy providers, credentials, and routing
rules. Dispatches on the `operation` param. Works the same as both a workflow
node and an AI-agent tool: `ProxyConfigNode.dispatch` calls the module-level
`execute_proxy_config(params.model_dump())`, and the AI-tool fast path in
`tools.py` imports the same function — there is no separate handler shim.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream trigger |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `operation` | options | `list_providers` | **yes** | - | One of the 10 operations below |
| `name` | string | `""` | (most ops) | `operation in [add/update/remove/set_credentials/test_provider]` | Provider name |
| `gateway_host` | string | `""` | no | `operation in [add/update]` | Proxy hostname |
| `gateway_port` | number | `0` | no | `operation in [add/update]` | Proxy port |
| `url_template` | string (JSON) | `"{}"` | no | `operation in [add/update]` | JSON template config |
| `cost_per_gb` | number | `0` | no | `operation in [add/update]` | USD per GB |
| `priority` | number | `50` | no | `operation in [add/update]` | Selection priority 0-100, higher ranks first |
| `enabled` | `Optional[bool]` | `null` | no | `operation in [add/update]` | Whether the provider is active (None = unchanged on update) |
| `username` | string | `""` | yes (op=set_credentials) | `operation=set_credentials` | Proxy username |
| `password` | string (password widget) | `""` | yes (op=set_credentials) | `operation=set_credentials` | Proxy password |
| `domain_pattern` | string | `""` | yes (op=add_routing_rule) | `operation=add_routing_rule` | Glob like `*.example.com` |
| `preferred_providers` | `Any` (JSON array string) | `"[]"` | no | `operation=add_routing_rule` | `["provider1"]` |
| `required_country` | string | `""` | no | `operation=add_routing_rule` | ISO country |
| `session_type` | options | `rotating` | no | `operation=add_routing_rule` | `rotating` or `sticky` |
| `rule_id` | `Optional[int]` | `null` | yes (op=remove_routing_rule) | `operation=remove_routing_rule` | Routing rule ID to remove |

### Operation index

| `operation` | Effect |
|-------------|--------|
| `list_providers` | `proxy_svc.get_providers()` -> `[ProviderStats]` |
| `get_stats` | `proxy_svc.get_stats()` -> dict |
| `list_routing_rules` | `proxy_svc.get_routing_rules()` -> `[RoutingRule]` |
| `add_provider` | `db.save_proxy_provider(...)` + `proxy_svc.reload_providers()` |
| `update_provider` | merge existing row + save + reload |
| `remove_provider` | `db.delete_proxy_provider(name)` + reload |
| `set_credentials` | `auth_svc.store_api_key('proxy_<name>_username', ...)` + `..._password` + reload |
| `test_provider` | `get_proxy_url` for `https://httpbin.org/ip` + real GET to httpbin (30s timeout) |
| `add_routing_rule` | `db.save_proxy_routing_rule(...)` + reload |
| `remove_routing_rule` | `db.delete_proxy_routing_rule(int(rule_id))` + reload |

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Standard envelope; `result` field carries op-specific payload |

### Output payload (success)

```ts
// execute_proxy_config returns this dict; dispatch returns it directly,
// then BaseNode.execute() wraps it once in the standard success envelope.
{
  success: true,
  operation: string,
  // op-specific fields: providers / rules / stats / name / rule_id / ip+latency_ms / status_code / updated_fields / domain_pattern
}
```

On `success=false`, `dispatch` raises `NodeUserError(result.error)` so the failure surfaces as an error envelope (a single WARN line), NOT a nested `result.result.success=false` payload.

## Logic Flow

```mermaid
flowchart TD
  A[ProxyConfigNode.dispatch] --> B[execute_proxy_config params.model_dump]
  B --> C{operation}
  C -- list_providers --> LP[proxy_svc.get_providers -> model_dump list]
  C -- get_stats --> GS[proxy_svc.get_stats]
  C -- list_routing_rules --> LR[proxy_svc.get_routing_rules]
  C -- add_provider --> AP{name truthy?}
  AP -- no --> Ename[success=false error: Provider name is required]
  AP -- yes --> AP2[parse url_template JSON fallback error]
  AP2 --> AP3[db.save_proxy_provider + reload_providers]
  C -- update_provider --> UP{name truthy?}
  UP -- no --> Ename
  UP -- yes --> UP2[db.get_proxy_provider existing?]
  UP2 -- not found --> Enf[success=false error not found]
  UP2 -- found --> UP3[merge updates + save + reload]
  C -- remove_provider --> RP[db.delete_proxy_provider + reload]
  C -- set_credentials --> SC{username+password?}
  SC -- no --> Ecred[success=false]
  SC -- yes --> SC2[auth_svc.store_api_key user + pass + reload]
  C -- test_provider --> TP{proxy_svc enabled?}
  TP -- no --> Etp[success=false]
  TP -- yes --> TP2[get_proxy_url for httpbin.org/ip<br/>httpx GET httpbin.org/ip 30s timeout]
  TP2 -- ok --> TP3[return ip + latency_ms + status_code]
  TP2 -- raises --> TP4[success=false error str(e)]
  C -- add_routing_rule --> AR{domain_pattern?}
  AR -- no --> Erule[success=false]
  AR -- yes --> AR2[parse preferred_providers JSON fallback []]
  AR2 --> AR3[db.save_proxy_routing_rule + reload]
  C -- remove_routing_rule --> RR{rule_id?}
  RR -- no --> Erid[success=false]
  RR -- yes --> RR2[db.delete_proxy_routing_rule int(rule_id) + reload]
  C -- unknown --> Eunk[success=false error Unknown operation]
  LP --> W[dispatch: success? return dict<br/>else raise NodeUserError]
  GS --> W
  LR --> W
  AP3 --> W
  UP3 --> W
  RP --> W
  SC2 --> W
  TP3 --> W
  TP4 --> W
  AR3 --> W
  RR2 --> W
  Ename --> W
  Enf --> W
  Ecred --> W
  Etp --> W
  Erule --> W
  Erid --> W
  Eunk --> W
```

## Decision Logic

- **Validation**: each op checks its own required fields before any service call.
- **Branches**: 10 operations dispatched by string match; unknown op returns `Unknown operation: <op>`.
- **Fallbacks**: `preferred_providers` JSON parse error -> `[]`; `url_template` JSON parse error -> early return with error (no fallback).
- **Error paths**: per-op validation failures return `{success: false, error: "..."}` from `execute_proxy_config`. `dispatch` checks `result.get("success")` and, when false, `raise NodeUserError(result.error or "Proxy config failed")`, so the failure surfaces as a single-WARN error envelope rather than a nested success-false payload.

## Side Effects

- **Database writes**: `proxy_providers` table via `save_proxy_provider` / `delete_proxy_provider`; `proxy_routing_rules` table via `save_proxy_routing_rule` / `delete_proxy_routing_rule`.
- **Credential writes**: `EncryptedAPIKey` rows for keys `proxy_<name>_username` and `proxy_<name>_password` (on `set_credentials`).
- **In-memory writes**: `proxy_svc.reload_providers()` reinitialises `_providers` / `_routing_rules` dicts on every successful mutation.
- **Broadcasts**: none.
- **External API calls**: `GET https://httpbin.org/ip` through the proxy on `operation=test_provider` (30s timeout).
- **File I/O**: none directly (DB only).
- **Subprocess**: none.

## External Dependencies

- **Credentials**: `auth_service.store_api_key(...)` for proxy username/password.
- **Services**: `ProxyService`, `Database`, `auth_service` (via `container`).
- **Python packages**: `httpx`, `json`.
- **Environment variables**: none.

## Edge cases & known limits

- The workflow-node path and the AI-tool path call the same `execute_proxy_config` function, so behaviour is identical between them.
- A failed op no longer double-wraps: `dispatch` raises `NodeUserError` on `success=false`, so tests inspect the error envelope rather than `result["result"]["success"]`.
- `test_provider` does a **real** HTTP call to `httpbin.org/ip` when invoked - there is no offline / dry-run path; this call is un-mocked in production.
- `update_provider` silently returns `success=true` with `updated_fields=[]` if no fields were changed.
- `remove_provider` / `remove_routing_rule` do not verify the row existed before issuing the delete; they always return `success=true` unless the DB raises.
- `url_template` is re-serialized via `json.dumps(...)` before being saved, so whitespace in the caller's JSON is normalised.

## Related

- **Skills using this as a tool**: [`proxy-config-skill/SKILL.md`](../../../server/skills/web_agent/proxy-config-skill/SKILL.md)
- **Companion nodes**: [`proxyRequest`](./proxyRequest.md), [`proxyStatus`](./proxyStatus.md), [`httpRequest`](./httpRequest.md)
- **Architecture docs**: [Proxy Service](../../proxy_service.md)
