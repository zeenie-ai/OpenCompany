# Apify Actor (`apifyActor`)

| Field | Value |
|------|-------|
| **Category** | web_automation / tool (dual-purpose) |
| **Backend handler** | [`server/nodes/scraper/apify_actor/__init__.py::ApifyActorNode`](../../../server/nodes/scraper/apify_actor/__init__.py) — dispatch via `BaseNode.execute()` -> `@Operation("run")` |
| **Tests** | [`server/tests/nodes/test_web_automation.py`](../../../server/tests/nodes/test_web_automation.py) |
| **Skill (if any)** | [`server/skills/web_agent/apify-skill/SKILL.md`](../../../server/skills/web_agent/apify-skill/SKILL.md) |
| **Dual-purpose tool** | yes |

## Purpose

Run an Apify actor (pre-built scraper / automation) via the official
`apify-client` async SDK, wait for it to finish, and return the dataset
items. Used for platforms that have no first-class integration in MachinaOs
(Instagram, TikTok, Twitter/X, LinkedIn, Facebook, YouTube, Google Search /
Maps, website content crawlers).

The handler merges two input sources into the actor's run-input dict: a raw
JSON string (`actorInput`) plus "quick helpers" specific to a handful of
curated actor ids (e.g. `instagramUrls` for `apify/instagram-scraper`).

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream trigger; not consumed directly |

## Parameters

Params model is `ApifyActorParams` (field names are snake_case).

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `actor_id` | options (Literal) | `apify/instagram-scraper` | **yes** | - | One of 10 actor presets or `custom` |
| `custom_actor_id` | string | `""` | yes if `actor_id=custom` | `actor_id=custom` | Used when `actor_id=custom` |
| `actor_input` | Any (JSON) | `{}` (empty dict) | no | - | Raw JSON run-input; merged with quick helpers. String input that fails `json.loads` (or non-dict) silently becomes `{}` |
| `instagram_urls` | string | `""` | no | `actor_id=apify/instagram-scraper` | Comma-separated -> `directUrls[]` |
| `tiktok_profiles` | string | `""` | no | `actor_id=clockworks/tiktok-scraper` | Comma-separated -> `profiles[]` |
| `tiktok_hashtags` | string | `""` | no | `actor_id=clockworks/tiktok-scraper` | Comma-separated -> `hashtags[]` |
| `twitter_search_terms` | string | `""` | no | `actor_id=apidojo/tweet-scraper` | Comma-separated -> `searchTerms[]` |
| `twitter_handles` | string | `""` | no | `actor_id=apidojo/tweet-scraper` | Comma-separated -> `twitterHandles[]` |
| `google_search_query` | string | `""` | no | `actor_id=apify/google-search-scraper` | -> `searchQuery` |
| `google_search_pages` | int (1-100) | `1` | no | `actor_id=apify/google-search-scraper` | -> `maxPagesPerQuery` |
| `crawler_start_urls` | string | `""` | no | `actor_id=apify/website-content-crawler` | Comma-separated -> `startUrls[{url}]` |
| `crawler_max_depth` | int (0-20) | `2` | no | `actor_id=apify/website-content-crawler` | -> `maxCrawlDepth` |
| `crawler_max_pages` | int (1-10000) | `50` | no | `actor_id=apify/website-content-crawler` | -> `maxCrawlPages` |
| `max_results` | int (1-10000) | `100` | no | - | Forwarded to `dataset.list_items(limit=...)` |
| `timeout` | int (1-3600) | `300` | no | - | Run timeout in seconds (SDK forwards as `timeout_secs`) |
| `memory` | options (Literal: 128/256/512/1024/2048/4096/8192) | `1024` | no | - | Actor memory in MB (`memory_mbytes`) |

Actor presets (`actor_id`): `apify/instagram-scraper`, `clockworks/tiktok-scraper`,
`apidojo/tweet-scraper`, `apify/linkedin-scraper`, `apify/facebook-pages-scraper`,
`streamers/youtube-scraper`, `apify/google-search-scraper`,
`compass/crawler-google-places`, `apify/website-content-crawler`,
`curious_coder/web-scraper`, `custom`.

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Standard envelope payload |
| `output-tool` | object | Same payload when wired to an AI agent's `input-tools` (`usable_as_tool=True`, tool name `apify_actor`) |

### Output payload (success)

```ts
{
  run_id: string;
  actor_id: string;
  status: string;           // 'SUCCEEDED' / 'FAILED' / 'TIMED-OUT' / 'ABORTED' / other
  items: any[];             // dataset items up to maxResults
  item_count: number;
  dataset_id: string;
  compute_units: number;    // mapped from usageTotalUsd on the run
  started_at: string;
  finished_at: string;
}
```

On `FAILED` / `TIMED-OUT` / `ABORTED` (and missing token / empty actor id /
`None` run result) the operation raises `NodeUserError`, which `BaseNode.execute()`
catches into a `{ success: false, error_type: "NodeUserError", error: <message> }`
envelope (single WARN log line, no traceback). There is no trimmed `result` dict.

## Logic Flow

