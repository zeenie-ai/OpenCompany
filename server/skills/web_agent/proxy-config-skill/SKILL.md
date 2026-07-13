---
name: proxy-config-skill
description: Configure residential proxy providers and make proxied HTTP requests with geo-targeting.
allowed-tools: "proxy_config proxy_request proxy_status python_executor"
metadata:
  author: opencompany
  version: "3.0"
  category: integration

---

# Proxy Configuration Skill

You manage residential proxy providers. Providers are configured with a JSON `url_template` that controls how geo/session parameters are encoded into the proxy URL.

## Setup Workflow

To add a new proxy provider, make these tool calls in order:

### Step 1: Add the provider

Call `proxy_config` with `operation: "add_provider"`. The `url_template` field is a **JSON string**.

Most residential proxy providers use username-based parameter encoding with dash separators. Use this default template unless the provider docs show otherwise:

```
proxy_config({
  "operation": "add_provider",
  "name": "smartproxy",
  "gateway_host": "gate.smartproxy.com",
  "gateway_port": 7777,
  "url_template": "{\"param_field\":\"username\",\"username_prefix\":\"{username}\",\"username_param_separator\":\"-\",\"param_separator\":\"-\",\"param_keys\":{\"country\":\"country-{v}\",\"city\":\"city-{v}\",\"state\":\"state-{v}\",\"session_id\":\"session-{v}\",\"session_duration\":\"sessTime-{v}\"},\"country_case\":\"lower\",\"city_separator\":\"_\"}"
})
```

### Step 2: Set credentials

```
proxy_config({
  "operation": "set_credentials",
  "name": "smartproxy",
  "username": "the_username",
  "password": "the_password"
})
```

### Step 3: Test

```
proxy_config({
  "operation": "test_provider",
  "name": "smartproxy"
})
```

If test returns `"success": true`, the provider is ready. If it fails, check credentials and gateway host/port.

## Making Proxied Requests

Once a provider is configured:

```
proxy_request({
  "url": "https://example.com/page",
  "proxyCountry": "US"
})
```

The system auto-selects the best healthy provider. To force a specific one:

```
proxy_request({
  "url": "https://example.com/page",
  "proxyProvider": "smartproxy",
  "proxyCountry": "US",
  "sessionType": "sticky"
})
```

## url_template Reference

The `url_template` JSON controls how the system builds the proxy URL. It determines how username/password strings are constructed with geo and session parameters.

**Fields:**
- `param_field`: Where params go. `"username"` (80% of providers), `"password"`, or `"none"`.
- `username_prefix`: Template for username base. `{username}` is replaced with actual username.
- `username_param_separator`: Separator between username and first param (e.g. `"-"`).
- `param_separator`: Separator between params (e.g. `"-"`).
- `param_keys`: Object mapping param names to format strings. `{v}` is replaced with the value.
  - `country`: e.g. `"country-{v}"` produces `country-us`
  - `city`: e.g. `"city-{v}"` produces `city-new_york`
  - `state`: e.g. `"state-{v}"` produces `state-california`
  - `session_id`: e.g. `"session-{v}"` produces `session-abc123`
  - `session_duration`: e.g. `"sessTime-{v}"` produces `sessTime-10`
- `country_case`: `"lower"` or `"upper"` for country codes.
- `city_separator`: Replaces spaces in city/state names (e.g. `"_"` makes `new_york`).

**Result for username encoding with country US:**
`http://myuser-country-us:mypass@gate.provider.com:7777`

**Result for password encoding with country US:**
`http://myuser:mypasscountry-us@gate.provider.com:7777`

## Common Provider Templates

### DataImpulse (username-based, dash separator)
Gateway: `gw.dataimpulse.com:823`
```json
{"param_field":"username","username_prefix":"{username}","username_param_separator":"__","param_separator":"__","param_keys":{"country":"country-{v}","city":"city-{v}","session_id":"session-{v}","session_duration":"sessionduration-{v}"},"country_case":"lower","city_separator":"_"}
```

### Decodo/Smartproxy (username-based, dash separator)
Gateway: `gate.smartproxy.com:7777`
```json
{"param_field":"username","username_prefix":"{username}","username_param_separator":"-","param_separator":"-","param_keys":{"country":"country-{v}","city":"city-{v}","state":"state-{v}","session_id":"session-{v}","session_duration":"sessTime-{v}"},"country_case":"lower","city_separator":"_"}
```

