# Text Chunker (`textChunker`)

| Field | Value |
|------|-------|
| **Category** | document |
| **Backend handler** | [`server/nodes/document/text_chunker/__init__.py`](../../../server/nodes/document/text_chunker/__init__.py) — `TextChunkerNode`; dispatch via `BaseNode.execute()` + `@Operation("chunk")` |
| **Tests** | [`server/tests/nodes/test_document.py`](../../../server/tests/nodes/test_document.py) |
| **Skill (if any)** | none |
| **Dual-purpose tool** | no |

## Purpose

Split documents into overlapping text chunks suitable for embedding. Uses
LangChain's `RecursiveCharacterTextSplitter` (default) or
`MarkdownTextSplitter` for structure-aware splitting of Markdown content.
Typically sits between `documentParser` and `embeddingGenerator` in a RAG
pipeline.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Provides `documents[]` upstream |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `text` | string | `""` | no | - | Declared Params field; wrapped into `documents` as `[{content: text}]` if `documents` absent |
| `strategy` | options | `recursive` | no | - | `recursive` / `markdown` / `token` (token falls through to recursive) |
| `chunk_size` | number | `1000` | no | - | Declared Params field (100-8000) |
| `overlap` | number | `200` | no | - | Declared Params field (0-1000) |

**Quirk**: `Params = TextChunkerParams` declares `text` / `strategy` / `chunk_size` / `overlap` (snake_case), but `extra="allow"` passes camelCase through and the `chunk` op reads `documents`, `chunkSize` (default `1000`), `chunkOverlap` or `overlap`, and `strategy` off `model_dump()`. So the operative inputs at runtime are `documents`/`text`, `chunkSize`, `chunkOverlap`/`overlap`, `strategy`.

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | `chunks`, `chunk_count` |

### Output payload

```ts
{
  chunks: Array<{
    source: string;       // 'input' when doc was a bare string
    chunk_index: number;  // 0-based within the source doc
    content: string;
    length: number;
  }>;
  chunk_count: number;
}
```

Wrapped in standard envelope: `{ success, result, execution_time, node_id, node_type, timestamp }`.

## Logic Flow

```mermaid
flowchart TD
  A[TextChunkerNode.chunk] --> B{documents empty?}
  B -- yes --> Ret0[Return success=true<br/>chunks=[], chunk_count=0]
  B -- no --> C{strategy}
  C -- markdown --> Cm[MarkdownTextSplitter]
  C -- other --> Cr[RecursiveCharacterTextSplitter]
  Cm --> D[For each doc: extract content + source]
  Cr --> D
  D --> E{content truthy?}
  E -- no --> Eskip[Skip]
  E -- yes --> F[splitter.split_text -> enumerate chunks]
  F --> G[Append chunk with source, chunk_index, content, length]
  G --> H[Return success=true chunks + count]
  Eskip --> H
```

## Decision Logic

- **Empty documents**: short-circuits to success with empty `chunks[]`.
- **Strategy dispatch**: only `markdown` gets the Markdown splitter; anything else (including `token`, `recursive`, typos) uses the recursive splitter.
- **Doc normalization**: dict -> `(content, source)`; string -> `(str(doc), 'input')`.
- **Empty content skip**: docs with falsy `content` are silently dropped (not counted, not listed).
- **Exception path**: any splitter error bubbles to the outer `except` -> `success=false` with the error string.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none.
- **External API calls**: none.
- **File I/O**: none.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: none.
- **Python packages**: `langchain-text-splitters>=0.3.0`.
- **Environment variables**: none.

## Edge cases & known limits

- `token` strategy is mentioned in the frontend options/description but there is no branch for it; it currently falls through to recursive. Documented gotcha.
- `chunkSize`/`chunkOverlap` are cast via `int(...)` without clamping - out-of-range values pass through to LangChain and may raise if `overlap >= chunkSize`.
- Pure function, no randomness - deterministic given the same inputs.
- Chunks preserve order per-document but there is no global index across documents.

## Related

- **Upstream producer**: [`documentParser`](./documentParser.md) supplies `documents[]`.
- **Downstream consumer**: [`embeddingGenerator`](./embeddingGenerator.md) consumes `chunks[]`.
