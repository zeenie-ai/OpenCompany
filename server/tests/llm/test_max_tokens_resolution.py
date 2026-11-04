"""max_tokens resolution contract — default is the MODEL's max output.

Two resolvers exist (native chat path in ``services/llm/config.py`` and
the agent path in ``services/ai.py``); the agent one must delegate to
the native one so all four call sites agree (execute_chat,
execute_agent, execute_chat_agent, and the Temporal F4.B
``prepare_agent_payload``):

- user value -> clamped to the model's max output tokens
- no user value -> the model's max output tokens (registry ->
  llm_defaults fallback), never an artificial provider-wide floor
  (the old behaviour capped agents at the 8192 ``_default`` from
  llm_defaults.json regardless of model capability), or none at all for
  a provider that declares ``omit_default_max_tokens`` (OpenRouter).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest


pytestmark = pytest.mark.unit

MODEL_MAX = 65536


def _registry_mock():
    registry = MagicMock(name="ModelRegistry")
    registry.get_max_output_tokens.return_value = MODEL_MAX
    return registry


class TestNativeResolver:
    def test_no_user_value_defaults_to_model_max(self):
        from services.llm.config import resolve_max_tokens

        with patch("services.model_registry.get_model_registry", return_value=_registry_mock()):
            assert resolve_max_tokens({}, "gemini-flash-latest", "gemini") == MODEL_MAX

    def test_user_value_clamped_to_model_max(self):
        from services.llm.config import resolve_max_tokens

        with patch("services.model_registry.get_model_registry", return_value=_registry_mock()):
            assert resolve_max_tokens({"max_tokens": 100_000}, "m", "p") == MODEL_MAX

    def test_user_value_below_max_respected(self):
        from services.llm.config import resolve_max_tokens

        with patch("services.model_registry.get_model_registry", return_value=_registry_mock()):
            assert resolve_max_tokens({"max_tokens": 2048}, "m", "p") == 2048


class TestOpenRouterLeavesTheLimitToItsEndpoints:
    """OpenRouter routes a sent max_tokens only to endpoints that support it
    and holds its price before the request runs, so an unset value sends
    none. Reads the real llm_defaults.json, so the flag itself is pinned."""

    def test_unset_sends_none(self):
        from services.llm.config import resolve_max_tokens

        with patch("services.model_registry.get_model_registry", return_value=_registry_mock()):
            assert resolve_max_tokens({}, "moonshotai/kimi-k3", "openrouter") is None

    def test_set_value_is_still_clamped_and_sent(self):
        from services.llm.config import resolve_max_tokens

        with patch("services.model_registry.get_model_registry", return_value=_registry_mock()):
            assert resolve_max_tokens({"max_tokens": 100_000}, "moonshotai/kimi-k3", "openrouter") == MODEL_MAX
            assert resolve_max_tokens({"max_tokens": 2048}, "moonshotai/kimi-k3", "openrouter") == 2048


class TestAgentResolverDelegates:
    def test_agent_resolver_matches_native_default(self):
        from services.ai import _resolve_max_tokens

        with patch("services.model_registry.get_model_registry", return_value=_registry_mock()):
            assert _resolve_max_tokens({}, "gemini-flash-latest", "gemini") == MODEL_MAX

    def test_agent_resolver_delegates_to_native(self):
        # Source invariant: no duplicated resolution logic — the agent
        # path must call the native resolver so the two can't drift.
        from services import ai

        src = inspect.getsource(ai._resolve_max_tokens)
        assert "native_resolve_max_tokens" in src

    def test_temporal_prepare_payload_uses_native_resolver(self):
        # The F4.B path imports the provider-neutral resolver directly so
        # newly prepared executions do not enter services.ai compatibility
        # code.
        from services.temporal.agent_activities import prepare_agent_payload

        src = inspect.getsource(prepare_agent_payload)
        assert "from services.llm.config import" in src
        assert "resolve_max_tokens(flattened, model, provider)" in src


class TestRegistryAliasNormalization:
    """OpenRouter "~provider" alias rows (~google/gemini-flash-latest)
    must key under the canonical provider, or get_model_info misses the
    registry and max_tokens/context_length degrade to llm_defaults
    fallbacks."""

    def test_parse_normalizes_tilde_provider(self):
        from services.model_registry import ModelRegistryService

        svc = ModelRegistryService()
        info = svc._parse_openrouter_model(
            {
                "id": "~google/gemini-test-latest",
                "name": "Google: Gemini Test",
                "context_length": 1_000_000,
                "top_provider": {"max_completion_tokens": 65536},
            }
        )
        assert info is not None
        assert info.provider == "gemini"
        assert info.local_id == "gemini-test-latest"

    def test_load_cache_normalizes_tilde_keys(self, tmp_path, monkeypatch):
        import services.model_registry as mr

        cache = tmp_path / "model_registry.json"
        cache.write_text(
            __import__("json").dumps(
                {
                    "models": {
                        "~google/gemini-test-latest": {
                            "id": "~google/gemini-test-latest",
                            "name": "Google: Gemini Test",
                            "provider": "~google",
                            "local_id": "gemini-test-latest",
                            "context_length": 1_000_000,
                            "max_output_tokens": 65536,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(mr, "CACHE_FILE", cache)
        svc = mr.ModelRegistryService()
        svc._load_cache()
        info = svc.get_model_info("gemini-test-latest", "gemini")
        assert info is not None
        assert info.max_output_tokens == 65536


class TestSarvamSubscriptionTierCap:
    """Sarvam's output ceiling is set by the ACCOUNT tier, not the model.

    Every other provider's max_output_tokens is a model capability, so a
    single JSON number is correct for all users. Sarvam rejects anything
    above the caller's tier with a 400 ('max_tokens (65536) exceeds the
    maximum allowed for sarvam-105b for your subscription tier
    (starter): 4096'), and since resolve_max_tokens uses this value both
    as the unset default and as the clamp ceiling, the shipped number has
    to be the Starter cap -- the only one that succeeds on every tier.

    A well-meaning "these models support way more" edit would silently
    break every Starter and Pro account, so it is pinned here.
    """

    STARTER_CAP = 4096

    @pytest.fixture
    def loaded_registry(self):
        """A registry with llm_defaults.json actually loaded.

        ``get_model_registry()`` only reads the JSON in ``startup()``, and
        an unloaded registry falls through to hardcoded 4096 / 128000
        defaults -- which would make every assertion below pass no matter
        what the config says. Patching in a locally loaded instance keeps
        the test honest without mutating the process-wide singleton.
        """
        from services.model_registry import ModelRegistryService

        svc = ModelRegistryService()
        svc._load_llm_defaults()
        with patch("services.model_registry.get_model_registry", return_value=svc):
            yield svc

    def test_fixture_actually_loaded_the_config(self, loaded_registry):
        """Guard against the other tests passing off the hard fallbacks.

        128000 is the hardcoded context fallback; reading 131072 proves
        the sarvam block was parsed.
        """
        assert loaded_registry.get_context_length("sarvam-105b", "sarvam") == 131072

    @pytest.mark.parametrize("model", ["sarvam-105b", "sarvam-30b"])
    def test_shipped_ceiling_is_the_starter_tier_cap(self, model):
        from services.llm.config import LLM_DEFAULTS

        caps = LLM_DEFAULTS["providers"]["sarvam"]["max_output_tokens"]
        assert caps[model] <= self.STARTER_CAP, (
            f"{model} ships max_output_tokens={caps[model]}, above the Starter "
            f"tier cap of {self.STARTER_CAP}. Sarvam 400s on that for any "
            "account below the matching tier, and resolve_max_tokens treats "
            "this value as a hard ceiling."
        )
        assert caps["_default"] <= self.STARTER_CAP

    @pytest.mark.parametrize("model", ["sarvam-105b", "sarvam-30b"])
    def test_unset_max_tokens_resolves_within_the_cap(self, model, loaded_registry):
        """The default budget must be sendable as-is on a Starter account."""
        from services.llm.config import resolve_max_tokens

        assert resolve_max_tokens({}, model, "sarvam") <= self.STARTER_CAP

    def test_oversized_request_is_clamped_not_forwarded(self, loaded_registry):
        from services.llm.config import resolve_max_tokens

        assert resolve_max_tokens(
            {"max_tokens": 65536}, "sarvam-105b", "sarvam"
        ) <= self.STARTER_CAP

    def test_context_length_is_untouched_by_the_tier_cap(self, loaded_registry):
        """The tier limits OUTPUT only -- the context window is a real
        model property and must not be shrunk along with it."""
        assert loaded_registry.get_context_length("sarvam-105b", "sarvam") == 131072
        assert loaded_registry.get_context_length("sarvam-30b", "sarvam") == 65536


class TestStandaloneWorkerRegistryStartup:
    def test_standalone_worker_loads_model_registry(self):
        # Without startup(), a standalone worker's registry is empty and
        # every agent resolves max_tokens to the hard 4096 fallback.
        from services.temporal.worker import run_standalone_worker

        src = inspect.getsource(run_standalone_worker)
        assert "get_model_registry().startup()" in src
