"""Vertex / Agent-Platform key support for the gemini provider.

AI Studio keys (``AIza...``) ride the Gemini Developer API; Agent
Platform / Vertex Express keys (``AQ.``) route the SAME provider to the
Vertex backend (``vertexai=True``) so usage bills the key's GCP project
instead of personal AI Studio credits. Detection lives in
``services/llm/vertex.py`` and is applied inside the two construction
points (``GeminiProvider.__init__`` and ``AIService.create_model``) so
every execution path picks it up with no call-site edits.

One curated model list (the ``max_output_tokens`` keys in
llm_defaults.json — real model names, no ``-latest`` aliases) serves
both backends, minus the ``vertex_incompatible_models`` entries which
are Developer-API-only (Gemma) and dropped from the Vertex branch.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm.vertex import VERTEX_KEY_PREFIX, is_vertex_express_key


def _curated_gemini_models():
    from services.llm import config as llm_config

    max_tokens_map = (
        llm_config.LLM_DEFAULTS["providers"]["gemini"]["max_output_tokens"]
    )
    return [m for m in max_tokens_map if m != "_default"]


def _vertex_incompatible_models():
    from services.llm import config as llm_config

    return llm_config.LLM_DEFAULTS["providers"]["gemini"].get(
        "vertex_incompatible_models", []
    )


# ---------------------------------------------------------------------------
# Key-prefix detection helper
# ---------------------------------------------------------------------------


class TestIsVertexExpressKey:
    def test_agent_platform_key_detected(self):
        assert is_vertex_express_key("AQ.Ab8RN6-test") is True

    def test_ai_studio_key_not_detected(self):
        assert is_vertex_express_key("AIzaSyTest123") is False

    def test_empty_and_none_are_false(self):
        assert is_vertex_express_key("") is False
        assert is_vertex_express_key(None) is False

    def test_prefix_constant(self):
        assert VERTEX_KEY_PREFIX == "AQ."


# ---------------------------------------------------------------------------
# Shared curated model list — real names only, same for both backends
# ---------------------------------------------------------------------------


class TestCuratedModelList:
    def test_no_alias_models(self):
        """Vertex 404s on ``-latest`` rolling aliases — the shared list
        must contain concrete versions only (live-verified on both
        endpoints)."""
        models = _curated_gemini_models()
        assert models, "gemini max_output_tokens list missing from llm_defaults.json"
        for model in models:
            assert "-latest" not in model, (
                f"{model!r} is a rolling alias — Vertex returns 404 for these"
            )

    def test_default_model_is_real_and_curated(self):
        from services.llm import config as llm_config

        default = llm_config.LLM_DEFAULTS["providers"]["gemini"]["default_model"]
        assert "-latest" not in default
        assert default in _curated_gemini_models()


# ---------------------------------------------------------------------------
# Gemma on the Developer API — curated inclusion + Vertex exclusion
# ---------------------------------------------------------------------------


class TestGemmaCuration:
    """Gemma 4 (gemma-4-31b-it / gemma-4-26b-a4b-it) is served on the
    Gemini Developer API only (free tier; supports systemInstruction and
    tools). It must appear in the curated list, be valid for the gemini
    provider (else the agent paths silently swap it to default_model),
    and be excluded from the Vertex branch where the plain ids 404.
    """

    GEMMA_MODELS = ("gemma-4-31b-it", "gemma-4-26b-a4b-it")

    def test_gemma_models_in_curated_list(self):
        curated = _curated_gemini_models()
        for model in self.GEMMA_MODELS:
            assert model in curated

    def test_gemma_models_are_vertex_incompatible(self):
        blocked = _vertex_incompatible_models()
        for model in self.GEMMA_MODELS:
            assert model in blocked

    def test_vertex_incompatible_is_subset_of_curated(self):
        """Every vertex-blocked entry must exist in the curated list —
        a stale blocklist entry means a renamed/removed model."""
        curated = set(_curated_gemini_models())
        for model in _vertex_incompatible_models():
            assert model in curated, (
                f"{model!r} in vertex_incompatible_models but not curated"
            )

    def test_default_model_not_vertex_incompatible(self):
        """The Vertex count_tokens probe and the invalid-model swap both
        fall back to default_model — it must be servable on Vertex."""
        from services.llm import config as llm_config

        default = llm_config.LLM_DEFAULTS["providers"]["gemini"]["default_model"]
        assert default not in _vertex_incompatible_models()

    def test_gemma_valid_for_gemini_provider(self):
        """The four runtime swap sites (execute_agent, execute_chat_agent,
        temporal agent, RLM) gate on is_model_valid_for_provider — without
        a matching detection pattern gemma silently swaps to default."""
        from services.ai import is_model_valid_for_provider

        for model in self.GEMMA_MODELS:
            assert is_model_valid_for_provider(model, "gemini") is True

    def test_cerebras_gemma_still_valid_for_cerebras(self):
        """Cerebras serves its own gemma-4-31b (no -it suffix); adding
        'gemma' to gemini's patterns must not break cerebras validity —
        the check is per-provider, not first-match-wins."""
        from services.ai import is_model_valid_for_provider

        assert is_model_valid_for_provider("gemma-4-31b", "cerebras") is True


# ---------------------------------------------------------------------------
# Native path: GeminiProvider client construction + fetch_models
# ---------------------------------------------------------------------------


class TestGeminiProviderVertexMode:
    def _make(self, api_key, proxy_url=None):
        with patch("google.genai.Client") as client_cls:
            from services.llm.providers.gemini import GeminiProvider

            provider = GeminiProvider(api_key, proxy_url=proxy_url)
        return provider, client_cls

    def test_vertex_key_builds_vertex_client(self):
        provider, client_cls = self._make("AQ.test-key")
        client_cls.assert_called_once_with(vertexai=True, api_key="AQ.test-key")
        assert provider._vertex is True

    def test_vertex_key_ignores_proxy_url(self):
        _, client_cls = self._make("AQ.test-key", proxy_url="http://localhost:11434")
        kwargs = client_cls.call_args[1]
        assert "http_options" not in kwargs
        assert kwargs["vertexai"] is True

    def test_ai_studio_key_unchanged(self):
        provider, client_cls = self._make("AIzaSyTest")
        client_cls.assert_called_once_with(api_key="AIzaSyTest")
        assert provider._vertex is False

    def test_ai_studio_key_with_proxy_unchanged(self):
        _, client_cls = self._make("AIzaSyTest", proxy_url="http://localhost:11434")
        kwargs = client_cls.call_args[1]
        assert kwargs["http_options"] == {"base_url": "http://localhost:11434"}
        assert "vertexai" not in kwargs

    @pytest.mark.asyncio
    async def test_fetch_models_vertex_probes_and_returns_curated_list(self):
        """Vertex model listing rejects API keys, so the key is verified
        with a free count_tokens call and the curated list minus the
        Developer-API-only entries (Gemma) is returned."""
        provider, _ = self._make("AQ.test-key")
        provider._client = MagicMock()
        provider._client.aio.models.count_tokens = AsyncMock()

        models = await provider.fetch_models("AQ.test-key")

        blocked = set(_vertex_incompatible_models())
        expected = [m for m in _curated_gemini_models() if m not in blocked]
        assert models == expected
        assert len(models) > 0
        provider._client.aio.models.count_tokens.assert_awaited_once_with(
            model=expected[0], contents="hello"
        )

    @pytest.mark.asyncio
    async def test_fetch_models_ai_studio_returns_same_curated_list(self):
        """The AI Studio branch probes the key via the models endpoint,
        then returns the FULL curated list — including Developer-API-only
        entries like Gemma that the Vertex branch filters out."""
        provider, _ = self._make("AIzaSyTest")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            models = await provider.fetch_models("AIzaSyTest")

        assert models == _curated_gemini_models()
        mock_response.raise_for_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_models_vertex_propagates_invalid_key_error(self):
        """An invalid key raises the typed SDK error, which the unifier
        translates into NodeUserError for the credentials modal."""
        provider, _ = self._make("AQ.bad-key")
        provider._client = MagicMock()
        provider._client.aio.models.count_tokens = AsyncMock(
            side_effect=RuntimeError("401 UNAUTHENTICATED")
        )

        with pytest.raises(RuntimeError, match="401"):
            await provider.fetch_models("AQ.bad-key")

    @pytest.mark.asyncio
    async def test_thinking_level_not_fabricated(self):
        """``ThinkingConfig.level`` defaults to None — when the user only
        configured a budget, the provider must send thinking_budget alone.
        Vertex rejects an unsolicited thinking_level on 2.5-era models with
        400 INVALID_ARGUMENT (live-verified); the SDK/API owns per-model
        validation of whatever the user explicitly set."""
        from services.llm.protocol import Message, ThinkingConfig

        provider, _ = self._make("AQ.test-key")
        mock_types = MagicMock()
        mock_resp = MagicMock()
        mock_resp.candidates = []
        mock_resp.usage_metadata = None
        provider._client = MagicMock()
        provider._client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        with patch("google.genai.types", mock_types):
            await provider.chat(
                [Message(role="user", content="test")],
                model="gemini-2.5-flash",
                thinking=ThinkingConfig(enabled=True, budget=1024),
            )

        kwargs = mock_types.ThinkingConfig.call_args[1]
        assert kwargs == {
            "include_thoughts": True,
            "thinking_budget": 1024,
        }


# ---------------------------------------------------------------------------
# LangChain agent path: AIService.create_model
# ---------------------------------------------------------------------------


class _KwargsCapture:
    """Stand-in for ChatGoogleGenerativeAI that records its kwargs."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


