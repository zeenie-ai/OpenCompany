"""Which model writes a setup screen: a local server when the owner keeps
everything on this computer, else the global default when it is usable,
else the first usable provider, else a saved OpenAI-compatible endpoint."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.employees import llm


class Database:
    def __init__(self, settings=None, defaults=None):
        self.settings = settings or {}
        self.defaults = defaults or {}

    async def get_user_settings(self, user_id):
        return self.settings

    async def get_provider_defaults(self, provider):
        return self.defaults.get(provider)


class Auth:
    async def get_stored_models(self, provider, session_id="default"):
        return {"ollama": ["qwen3:8b", "llama3.2"]}.get(provider, [])


class Connections:
    def __init__(self, usable):
        self.usable = usable

    async def ai_providers(self):
        return list(self.usable)


@pytest.fixture(autouse=True)
def local_and_endpoints(monkeypatch):
    monkeypatch.setattr(llm, "runs_locally", lambda provider: provider in {"ollama", "lmstudio"})
    endpoints = []

    async def list_endpoints(_auth):
        return endpoints

    import services.llm.endpoints as endpoints_module

    monkeypatch.setattr(endpoints_module, "list_endpoints", list_endpoints)
    return endpoints


async def choose(usable, **database):
    return await llm.resolve_llm_choice(Database(**database), Auth(), Connections(usable))


async def test_a_local_server_wins_while_the_owner_keeps_things_local():
    choice = await choose(["openai", "ollama"], settings={"default_llm_provider": "openai", "default_llm_model": "gpt-x"})
    assert choice == llm.LLMChoice(provider="ollama", model="qwen3:8b", local=True)
    assert choice.budget_seconds == llm.LOCAL_BUDGET_SECONDS


async def test_the_global_default_when_local_is_off():
    choice = await choose(
        ["openai", "ollama"],
        settings={"prefer_local_ai": False, "default_llm_provider": "openai", "default_llm_model": "gpt-x"},
    )
    assert choice == llm.LLMChoice(provider="openai", model="gpt-x", local=False)
    assert choice.budget_seconds == llm.CLOUD_BUDGET_SECONDS


async def test_a_default_that_is_not_usable_falls_through_to_the_first_usable():
    choice = await choose(["anthropic"], settings={"prefer_local_ai": False, "default_llm_provider": "openai"})
    assert choice.provider == "anthropic"


async def test_the_saved_provider_default_model_beats_the_json_default():
    choice = await choose(["anthropic"], defaults={"anthropic": {"default_model": "claude-x"}})
    assert choice.model == "claude-x"


async def test_an_endpoint_when_nothing_else_is_set_up(local_and_endpoints):
    local_and_endpoints.append(SimpleNamespace(ref="openai_compatible:home", models=["m1"]))
    choice = await choose([])
    assert choice == llm.LLMChoice(provider="openai_compatible:home", model="m1", local=False)


async def test_nothing_set_up():
    assert await choose([]) is None
