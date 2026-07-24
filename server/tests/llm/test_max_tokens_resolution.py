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
  llm_defaults.json regardless of model capability).
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


class TestStandaloneWorkerRegistryStartup:
    def test_standalone_worker_loads_model_registry(self):
        # Without startup(), a standalone worker's registry is empty and
        # every agent resolves max_tokens to the hard 4096 fallback.
        from services.temporal.worker import run_standalone_worker

        src = inspect.getsource(run_standalone_worker)
        assert "get_model_registry().startup()" in src
