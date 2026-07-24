"""Tests for the dependency-free in-memory semantic index."""

import pytest

from services.memory.vector_store import (
    NativeMemoryVectorStore,
    _memory_vector_stores,
    clear_memory_vector_stores,
    get_memory_vector_store,
)


class _Embedder:
    _vectors = {
        "apple": [1.0, 0.0],
        "banana": [0.0, 1.0],
        "fruit": [0.9, 0.1],
    }

    async def embed_documents(self, texts, *, batch_size=32):
        return [self._vectors[text] for text in texts]

    async def embed_query(self, text):
        return self._vectors[text]

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_similarity_search_returns_cosine_ranked_documents():
    store = NativeMemoryVectorStore(_Embedder())
    ids = await store.add_texts(
        ["apple", "banana"],
        [{"source": "a"}, {"source": "b"}],
    )

    assert len(ids) == 2 and ids[0] != ids[1]
    documents = await store.similarity_search("fruit", k=2)
    assert [document.page_content for document in documents] == [
        "apple",
        "banana",
    ]
    assert documents[0].metadata == {"source": "a"}


@pytest.mark.asyncio
async def test_empty_and_zero_k_operations_are_noops():
    store = NativeMemoryVectorStore(_Embedder())
    assert await store.add_texts([]) == []
    assert await store.similarity_search("fruit", k=0) == []


@pytest.mark.asyncio
async def test_store_cache_separates_credentials_without_exposing_them(
    monkeypatch,
):
    from services.memory import vector_store as vector_store_module

    class _Auth:
        def __init__(self, key):
            self.key = key

        async def get_api_key(self, provider, session_id):
            assert (provider, session_id) == ("openai", "default")
            return self.key

    monkeypatch.setattr(
        vector_store_module,
        "create_embedder",
        lambda *args, **kwargs: _Embedder(),
    )
    session_id = "credential-cache-test"
    first = await get_memory_vector_store(
        session_id,
        provider="openai",
        model="text-embedding-3-small",
        endpoint="https://example.invalid/v1/",
        auth_service=_Auth("first-secret"),
    )
    again = await get_memory_vector_store(
        session_id,
        provider="openai",
        model="text-embedding-3-small",
        endpoint="https://example.invalid/v1",
        auth_service=_Auth("first-secret"),
    )
    rotated = await get_memory_vector_store(
        session_id,
        provider="openai",
        model="text-embedding-3-small",
        endpoint="https://example.invalid/v1",
        auth_service=_Auth("second-secret"),
    )
    try:
        assert first is again
        assert rotated is not first
        matching_keys = [
            key
            for key in _memory_vector_stores
            if getattr(key, "session_id", None) == session_id
        ]
        assert len(matching_keys) == 2
        assert "first-secret" not in repr(matching_keys)
        assert "second-secret" not in repr(matching_keys)
    finally:
        assert await clear_memory_vector_stores(session_id) is True


@pytest.mark.asyncio
async def test_missing_optional_huggingface_extra_is_a_noop(monkeypatch):
    from services.memory import vector_store as vector_store_module

    monkeypatch.setattr(
        vector_store_module,
        "_module_available",
        lambda _name: False,
    )
    store = await get_memory_vector_store(
        "missing-local-extra",
        provider="huggingface",
    )
    assert store is None
