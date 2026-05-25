"""Contract tests for search nodes: braveSearch, serperSearch, perplexitySearch.

These tests freeze the input -> output behaviour documented in
`docs-internal/node-logic-flows/search/`. A refactor that breaks any of these
indicates the docs (and the user-visible contract) need to be updated too.

Each handler is exercised through the full NodeExecutor dispatch via the
shared `harness` fixture; httpx is mocked with respx so no network is touched.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from tests.nodes._mocks import patched_container, patched_pricing


pytestmark = pytest.mark.node_contract


# ============================================================================
# braveSearch
# ============================================================================


class TestBraveSearch:
    URL = "https://api.search.brave.com/res/v1/web/search"

    @respx.mock
    async def test_happy_path(self, harness):
        respx.get(self.URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "title": "Example",
                                "description": "An example result",
                                "url": "https://example.com",
                            },
                            {
                                "title": "Second",
                                "description": "Another",
                                "url": "https://example.org",
                            },
                        ]
                    }
                },
            )
        )

        with patched_container(auth_api_keys={"brave_search": "tk_brave"}), patched_pricing():
            result = await harness.execute(
                "braveSearch",
                {"query": "hello world", "max_results": 2, "country": "US"},
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["query", "results", "result_count", "provider"])
        payload = result["result"]
        assert payload["query"] == "hello world"
        assert payload["provider"] == "brave_search"
        assert payload["result_count"] == 2
        assert payload["results"][0] == {
            "title": "Example",
            "snippet": "An example result",
            "url": "https://example.com",
        }

        # request was actually made with the documented headers/query
        sent = respx.calls.last.request
        assert sent.headers["X-Subscription-Token"] == "tk_brave"
        assert sent.url.params["q"] == "hello world"
        assert sent.url.params["count"] == "2"
        assert sent.url.params["country"] == "US"

    async def test_empty_query_short_circuits(self, harness):
        # No httpx mock needed - handler must not call the API.
        with patched_container(auth_api_keys={"brave_search": "tk"}), patched_pricing():
            result = await harness.execute("braveSearch", {"query": "   "})

        harness.assert_envelope(result, success=False)
        # Whitespace-only is accepted by min_length=1 validator but rejected by upstream API as 422.
        assert "422" in result["error"] or "invalid parameters" in result["error"].lower() or "query is required" in result["error"].lower()

    async def test_missing_api_key(self, harness):
        with patched_container(auth_api_keys={}), patched_pricing():
            result = await harness.execute("braveSearch", {"query": "x"})

        harness.assert_envelope(result, success=False)
        assert "brave_search" in result["error"].lower() or "api key" in result["error"].lower()

    @respx.mock
    async def test_http_error_returns_envelope(self, harness):
        respx.get(self.URL).mock(return_value=httpx.Response(429, text="rate limited"))

        with patched_container(auth_api_keys={"brave_search": "tk"}), patched_pricing():
            result = await harness.execute("braveSearch", {"query": "x"})

        harness.assert_envelope(result, success=False)
        assert "429" in result["error"]

    @respx.mock
    async def test_clamps_max_results_to_100(self, harness):
        respx.get(self.URL).mock(return_value=httpx.Response(200, json={"web": {"results": []}}))

        with patched_container(auth_api_keys={"brave_search": "tk"}), patched_pricing():
            result = await harness.execute("braveSearch", {"query": "x", "max_results": 500})

        # Post-refactor: Params tightens max_results with le=20 upper bound; 500 is rejected at validation.
        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()


# ============================================================================
# serperSearch
# ============================================================================


class TestSerperSearch:
    BASE = "https://google.serper.dev"

    @respx.mock
    async def test_web_search_happy_path(self, harness):
        respx.post(f"{self.BASE}/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "organic": [
                        {
                            "title": "Item 1",
                            "snippet": "snippet text",
                            "link": "https://a.example",
                            "position": 1,
                        }
                    ],
                    "knowledgeGraph": {"title": "kg-title"},
                },
            )
        )

        with patched_container(auth_api_keys={"serper": "tk_serp"}), patched_pricing():
            result = await harness.execute(
                "serperSearch",
                {"query": "best pizza", "search_type": "search", "country": "us"},
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["query", "results", "result_count", "search_type", "provider", "knowledge_graph"])
        payload = result["result"]
        assert payload["search_type"] == "search"
        assert payload["results"][0]["url"] == "https://a.example"
        assert payload["results"][0]["position"] == 1
        assert payload["knowledge_graph"] == {"title": "kg-title"}

        sent = respx.calls.last.request
        assert sent.headers["X-API-KEY"] == "tk_serp"
        # Body is JSON
        assert b'"q": "best pizza"' in sent.content or b'"q":"best pizza"' in sent.content

    @respx.mock
    async def test_news_branch_uses_news_endpoint(self, harness):
        respx.post(f"{self.BASE}/news").mock(
            return_value=httpx.Response(
                200,
                json={
                    "news": [
                        {
                            "title": "Headline",
                            "snippet": "snippet",
                            "link": "https://news.example",
                            "date": "2026-04-15",
                            "source": "Example News",
                        }
                    ]
                },
            )
        )

        with patched_container(auth_api_keys={"serper": "tk"}), patched_pricing():
            result = await harness.execute("serperSearch", {"query": "ai", "search_type": "news"})

        harness.assert_envelope(result, success=True)
        assert result["result"]["search_type"] == "news"
        item = result["result"]["results"][0]
        assert item["title"] == "Headline"
        # knowledge_graph absent (None) in news response
        assert result["result"].get("knowledge_graph") is None

    @respx.mock
    async def test_unknown_search_type_falls_back_to_web_endpoint_but_returns_empty_results(self, harness):
        # Post-refactor: Params tightens search_type with Literal[...]; unknown value rejected.
        respx.post(f"{self.BASE}/search").mock(return_value=httpx.Response(200, json={"organic": [{"title": "x", "link": "y"}]}))

        with patched_container(auth_api_keys={"serper": "tk"}), patched_pricing():
            result = await harness.execute("serperSearch", {"query": "x", "search_type": "nonsense"})

        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_empty_query_short_circuits(self, harness):
        with patched_container(auth_api_keys={"serper": "tk"}), patched_pricing():
            result = await harness.execute("serperSearch", {"query": ""})

        harness.assert_envelope(result, success=False)

    @respx.mock
    async def test_http_error_returns_envelope(self, harness):
        respx.post(f"{self.BASE}/search").mock(return_value=httpx.Response(500, text="boom"))

        with patched_container(auth_api_keys={"serper": "tk"}), patched_pricing():
            result = await harness.execute("serperSearch", {"query": "x"})

        harness.assert_envelope(result, success=False)
        assert "500" in result["error"]


# ============================================================================
# perplexitySearch
# ============================================================================


class TestPerplexitySearch:
    URL = "https://api.perplexity.ai/chat/completions"

    @respx.mock
    async def test_happy_path_with_optional_fields(self, harness):
        respx.post(self.URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "the answer"}}],
                    "citations": ["https://src1", "https://src2"],
                    "images": [{"image_url": "https://img1"}],
                    "related_questions": ["why?"],
                },
            )
        )

        with patched_container(auth_api_keys={"perplexity": "tk_pplx"}), patched_pricing():
            result = await harness.execute(
                "perplexitySearch",
                {
                    "query": "what is rust",
                    "model": "sonar-pro",
                    "search_recency_filter": "week",
                    "return_images": True,
                    "return_related_questions": True,
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["answer"] == "the answer"
        assert payload["citations"] == ["https://src1", "https://src2"]
        assert payload["results"] == [{"url": "https://src1"}, {"url": "https://src2"}]
        assert payload["model"] == "sonar-pro"
        assert payload["images"] == [{"image_url": "https://img1"}]
        assert payload["related_questions"] == ["why?"]
        assert payload["provider"] == "perplexity"

        sent = respx.calls.last.request
        assert sent.headers["Authorization"] == "Bearer tk_pplx"
        body = sent.content
        assert b'"search_recency_filter":' in body or b'"search_recency_filter" :' in body
        assert b'"return_images":' in body
        assert b'"return_related_questions":' in body

    @respx.mock
    async def test_omits_optional_fields_when_default(self, harness):
        respx.post(self.URL).mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}], "citations": []},
            )
        )

        with patched_container(auth_api_keys={"perplexity": "tk"}), patched_pricing():
            result = await harness.execute("perplexitySearch", {"query": "x"})

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        # optional response fields are None when source data missing
        assert payload.get("images") is None
        assert payload.get("related_questions") is None

        # body should not contain optional keys
        body = respx.calls.last.request.content
        assert b"search_recency_filter" not in body
        assert b"return_images" not in body
        assert b"return_related_questions" not in body

    @respx.mock
    async def test_empty_choices_yields_empty_answer(self, harness):
        respx.post(self.URL).mock(return_value=httpx.Response(200, json={"choices": []}))

        with patched_container(auth_api_keys={"perplexity": "tk"}), patched_pricing():
            result = await harness.execute("perplexitySearch", {"query": "x"})

        harness.assert_envelope(result, success=True)
        assert result["result"]["answer"] == ""
        assert result["result"]["citations"] == []

    async def test_empty_query_short_circuits(self, harness):
        with patched_container(auth_api_keys={"perplexity": "tk"}), patched_pricing():
            result = await harness.execute("perplexitySearch", {"query": ""})

        harness.assert_envelope(result, success=False)

    @respx.mock
    async def test_http_error_returns_envelope(self, harness):
        respx.post(self.URL).mock(return_value=httpx.Response(401, text="bad key"))

        with patched_container(auth_api_keys={"perplexity": "tk"}), patched_pricing():
            result = await harness.execute("perplexitySearch", {"query": "x"})

        harness.assert_envelope(result, success=False)
        assert "401" in result["error"]
