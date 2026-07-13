---
name: http-request-skill
description: Make HTTP requests to external APIs and web services. Supports GET, POST, PUT, DELETE, PATCH methods with headers and JSON body.
allowed-tools: http_request
metadata:
  author: opencompany
  version: "1.0"
  category: integration

---

# HTTP Request Tool

Make HTTP requests to external APIs and web services.

## How It Works

This skill provides instructions for the **HTTP Request** tool node. Connect the **HTTP Request** node to Zeenie's `input-tools` handle to enable API calls.

## http_request Tool

Make an HTTP request to any URL.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| url | string | Yes | Full URL to request (e.g., `https://api.example.com/data`) |
| method | string | No | HTTP method: `GET`, `POST`, `PUT`, `DELETE`, `PATCH` (default: `GET`) |
| body | object | No | Request body as JSON object (for POST/PUT/PATCH) |
| useProxy | boolean | No | Route through proxy (default: `false`). The proxy service handles provider selection, geo-targeting, and session type automatically. |

### Node Parameters

Additional options can be configured on the node:

| Parameter | Description |
|-----------|-------------|
| headers | Custom headers as JSON (e.g., `{"Authorization": "Bearer token"}`) |
| base_url | Base URL prepended to the url parameter |

### Response Format

```json
{
  "status": 200,
  "data": { "key": "value" },
  "url": "https://api.example.com/data",
  "method": "GET",
  "proxied": false
}
```

### Examples

**GET request (fetch data):**
```json
{
  "url": "https://api.example.com/users/123",
  "method": "GET"
}
```

**POST request (create resource):**
```json
{
  "url": "https://api.example.com/users",
  "method": "POST",
  "body": {
    "name": "John Doe",
    "email": "john@example.com"
  }
}
```

**PUT request (update resource):**
```json
{
  "url": "https://api.example.com/users/123",
  "method": "PUT",
  "body": {
    "name": "John Updated"
  }
}
```

**DELETE request:**
```json
{
  "url": "https://api.example.com/users/123",
  "method": "DELETE"
}
```

**PATCH request (partial update):**
```json
{
  "url": "https://api.example.com/users/123",
  "method": "PATCH",
  "body": {
    "status": "active"
  }
}
```

### Real-World Examples

**Get Bitcoin price:**
```json
{
  "url": "https://api.coindesk.com/v1/bpi/currentprice.json",
  "method": "GET"
}
```

**Get weather:**
```json
{
  "url": "https://api.openweathermap.org/data/2.5/weather?q=London&appid=YOUR_KEY",
  "method": "GET"
}
```

**Post to webhook:**
```json
{
  "url": "https://hooks.slack.com/services/xxx",
  "method": "POST",
  "body": {
    "text": "Hello from OpenCompany!"
  }
}
```

**Check website status:**
```json
{
  "url": "https://example.com",
  "method": "GET"
}
```

### Error Responses

**Timeout:**
```json
{
  "error": "Request timed out"
}
```

**Connection failed:**
```json
{
  "error": "Connection failed: Unable to reach host"
}
```

**HTTP error:**
```json
{
  "status": 404,
  "data": "Not Found",
  "url": "https://api.example.com/missing",
  "method": "GET"
}
```

## HTTP Status Codes

| Status | Meaning | Action |
|--------|---------|--------|
| 200-299 | Success | Process the response data |
| 400 | Bad Request | Check request parameters |
| 401 | Unauthorized | Check API key/authentication |
| 403 | Forbidden | Insufficient permissions |
| 404 | Not Found | Check URL path |
| 429 | Too Many Requests | Rate limited, wait and retry |
| 500-599 | Server Error | External service issue |

## Use Cases

| Use Case | Method | Description |
|----------|--------|-------------|
| Fetch data | GET | Retrieve resources |
| Create resource | POST | Add new data |
| Update resource | PUT/PATCH | Modify existing data |
| Delete resource | DELETE | Remove data |
| Health check | GET | Verify service availability |
| Webhook trigger | POST | Send events to services |

## Proxy Usage

To route a request through a residential proxy, just set `useProxy: true`. The proxy service automatically selects the best provider, geo-target, and session type from its configuration. A proxy provider must be configured first via the `proxy_config` tool (see proxy-config-skill).

**Proxied request:**
```json
{
  "url": "https://example.com/data",
  "method": "GET",
  "useProxy": true
}
```

If no proxy providers are configured or the proxy lookup fails, the request proceeds directly without a proxy and `"proxied": false` appears in the response.

## Guidelines

1. **URLs**: Must be fully qualified with protocol (https://)
2. **Authentication**: Use node headers for API keys/tokens
3. **Timeout**: Default 30 seconds
4. **JSON body**: Automatically serialized for POST/PUT/PATCH
5. **Response**: JSON responses are automatically parsed
6. **Proxy**: Set `useProxy: true` to route through proxy -- the proxy service handles all routing details

## Security Notes

1. Never expose API keys in responses to users
2. Validate URLs before making requests
3. Avoid internal/private network addresses (localhost, 192.168.x.x)
4. Respect rate limits of external services
5. Don't store sensitive data from API responses

## Setup Requirements

1. Connect the **HTTP Request** node to Zeenie's `input-tools` handle
2. Configure authentication headers on the node if needed
3. Ensure network access to target APIs
4. For proxy usage: configure a proxy provider via the proxy-config-skill first