### Bright Data (username-based, dash separator)
Gateway: `brd.superproxy.io:22225`
```json
{"param_field":"username","username_prefix":"{username}","username_param_separator":"-","param_separator":"-","param_keys":{"country":"country-{v}","city":"city-{v}","state":"state-{v}","session_id":"session-{v}"},"country_case":"lower","city_separator":"_"}
```

### IPRoyal (password-based)
Gateway: `geo.iproyal.com:12321`
```json
{"param_field":"password","username_prefix":"{username}","param_separator":"_","param_keys":{"country":"country-{v}","city":"city-{v}","session_id":"session-{v}","session_duration":"sessionduration-{v}"},"country_case":"lower","city_separator":"_"}
```

### Custom/Generic (no encoding)
For providers that don't encode params in credentials:
```json
{"param_field":"none"}
```

## If Provider Uses Unfamiliar Format

Use `python_code` to reverse-engineer the template from the provider's example URL:

```python
import json

# Example from provider docs:
# Username: user123-cc-US-city-NewYork-sess-abc123
# Password: pass456
# Host: proxy.example.com:8080

template = {
    "param_field": "username",
    "username_prefix": "{username}",
    "username_param_separator": "-",
    "param_separator": "-",
    "param_keys": {
        "country": "cc-{v}",
        "city": "city-{v}",
        "session_id": "sess-{v}"
    },
    "country_case": "upper",
    "city_separator": ""
}

# Verify it produces the right URL
username = "user123"
params = []
params.append(template["param_keys"]["country"].replace("{v}", "US"))
params.append(template["param_keys"]["city"].replace("{v}", "NewYork"))
params.append(template["param_keys"]["session_id"].replace("{v}", "abc123"))
param_str = template["param_separator"].join(params)
result = f"{username}{template['username_param_separator']}{param_str}"
print(f"Built username: {result}")
assert result == "user123-cc-US-city-NewYork-sess-abc123", f"Mismatch: {result}"

output = json.dumps(template)
print(f"Template: {output}")
```

Then pass the `output` as the `url_template` string to `proxy_config(add_provider)`.

## All proxy_config Operations

| Operation | Required Parameters | Description |
|-----------|-------------------|-------------|
| `list_providers` | none | List all configured providers |
| `add_provider` | `name`, `gateway_host`, `gateway_port`, `url_template` | Add a new provider |
| `update_provider` | `name` + any fields to change | Update existing provider |
| `remove_provider` | `name` | Delete a provider |
| `set_credentials` | `name`, `username`, `password` | Store proxy credentials |
| `test_provider` | `name` | Test via httpbin.org/ip |
| `get_stats` | none | Usage statistics |
| `add_routing_rule` | `domain_pattern` | Route domains to specific providers |
| `list_routing_rules` | none | List all routing rules |
| `remove_routing_rule` | `rule_id` | Delete a routing rule |

Optional fields for `add_provider`/`update_provider`: `enabled` (bool), `priority` (int, lower=preferred), `cost_per_gb` (float).

## proxy_request Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `url` | yes | | Target URL |
| `method` | no | GET | HTTP method |
| `headers` | no | | JSON string of headers |
| `body` | no | | Request body string |
| `proxyProvider` | no | auto | Provider name |
| `proxyCountry` | no | | ISO country code (US, GB, DE) |
| `sessionType` | no | rotating | `rotating` or `sticky` |
| `timeout` | no | 30 | Seconds |
| `maxRetries` | no | 3 | Retry with failover |

## Transparent Proxy on HTTP Nodes

The `httpRequest` and `httpScraper` nodes have built-in proxy support. Set `useProxy: true` and the proxy service handles everything -- provider selection, geo-targeting, and session type are all managed from the proxy service configuration (provider priorities, routing rules, default country).

When the AI Agent uses `http_request` as a tool, it can set `useProxy: true` in the tool arguments. No other proxy fields needed.

**When to use which:**
- Use **http_request with useProxy: true** for proxied requests (simple, proxy service handles routing)
- Use **proxy_request** tool only when you need explicit control over retries and failover

## Notes

- Always run `test_provider` after setting up a new provider
- If the user does not know their credentials, they need to get them from their proxy provider dashboard
- If test fails: check credentials, gateway host/port, and that the url_template matches the provider's format
- Use `proxy_status` to check provider health before making requests
- `sessionType: "sticky"` keeps the same IP across requests (good for scraping sessions)
- `sessionType: "rotating"` gets a new IP each request (good for anonymity)
