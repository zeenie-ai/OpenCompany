---
name: cloudflare-skill
description: Work with Cloudflare via the official cf CLI — check auth identity, list zones, list/create/delete DNS records, and run any other cf command (accounts, registrar, per-product schemas). Output is parsed JSON.
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

The cf CLI owns its auth — OpenCompany never stores a token. Three
equivalent ways to connect (any one is enough):

1. **Credentials Modal → Cloudflare → Login with Cloudflare** — cf
   (auto-installed) opens the Cloudflare dashboard OAuth page and
   completes via a localhost callback (browser must be on the same
   machine as the server).
2. **`cf auth login`** in a terminal on this machine.
3. **`CLOUDFLARE_API_TOKEN`** set in the server environment
   (create at dash.cloudflare.com/profile/api-tokens) — cf's
   documented first-priority credential source; works headless.

If a command fails with an authentication error, tell the user to
connect via one of these; don't ask them for a token.

## Best practices

1. **Run `whoami` first** when auth state is unknown — it returns the
   authenticated email and scopes without side effects.
2. **Pass `zone` as the domain name** (cf resolves it to the zone ID).
3. **Preview writes with `--dry-run`** via `custom` when the user's
   intent is ambiguous.
4. **Destructive operations** (zone deletes, record deletes) — confirm
   with the user before running unless they explicitly asked.
5. **Surface cf error messages verbatim** — don't paraphrase.
