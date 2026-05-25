"""Vector Store — Wave 11.D.7 inlined.

Backends: ChromaDB (local), Qdrant (self-hosted), Pinecone (cloud).
Each backend helper is a pure function that reads the raw param dict;
the dispatcher routes by ``backend`` + ``operation``.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue


class VectorStoreParams(BaseModel):
    operation: Literal["store", "query", "delete"] = "store"
    backend: Literal["chroma", "chromadb", "qdrant", "pinecone"] = "chroma"
    collection_name: str = Field(default="documents")
    embeddings: Optional[list] = None
    chunks: Optional[list] = Field(
        default=None,
        json_schema_extra={"displayOptions": {"show": {"operation": ["store"]}}},
    )
    query_embedding: Optional[list] = Field(
        default=None,
        json_schema_extra={"displayOptions": {"show": {"operation": ["query"]}}},
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=100,
        json_schema_extra={"displayOptions": {"show": {"operation": ["query"]}}},
    )
    ids: Optional[list] = Field(
        default=None,
        json_schema_extra={"displayOptions": {"show": {"operation": ["delete"]}}},
    )
    persist_dir: str = Field(
        default="./data/vectors",
        json_schema_extra={"displayOptions": {"show": {"backend": ["chroma", "chromadb"]}}},
    )
    qdrant_url: str = Field(
        default="http://localhost:6333",
        json_schema_extra={"displayOptions": {"show": {"backend": ["qdrant"]}}},
    )
    pinecone_api_key: str = Field(
        default="",
        json_schema_extra={"displayOptions": {"show": {"backend": ["pinecone"]}}},
    )

    model_config = ConfigDict(extra="ignore")


class VectorStoreOutput(BaseModel):
    operation: Optional[str] = None
    backend: Optional[str] = None
    collection_name: Optional[str] = None
    matches: Optional[list] = None
    stored_count: Optional[int] = None
    collection_count: Optional[int] = None
    deleted: Optional[bool] = None
    count: Optional[int] = None

    model_config = ConfigDict(extra="allow")


async def _chroma_op(operation: str, params: Dict[str, Any], collection: str) -> Dict[str, Any]:
    try:
        import chromadb
    except ImportError:
        raise NodeUserError("ChromaDB unavailable. pip install chromadb")

    persist_dir = params.get("persist_dir", "./data/vectors")
    client = chromadb.PersistentClient(path=persist_dir)
    coll = client.get_or_create_collection(name=collection)

    if operation == "store":
        embeddings = params.get("embeddings", [])
        chunks = params.get("chunks", [])
        if not embeddings:
            return {"stored_count": 0, "collection_count": coll.count()}
        ids = [str(uuid.uuid4()) for _ in embeddings]
        docs = [c.get("content", "") if isinstance(c, dict) else str(c) for c in chunks]
        while len(docs) < len(embeddings):
            docs.append("")
        metas = [
            {"source": c.get("source", "unknown"), "chunk_index": c.get("chunk_index", i)}
            if isinstance(c, dict)
            else {"source": "input", "chunk_index": i}
            for i, c in enumerate(chunks)
        ]
        while len(metas) < len(embeddings):
            metas.append({"source": "unknown", "chunk_index": len(metas)})
        await asyncio.to_thread(coll.add, ids=ids, embeddings=embeddings, documents=docs, metadatas=metas)
        return {"stored_count": len(embeddings), "collection_count": coll.count()}

    if operation == "query":
        query_emb = params.get("query_embedding", [])
        top_k = int(params.get("top_k", 5))
        if not query_emb:
            return {"matches": []}
        results = coll.query(query_embeddings=[query_emb], n_results=top_k)
        matches = []
        if results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                matches.append(
                    {
                        "id": results["ids"][0][i],
                        "document": results["documents"][0][i] if results["documents"] else "",
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "distance": results["distances"][0][i] if results.get("distances") else None,
                    }
                )
        return {"matches": matches}

    if operation == "delete":
        ids = params.get("ids", [])
        if ids:
            await asyncio.to_thread(coll.delete, ids=ids)
        return {"deleted": True, "count": len(ids)}

    return {}


async def _qdrant_op(operation: str, params: Dict[str, Any], collection: str) -> Dict[str, Any]:
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, PointStruct, VectorParams
    except ImportError:
        raise NodeUserError("Qdrant client unavailable. pip install qdrant-client")

    url = params.get("qdrant_url", "http://localhost:6333")
    client = QdrantClient(url=url)

    if operation == "store":
        embeddings = params.get("embeddings", [])
        chunks = params.get("chunks", [])
        if not embeddings:
            return {"stored_count": 0}
        vec_size = len(embeddings[0])
        colls = client.get_collections().collections
        if collection not in [c.name for c in colls]:
            client.create_collection(
                collection,
                vectors_config=VectorParams(size=vec_size, distance=Distance.COSINE),
            )
        points = []
        for i, emb in enumerate(embeddings):
            payload: Dict[str, Any] = {}
            if i < len(chunks):
                c = chunks[i]
                if isinstance(c, dict):
                    payload = {
                        "content": c.get("content", ""),
                        "source": c.get("source", "unknown"),
                        "chunk_index": c.get("chunk_index", i),
                    }
                else:
                    payload = {"content": str(c), "source": "input", "chunk_index": i}
            points.append(PointStruct(id=str(uuid.uuid4()), vector=emb, payload=payload))
        await asyncio.to_thread(client.upsert, collection_name=collection, points=points)
        return {"stored_count": len(embeddings)}

    if operation == "query":
        query_emb = params.get("query_embedding", [])
        top_k = int(params.get("top_k", 5))
        if not query_emb:
            return {"matches": []}
        results = await asyncio.to_thread(
            client.search,
            collection_name=collection,
            query_vector=query_emb,
            limit=top_k,
        )
        return {
            "matches": [
                {"id": str(r.id), "document": r.payload.get("content", ""), "metadata": r.payload, "score": r.score} for r in results
            ]
        }

    if operation == "delete":
        ids = params.get("ids", [])
        if ids:
            await asyncio.to_thread(client.delete, collection_name=collection, points_selector=ids)
        return {"deleted": True, "count": len(ids)}

    return {}


async def _pinecone_op(operation: str, params: Dict[str, Any], collection: str) -> Dict[str, Any]:
    from pinecone import Pinecone

    api_key = params.get("pinecone_api_key", "")
    if not api_key:
        raise NodeUserError("Pinecone API key required")

    pc = Pinecone(api_key=api_key)
    index = pc.Index(collection)

    if operation == "store":
        embeddings = params.get("embeddings", [])
        chunks = params.get("chunks", [])
        if not embeddings:
            return {"stored_count": 0}
        vectors = []
        for i, emb in enumerate(embeddings):
            meta: Dict[str, Any] = {}
            if i < len(chunks):
                c = chunks[i]
                if isinstance(c, dict):
                    meta = {
                        "content": c.get("content", ""),
                        "source": c.get("source", "unknown"),
                        "chunk_index": c.get("chunk_index", i),
                    }
                else:
                    meta = {"content": str(c), "source": "input", "chunk_index": i}
            vectors.append({"id": str(uuid.uuid4()), "values": emb, "metadata": meta})
        await asyncio.to_thread(index.upsert, vectors=vectors)
        return {"stored_count": len(embeddings)}

    if operation == "query":
        query_emb = params.get("query_embedding", [])
        top_k = int(params.get("top_k", 5))
        if not query_emb:
            return {"matches": []}
        results = await asyncio.to_thread(
            index.query,
            vector=query_emb,
            top_k=top_k,
            include_metadata=True,
        )
        return {
            "matches": [
                {
                    "id": m.id,
                    "document": m.metadata.get("content", "") if m.metadata else "",
                    "metadata": m.metadata or {},
                    "score": m.score,
                }
                for m in results.matches
            ]
        }

    if operation == "delete":
        ids = params.get("ids", [])
        if ids:
            await asyncio.to_thread(index.delete, ids=ids)
        return {"deleted": True, "count": len(ids)}

    return {}


class VectorStoreNode(ActionNode):
    type = "vectorStore"
    display_name = "Vector Store"
    subtitle = "Store/Query"
    group = ("document",)
    description = "Store and query vector embeddings"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": False}
    task_queue = TaskQueue.DEFAULT

    Params = VectorStoreParams
    Output = VectorStoreOutput

    @Operation("dispatch")
    async def dispatch(self, ctx: NodeContext, params: VectorStoreParams) -> VectorStoreOutput:
        p = params.model_dump()
        operation = params.operation
        backend_raw = params.backend
        backend = "chroma" if backend_raw in ("chroma", "chromadb") else backend_raw
        collection = params.collection_name

        if backend == "chroma":
            result = await _chroma_op(operation, p, collection)
        elif backend == "qdrant":
            result = await _qdrant_op(operation, p, collection)
        elif backend == "pinecone":
            result = await _pinecone_op(operation, p, collection)
        else:
            raise NodeUserError(f"Unknown backend: {backend_raw}")

        result["backend"] = backend
        result["collection_name"] = collection
        result["operation"] = operation
        return VectorStoreOutput(**result)
