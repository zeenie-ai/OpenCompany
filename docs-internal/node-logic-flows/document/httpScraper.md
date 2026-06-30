# HTTP Scraper (`httpScraper`)

| Field | Value |
|------|-------|
| **Category** | document |
| **Backend handler** | [`server/nodes/document/http_scraper/__init__.py`](../../../server/nodes/document/http_scraper/__init__.py) — `HttpScraperNode`; dispatch via `BaseNode.execute()` + `@Operation("scrape")` |
| **Tests** | [`server/tests/nodes/test_document.py`](../../../server/tests/nodes/test_document.py) |
| **Skill (if any)** | none |
| **Dual-purpose tool** | no |

## Purpose

Scrape links from one or more web pages into a normalized `items[]` array
suitable for the `fileDownloader` node. Supports three iteration modes:
single URL, date-range iteration with a `{date}` placeholder, and page
pagination with a `{page}` placeholder. A CSS selector picks which anchor-like
elements become items. Typically the first stage of an ingestion pipeline:
`httpScraper -> fileDownloader -> documentParser -> textChunker -> ...`.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Trigger / upstream data; not consumed directly |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `url` | string | `""` | **yes** | - | URL with optional `{date}` / `{page}` placeholder |
| `iteration_mode` | options | `single` | no | - | `single` / `date` / `page` |
| `link_selector` | string | `a[href$=".pdf"]` | no | - | BeautifulSoup CSS selector |
| `headers` | string (JSON) | `{}` | no | - | Extra HTTP headers as JSON object |
| `start_date` | string | `""` | yes (date mode) | `iteration_mode=date` | `YYYY-MM-DD` |
| `end_date` | string | `""` | yes (date mode) | `iteration_mode=date` | `YYYY-MM-DD`, inclusive |
| `date_placeholder` | string | `{date}` | no | `iteration_mode=date` | Substring replaced with formatted date |
| `start_page` | number | `1` | no | `iteration_mode=page` | Inclusive |
| `end_page` | number | `10` | no | `iteration_mode=page` | Inclusive; `{page}` literal replaced |
| `max_pages` | number | `10` | no | - | Safety cap on pages fetched (1-1000) |
| `use_proxy` | boolean | `false` | no | - | Route through proxy service if enabled |
| `proxy_provider` | string | `auto` | no | `use_proxy=true` | Provider name |
| `proxy_country` | string | `""` | no | `use_proxy=true` | ISO country code |
| `session_type` | options | `rotating` | no | `use_proxy=true` | `rotating` / `sticky` |
| `sticky_duration` | number | `600` | no | `use_proxy=true, session_type=sticky` | Sticky session seconds |

Params are snake_case (`Params = HttpScraperParams`, `extra="ignore"`).

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | `items`, `item_count`, `errors` |

### Output payload

```ts
{
  items: Array<{
    url: string;          // urljoin(fetch_url, href)
    text: string;         // anchor text, stripped
    source_url: string;   // page the link was found on
    date?: string;        // ISO date, present in date mode
    page?: number;        // present in page mode
  }>;
  item_count: number;
  errors: string[];       // "<url>: <error str>" per failed fetch
}
```

Wrapped in standard envelope: `{ success, result, execution_time, node_id, node_type, timestamp }`.

## Logic Flow

```mermaid
flowchart TD
  A[HttpScraperNode.scrape] --> B{url provided?}
  B -- no --> Eret[raise NodeUserError URL is required<br/>-> success=false envelope]
  B -- yes --> C[json.loads headers]
  C --> D{iteration_mode}
  D -- date --> D1[Iterate start_date..end_date days<br/>replace date_placeholder per URL]
  D -- page --> D2[Iterate start_page..end_page<br/>replace `{page}` literal per URL]
  D -- single --> D3[Single URL, meta=`{}`]
  D1 --> E[Build urls_to_fetch list]
  D2 --> E
  D3 --> E
  E --> F{useProxy?}
  F -- yes --> F1[Lookup proxy URL via proxy_service<br/>swallow errors, continue without proxy]
  F -- no --> G[httpx.AsyncClient timeout=30 follow_redirects=True]
  F1 --> G
  G --> H[For each URL: GET + BeautifulSoup parse]
  H -- HTTPStatusError / Exception --> I[Append to errors, keep going]
  H -- ok --> J[Select by linkSelector, build item dict with urljoin + meta]
  I --> K[Return success=true with items/errors]
  J --> K
```

## Decision Logic

- **Validation**: missing `url` raises `NodeUserError("URL is required")` (single WARN line, no traceback) -> `success=false` envelope.
- **Date mode**: missing `start_date` or `end_date` raises `NodeUserError` -> `success=false`.
- **Iteration mode**: `iteration_mode` is a `Literal`, so unknown values are rejected by Pydantic; `single` is the only non-date/page branch.
- **Per-URL errors**: collected into `errors` list; the op still returns `success=true` even if every URL failed (only top-level setup errors fail the envelope).
- **Proxy failure**: logged at warning, request proceeds without proxy.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none.
- **External API calls**: `GET <fetch_url>` for each expanded URL, timeout 30s, `follow_redirects=True`.
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: none.
- **Services**: optional `services.proxy.service.get_proxy_service()` when `useProxy=true`.
- **Python packages**: `httpx`, `beautifulsoup4`.
- **Environment variables**: none.

## Edge cases & known limits

- `date_placeholder` is a plain `str.replace`, not a token boundary - a URL containing the placeholder substring twice is replaced twice.
- Date mode formats dates as `YYYY-MM-DD`; placeholder in URL is replaced with this exact format regardless of what the user wrote.
- `{page}` in page mode is hard-coded, not configurable (unlike the date placeholder).
- `headers` is parsed with `json.loads` with no try/except - invalid JSON raises (caught as a bare `Exception` -> full traceback) and fails the envelope.
- `max_pages` is declared but not enforced in the page-iteration loop (the loop runs `start_page..end_page`).
- Per-URL failures are silently aggregated into `errors` but the envelope is still `success=true`; downstream nodes must check `errors`.
- `end_page`/`end_date` are inclusive.

## Related

- **Skills using this as a tool**: none (not a dual-purpose tool).
- **Downstream nodes**: [`fileDownloader`](./fileDownloader.md) consumes the `items` array directly.
- **Architecture docs**: [Proxy Service](../../proxy_service.md).
