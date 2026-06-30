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
(API-hosted), or Ollama (local server). The heavy embedding call runs in a
thread pool via `asyncio.to_thread` so the event loop is not blocked.

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
| `batch_size` | number | `32` | no | - | Texts per embedding batch (1-256). Declared but not currently used by the embed op. |
| `api_key` | string (password) | `""` | no (required for openai) | `provider=openai` | OpenAI API key |

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
  C --> D{provider}
  D -- huggingface --> Dh[Import langchain_huggingface<br/>raise NodeUserError with install hint if missing]
  D -- openai --> Do[Import langchain_openai.OpenAIEmbeddings]
  D -- ollama --> Dl[Import langchain_ollama.OllamaEmbeddings]
  D -- other --> Dx[raise NodeUserError Unknown provider]
  Dh --> E[HuggingFaceEmbeddings(model_name=model)]
  Do --> F[OpenAIEmbeddings(model=model, api_key=api_key)]
  Dl --> G[OllamaEmbeddings(model=model)]
  E --> H[asyncio.to_thread embedder.embed_documents(texts)]
  F --> H
  G --> H
  H --> I[Compute dimensions from len(embeddings[0])]
  I --> J[Return success=true with embeddings + echoed chunks]
```

## Decision Logic

- **Empty chunks**: short-circuits to the empty-payload success envelope.
- **Provider dispatch**: string match on `provider`; `provider` is a `Literal` so Pydantic rejects unknown values before dispatch (the `else: NodeUserError` is a dead path).
- **Embedding call**: always runs in a worker thread via `asyncio.to_thread`.
- **Dimensions**: inferred from first embedding; `0` if the call returned `[]`.
- **Missing huggingface package**: raises `NodeUserError` with an install hint (`pip install langchain-huggingface sentence-transformers`); `BaseNode.execute()` logs one WARN line (no traceback) and returns `{success: false, error_type: "NodeUserError"}`.

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

- **Credentials**: `api_key` param (OpenAI only). Not fetched from `auth_service` - must be passed as a node parameter.
- **Python packages**: `langchain-huggingface` + `sentence-transformers` (HF), `langchain-openai` (OpenAI), `langchain-ollama` (Ollama).
- **Environment variables**: `OLLAMA_HOST` respected by `langchain_ollama`.

## Edge cases & known limits

- OpenAI `api_key` is read from the node parameter, NOT from `auth_service.get_api_key('openai')`. This is inconsistent with other OpenAI-using nodes (documented gotcha).
- `batch_size` is a declared param but the embed op passes everything in one `embed_documents` call; provider-side batch limits apply.
- HF first-run model download can take minutes; the node will appear to hang.
- Model name default (`BAAI/bge-small-en-v1.5`) is HF-specific; you must override `model` when switching provider.
- No cost tracking (unlike LLM chat nodes).

## Related

- **Upstream producer**: [`textChunker`](./textChunker.md) supplies `chunks[]`.
- **Downstream consumer**: [`vectorStore`](./vectorStore.md) consumes `embeddings` + `chunks` together.
