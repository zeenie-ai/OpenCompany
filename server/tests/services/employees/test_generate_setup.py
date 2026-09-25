"""``generate_employee_setup`` / ``cancel_employee_setup``: the model
answer comes back with the apps it names resolved; a cut-off or unreadable
answer gets exactly one retry; failures come back as codes; every call is
booked; a newer draft cancels the older; and the owner's words are never
logged."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import services.employees  # noqa: F401 - registers the handlers
from services.authz.ws_surface import INTERNAL_SOCKET_HANDLERS
from services.employees import setup
from services.employees.llm import LLMChoice
from services.employees.setup_prompt import RETRY_NUDGE
from services.llm.protocol import LLMResponse, Usage
from services.plugin.base import NodeUserError
from services.ws_handler_registry import get_ws_handlers

CORPUS = Path(__file__).resolve().parents[4] / "client" / "src" / "features" / "home" / "genui" / "__fixtures__" / "replies.json"
GOOD = next(case["reply"] for case in json.loads(CORPUS.read_text(encoding="utf-8")) if case["name"] == "clean minified reply")
JOB = "Answer customer messages on WhatsApp and book appointments"


class FakeDatabase:
    def __init__(self):
        self.metrics = []

    async def get_user_settings(self, user_id):
        return {"profile_call_name": "Alex", "profile_timezone": "Europe/London"}

    async def get_all_workflows(self):
        return [SimpleNamespace(name="Leo")]

    async def get_provider_defaults(self, provider):
        return None

    async def save_token_metric(self, row):
        self.metrics.append(row)


class FakeConnections:
    def __init__(self, auth_service=None):
        pass

    async def is_connected(self, provider_id):
        return provider_id == "whatsapp"

    async def connected_app_ids(self):
        return ["whatsapp"]

    async def app_ref(self, app):
        return {"app_id": app.id, "provider_id": app.provider_id, "name": app.name, "icon_ref": None, "connected": app.provider_id == "whatsapp", "supported": True}

    async def has_ai(self):
        return True

    async def ai_providers(self):
        return ["openai"]


class FakeUnifier:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            return await reply()
        content, finish = reply if isinstance(reply, tuple) else (reply, "stop")
        return LLMResponse(content=content, finish_reason=finish, usage=Usage(input_tokens=100, output_tokens=50), model="gpt-x")


class Auth:
    async def get_api_key(self, provider, session_id="default"):
        return "sk-test"


@pytest.fixture()
def harness(monkeypatch):
    database = FakeDatabase()
    state = SimpleNamespace(unifier=FakeUnifier(GOOD), choice=LLMChoice(provider="openai", model="gpt-x", local=False))
    fake = SimpleNamespace(database=lambda: database, auth_service=lambda: Auth(), chat_unifier=lambda: state.unifier)

    import core.container as container_module

    monkeypatch.setattr(container_module, "container", fake)
    monkeypatch.setattr(setup, "Connections", FakeConnections)

    async def choose(*_args, **_kwargs):
        return state.choice

    monkeypatch.setattr(setup, "resolve_llm_choice", choose)
    import services.pricing as pricing

    costs = {"input_cost": 0.1, "output_cost": 0.2, "cache_cost": 0.0, "total_cost": 0.3}
    monkeypatch.setattr(pricing, "get_pricing_service", lambda: SimpleNamespace(calculate_cost=lambda **_: costs))
    setup.reset_for_tests()
    state.database = database
    yield state
    setup.reset_for_tests()


def socket():
    return SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner-1"))


async def generate(**payload):
    data = {"job": JOB, "draft_token": "t1", **payload}
    return await setup.handle_generate_employee_setup(data, socket())


def test_handlers_are_registered_and_not_internal():
    registered = get_ws_handlers()
    for name in ("generate_employee_setup", "cancel_employee_setup"):
        assert name in registered
        assert name not in INTERNAL_SOCKET_HANDLERS


async def test_returns_the_reply_with_its_apps_resolved(harness):
    result = await generate()
    assert result["success"] is True
    assert result["draft_token"] == "t1"
    assert result["reply"] == GOOD
    assert result["retried"] is False
    assert result["provider"] == "openai" and result["model"] == "gpt-x"
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
    assert result["apps"]["whatsapp"]["provider_id"] == "whatsapp"
    assert result["apps"]["whatsapp"]["connected"] is True
    assert result["apps"]["google calendar"]["provider_id"] == "google"
    # One call, booked once, under the owner.
    assert len(harness.unifier.calls) == 1
    assert [row["session_id"] for row in harness.database.metrics] == ["owner-1"]
    messages = harness.unifier.calls[0]["messages"]
    assert messages[-1].content == "The job: " + JOB
    assert "Names already on the team (don't reuse): Leo." in messages[0].content


async def test_a_cut_off_reply_gets_one_retry(harness):
    harness.unifier = FakeUnifier((GOOD[: len(GOOD) // 2], "length"), GOOD)
    result = await generate()
    assert result["success"] is True
    assert result["retried"] is True
    assert result["reply"] == GOOD
    assert len(harness.database.metrics) == 2
    assert harness.unifier.calls[1]["messages"][-1].content.endswith(RETRY_NUDGE)


async def test_two_unreadable_replies_fail_as_unparseable(harness):
    harness.unifier = FakeUnifier("Sorry, no.", "Still no.")
    result = await generate()
    assert result == {"success": False, "error": "unparseable", "draft_token": "t1", "retried": True}
    assert len(harness.unifier.calls) == 2


async def test_a_readable_but_cut_off_reply_survives_a_failed_retry(harness):
    cut = GOOD[: GOOD.index('"e":{"type":"Stack"')]
    harness.unifier = FakeUnifier((cut, "max_tokens"), NodeUserError("rate limited"))
    result = await generate()
    assert result["success"] is True
    assert result["reply"] == cut


async def test_failures_come_back_as_codes(harness):
    harness.unifier = FakeUnifier(NodeUserError("Invalid API key"))
    failed = await generate()
    assert failed["error"] == "provider_error"
    assert failed["detail"] == "Invalid API key"

    harness.choice = None
    assert (await generate())["error"] == "no_ai_provider"
    assert (await generate(job="  "))["error"] == "invalid_request"


async def test_a_slow_model_times_out(harness, monkeypatch):
    async def slow():
        await asyncio.sleep(5)

    harness.unifier = FakeUnifier(slow)
    harness.choice = LLMChoice(provider="openai", model="gpt-x", local=False)
    monkeypatch.setattr("services.employees.llm.CLOUD_BUDGET_SECONDS", 0.05)
    assert (await generate())["error"] == "timeout"


async def test_a_newer_draft_cancels_the_older_one(harness):
    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(10)

    harness.unifier = FakeUnifier(slow, GOOD)
    older = asyncio.ensure_future(generate(draft_token="old"))
    await started.wait()
    newer = await generate(draft_token="new")
    assert newer["success"] is True
    assert (await older) == {"success": False, "error": "cancelled", "draft_token": "old"}


async def test_the_same_draft_twice_is_busy_and_cancel_stops_it(harness):
    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(10)

    harness.unifier = FakeUnifier(slow)
    first = asyncio.ensure_future(generate(draft_token="d"))
    await started.wait()
    assert (await generate(draft_token="d"))["error"] == "busy"
    cancelled = await setup.handle_cancel_employee_setup({"draft_token": "d"}, socket())
    assert cancelled["cancelled"] is True
    assert (await first)["error"] == "cancelled"
    again = await setup.handle_cancel_employee_setup({"draft_token": "d"}, socket())
    assert again["cancelled"] is False


async def test_a_change_request_sends_the_history(harness):
    result = await generate(refine="Only weekdays", history=[{"change": None, "reply": GOOD}])
    assert result["success"] is True
    roles = [m.role for m in harness.unifier.calls[0]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert harness.unifier.calls[0]["messages"][-1].content == "Change the setup: Only weekdays"


async def test_the_owner_words_are_never_logged(harness, monkeypatch):
    spy = MagicMock()
    monkeypatch.setattr(setup, "logger", spy)
    harness.unifier = FakeUnifier("not json at all", GOOD)
    await generate()
    logged = repr(spy.mock_calls)
    assert JOB not in logged
    assert "Maya" not in logged  # nothing from the reply either
