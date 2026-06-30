# Document Parser (`documentParser`)

| Field | Value |
|------|-------|
| **Category** | document |
| **Backend handler** | [`server/nodes/document/document_parser/__init__.py`](../../../server/nodes/document/document_parser/__init__.py) — `DocumentParserNode`; dispatch via `BaseNode.execute()` + `@Operation("parse")` |
| **Tests** | [`server/tests/nodes/test_document.py`](../../../server/tests/nodes/test_document.py) |
| **Skill (if any)** | none |
| **Dual-purpose tool** | no |

## Purpose

Parse a batch of files into plain/Markdown text documents via one of four
parsers: `pypdf` (default, fast), `marker` (GPU OCR for scanned PDFs),
`unstructured` (multi-format - DOCX, PPTX, HTML, ...), `beautifulsoup` (HTML).
Parsing runs in a thread pool (`asyncio.to_thread`) so the event loop stays
responsive. Inputs can be a list of file dicts/strings, or a directory +
glob pattern.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream data resolved into params (e.g. `input_dir` pointing at `fileDownloader`'s `output_dir`) |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `parser` | options | `pypdf` | no | - | `pypdf` / `marker` / `unstructured` / `beautifulsoup` |
| `file_path` | string | `""` | no | - | Single file path (takes precedence over `input_dir`) |
| `input_dir` | string | `""` | no | - | Directory to glob for files |
| `file_pattern` | string | `*.pdf` | no | - | Glob pattern applied inside `input_dir` |

Params are snake_case (`Params = DocumentParserParams`, `extra="ignore"`). There is no `files` param — paths come from `file_path` and/or the `input_dir` glob.

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | `documents`, `parsed_count`, `failed` |

### Output payload

```ts
{
  documents: Array<{
    source: string;    // full path
    filename: string;  // basename
    content: string;   // extracted text / markdown
    length: number;
    parser: string;    // echoed
  }>;
  parsed_count: number;
  failed: Array<{ file: string; error: string }>;
}
```

Wrapped in standard envelope: `{ success, result, execution_time, node_id, node_type, timestamp }`.

## Logic Flow

```mermaid
flowchart TD
  A[DocumentParserNode.parse] --> B[Collect paths from file_path]
  B --> C{input_dir exists?}
  C -- yes --> C1[extend paths with input_dir.glob(file_pattern)]
  C -- no --> D{paths empty?}
  C1 --> D
  D -- yes --> Ret0[Return success=true<br/>documents=[], parsed_count=0]
  D -- no --> E[For each path: asyncio.to_thread _parse_file_sync]
  E --> F{parser branch}
  F -- pypdf --> Fp[pypdf.PdfReader -> join page.extract_text]
  F -- marker --> Fm[marker.PdfConverter -> markdown]
  F -- unstructured --> Fu[unstructured.partition.auto -> join elements]
  F -- beautifulsoup --> Fb[BeautifulSoup + decompose script/style -> get_text]
  F -- other --> Fx[raise ValueError Unknown parser]
  Fp --> G[append to documents]
  Fm --> G
  Fu --> G
  Fb --> G
  Fx --> Gx[append to failed]
  G --> H[Return success=true with documents + failed]
  Gx --> H
```

## Decision Logic

- **No paths**: returns success with empty `documents[]`, no error.
- **Per-file failure**: any exception inside `_parse_file_sync` is captured into `failed` with `{file, error}`; other files continue.
- **Unknown parser**: `_parse_file_sync` raises `ValueError`, but `parser` is a `Literal` so Pydantic rejects unknown values before dispatch; if it ever reaches the loop it is recorded per-file in `failed` (every file fails identically).
- **Lazy imports**: each parser imports its backend only when selected, so unused heavy deps (marker CUDA stack, unstructured) are not loaded.
- **BeautifulSoup special handling**: strips `<script>` and `<style>` tags before extracting text.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none.
- **External API calls**: none.
- **File I/O**: reads every input file (text or binary depending on parser). BeautifulSoup branch uses `path.read_text(errors='ignore')`.
- **Subprocess**: none (parsers run in-process via `asyncio.to_thread`).

## External Dependencies

- **Credentials**: none.
- **Python packages** (lazy-imported per parser):
  - `pypdf>=4.0.0` for `pypdf`
  - `marker-pdf` + CUDA runtime for `marker`
  - `unstructured>=0.16.0` for `unstructured`
  - `beautifulsoup4>=4.12.0` for `beautifulsoup`
- **Environment variables**: none.

## Edge cases & known limits

- `pypdf`: `page.extract_text()` can return `None` for scanned pages - coerced to empty string via `or ''`.
- `marker`: downloads models on first call and requires CUDA; will fail on CPU-only hosts.
- `unstructured`: classifies file type from extension/content; no explicit type param.
- BeautifulSoup branch is the only one that handles HTML via `read_text`, so passing a binary `.pdf` there returns garbled `get_text`.
- `file_path` and `input_dir` can be combined - directory glob is appended to the explicit single path.
- `file_pattern` is applied via `Path.glob` (non-recursive). Use `**/*.pdf` for recursive.
- Returns `success=true` even when every file failed - consumers must inspect `failed` and `parsed_count`.

## Related

- **Upstream producer**: [`fileDownloader`](./fileDownloader.md) drops files into a directory consumed here via `inputDir`.
- **Downstream consumer**: [`textChunker`](./textChunker.md) consumes the `documents[]` array directly.