```mermaid
flowchart TD
  A[BaseNode.execute -> ApifyActorNode.run] --> B[await _get_apify_client<br/>via get_auth_service.get_api_key 'apify']
  B -- token missing --> Enotk[raise NodeUserError:<br/>Apify API token not configured]
  B -- token ok --> C[Read actor_id, swap to custom_actor_id if 'custom']
  C --> C1{actor_id empty?}
  C1 -- yes --> Eaid[raise NodeUserError: Actor ID is required]
  C1 -- no --> D[_build_actor_input params.model_dump<br/>parse actor_input JSON else {}<br/>merge per-actor quick helpers]
  D --> E[Read timeout, max_results, memory_mbytes]
  E --> F[client.actor id .call<br/>run_input, timeout_secs, memory_mbytes]
  F -- run_info None --> Enone[raise NodeUserError:<br/>no result returned]
  F -- ok --> G{run_status?}
  G -- FAILED --> Gf[raise NodeUserError errorMessage]
  G -- TIMED-OUT --> Gt[raise NodeUserError: Actor timed out]
  G -- ABORTED --> Ga[raise NodeUserError: Actor run was aborted]
  G -- other --> H{dataset_id truthy?}
  H -- yes --> H1[client.dataset id .list_items limit=max_results<br/>items = listing.items]
  H -- no --> H2[items = empty list]
  H1 & H2 --> I[Build ApifyActorOutput:<br/>run_id, actor_id, status, items, item_count,<br/>dataset_id, compute_units=usageTotalUsd,<br/>started_at, finished_at]
  I --> J[Return BaseNode success envelope]
```

## Decision Logic

- **Auth lookup**: `get_auth_service().get_api_key("apify", "default")` - a None
  return raises `NodeUserError("Apify API token not configured")`.
- **Actor id swap**: literal string `"custom"` triggers replacement with
  `custom_actor_id` BEFORE the empty check. An empty `custom_actor_id` still
  raises the `Actor ID is required` error.
- **Run-input merge** (`_build_actor_input` on `params.model_dump()`):
  - `actor_input` parsed as JSON; string input that fails `json.loads` (or is
    empty/blank) silently becomes `{}`. Non-dict, non-string input becomes `{}`.
  - Quick helpers run even when `actor_input` already contains those keys -
    they OVERWRITE any existing value.
  - Quick helpers only fire for five hard-coded actor ids
    (instagram / tiktok / tweet-scraper / google-search / website-content-crawler).
- **Run statuses**: three status strings (`FAILED`, `TIMED-OUT`, `ABORTED`)
  raise `NodeUserError` with specific messages. Every other status (including
  `SUCCEEDED`, `RUNNING`, `READY`, etc.) proceeds to dataset fetch. Runs that
  stay `RUNNING` past the SDK's internal polling are handled by the SDK and
  should not reach here.
- **Empty dataset id**: `items = []`, no API call to the dataset endpoint.
- **Error rewriting**: the `run` operation does NOT rewrite SDK exception
  strings — those propagate to `BaseNode.execute()`'s generic handler. Only the
  separate `validate_apify_token()` helper (used by the `validate_apify_key` WS
  handler / Credentials modal) rewrites `401`/`Unauthorized` -> `Invalid API token`.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none.
- **External API calls**:
  - `POST https://api.apify.com/v2/acts/<actor_id>/runs` (via SDK `actor.call`)
  - `GET https://api.apify.com/v2/datasets/<dataset_id>/items?limit=<n>` (via SDK `dataset.list_items`)
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: `auth_service.get_api_key("apify", "default")` -> stored in
  the `EncryptedAPIKey` table under provider `apify`.
- **Services**: Apify platform (api.apify.com).
- **Python packages**: `apify-client` (>= async variant `ApifyClientAsync`).
- **Environment variables**: none (token lives in credentials DB).

## Edge cases & known limits

- **Invalid `actorInput` JSON is silent**: the run is sent with `{}` and may
  succeed unexpectedly (actor uses defaults) or fail with a validation error
  that surfaces in `errorMessage`.
- **Quick helpers overwrite raw input**: setting both `actorInput` JSON AND
  the per-actor helper means the helper wins. This is not documented in the
  frontend.
- **No streaming**: the handler blocks until the actor finishes or the SDK
  raises. `timeout` is the hard cap.
- **`compute_units` is misleading**: the value returned is `run.usageTotalUsd`
  (USD), not compute units. The key name is preserved for backward compat.
- **Error rewriting is substring-based (validation only)**: `validate_apify_token`
  pattern-matches `401`/`Unauthorized` to a generic message; the runtime `run`
  operation does not, so a genuine SDK error surfaces verbatim.
- **No usage tracking**: unlike search / HTTP nodes, there is no
  `api_usage_metrics` row written for Apify calls.
- **Stale dataset on repeat runs**: `dataset.list_items(limit=maxResults)`
  always reads the run's default dataset; concurrent runs on the same actor
  use separate datasets per run (handled by Apify), but a user who supplies
  a custom `datasetId` via `actorInput` cannot influence which dataset the
  handler reads from.

## Related

- **Skills using this as a tool**: [`apify-skill/SKILL.md`](../../../server/skills/web_agent/apify-skill/SKILL.md)
- **Companion nodes**: [`browser`](./browser.md), [`crawleeScraper`](./crawleeScraper.md)
- **Architecture docs**: [Credentials Encryption](../../credentials_encryption.md)
