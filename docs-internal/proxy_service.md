# Proxy Service

Residential proxy provider management with template-based URL formatting, health-scored auto-selection, geo-targeting, sticky sessions, routing rules, and transparent injection on HTTP nodes.

## Architecture

```
                    ┌──────────────────────────────────────────┐
                    │           ProxyService (singleton)        │
                    │                                          │
  proxy_config ────►│  _providers: Dict[name, _ProviderRuntime]│
  (AI tool)        │  _routing_rules: List[RoutingRule]       │
                    │  _daily_spend_usd: float                 │
                    │                                          │
                    │  get_proxy_url(url, params) -> str|None  │
                    │  report_result(name, ProxyResult)        │
                    └──────────┬───────────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
     TemplateProxyProvider  AuthService      Database
     (format_proxy_url)    (credentials)   (providers, rules)
```

### Request Flow

```
httpRequest(use_proxy=true)
       │
       ▼
_resolve_proxy_url()   (nodes/utility/http_request)
       │
       ▼
ProxyService.get_proxy_url(url, parameters)
       │
       ├── Match routing rule by domain (fnmatch glob)
       ├── Pick provider: explicit > rule preferred > best score
       ├── Load credentials from AuthService
       ├── Build GeoTarget from params/rule/default
       ├── Determine SessionType (rotating/sticky)
       │
       ▼
TemplateProxyProvider.format_proxy_url()
       │
       ▼
"http://user-country-us:pass@gate.provider.com:7777"
       │
       ▼
httpx.AsyncClient(proxy=proxy_url)
```

## Key Files

| File | Description |
|------|-------------|
| `server/services/proxy/__init__.py` | Exports `ProxyService`, `get_proxy_service` (`init_proxy_service` lives in `service.py` and is imported there directly) |
| `server/services/proxy/service.py` | ProxyService singleton - provider selection, URL generation, health scoring |
| `server/services/proxy/providers.py` | TemplateProxyProvider - formats proxy URLs from JSON template config |
| `server/services/proxy/models.py` | Pydantic models: ProviderConfig, RoutingRule, SessionType, GeoTarget, ProxyResult, ProviderStats |
| `server/services/proxy/exceptions.py` | Custom exceptions: ProxyError, ProviderError, NoHealthyProviderError, BudgetExceededError, ProxyConfigError |
| `server/nodes/proxy/proxy_request/__init__.py` | `proxyRequest` plugin - direct proxy HTTP request with retry/failover + usage tracking |
| `server/nodes/proxy/proxy_config/__init__.py` | `proxyConfig` plugin (dual-purpose) - `execute_proxy_config()` + the per-operation helpers (`_add_provider`, `_set_credentials`, `_test_provider`, routing-rule ops, ...) |
| `server/nodes/proxy/proxy_status/__init__.py` | `proxyStatus` plugin - provider health stats |
| `server/nodes/proxy/_usage.py` | `track_proxy_usage()` - shared cost tracker (computes cost from `pricing.json:proxy.<provider>.cost_per_gb`, writes `APIUsageMetric`) |
| `server/nodes/utility/http_request/__init__.py` | `httpRequest` plugin - `_resolve_proxy_url()` transparent proxy injection helper (`use_proxy` param) |
| `server/nodes/document/http_scraper/__init__.py` | `httpScraper` plugin - inline proxy injection via `get_proxy_service().get_proxy_url()` (`use_proxy` param) |
| `server/nodes/scraper/crawlee_scraper/__init__.py` | `crawleeScraper` plugin - `_get_proxy_config()` proxy injection |
| `server/services/handlers/tools.py` | AI-tool dispatch - the `proxyConfig` tool merges params then calls `nodes.proxy.proxy_config.execute_proxy_config()` |
| `server/core/database.py` | CRUD: get/save/delete proxy providers and routing rules |
| `server/models/database.py` | SQLModel tables: ProxyProvider, ProxyRoutingRule |
| `server/main.py` | Startup: `init_proxy_service(...)` then `proxy_svc.startup()` after database init (not wired through the DI container) |
| `server/constants.py` | `PROXY_NODE_TYPES` frozenset |
| `server/skills/web_agent/proxy-config-skill/SKILL.md` | AI skill: proxy provider setup and management |
| `server/skills/web_agent/http-request-skill/SKILL.md` | AI skill: HTTP requests with `useProxy: true` |

## Proxy Nodes (3)

### proxyRequest
Direct proxy HTTP request with full controls. Parameters: method, url, headers, body, timeout, proxyProvider, proxyCountry, sessionType (rotating/sticky), stickyDuration, maxRetries, followRedirects. Includes retry/failover loop and result reporting to health scorer.

