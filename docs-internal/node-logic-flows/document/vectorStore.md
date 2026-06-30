# Vector Store (`vectorStore`)

| Field | Value |
|------|-------|
| **Category** | document |
| **Backend handler** | [`server/nodes/document/vector_store/__init__.py`](../../../server/nodes/document/vector_store/__init__.py) — `VectorStoreNode`; dispatch via `BaseNode.execute()` + `@Operation("dispatch")` |
| **Tests** | [`server/tests/nodes/test_document.py`](../../../server/tests/nodes/test_document.py) |
| **Skill (if any)** | none |
| **Dual-purpose tool** | no |

## Purpose

Store, query, or delete embedding vectors in one of three vector database
backends: ChromaDB (local persistent, default), Qdrant (self-hosted or cloud),
or Pinecone (managed cloud). Acts as the final stage of the RAG indexing
pipeline and/or the retrieval stage at query time.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Provides `embeddings` + `chunks` from `embeddingGenerator` |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `operation` | options | `store` | no | - | `store` / `query` / `delete` |
| `backend` | options | `chroma` | no | - | `chroma` / `chromadb` / `qdrant` / `pinecone` (`chromadb` aliased to `chroma`) |
| `collection_name` | string | `documents` | no | - | Collection / index name |
| `embeddings` | array | `null` | yes (store) | - | Vectors from `embeddingGenerator` |
| `chunks` | array | `null` | no (store) | `operation=store` | Metadata pairs for the vectors |
| `query_embedding` | array | `null` | yes (query) | `operation=query` | Single vector to query |
| `top_k` | number | `5` | no | `operation=query` | Max matches to return (1-100) |
| `ids` | array | `null` | yes (delete) | `operation=delete` | Vector IDs to delete |
| `persist_dir` | string | `./data/vectors` | no | `backend=chroma,chromadb` | ChromaDB persistence dir |
| `qdrant_url` | string | `http://localhost:6333` | no | `backend=qdrant` | Qdrant URL |
| `pinecone_api_key` | string | `""` | yes | `backend=pinecone` | Pinecone API key |

Params are snake_case (`Params = VectorStoreParams`, `extra="ignore"`).

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Operation-specific payload + `backend` + `collection_name` |

### Output payload

Store:
```ts
{ stored_count: number; collection_count?: number; backend: string; collection_name: string; }
```

Query:
```ts
{
  matches: Array<{
    id: string;
    document: string;        // extracted content
    metadata: object;
    distance?: number;       // chroma
    score?: number;          // qdrant, pinecone
  }>;
  backend: string;
  collection_name: string;
}
```

Delete:
```ts
{ deleted: true; count: number; backend: string; collection_name: string; }
```

Wrapped in standard envelope: `{ success, result, execution_time, node_id, node_type, timestamp }`.

## Logic Flow

```mermaid
flowchart TD
  A[VectorStoreNode.dispatch] --> B{backend}
  B -- chroma/chromadb --> C1[_chroma_op]
  B -- qdrant --> C2[_qdrant_op]
  B -- pinecone --> C3[_pinecone_op]
  B -- other --> Ex[raise NodeUserError Unknown backend]
  C1 --> D{operation}
  C2 --> D
  C3 --> D
  D -- store --> Ds[Auto-create collection if needed<br/>generate uuid ids + metas<br/>upsert via asyncio.to_thread]
  D -- query --> Dq[query / search with query_embedding + top_k<br/>normalize matches across backends]
  D -- delete --> Dd[delete by ids, count = len(ids)]
  Ds --> E[Return partial result]
  Dq --> E
  Dd --> E
  E --> F[Attach backend + collection_name]
  F --> G[Return success=true envelope]
```

## Decision Logic

- **Backend dispatch**: `backend` is a `Literal` (`chroma` / `chromadb` / `qdrant` / `pinecone`); `chromadb` is normalized to `chroma`. Unknown values are rejected by Pydantic before dispatch (the `else: NodeUserError` is a dead path).
- **Pinecone without key**: `_pinecone_op` raises `NodeUserError("Pinecone API key required")` when `pinecone_api_key` is empty.
- **Empty embeddings (store)**: returns `stored_count=0` without raising; for Chroma also returns current `collection_count`.
- **Empty query_embedding (query)**: returns `matches=[]` without hitting the backend.
- **Empty ids (delete)**: returns `deleted=true, count=0` without hitting the backend.
- **Collection auto-creation**:
  - Chroma: `get_or_create_collection` always.
  - Qdrant: existence check via `get_collections()`; created with `Distance.COSINE` and vector size inferred from first embedding.
  - Pinecone: assumed pre-existing; `pc.Index(collection)` is called unconditionally.
- **Metadata padding (Chroma)**: `docs` and `metas` are right-padded when `chunks` is shorter than `embeddings` so `coll.add` gets matching lengths.
- **ID generation**: random `uuid.uuid4()` per vector. Store is not idempotent; re-running a store op writes duplicates.

## Side Effects

- **Database writes**: writes into the selected vector DB (Chroma files, Qdrant collection, Pinecone index).
- **Broadcasts**: none.
- **External API calls**:
  - Chroma: none (local).
  - Qdrant: HTTP to `qdrantUrl`.
  - Pinecone: HTTPS to Pinecone API.
- **File I/O**: Chroma persists to `persistDir` (SQLite + parquet by default).
- **Subprocess**: none.

## External Dependencies

- **Credentials**: `pinecone_api_key` node parameter (Pinecone only). Not fetched from `auth_service`.
- **Python packages**: `chromadb>=0.5.0`, `qdrant-client>=1.12.0`, `pinecone` (all lazy-imported per backend).
- **Environment variables**: none (Qdrant URL is a param, Pinecone key is a param).

## Edge cases & known limits

- Every `store` call generates fresh UUIDs - there is no upsert-by-key; re-indexing the same chunk creates duplicates.
- Chroma is the only backend that returns `collection_count` after a store.
- Qdrant collection creation infers the vector size from the first embedding; mixed-size batches will be rejected by Qdrant on upsert.
- Pinecone `pc.Index(collection)` does not create the index - it must exist, or the op fails with Pinecone's own error.
- Pinecone `pinecone_api_key` is a node parameter, inconsistent with other cloud services that pull from `auth_service`.
- Delete is by ID only - no metadata-filter delete.
- Query match shape differs per backend: Chroma returns `distance`, Qdrant and Pinecone return `score`. Downstream consumers must handle both.

## Related

- **Upstream producer**: [`embeddingGenerator`](./embeddingGenerator.md) supplies `embeddings` + `chunks`.
- **Architecture docs**: none (there is no dedicated RAG architecture doc yet).
