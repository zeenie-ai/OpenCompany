---
name: cloudflare-skill
description: Work with Cloudflare via the official cf CLI — check auth identity, list zones, list/create/delete DNS records, query the GraphQL Analytics API (zone traffic, Web Analytics/RUM), and run any other cf command. Output is parsed JSON.
allowed-tools: "cloudflare"
metadata:
  author: opencompany
  version: "1.0"
  category: deployment

---

# Cloudflare Skill

Wrapper over the official [Cloudflare CLI](https://blog.cloudflare.com/cf-cli-local-explorer/)
(`cf`, a Technical Preview — "the next version of Wrangler"). Typed
operations for the core flows plus a `custom` passthrough that covers
the entire cf surface. cf prints JSON to stdout by default, so results
come back parsed in `result`.

## Tool: cloudflare

### Operations

| Operation | Purpose | Key fields |
|---|---|---|
| `whoami` | Auth status + identity (email, scopes) | — |
| `zones_list` | List/filter zones (parsed JSON) | `name_filter`, `account_id` (both optional) |
| `dns_records_list` | DNS records for a zone (parsed JSON) | `zone` (zone ID or domain) |
| `dns_record_create` | Create a DNS record | `zone`, `record_body` (raw JSON record) |
| `dns_record_delete` | Delete a DNS record by id | `zone`, `record_id` |
| `graphql_query` | GraphQL Analytics API (traffic, RUM, DNS analytics) | `graphql_query`, `graphql_variables` (JSON) |
| `custom` | Any other cf command | `command` — exactly what you would type after `cf ` |

### Response

```json
{
  "operation": "zones_list",
  "success": true,
  "result": [{ "id": "023e105f4ecef8ad9ca31a8372d0c353", "name": "example.com", "status": "active" }],
  "stdout": null,
  "stderr_tail": null
}
```

Parsed JSON lands in `result`; plain text (rare) lands in `stdout`.
On failure the tool raises an error carrying cf's own message —
surface it verbatim; cf's errors are precise (including "Not logged
in", which tells the user exactly how to authenticate).

## DNS records

`record_body` is the raw JSON request body (the API's DNS record
shape — stable across cf versions, unlike per-field flags):

```json
{ "operation": "dns_record_create", "zone": "example.com", "record_body": "{\"type\":\"A\",\"name\":\"www\",\"content\":\"192.0.2.1\",\"ttl\":1,\"proxied\":false}" }
```

Common record fields: `type` (A/AAAA/CNAME/TXT/MX/NS/SRV), `name`
(record name; `@` for the zone apex), `content`, `ttl` (`1` = auto),
`proxied` (orange-cloud), `priority` (MX/SRV). Get record ids from
`dns_records_list` before deleting:

```json
{ "operation": "dns_records_list", "zone": "example.com" }
{ "operation": "dns_record_delete", "zone": "example.com", "record_id": "023e105f4ecef8ad9ca31a8372d0c353" }
```

To update a record, use `custom` with `--body`:

```json
{ "operation": "custom", "command": "dns records update <record-id> --zone example.com --body '{\"content\":\"192.0.2.2\"}'" }
```

## Analytics — the GraphQL Analytics API

Cloudflare's legacy Zone Analytics REST API was deprecated (2021) and
the DNS analytics REST API is scheduled for removal (2026-12-01); the
official replacement for both is the GraphQL Analytics API. The cf CLI
does not expose it (its commands are generated from the REST schema),
so use the `graphql_query` operation — it POSTs to the official
endpoint `api.cloudflare.com/client/v4/graphql`.

**Requires the API token** from Credentials -> Cloudflare (the OAuth
login has no analytics scopes): permission `Account > Account
Analytics > Read`, plus `Zone > Analytics > Read` for zone-scoped
queries.

Zone HTTP traffic (official migration-guide query, current dataset):

```json
{
  "operation": "graphql_query",
  "graphql_query": "query Sample($zoneTag: string, $start: Time, $end: Time) { viewer { zones(filter: {zoneTag: $zoneTag}) { series: httpRequestsAdaptiveGroups(limit: 5, orderBy: [count_DESC], filter: {datetime_geq: $start, datetime_lt: $end, requestSource: \"eyeball\"}) { count avg { sampleInterval } sum { visits edgeResponseBytes } dimensions { coloCode } } } } }",
  "graphql_variables": "{\"zoneTag\": \"<zone id>\", \"start\": \"2026-07-01T00:00:00Z\", \"end\": \"2026-07-15T00:00:00Z\"}"
}
```

Web Analytics / RUM (account-scoped datasets:
`rumPageloadEventsAdaptiveGroups`, `rumPerformanceEventsAdaptiveGroups`,
`rumWebVitalsEventsAdaptiveGroups`):

```json
{
  "operation": "graphql_query",
  "graphql_query": "query Rum($accountTag: string, $start: Time, $end: Time) { viewer { accounts(filter: {accountTag: $accountTag}) { rumPageloadEventsAdaptiveGroups(limit: 100, orderBy: [count_DESC], filter: {datetime_geq: $start, datetime_lt: $end}) { count sum { visits } dimensions { requestPath } } } } }",
  "graphql_variables": "{\"accountTag\": \"<account id>\", \"start\": \"2026-07-01T00:00:00Z\", \"end\": \"2026-07-15T00:00:00Z\"}"
}
```

DNS analytics: `dnsAnalyticsAdaptive` / `dnsAnalyticsAdaptiveGroups`
(zone- and account-scoped). Notes: a query spans at most 10 zones or 1
account; the user quota is 300 queries per 5 minutes; wide time ranges
are sampled (multiply by `sampleInterval` for estimates). Web Analytics
site CONFIG (create/list sites) lives on REST under
`accounts/{account_id}/rum/site_info` — the cf CLI has no `rum`
commands yet, and those endpoints also need the API token.

## The full cf surface via custom

```json
{ "operation": "custom", "command": "accounts list" }
{ "operation": "custom", "command": "dns records get <record-id> --zone example.com" }
{ "operation": "custom", "command": "registrar domains list" }
{ "operation": "custom", "command": "zones get --zone example.com" }
{ "operation": "custom", "command": "dns analytics report --zone example.com" }
```

Discovery helpers when unsure of a command's shape:

- `{ "operation": "custom", "command": "agent-context dns" }` — cf's
  built-in per-product agent context (also `agent-context --list` for
  the product catalogue).
- `{ "operation": "custom", "command": "schema zones list" }` — the
  API schema behind a command.
- Append `--dry-run` to any write command to validate without
  executing.

## Quoting and escaping

`command` is parsed with `shlex.split` — wrap JSON bodies in single
quotes: `custom: "dns records create --zone example.com --body '{\"type\":\"A\",...}'"`.

## Authentication

Two independent paths; the token wins when both exist (cf's documented
resolution order):

1. **OAuth login** — Credentials Modal → Cloudflare → Login (cf opens
   the browser itself; localhost callback, so the browser must be on
   the server's machine), or `cf auth login` in a terminal. The OAuth
   grant is a FIXED scope set: zones, DNS (incl. `dns_analytics:read`),
   accounts, registrar, Workers — but NO Web Analytics/RUM or zone
   analytics scopes, and no way to request more.
2. **API token** — pasted in the credentials panel (or
   `CLOUDFLARE_API_TOKEN` in the server env; the panel token takes
   precedence). Create at dash.cloudflare.com/profile/api-tokens with
   the permission groups the task needs — required for `graphql_query`
   and anything else outside the OAuth scopes.

If a command fails with an authentication or 403 error, tell the user
which permission group the token needs; don't ask them to paste a
token in chat.

## Best practices

1. **Run `whoami` first** when auth state is unknown — it returns the
   authenticated email and scopes without side effects.
2. **Pass `zone` as the domain name** (cf resolves it to the zone ID).
3. **Preview writes with `--dry-run`** via `custom` when the user's
   intent is ambiguous.
4. **Destructive operations** (zone deletes, record deletes) — confirm
   with the user before running unless they explicitly asked.
5. **Surface cf error messages verbatim** — don't paraphrase.
