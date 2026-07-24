"""Native async embedders and a small in-memory cosine index.

The default Hugging Face backend is optional.  Merely enabling long-term
memory never imports PyTorch or downloads a model on the event-loop thread:
model construction and encoding run through :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import inspect
import math
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Literal, Optional, Protocol
from uuid import uuid4

from core.logging import get_logger

logger = get_logger(__name__)

EmbeddingProvider = Literal["huggingface", "openai", "ollama"]
DEFAULT_EMBEDDING_MODELS: Dict[str, str] = {
    "huggingface": "BAAI/bge-small-en-v1.5",
    "openai": "text-embedding-3-small",
    "ollama": "nomic-embed-text",
}


@dataclass(frozen=True)
class MemoryVectorStoreKey:
    """Non-secret identity for one session's compatible vector space."""

    session_id: str
    provider: str
    model: str
    endpoint: str
    credential_fingerprint: str


# Legacy string keys remain accepted so state-clear can clean stores created by
# a worker running the pre-migration code during a rolling deployment.
_memory_vector_stores: Dict[MemoryVectorStoreKey | str, Any] = {}
_memory_vector_stores_lock = threading.RLock()


@dataclass(frozen=True)
class MemoryDocument:
    """Search result compatible with the historical ``page_content`` API."""

    page_content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class EmbeddingError(RuntimeError):
    """Base class for user-safe embedding configuration/request failures."""


class EmbedderUnavailableError(EmbeddingError):
    """The requested direct SDK or optional embedding extra is unavailable."""


class EmbeddingProviderError(EmbeddingError):
    """A provider SDK failed while generating embeddings."""


class AsyncEmbedder(Protocol):
    """Provider-neutral async embedding contract."""

    async def embed_documents(
        self,
        texts: List[str],
        *,
        batch_size: int = 32,
    ) -> List[List[float]]: ...

    async def embed_query(self, text: str) -> List[float]: ...

    async def aclose(self) -> None: ...


# Compatibility name for code that imported the old protocol.
Embedder = AsyncEmbedder