### proxyConfig
Dual-purpose node (workflow + AI tool). Operations:

| Operation | Required Params | Description |
|-----------|----------------|-------------|
| `list_providers` | none | List all configured providers |
| `add_provider` | name, gateway_host, gateway_port, url_template | Add provider with JSON template |
| `update_provider` | name + fields | Update existing provider |
| `remove_provider` | name | Delete provider |
| `set_credentials` | name, username, password | Store proxy credentials via AuthService |
| `test_provider` | name | Test via httpbin.org/ip |
| `get_stats` | none | Usage and health statistics |
| `add_routing_rule` | domain_pattern | Route domains to specific providers |
| `list_routing_rules` | none | List all routing rules |
| `remove_routing_rule` | rule_id | Delete routing rule |

### proxyStatus
View provider health stats. Optionally filter by provider name.

## Transparent Proxy on HTTP Nodes

The `httpRequest`, `httpScraper`, and `crawleeScraper` plugins support a `use_proxy` parameter. When set, the plugin calls `ProxyService.get_proxy_url()` and passes the result to `httpx.AsyncClient(proxy=...)`. `httpRequest` routes through `_resolve_proxy_url()` (which swallows exceptions so the request proceeds without proxy on failure); `httpScraper` inlines the same lookup directly. Graceful degradation either way.

`httpRequest` is also a dual-purpose AI tool (`tool_name = "http_request"`, `group = ("utility", "tool")`). Its Pydantic `Params` is the AI-tool schema, so `use_proxy` is exposed to the LLM. The LLM only sets the flag -- the proxy service handles provider selection, geo-targeting, and session type from its own configuration.

```python
# In server/nodes/utility/http_request/__init__.py
class HttpRequestNode(ActionNode):
    type = "httpRequest"
    tool_name = "http_request"

    class Params(BaseModel):
        url: str
        method: str = "GET"
        body: Optional[Any] = None
        use_proxy: bool = False  # LLM sets this, proxy service handles the rest
        proxy_provider: str = "auto"
        proxy_country: str = ""
        session_type: Literal["rotating", "sticky"] = "rotating"
```

## Template System

### url_template JSON

Each provider has a JSON `url_template` that controls how credentials and parameters are encoded into the proxy URL. This eliminates provider-specific code -- one class (`TemplateProxyProvider`) handles all providers.

**Fields:**
| Field | Description | Example |
|-------|-------------|---------|
| `param_field` | Where params go: `"username"`, `"password"`, or `"none"` | `"username"` |
| `username_prefix` | Template for base username. `{username}` replaced with actual. | `"{username}"` |
| `username_param_separator` | Separator between username and first param | `"-"` |
| `param_separator` | Separator between params | `"-"` |
| `param_keys` | Map of param names to format strings. `{v}` = value. | `{"country": "country-{v}"}` |
| `country_case` | `"lower"` or `"upper"` for country codes | `"lower"` |
| `city_separator` | Replaces spaces in city/state names | `"_"` |

### Example Templates

**Smartproxy/Decodo** (username-based, dash separator):
```json
{
  "param_field": "username",
  "username_prefix": "{username}",
  "username_param_separator": "-",
  "param_separator": "-",
  "param_keys": {
    "country": "country-{v}",
    "city": "city-{v}",
    "state": "state-{v}",
    "session_id": "session-{v}",
    "session_duration": "sessTime-{v}"
  },
  "country_case": "lower",
  "city_separator": "_"
}
```

Result: `http://myuser-country-us-session-abc123:mypass@gate.smartproxy.com:7777`

**IPRoyal** (password-based):
```json
{
  "param_field": "password",
  "username_prefix": "{username}",
  "param_separator": "_",
  "param_keys": {
    "country": "country-{v}",
    "city": "city-{v}",
    "session_id": "session-{v}"
  },
  "country_case": "lower",
  "city_separator": "_"
}
```

Result: `http://myuser:mypasscountry-us_session-abc123@geo.iproyal.com:12321`

## Health Scoring

Providers are ranked by a composite score (0.0 - 1.0):

| Weight | Factor | Formula |
|--------|--------|---------|
| 0.4 | Success rate | `successes / total_requests` |
| 0.2 | Latency | `1.0 - (avg_ms / 10000)` |
| 0.3 | Cost | `1.0 - (cost_per_gb / 10.0)` |
| 0.1 | Freshness | `min(1.0, sample_count / 10)` |

A provider is considered unhealthy when its success rate drops below 30%. The last 100 results are kept per provider.

## Provider Selection Priority

