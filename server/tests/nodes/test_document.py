"""Contract tests for document nodes: httpScraper, fileDownloader, documentParser,
textChunker, embeddingGenerator, vectorStore.

These tests freeze the input -> output behaviour documented in
`docs-internal/node-logic-flows/document/`. A refactor that breaks any of
these indicates the docs (and the user-visible contract) need to be updated
too.

External side-effects are mocked:
  - httpScraper / fileDownloader: httpx calls stubbed via respx.
  - documentParser: each parser library is patched at the module level
    (`services.handlers.document.<lib>`), or real HTML is fed through
    BeautifulSoup for the happy path.
  - textChunker: pure function, exercised with real LangChain splitters.
  - embeddingGenerator: the embedder class is patched to return canned vectors.
  - vectorStore: the ChromaDB client is patched to a fake collection.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx


pytestmark = pytest.mark.node_contract


# ============================================================================
# httpScraper
# ============================================================================


class TestHttpScraper:
    @respx.mock
    async def test_happy_path_single_mode_extracts_links(self, harness):
        html = """
        <html><body>
          <a href="/file1.pdf">Doc 1</a>
          <a href="https://other.example/file2.pdf">Doc 2</a>
          <a href="/not-a-pdf.html">Skip</a>
        </body></html>
        """
        respx.get("https://example.com/list").mock(return_value=httpx.Response(200, text=html))

        result = await harness.execute(
            "httpScraper",
            {"url": "https://example.com/list", "link_selector": 'a[href$=".pdf"]'},
        )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["items", "item_count", "errors"])
        payload = result["result"]
        assert payload["item_count"] == 2
        # urljoin resolves the relative link against the source URL
        urls = {i["url"] for i in payload["items"]}
        assert "https://example.com/file1.pdf" in urls
        assert "https://other.example/file2.pdf" in urls
        for item in payload["items"]:
            assert item["source_url"] == "https://example.com/list"
        assert payload["errors"] == []

    async def test_missing_url_fails_envelope(self, harness):
        result = await harness.execute("httpScraper", {"url": ""})
        harness.assert_envelope(result, success=False)
        assert "url is required" in result["error"].lower()

    @respx.mock
    async def test_per_url_http_error_collected_in_errors(self, harness):
        # Page iteration mode fetches two URLs; one 404s, the other succeeds.
        respx.get("https://example.com/p1").mock(return_value=httpx.Response(404))
        respx.get("https://example.com/p2").mock(return_value=httpx.Response(200, text='<a href="ok.pdf">OK</a>'))

        result = await harness.execute(
            "httpScraper",
            {
                "url": "https://example.com/p{page}",
                "iteration_mode": "page",
                "start_page": 1,
                "end_page": 2,
                "link_selector": "a",
            },
        )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["item_count"] == 1
        assert payload["items"][0]["page"] == 2
        assert len(payload["errors"]) == 1
        assert "p1" in payload["errors"][0]

    @respx.mock
    async def test_date_mode_expands_range(self, harness):
        respx.get(url__regex=r"https://example\.com/\?d=\d{4}-\d{2}-\d{2}").mock(return_value=httpx.Response(200, text="<html></html>"))

        result = await harness.execute(
            "httpScraper",
            {
                "url": "https://example.com/?d={date}",
                "iteration_mode": "date",
                "start_date": "2025-01-01",
                "end_date": "2025-01-03",
                "link_selector": "a",
            },
        )

        harness.assert_envelope(result, success=True)
        # Three calls: 01, 02, 03
        assert len(respx.calls) == 3


# ============================================================================
# fileDownloader
# ============================================================================


class TestFileDownloader:
    @respx.mock
    async def test_happy_path_downloads_files(self, harness, tmp_path):
        respx.get("https://example.com/a.pdf").mock(return_value=httpx.Response(200, content=b"AAA"))
        respx.get("https://example.com/b.pdf").mock(return_value=httpx.Response(200, content=b"BBBB"))

        result = await harness.execute(
            "fileDownloader",
            {
                "items": [
                    {"url": "https://example.com/a.pdf"},
                    {"url": "https://example.com/b.pdf"},
                ],
                "outputDir": str(tmp_path),
                "maxWorkers": 2,
            },
        )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["downloaded", "skipped", "failed", "files", "output_dir"])
        payload = result["result"]
        assert payload["downloaded"] == 2
        assert payload["failed"] == 0
        assert (tmp_path / "a.pdf").read_bytes() == b"AAA"
        assert (tmp_path / "b.pdf").read_bytes() == b"BBBB"

    async def test_empty_items_short_circuits(self, harness):
        result = await harness.execute("fileDownloader", {"items": []})
        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert (
            payload
            == {
                "downloaded": 0,
                "skipped": 0,
                "failed": 0,
                "files": [],
            }
            or payload["downloaded"] == 0
        )

    @respx.mock
    async def test_skip_existing_does_not_refetch(self, harness, tmp_path):
        existing = tmp_path / "a.pdf"
        existing.write_bytes(b"OLD")

        # No respx mock for a.pdf - if the handler tried to fetch, respx would raise.
        result = await harness.execute(
            "fileDownloader",
            {
                "items": [{"url": "https://example.com/a.pdf"}],
                "outputDir": str(tmp_path),
                "skipExisting": True,
            },
        )

        harness.assert_envelope(result, success=True)
        assert result["result"]["skipped"] == 1
        assert result["result"]["downloaded"] == 0
        # Existing bytes untouched
        assert existing.read_bytes() == b"OLD"

    @respx.mock
    async def test_http_error_counted_as_failed(self, harness, tmp_path):
        respx.get("https://example.com/bad.pdf").mock(return_value=httpx.Response(500, text="boom"))

        result = await harness.execute(
            "fileDownloader",
            {
                "items": [{"url": "https://example.com/bad.pdf"}],
                "outputDir": str(tmp_path),
            },
        )

        harness.assert_envelope(result, success=True)
        assert result["result"]["failed"] == 1
        assert result["result"]["downloaded"] == 0


# ============================================================================
# documentParser
# ============================================================================


class TestDocumentParser:
    async def test_beautifulsoup_parses_html_file(self, harness, tmp_path):
        html = "<html><body>" "<script>alert(1)</script>" "<style>.x {color:red}</style>" "<p>Hello <b>World</b></p>" "</body></html>"
        f = tmp_path / "page.html"
        f.write_text(html, encoding="utf-8")

        result = await harness.execute(
            "documentParser",
            {"file_path": str(f), "parser": "beautifulsoup"},
        )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["documents", "parsed_count", "failed"])
        payload = result["result"]
        assert payload["parsed_count"] == 1
        doc = payload["documents"][0]
        assert doc["filename"] == "page.html"
        assert doc["parser"] == "beautifulsoup"
        # script and style tags stripped
        assert "alert(1)" not in doc["content"]
        assert ".x {color:red}" not in doc["content"]
        assert "Hello" in doc["content"] and "World" in doc["content"]

    async def test_no_files_returns_empty_documents(self, harness):
        result = await harness.execute("documentParser", {"files": [], "inputDir": ""})
        harness.assert_envelope(result, success=True)
        assert result["result"]["documents"] == []
        assert result["result"]["parsed_count"] == 0

    async def test_unknown_parser_collected_in_failed(self, harness, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("hello", encoding="utf-8")

        result = await harness.execute(
            "documentParser",
            {"files": [{"path": str(f)}], "parser": "nonsense"},
        )

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_pypdf_branch_via_patched_reader(self, harness, tmp_path):
        # Patch pypdf at its import site inside _parse_file_sync.
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-FAKE")

        fake_page = SimpleNamespace(extract_text=lambda: "page text")
        fake_reader = MagicMock()
        fake_reader.pages = [fake_page, fake_page]

        fake_pypdf = MagicMock()
        fake_pypdf.PdfReader = MagicMock(return_value=fake_reader)

        with patch.dict(sys.modules, {"pypdf": fake_pypdf}):
            result = await harness.execute(
                "documentParser",
                {"file_path": str(f), "parser": "pypdf"},
            )

        harness.assert_envelope(result, success=True)
        doc = result["result"]["documents"][0]
        assert doc["parser"] == "pypdf"
        assert "page text" in doc["content"]


# ============================================================================
# textChunker
# ============================================================================


class TestTextChunker:
    async def test_recursive_chunks_long_text(self, harness):
        content = "abcdefghij" * 200  # 2000 chars
        result = await harness.execute(
            "textChunker",
            {
                "documents": [{"content": content, "source": "src1"}],
                "chunk_size": 500,
                "chunk_overlap": 50,
                "strategy": "recursive",
            },
        )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["chunks", "chunk_count"])
        payload = result["result"]
        assert payload["chunk_count"] >= 3  # 2000 chars / 500 with overlap
        # chunk_index is 0-based and monotonically increasing
        indices = [c["chunk_index"] for c in payload["chunks"]]
        assert indices == list(range(len(payload["chunks"])))
        # source preserved
        assert all(c["source"] == "src1" for c in payload["chunks"])

    async def test_empty_documents_short_circuits(self, harness):
        result = await harness.execute("textChunker", {"documents": []})
        harness.assert_envelope(result, success=True)
        assert result["result"] == {"chunks": [], "chunk_count": 0}

    async def test_empty_content_doc_is_skipped(self, harness):
        result = await harness.execute(
            "textChunker",
            {
                "documents": [
                    {"content": "", "source": "empty"},
                    {"content": "short text", "source": "s2"},
                ],
                "chunk_size": 1024,
                "chunk_overlap": 100,
            },
        )
        harness.assert_envelope(result, success=True)
        chunks = result["result"]["chunks"]
        # Only the non-empty doc contributed a chunk
        assert all(c["source"] == "s2" for c in chunks)
        assert len(chunks) == 1

    async def test_markdown_strategy_uses_markdown_splitter(self, harness):
        md = "# Heading\n\nParagraph one.\n\n## Sub\n\nParagraph two.\n"
        result = await harness.execute(
            "textChunker",
            {
                "documents": [{"content": md, "source": "md1"}],
                "chunk_size": 200,
                "chunk_overlap": 20,
                "strategy": "markdown",
            },
        )
        harness.assert_envelope(result, success=True)
        assert result["result"]["chunk_count"] >= 1


# ============================================================================
# embeddingGenerator
# ============================================================================


class TestEmbeddingGenerator:
    async def test_huggingface_happy_path_with_patched_embedder(self, harness):
        # Patch langchain_huggingface at its lazy import site.
        fake_embedder = MagicMock()
        fake_embedder.embed_documents = MagicMock(return_value=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])

        fake_hf_mod = MagicMock()
        fake_hf_mod.HuggingFaceEmbeddings = MagicMock(return_value=fake_embedder)

        with patch.dict(sys.modules, {"langchain_huggingface": fake_hf_mod}):
            result = await harness.execute(
                "embeddingGenerator",
                {
                    "chunks": [
                        {"content": "first chunk", "source": "s1", "chunk_index": 0},
                        {"content": "second chunk", "source": "s1", "chunk_index": 1},
                    ],
                    "provider": "huggingface",
                    "model": "BAAI/bge-small-en-v1.5",
                },
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(
            result,
            ["embeddings", "embedding_count", "dimensions", "chunks", "provider", "model"],
        )
        payload = result["result"]
        assert payload["embedding_count"] == 2
        assert payload["dimensions"] == 3
        assert payload["provider"] == "huggingface"
        # Embedder was constructed with the requested model
        fake_hf_mod.HuggingFaceEmbeddings.assert_called_once()
        kwargs = fake_hf_mod.HuggingFaceEmbeddings.call_args.kwargs
        assert kwargs.get("model_name") == "BAAI/bge-small-en-v1.5"

    async def test_empty_chunks_short_circuits(self, harness):
        result = await harness.execute("embeddingGenerator", {"chunks": [], "provider": "huggingface"})
        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["embedding_count"] == 0
        assert payload["dimensions"] == 0
        assert payload["embeddings"] == []

    async def test_unknown_provider_fails_envelope(self, harness):
        result = await harness.execute(
            "embeddingGenerator",
            {"chunks": [{"content": "x"}], "provider": "made_up_provider"},
        )
        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_openai_provider_uses_apikey_param(self, harness):
        fake_embedder = MagicMock()
        fake_embedder.embed_documents = MagicMock(return_value=[[0.9, 0.8]])

        fake_openai_mod = MagicMock()
        fake_openai_mod.OpenAIEmbeddings = MagicMock(return_value=fake_embedder)

        with patch.dict(sys.modules, {"langchain_openai": fake_openai_mod}):
            result = await harness.execute(
                "embeddingGenerator",
                {
                    "chunks": [{"content": "only"}],
                    "provider": "openai",
                    "model": "text-embedding-3-small",
                    "api_key": "sk-test",
                },
            )

        harness.assert_envelope(result, success=True)
        fake_openai_mod.OpenAIEmbeddings.assert_called_once()
        kwargs = fake_openai_mod.OpenAIEmbeddings.call_args.kwargs
        assert kwargs.get("model") == "text-embedding-3-small"
        assert kwargs.get("api_key") == "sk-test"


# ============================================================================
# vectorStore
# ============================================================================


class _FakeChromaCollection:
    def __init__(self):
        self.added = []
        self.deleted_ids = []
        self._count = 0

    def add(self, ids, embeddings, documents, metadatas):
        self.added.append({"ids": ids, "embeddings": embeddings, "documents": documents, "metadatas": metadatas})
        self._count += len(ids)

    def count(self):
        return self._count

    def query(self, query_embeddings, n_results):
        return {
            "ids": [["id-1", "id-2"]],
            "documents": [["doc one", "doc two"]],
            "metadatas": [[{"source": "s1"}, {"source": "s2"}]],
            "distances": [[0.1, 0.2]],
        }

    def delete(self, ids):
        self.deleted_ids.extend(ids)


def _patched_chromadb_module():
    coll = _FakeChromaCollection()
    client = MagicMock()
    client.get_or_create_collection = MagicMock(return_value=coll)

    module = MagicMock()
    module.PersistentClient = MagicMock(return_value=client)
    return module, coll


class TestVectorStore:
    async def test_chroma_store_happy_path(self, harness, tmp_path):
        chromadb_mod, coll = _patched_chromadb_module()

        with patch.dict(sys.modules, {"chromadb": chromadb_mod}):
            result = await harness.execute(
                "vectorStore",
                {
                    "operation": "store",
                    "backend": "chroma",
                    "collection_name": "docs",
                    "embeddings": [[0.1, 0.2], [0.3, 0.4]],
                    "chunks": [
                        {"content": "c1", "source": "s1", "chunk_index": 0},
                        {"content": "c2", "source": "s1", "chunk_index": 1},
                    ],
                    "persist_dir": str(tmp_path),
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["stored_count"] == 2
        assert payload["backend"] == "chroma"
        assert payload["collection_name"] == "docs"
        # The collection actually received our vectors + docs
        assert len(coll.added) == 1
        batch = coll.added[0]
        assert batch["embeddings"] == [[0.1, 0.2], [0.3, 0.4]]
        assert batch["documents"] == ["c1", "c2"]

    async def test_chroma_store_with_empty_embeddings_returns_zero(self, harness, tmp_path):
        chromadb_mod, coll = _patched_chromadb_module()
        with patch.dict(sys.modules, {"chromadb": chromadb_mod}):
            result = await harness.execute(
                "vectorStore",
                {
                    "operation": "store",
                    "backend": "chroma",
                    "embeddings": [],
                    "chunks": [],
                    "persist_dir": str(tmp_path),
                },
            )
        harness.assert_envelope(result, success=True)
        assert result["result"]["stored_count"] == 0
        # Nothing written to the underlying collection
        assert coll.added == []

    async def test_chroma_query_happy_path(self, harness, tmp_path):
        chromadb_mod, coll = _patched_chromadb_module()
        with patch.dict(sys.modules, {"chromadb": chromadb_mod}):
            result = await harness.execute(
                "vectorStore",
                {
                    "operation": "query",
                    "backend": "chroma",
                    "collection_name": "docs",
                    "query_embedding": [0.1, 0.2],
                    "top_k": 2,
                    "persist_dir": str(tmp_path),
                },
            )

        harness.assert_envelope(result, success=True)
        matches = result["result"]["matches"]
        assert len(matches) == 2
        assert matches[0]["id"] == "id-1"
        assert matches[0]["document"] == "doc one"
        assert matches[0]["distance"] == 0.1
        assert matches[0]["metadata"] == {"source": "s1"}

    async def test_chroma_delete_happy_path(self, harness, tmp_path):
        chromadb_mod, coll = _patched_chromadb_module()
        with patch.dict(sys.modules, {"chromadb": chromadb_mod}):
            result = await harness.execute(
                "vectorStore",
                {
                    "operation": "delete",
                    "backend": "chroma",
                    "ids": ["id-a", "id-b", "id-c"],
                    "persist_dir": str(tmp_path),
                },
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["deleted"] is True
        assert result["result"]["count"] == 3
        assert coll.deleted_ids == ["id-a", "id-b", "id-c"]

    async def test_unknown_backend_fails_envelope(self, harness):
        result = await harness.execute(
            "vectorStore",
            {"operation": "store", "backend": "madeup", "embeddings": [[0.1]]},
        )
        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_pinecone_requires_api_key(self, harness):
        result = await harness.execute(
            "vectorStore",
            {
                "operation": "store",
                "backend": "pinecone",
                "embeddings": [[0.1]],
                "pineconeApiKey": "",
            },
        )
        harness.assert_envelope(result, success=False)
        assert "pinecone" in result["error"].lower() or "api key" in result["error"].lower()
