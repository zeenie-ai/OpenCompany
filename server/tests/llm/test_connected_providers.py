"""The AI providers the owner can run on now, with their models
(services/llm/connected.py): the one list the editor's Set Global Model
(``get_validated_ai_providers``) and Home's chat model picker
(services/chat/choice.py) both read."""

from __future__ import annotations

from types import SimpleNamespace

from services.llm import connected
from services.llm.config import curated_models


class Auth:
    """Keys stored per provider, each with the models saved with it."""

    def __init__(self, keys):
        self.keys = keys

    async def has_valid_key(self, provider, session_id="default", *, principal=None):
        return provider in self.keys

    async def get_stored_models(self, provider, session_id="default", *, principal=None):
        return list(self.keys[provider])


class Database:
    def __init__(self, defaults):
        self.defaults = defaults

    async def get_provider_defaults(self, provider):
        return self.defaults.get(provider)


def _gateway(monkeypatch, *covered):
    from services.gateway import routing

    async def covers(provider):
        return provider in covered

    monkeypatch.setattr(routing, "gateway_covers", covers)


def _endpoints(monkeypatch, listed):
    from services.llm import endpoints

    async def list_endpoints(auth, *, principal=None):
        if isinstance(listed, Exception):
            raise listed
        return listed

    monkeypatch.setattr(endpoints, "list_endpoints", list_endpoints)


async def test_keys_the_gateway_and_named_endpoints(monkeypatch):
    _gateway(monkeypatch, "gemini")
    _endpoints(monkeypatch, [SimpleNamespace(ref="openai_compatible:home-vllm", label="Home vLLM", models=["qwen3-32b"])])
    auth = Auth({"openrouter": ["anthropic/claude-sonnet-5.5", "x-ai/grok-4.20"], "ollama": ["llama3.3:70b"]})
    database = Database({"openrouter": {"default_model": "x-ai/grok-4.20"}})

    providers = await connected.connected_providers(auth, database, principal=None)

    assert [entry["provider"] for entry in providers] == ["gemini", "openrouter", "ollama", "openai_compatible:home-vllm"]
    gemini, openrouter, ollama, endpoint = providers
    # The gateway serves Gemini: its curated list stands for stored models.
    assert gemini == {
        "provider": "gemini",
        "display_name": "Gemini",
        "models": curated_models("gemini"),
        "popular_models": curated_models("gemini"),
        "offered": curated_models("gemini"),
        "default_model": "gemini-3.8-flash",
    }
    # The owner's provider default wins over the configured one, and a
    # picker offers the curated list, not every model stored.
    assert (openrouter["display_name"], openrouter["default_model"]) == ("OpenRouter", "x-ai/grok-4.20")
    assert openrouter["models"] == ["anthropic/claude-sonnet-5.5", "x-ai/grok-4.20"]
    assert openrouter["offered"] == curated_models("openrouter")
    # A local server has no curated list: what is stored with it is all it offers.
    assert (ollama["models"], ollama["popular_models"], ollama["offered"]) == (["llama3.3:70b"], [], ["llama3.3:70b"])
    assert endpoint == {
        "provider": "openai_compatible:home-vllm",
        "display_name": "Home vLLM (OpenAI-compatible)",
        "models": ["qwen3-32b"],
        "popular_models": [],
        "offered": ["qwen3-32b"],
        "default_model": "",
    }


async def test_an_endpoint_listing_failure_leaves_the_rest(monkeypatch):
    _gateway(monkeypatch)
    _endpoints(monkeypatch, RuntimeError("credential store busy"))

    providers = await connected.connected_providers(Auth({"anthropic": []}), Database({}), principal=None)

    assert [entry["provider"] for entry in providers] == ["anthropic"]


async def test_set_global_model_reads_the_same_list(monkeypatch):
    from services.settings import handlers

    listed = [
        {
            "provider": "openrouter",
            "display_name": "OpenRouter",
            "models": [],
            "popular_models": ["x-ai/grok-4.20"],
            "offered": ["x-ai/grok-4.20"],
            "default_model": "",
        }
    ]

    async def connected_providers(auth, database, *, principal, session_id="default"):
        return listed

    async def get_user_settings(user_id):
        return {"default_llm_provider": "openrouter", "default_llm_model": "x-ai/grok-4.20"}

    monkeypatch.setattr(connected, "connected_providers", connected_providers)
    monkeypatch.setattr(
        "core.container.container",
        SimpleNamespace(database=lambda: SimpleNamespace(get_user_settings=get_user_settings), auth_service=lambda: None),
    )

    reply = await handlers.handle_get_validated_ai_providers({}, None)

    assert reply == {"success": True, "providers": listed, "global_provider": "openrouter", "global_model": "x-ai/grok-4.20"}
