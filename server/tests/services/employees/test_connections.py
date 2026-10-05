"""Which AI models an employee can run on (services/employees/connections.py):
a saved OpenAI-compatible endpoint counts by its reference, never as the bare
provider id, so a hire whose only model is an endpoint runs on it, and an
employee left on the bare id is moved onto the endpoint when it starts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.employees import connections, llm, start


@pytest.fixture
def setup(monkeypatch):
    state = SimpleNamespace(connected=set(), endpoints=[], saved={})

    async def is_connected(self, provider_id):
        return provider_id in state.connected

    async def list_endpoints(_auth):
        return state.endpoints

    import services.llm.endpoints as endpoints_module

    monkeypatch.setattr(connections.Connections, "is_connected", is_connected)
    monkeypatch.setattr(endpoints_module, "list_endpoints", list_endpoints)
    return state


class Database:
    def __init__(self, state):
        self.state = state

    async def get_user_settings(self, user_id):
        return {}

    async def get_provider_defaults(self, provider):
        return None

    async def get_node_parameters(self, node_id):
        return {"provider": "openai_compatible", "model": "", "prompt": "p"}

    async def save_node_parameters(self, node_id, params):
        self.state.saved[node_id] = params


class Auth:
    async def get_stored_models(self, provider, session_id="default"):
        return []


async def test_a_saved_endpoint_counts_by_its_reference(setup):
    # The credential store reports the base id as connected once any
    # endpoint is saved.
    setup.connected = {"anthropic", "openai_compatible"}
    setup.endpoints = [SimpleNamespace(ref="openai_compatible:home", models=["m1"])]
    assert await connections.Connections(Auth()).ai_providers() == ["anthropic", "openai_compatible:home"]


async def test_a_hire_whose_only_model_is_an_endpoint_runs_on_it(setup):
    setup.connected = {"openai_compatible"}
    setup.endpoints = [SimpleNamespace(ref="openai_compatible:home", models=["m1"])]
    choice = await llm.resolve_llm_choice(Database(setup), Auth(), connections.Connections(Auth()))
    assert choice == llm.LLMChoice(provider="openai_compatible:home", model="m1", local=False)


async def test_an_employee_left_on_the_bare_id_is_moved_onto_the_endpoint(setup, monkeypatch):
    setup.connected = {"openai_compatible"}
    setup.endpoints = [SimpleNamespace(ref="openai_compatible:home", models=["m1"])]

    async def agents(_database, _workflow_id):
        return ["7:aiAgent:1"]

    monkeypatch.setattr(start, "_agent_ids", agents)
    auth = Auth()
    assert await start.heal_agent_models(Database(setup), auth, connections.Connections(auth), "7") == ["7:aiAgent:1"]
    assert setup.saved["7:aiAgent:1"] == {"provider": "openai_compatible:home", "model": "m1", "prompt": "p"}


async def test_specialist_models_preserve_explicit_and_persist_missing_defaults(setup, monkeypatch):
    setup.connected = {"anthropic"}
    params = {"lead": {"provider": "anthropic", "model": "chosen-explicit"}, "specialist": {"provider": "anthropic", "model": "", "prompt": "research"}}
    async def agents(_database, _workflow_id):
        return list(params)
    class TeamDatabase(Database):
        async def get_node_parameters(self, node_id):
            return params[node_id]
        async def get_provider_defaults(self, provider):
            return {"default_model": "saved-default"}
    monkeypatch.setattr(start, "_agent_ids", agents)
    auth = Auth()
    changed = await start.heal_agent_models(TeamDatabase(setup), auth, connections.Connections(auth), "7")
    assert changed == ["specialist"]
    assert setup.saved == {"specialist": {"provider": "anthropic", "model": "saved-default", "prompt": "research"}}


async def test_endpoint_missing_model_uses_its_models_instead_of_cloud_fallback(setup, monkeypatch):
    setup.connected = {"openai_compatible"}
    setup.endpoints = [SimpleNamespace(ref="openai_compatible:home", models=["local-custom"])]
    async def agents(_database, _workflow_id):
        return ["specialist"]
    class TeamDatabase(Database):
        async def get_node_parameters(self, node_id):
            return {"provider": "openai_compatible:home", "model": ""}
    monkeypatch.setattr(start, "_agent_ids", agents)
    auth = Auth()
    await start.heal_agent_models(TeamDatabase(setup), auth, connections.Connections(auth), "7")
    assert setup.saved["specialist"] == {"provider": "openai_compatible:home", "model": "local-custom"}
