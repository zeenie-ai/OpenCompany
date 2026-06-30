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
`documentParser` or the Deep Agent filesystem tools.

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
  G -- fetch --> Gf[GET with timeout, follow_redirects=True]
  Gf -- HTTPStatusError/Exception --> Ge[status=failed]
  Gf -- ok --> Gw[write_bytes, status=downloaded]
  G --> H[Collect Exception results into failed]
  H --> I[Return success=true with totals + downloaded files list]
```

## Decision Logic

- **Empty items**: short-circuits to a zero-count success envelope before any directory work.
- **Output dir resolution**: `outputDir` param -> `<context.workspace_dir>/downloads` -> literal `downloads` relative to CWD.
- **Filename**: `unquote(basename(urlparse(url).path))` or literal `"download"` when URL has no path. Collisions silently overwrite unless `skipExisting=true`.
- **Skip logic**: path-existence check, not size/hash check.
- **Per-URL failure**: appended to `failed` list, envelope remains `success=true`.
- **Gather exceptions**: wrapped tasks may raise - `return_exceptions=True` collects them and they are normalized into `failed` entries with only an `error` field.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none.
- **External API calls**: `GET <url>` per item with user-supplied timeout, follow redirects.
- **File I/O**: creates `output_dir` (recursive), writes each download via `Path.write_bytes`.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: none.
- **Services**: uses `context["workspace_dir"]` injected by the executor.
- **Python packages**: `httpx`.
- **Environment variables**: none.

## Edge cases & known limits

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
