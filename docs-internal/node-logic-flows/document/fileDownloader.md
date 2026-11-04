# File Downloader (`fileDownloader`)

| Field | Value |
|------|-------|
| **Category** | document |
| **Backend handler** | [`server/nodes/document/file_downloader/__init__.py`](../../../server/nodes/document/file_downloader/__init__.py) — `FileDownloaderNode`; dispatch via `BaseNode.execute()` + `@Operation("download")` |
| **Tests** | [`server/tests/nodes/test_document.py`](../../../server/tests/nodes/test_document.py) |
| **Skill (if any)** | none |
| **Dual-purpose tool** | no |

## Purpose

Download a batch of URLs in parallel to a local directory using a bounded
`asyncio.Semaphore`. Typically consumes the `items` array produced by
`httpScraper`. Output directory defaults to `<workspace_dir>/downloads/` so
downloaded files land in the per-workflow workspace and can be picked up by
`documentParser` or the Coding Agent filesystem tools.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream data - provides `items` via parameter resolution |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `urls` | array | `[]` | no | - | Declared Params field; wrapped into `items` as `[{url}]` if `items` not present |
| `output_dir` | string | `downloads` | no | - | Declared Params field |
| `max_workers` | number | `4` | no | - | Declared Params field (1-32) |
| `skip_existing` | boolean | `true` | no | - | Declared Params field |
| `timeout` | number | `60` | no | - | Declared Params field (1-600) |

**Known code gap:** normal schema fields `output_dir`, `max_workers`, and `skip_existing` are accepted but are not consumed by the current download operation. It reads retained legacy extra keys instead, so a UI edit to those canonical fields can leave the effective directory, concurrency (8), and skip behavior unchanged. This audit documents the discrepancy without changing execution.

**Quirk**: `Params = FileDownloaderParams` declares snake_case fields (above), but `extra="allow"` lets camelCase keys pass through, and the `download` op reads camelCase off `model_dump()`: `items`, `outputDir`, `maxWorkers` (default `8` here, not `4`), `skipExisting`, plus `urls`/`timeout`. So the operative keys consumed at runtime are `items`/`urls`, `outputDir`, `maxWorkers`, `skipExisting`, `timeout`.

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Counts plus downloaded file metadata |

### Output payload

```ts
{
  downloaded: number;
  skipped: number;
  failed: number;
  files: Array<{
    status: 'downloaded';
    path: string;
    url: string;
    size: number;      // bytes
    filename: string;
  }>;
  output_dir: string;
}
```

Wrapped in standard envelope: `{ success, result, execution_time, node_id, node_type, timestamp }`.

## Logic Flow

```mermaid
flowchart TD
  A[FileDownloaderNode.download] --> B{items empty?}
  B -- yes --> Ret0[Return success=true<br/>counts all zero]
  B -- no --> C[Resolve output_dir from outputDir or context.workspace_dir or ./downloads]
  C --> D[mkdir parents exist_ok]
  D --> E[Build semaphore(maxWorkers)]
  E --> F[asyncio.gather over items<br/>each inside semaphore slot]
  F --> G{Per-item}
  G -- empty url --> Gx[status=failed]
  G -- skip_existing + exists --> Gs[status=skipped]
  G -- fetch --> Gf[GET with timeout, follow_redirects=True<br/>netpolicy.check_request on every hop]
  Gf -- BlockedURL/HTTPStatusError/Exception --> Ge[status=failed]
  Gf -- ok --> Gw[write_bytes in a worker thread, status=downloaded]
  G --> H[Collect Exception results into failed]
  H --> I[Return success=true with totals + downloaded files list]
```

## Decision Logic

- **Empty items**: short-circuits to a zero-count success envelope before any directory work.
- **Output dir resolution**: `outputDir` param -> `<context.workspace_dir>/downloads` -> literal `downloads` relative to CWD.
- **Filename**: the last non-empty segment of the URL's decoded path (trailing slashes ignored), with `/` and `\` (encoded or not) both separators and `:` replaced by `_`, so a link such as `..%2f..%2fname` or `C:name` cannot place the file outside the output folder; literal `"download"` when that segment is empty, `.` or `..`. Collisions silently overwrite unless `skipExisting=true`.
- **Skip logic**: path-existence check, not size/hash check.
- **Network policy**: the client runs `check_request` (`services/netpolicy.py`) as an httpx request hook, so every request, each redirect hop included, is checked with `fetch_policy()` on the URL and every address its host resolves to. A hop to OpenCompany's own ports on this machine (the `*_PORT` settings, and the Browser's and Android's live ports), to cloud metadata, or to a host that does not resolve raises `BlockedURL("Cannot request <url>: <reason>.")` before anything is sent to it, and the item fails like any other. The owner's own network and other loopback ports stay reachable.
- **Per-URL failure**: appended to `failed` list, envelope remains `success=true`.
- **Gather exceptions**: wrapped tasks may raise - `return_exceptions=True` collects them and they are normalized into `failed` entries with only an `error` field.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none.
- **External API calls**: `GET <url>` per item with user-supplied timeout, follow redirects; each hop passes the network policy above.
- **File I/O**: creates `output_dir` (recursive), writes each download via `Path.write_bytes` in a worker thread (`asyncio.to_thread`), off the event loop.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: none.
- **Services**: uses `context["workspace_dir"]` injected by the executor.
- **Python packages**: `httpx`.
- **Environment variables**: none.

## Edge cases & known limits

- **Windows device names**: a URL whose last segment is a device name (`NUL`, and on Windows 10 and earlier `CON`, `AUX`, `PRN`, `COM1`-`COM9`, `LPT1`-`LPT9`) names that device, not a file: nothing is saved there.
- **Unbounded response body**: the HTTP client materializes `resp.content` before writing; no download byte limit is enforced by this operation.


- Each download opens its own `httpx.AsyncClient` inside the semaphore - overhead per file, not a shared client/session.
- No retry on failure; one HTTP error per URL means one `failed` entry.
- Filename collisions are not deduplicated across URLs that share a basename - later writes overwrite earlier ones.
- `maxWorkers` is cast via `int(...)` with no clamping; out-of-range values pass through.
- `skipExisting` checks the target file path only; a partially downloaded file from a previous crashed run will be treated as complete.
- The top-level `files` array contains ONLY successfully downloaded items; skipped files are counted but not listed.

## Related

- **Upstream producer**: [`httpScraper`](./httpScraper.md) produces the expected `items[]` shape.
- **Downstream consumer**: [`documentParser`](./documentParser.md) can glob files from `output_dir`.
- **Architecture docs**: [Workspace directory](../../../CLAUDE.md#per-workflow-workspace-directory) (per-workflow workspace concept).