1. **Explicit**: `parameters.proxyProvider` specified by user/node
2. **Routing rule**: Domain matches a rule with `preferred_providers`
3. **Best score**: Auto-select highest-scoring healthy provider

Fallback: if preferred providers from a routing rule are all unhealthy, falls back to best available provider.

## Routing Rules

Domain-based rules using `fnmatch` glob patterns:

```python
RoutingRule(
    domain_pattern="*.linkedin.com",
    preferred_providers=["smartproxy", "brightdata"],
    required_country="US",
    session_type=SessionType.STICKY,
    sticky_duration_seconds=300,
    max_retries=3,
    failover=True,
    min_success_rate=0.7,
    priority=0,  # lower = evaluated first
)
```

Rules are matched first-match against the target URL's hostname.

## Credential Storage

Proxy credentials are stored via AuthService using the pattern `proxy_{name}_username` and `proxy_{name}_password`:

```python
# In nodes/proxy/proxy_config _set_credentials operation:
await auth_svc.store_api_key(f"proxy_{name}_username", username, [])
await auth_svc.store_api_key(f"proxy_{name}_password", password, [])
```

On startup, `ProxyService.startup()` loads providers from the DB and `_load_providers()` reads credentials from AuthService for each provider.

## Database Tables

### ProxyProvider

| Column | Type | Description |
|--------|------|-------------|
| id | Integer (PK) | Auto-increment |
| name | String (unique) | Provider identifier |
| enabled | Boolean | Active flag |
| priority | Integer | Lower = preferred |
| cost_per_gb | Float | USD per GB |
| gateway_host | String | Proxy gateway hostname |
| gateway_port | Integer | Proxy gateway port |
| url_template | Text (JSON) | Template config for URL formatting |
| geo_coverage | Text (JSON) | Supported country codes |
| sticky_support | Boolean | Supports sticky sessions |
| max_sticky_seconds | Integer | Max sticky duration |
| created_at | DateTime | Creation timestamp |

### ProxyRoutingRule

| Column | Type | Description |
|--------|------|-------------|
| id | Integer (PK) | Auto-increment |
| domain_pattern | String | fnmatch glob pattern |
| preferred_providers | Text (JSON) | Provider name array |
| required_country | String | ISO country code |
| session_type | String | "rotating" or "sticky" |
| sticky_duration_seconds | Integer | Sticky session duration |
| max_retries | Integer | Retry attempts |
| failover | Boolean | Try other providers on failure |
| min_success_rate | Float | Health threshold |
| priority | Integer | Rule evaluation order |

## Configuration

Environment variables in `server/.env`:

```bash
PROXY_ENABLED=true              # Enable proxy service
PROXY_DEFAULT_COUNTRY=          # Default country code (empty = no default)
PROXY_BUDGET_DAILY_USD=         # Daily spend limit (empty = unlimited)
```

## Service Lifecycle

```python
# In server/main.py startup
from services.proxy.service import init_proxy_service
proxy_svc = init_proxy_service(           # Builds + stores the module-level singleton
    auth_service=container.auth_service(),
    database=container.database(),
    settings=settings,
)
await proxy_svc.startup()                 # Loads providers from DB + credentials from AuthService

# In server/main.py shutdown
from services.proxy.service import get_proxy_service
_proxy_svc = get_proxy_service()
if _proxy_svc:
    await _proxy_svc.shutdown()           # Clears in-memory state
```

`init_proxy_service` / `get_proxy_service` live in `services/proxy/service.py` (the latter is re-exported from `services/proxy/__init__.py`); the singleton is module-level, not wired through the DI container. The proxy service always initializes regardless of `PROXY_ENABLED`. Providers are managed dynamically by the LLM via the `proxyConfig` tool. If no providers are configured, `get_proxy_url()` raises `NoHealthyProviderError`.

## Adding a New Provider (AI Workflow)

The LLM uses the `proxy_config` tool in 3 steps:

1. **Add provider** with gateway host, port, and url_template JSON
2. **Set credentials** with username and password
3. **Test provider** via httpbin.org/ip health check

If the provider uses an unfamiliar URL format, the LLM can use `python_code` to reverse-engineer the template from the provider's documentation examples.

## Cost Tracking

Proxy usage is tracked via the pricing service. When a proxied request completes, `track_proxy_usage()` in `server/nodes/proxy/_usage.py` (called by the `proxyRequest` plugin) calculates cost based on bytes transferred and the provider's `cost_per_gb` from `server/config/pricing.json` (`proxy.<provider>.cost_per_gb`). The cost is persisted to the `APIUsageMetric` table.

The daily spend is also tracked in-memory on `ProxyService._daily_spend_usd` and checked against `PROXY_BUDGET_DAILY_USD` before each request.