class TestCreateModelVertexMode:
    def _create(self, api_key, *, thinking=None, proxy_url=None):
        from services.ai import AIService

        with patch("services.ai._get_google_genai_class", return_value=_KwargsCapture):
            model = AIService.create_model(
                MagicMock(),  # create_model touches no instance state
                provider="gemini",
                api_key=api_key,
                model="gemini-2.5-flash",
                temperature=0.7,
                max_tokens=4096,
                thinking=thinking,
                proxy_url=proxy_url,
            )
        return model.kwargs

    def test_vertex_key_sets_vertexai_flag(self):
        kwargs = self._create("AQ.test-key")
        assert kwargs["vertexai"] is True
        assert kwargs["google_api_key"] == "AQ.test-key"
        assert kwargs["max_output_tokens"] == 4096

    def test_vertex_key_nulls_proxy(self):
        kwargs = self._create("AQ.test-key", proxy_url="http://localhost:11434")
        assert "base_url" not in kwargs
        assert kwargs["google_api_key"] == "AQ.test-key"

    def test_vertex_key_does_not_alter_thinking_handling(self):
        """The vertex branch only adds ``vertexai=True`` — thinking kwargs
        (resolved by the model registry) must be byte-identical to the
        AI-Studio path for the same model + thinking config."""
        from services.ai import ThinkingConfig

        thinking = ThinkingConfig(enabled=True, budget=2048)
        vertex_kwargs = self._create("AQ.test-key", thinking=thinking)
        studio_kwargs = self._create("AIzaSyTest", thinking=thinking)

        assert vertex_kwargs.pop("vertexai") is True
        assert vertex_kwargs.pop("google_api_key") == "AQ.test-key"
        assert studio_kwargs.pop("google_api_key") == "AIzaSyTest"
        assert vertex_kwargs == studio_kwargs

    def test_ai_studio_key_has_no_vertexai_flag(self):
        kwargs = self._create("AIzaSyTest")
        assert "vertexai" not in kwargs
        assert kwargs["google_api_key"] == "AIzaSyTest"

    def test_ai_studio_key_proxy_still_applies(self):
        kwargs = self._create("AIzaSyTest", proxy_url="http://localhost:11434")
        assert kwargs["base_url"] == "http://localhost:11434"
        assert "vertexai" not in kwargs