def default_embedding_model(provider: str) -> str:
    normalized = str(provider or "huggingface").strip().lower()
    try:
        return DEFAULT_EMBEDDING_MODELS[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported embedding provider: {provider}") from exc


def _normalize_provider(provider: str) -> EmbeddingProvider:
    normalized = str(provider or "huggingface").strip().lower()
    if normalized not in DEFAULT_EMBEDDING_MODELS:
        raise ValueError(f"Unsupported embedding provider: {provider}")
    return normalized  # type: ignore[return-value]


def _normalize_endpoint(endpoint: Optional[str]) -> str:
    return str(endpoint or "").strip().rstrip("/")


def _module_available(module_name: str) -> bool:
    if module_name in sys.modules:
        return sys.modules[module_name] is not None
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _vector_lists(vectors: Any) -> List[List[float]]:
    if hasattr(vectors, "tolist"):
        vectors = vectors.tolist()
    return [
        [float(value) for value in vector]
        for vector in (vectors or [])
    ]


async def _close_sdk_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


class SentenceTransformerEmbedder:
    """Lazy, non-blocking adapter for the optional sentence-transformers SDK."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model: Any = None
        self._model_lock = threading.Lock()

    def _encode(
        self,
        texts: List[str],
        batch_size: int,
    ) -> List[List[float]]:
        # Loading a model may touch disk or download model assets. It executes
        # only in the worker thread that also performs CPU-heavy encoding.
        with self._model_lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise EmbedderUnavailableError(
                        "HuggingFace embeddings unavailable. Install the "
                        "optional 'local-embeddings' extra."
                    ) from exc

                self._model = SentenceTransformer(self.model_name)
            vectors = self._model.encode(
                texts,
                batch_size=batch_size,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return _vector_lists(vectors)

    async def embed_documents(
        self,
        texts: List[str],
        *,
        batch_size: int = 32,
    ) -> List[List[float]]:
        if not texts:
            return []
        try:
            return await asyncio.to_thread(
                self._encode,
                list(texts),
                batch_size,
            )
        except EmbedderUnavailableError:
            raise
        except Exception as exc:
            raise EmbeddingProviderError(
                "HuggingFace embedding generation failed "
                f"({type(exc).__name__})"
            ) from exc

    async def embed_query(self, text: str) -> List[float]:
        vectors = await self.embed_documents([text], batch_size=1)
        return vectors[0] if vectors else []

    async def aclose(self) -> None:
        return None


class OpenAIEmbedder:
    """Direct ``AsyncOpenAI.embeddings`` adapter."""

    def __init__(
        self,
        model_name: str,
        *,
        api_key: Optional[str],
        endpoint: Optional[str] = None,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise EmbedderUnavailableError(
                "OpenAI embeddings unavailable. Install the 'openai' package."
            ) from exc

        kwargs: Dict[str, Any] = {"api_key": api_key or None}
        if endpoint:
            kwargs["base_url"] = endpoint
        self.model_name = model_name
        try:
            self._client = AsyncOpenAI(**kwargs)
        except Exception as exc:
            raise EmbedderUnavailableError(
                "OpenAI embedding client initialization failed "
                f"({type(exc).__name__})"
            ) from exc

    async def embed_documents(
        self,
        texts: List[str],
        *,
        batch_size: int = 32,
    ) -> List[List[float]]:
        embeddings: List[List[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            try:
                response = await self._client.embeddings.create(
                    model=self.model_name,
                    input=batch,
                )
            except Exception as exc:
                raise EmbeddingProviderError(
                    "OpenAI embedding request failed "
                    f"({type(exc).__name__})"
                ) from exc
            data = sorted(response.data, key=lambda item: int(item.index))
            vectors = [
                [float(value) for value in item.embedding]
                for item in data
            ]
            if len(vectors) != len(batch):
                raise ValueError(
                    "OpenAI returned a different number of embeddings "
                    "than requested"
                )
            embeddings.extend(vectors)
        return embeddings

    async def embed_query(self, text: str) -> List[float]:
        vectors = await self.embed_documents([text], batch_size=1)
        return vectors[0] if vectors else []

    async def aclose(self) -> None:
        await _close_sdk_client(self._client)


class OllamaEmbedder:
    """Direct ``ollama.AsyncClient.embed`` adapter."""

    def __init__(
        self,
        model_name: str,
        *,
        endpoint: Optional[str] = None,
    ) -> None:
        try:
            from ollama import AsyncClient
        except ImportError as exc:
            raise EmbedderUnavailableError(
                "Ollama embeddings unavailable. Install the 'ollama' package."
            ) from exc

        kwargs: Dict[str, Any] = {}
        if endpoint:
            kwargs["host"] = endpoint
        self.model_name = model_name
        try:
            self._client = AsyncClient(**kwargs)
        except Exception as exc:
            raise EmbedderUnavailableError(
                "Ollama embedding client initialization failed "
                f"({type(exc).__name__})"
            ) from exc

    async def embed_documents(
        self,
        texts: List[str],
        *,
        batch_size: int = 32,
    ) -> List[List[float]]:
        embeddings: List[List[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            try:
                response = await self._client.embed(
                    model=self.model_name,
                    input=batch,
                )
            except Exception as exc:
                raise EmbeddingProviderError(
                    "Ollama embedding request failed "
                    f"({type(exc).__name__})"
                ) from exc
            vectors = (
                response.get("embeddings")
                if isinstance(response, dict)
                else getattr(response, "embeddings", None)
            )
            if vectors is None:
                raise ValueError("Ollama returned no embeddings")
            batch_vectors = _vector_lists(vectors)
            if len(batch_vectors) != len(batch):
                raise ValueError(
                    "Ollama returned a different number of embeddings "
                    "than requested"
                )
            embeddings.extend(batch_vectors)
        return embeddings

    async def embed_query(self, text: str) -> List[float]:
        vectors = await self.embed_documents([text], batch_size=1)
        return vectors[0] if vectors else []

    async def aclose(self) -> None:
        await _close_sdk_client(self._client)


def create_embedder(
    provider: str,
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
) -> AsyncEmbedder:
    """Build a direct-SDK embedder without making a network request."""

    normalized = _normalize_provider(provider)
    model_name = str(model or "").strip() or default_embedding_model(normalized)
    normalized_endpoint = _normalize_endpoint(endpoint)
    if normalized == "huggingface":
        if not _module_available("sentence_transformers"):
            raise EmbedderUnavailableError(
                "HuggingFace embeddings unavailable. Install the optional "
                "'local-embeddings' extra."
            )
        return SentenceTransformerEmbedder(model_name)
    if normalized == "openai":
        if not str(api_key or "").strip():
            raise EmbedderUnavailableError(
                "OpenAI API key is required for embeddings."
            )
        return OpenAIEmbedder(
            model_name,
            api_key=api_key,
            endpoint=normalized_endpoint or None,
        )
    return OllamaEmbedder(
        model_name,
        endpoint=normalized_endpoint or None,
    )


class NativeMemoryVectorStore:
    """Concurrency-safe async cosine index with the historical tiny API."""

    def __init__(
        self,
        embedder: AsyncEmbedder,
        *,
        batch_size: int = 32,
    ) -> None:
        self._embedder = embedder
        self._batch_size = batch_size
        self._entries: List[
            tuple[str, str, List[float], Dict[str, Any]]
        ] = []
        self._dimension: Optional[int] = None
        self._lock = threading.RLock()

    async def add_texts(
        self,
        texts: Iterable[str],
        metadatas: Optional[Iterable[Dict[str, Any]]] = None,
        **_: Any,
    ) -> List[str]:
        values = [str(text) for text in texts]
        if not values:
            return []
        metadata_values = list(metadatas or ({} for _ in values))
        if len(metadata_values) != len(values):
            raise ValueError("metadatas must have the same length as texts")
        vectors = await self._embedder.embed_documents(
            values,
            batch_size=self._batch_size,
        )
        if len(vectors) != len(values):
            raise ValueError("embedder returned a different number of vectors")
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1 or not dimensions or 0 in dimensions:
            raise ValueError("embedder returned empty or inconsistent vectors")
        dimension = dimensions.pop()
        ids = [uuid4().hex for _ in values]
        with self._lock:
            if self._dimension is not None and self._dimension != dimension:
                raise ValueError(
                    "embedding dimension changed within one vector store"
                )
            self._dimension = dimension
            self._entries.extend(
                (
                    item_id,
                    text,
                    [float(value) for value in vector],
                    dict(metadata),
                )
                for item_id, text, vector, metadata in zip(
                    ids,
                    values,
                    vectors,
                    metadata_values,
                )
            )
        return ids

    @staticmethod
    def _cosine(left: List[float], right: List[float]) -> float:
        if len(left) != len(right) or not left:
            return float("-inf")
        denominator = math.sqrt(sum(v * v for v in left)) * math.sqrt(
            sum(v * v for v in right)
        )
        if denominator == 0:
            return 0.0
        return sum(a * b for a, b in zip(left, right)) / denominator

    async def similarity_search(
        self,
        query: str,
        k: int = 4,
        **_: Any,
    ) -> List[MemoryDocument]:
        if k <= 0:
            return []
        with self._lock:
            entries = list(self._entries)
            dimension = self._dimension
        if not entries:
            return []
        query_vector = [
            float(value)
            for value in await self._embedder.embed_query(str(query))
        ]
        if dimension is not None and len(query_vector) != dimension:
            raise ValueError(
                "query embedding dimension does not match stored vectors"
            )
        ranked = sorted(
            entries,
            key=lambda entry: self._cosine(query_vector, entry[2]),
            reverse=True,
        )
        return [
            MemoryDocument(page_content=text, metadata=dict(metadata))
            for _, text, _, metadata in ranked[:k]
        ]

    async def aclose(self) -> None:
        await self._embedder.aclose()


def _credential_fingerprint(api_key: Optional[str]) -> str:
    if not api_key:
        return "none"
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


async def get_memory_vector_store(
    session_id: str,
    *,
    provider: str = "huggingface",
    model: Optional[str] = None,
    endpoint: Optional[str] = None,
    api_key: Optional[str] = None,
    auth_service: Any = None,
) -> Optional[NativeMemoryVectorStore]:
    """Get one compatible native semantic store for a memory session.

    OpenAI credentials are accepted only in-memory and represented in the
    cache key by a short SHA-256 fingerprint. The plaintext credential is
    never persisted in the key or store metadata.

    Missing optional Hugging Face support and missing OpenAI credentials are
    deliberate no-op outcomes so long-term memory cannot break agent runs.
    """

    normalized = _normalize_provider(provider)
    model_name = str(model or "").strip() or default_embedding_model(normalized)
    normalized_endpoint = _normalize_endpoint(endpoint)
    resolved_key = str(api_key or "").strip() or None
    if normalized == "openai" and resolved_key is None:
        if auth_service is not None:
            try:
                auth_key = await auth_service.get_api_key(
                    "openai",
                    "default",
                )
                resolved_key = str(auth_key or "").strip() or None
            except Exception as exc:  # noqa: BLE001 - best-effort memory
                logger.warning(
                    "[Memory] Could not resolve OpenAI embedding credential: %s",
                    exc,
                )
                return None
        if not resolved_key:
            logger.warning(
                "[Memory] OpenAI long-term memory is disabled because no "
                "OpenAI credential is configured"
            )
            return None

    cache_key = MemoryVectorStoreKey(
        session_id=str(session_id),
        provider=normalized,
        model=model_name,
        endpoint=normalized_endpoint,
        credential_fingerprint=_credential_fingerprint(resolved_key),
    )
    with _memory_vector_stores_lock:
        cached = _memory_vector_stores.get(cache_key)
        if cached is not None:
            return cached
        try:
            embedder = create_embedder(
                normalized,
                model=model_name,
                api_key=resolved_key,
                endpoint=normalized_endpoint,
            )
        except EmbedderUnavailableError as exc:
            logger.warning("[Memory] Vector store not available: %s", exc)
            return None
        store = NativeMemoryVectorStore(embedder)
        _memory_vector_stores[cache_key] = store
    logger.debug(
        "[Memory] Created vector store for session=%s provider=%s model=%s",
        session_id,
        normalized,
        model_name,
    )
    return store


async def clear_memory_vector_stores(session_id: str) -> bool:
    """Remove and close every provider/model store for ``session_id``."""

    with _memory_vector_stores_lock:
        matching = [
            key
            for key in _memory_vector_stores
            if key == session_id
            or (
                isinstance(key, MemoryVectorStoreKey)
                and key.session_id == session_id
            )
        ]
        stores = [_memory_vector_stores.pop(key) for key in matching]
    seen: set[int] = set()
    for store in stores:
        if id(store) in seen:
            continue
        seen.add(id(store))
        close = getattr(store, "aclose", None)
        if close is None:
            continue
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 - clearing remains best effort
            logger.warning("[Memory] Failed to close vector store: %s", exc)
    return bool(matching)
