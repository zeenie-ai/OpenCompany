# Embedding Generator (`embeddingGenerator`)

| Field | Value |
|------|-------|
| **Category** | document |
| **Backend handler** | [`server/nodes/document/embedding_generator/__init__.py`](../../../server/nodes/document/embedding_generator/__init__.py) — `EmbeddingGeneratorNode`; dispatch via `BaseNode.execute()` + `@Operation("embed")` |
| **Tests** | [`server/tests/nodes/test_document.py`](../../../server/tests/nodes/test_document.py) |
| **Skill (if any)** | none |
| **Dual-purpose tool** | no |

## Purpose

Generate vector embeddings from a list of text chunks using one of three
backends: HuggingFace (local sentence-transformers, default), OpenAI
(API-hosted), or Ollama (local server). Local HuggingFace encoding runs in a
worker thread; OpenAI and Ollama use their native asynchronous SDK clients.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Provides `chunks[]` upstream |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `chunks` | array | `[]` | yes | - | List of `{content, source, chunk_index}` dicts |
| `provider` | options | `huggingface` | no | - | `huggingface` / `openai` / `ollama` |
| `model` | string | `BAAI/bge-small-en-v1.5` | no | - | Model name for the chosen provider |
| `batch_size` | number | `32` | no | - | Texts per embedding batch (1-256), used by every provider |
| `api_key` | string (password) | `""` | no (required for openai) | `provider=openai` | OpenAI API key |
| `endpoint` | string | `""` | no | `provider=openai/ollama` | Optional OpenAI `base_url` or Ollama `host` |

Params are snake_case (`Params = EmbeddingGeneratorParams`).

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Embeddings + original chunks + metadata |

### Output payload

```ts
{
  embeddings: number[][];     // one vector per chunk
  embedding_count: number;
  dimensions: number;         // len(embeddings[0])
  chunks: any[];              // echoed back for pairing
  provider: string;
  model: string;
}
```

Empty-input variant:

```ts
{ embeddings: [], embedding_count: 0, dimensions: 0, chunks: [] }
```

Wrapped in standard envelope: `{ success, result, execution_time, node_id, node_type, timestamp }`.

## Logic Flow

```mermaid
flowchart TD
  A[EmbeddingGeneratorNode.embed] --> B{chunks empty?}
  B -- yes --> Ret0[Return success=true<br/>zero-valued payload]
  B -- no --> C[Extract text list: dict.content or str(chunk)]
  C --> D[services.memory.vector_store.create_embedder]
  D --> Dh[HuggingFace: lazy SentenceTransformer]
  D --> Do[OpenAI: openai.AsyncOpenAI]
  D --> Dl[Ollama: ollama.AsyncClient]
  Dh --> E[SentenceTransformer model<br/>encode batches in asyncio.to_thread]
  Do --> F[Await embeddings.create per batch]
  Dl --> G[Await client.embed per batch]
  E --> H[Normalized number vectors]
  F --> H
  G --> H
  H --> I[Compute dimensions from len(embeddings[0])]
  I --> J[Return success=true with embeddings + echoed chunks]
```

## Decision Logic

- **Empty chunks**: short-circuits to the empty-payload success envelope.
- **Provider dispatch**: the node and long-term memory use the same
  `create_embedder` factory and `AsyncEmbedder` protocol. `provider` is a
  `Literal`, so Pydantic rejects unknown values before dispatch.
- **Embedding call**: Hugging Face encoding runs in a worker thread; OpenAI
  and Ollama use their native async SDKs directly.
- **Dimensions**: inferred from first embedding; `0` if the call returned `[]`.
- **Missing huggingface package**: raises `NodeUserError` with an install hint
  for the `local-embeddings` extra; `BaseNode.execute()` logs one WARN line
  (no traceback) and returns `{success: false, error_type: "NodeUserError"}`.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none.
- **External API calls**:
  - `openai`: HTTPS call to OpenAI embeddings endpoint.
  - `ollama`: HTTP call to local Ollama server (default `localhost:11434`).
  - `huggingface`: no network after the model is cached; first run downloads model to `~/.cache/huggingface`.
- **File I/O**: HuggingFace model cache on first run.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: `api_key` parameter (OpenAI only). This document node is
  outside `AI_MODEL_TYPES`, so callers must provide it explicitly.
- **Python packages**: optional `sentence-transformers` (HF), direct `openai`
  SDK (OpenAI), and direct `ollama` SDK (Ollama).
- **Environment variables**: `OLLAMA_HOST` is respected by `ollama.AsyncClient`.

## Edge cases & known limits

- OpenAI `api_key` is passed to the shared factory in memory only and is never
  included in output.
- `batch_size` controls requests/encoding batches for every provider.
- HF first-run model download can take minutes; the node will appear to hang.
- Model name default (`BAAI/bge-small-en-v1.5`) is HF-specific; you must override `model` when switching provider.
- No cost tracking (unlike LLM chat nodes).

## Related

- **Upstream producer**: [`textChunker`](./textChunker.md) supplies `chunks[]`.
- **Downstream consumer**: [`vectorStore`](./vectorStore.md) consumes `embeddings` + `chunks` together.
