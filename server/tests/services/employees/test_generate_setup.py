"""``generate_employee_setup`` / ``cancel_employee_setup``: the model
answer comes back with the apps it names resolved (and whether each can
start the work); a cut-off or unreadable
answer gets exactly one retry; failures come back as codes; every call is
booked; a newer draft cancels the older; and the owner's words are never
logged. With Generated UI off the model fills a fixed form and the reply is
the card the server draws from it (services/employees/setup_form.py)."""

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
from services.employees.apps import get_apps
from services.employees.llm import LLMChoice
from services.employees.setup_form import FORM_RETRY_NUDGE, fields_prompt
from services.employees.setup_prompt import RETRY_NUDGE
from services.employees.team_recipe import preview_from_reply
from services.llm.protocol import LLMResponse, Usage
from services.plugin.base import NodeUserError
from services.ws_handler_registry import get_ws_handlers

CORPUS = Path(__file__).resolve().parents[4] / "client" / "src" / "features" / "home" / "genui" / "__fixtures__" / "replies.json"
CASES = json.loads(CORPUS.read_text(encoding="utf-8"))
GOOD = next(case["reply"] for case in CASES if case["name"] == "clean minified reply")
#: The form a model filled in while Generated UI was off, and the card drawn from it.
FORM = next(case for case in CASES if "fields" in case)
JOB = "Answer customer messages on WhatsApp and book appointments"


class FakeDatabase:
    def __init__(self):
        self.metrics = []
        self.settings = {"profile_call_name": "Alex", "profile_timezone": "Europe/London", "generated_ui": True}

    async def get_user_settings(self, user_id):
        return self.settings

    async def get_all_workflows(self):
        return [SimpleNamespace(name="Leo")]

    async def get_provider_defaults(self, provider):
        return None

    async def save_token_metric(self, row):
        self.metrics.append(row)


class FakeConnections:
    def __init__(self, auth_service=None, *, principal=None):
        pass

    async def is_connected(self, provider_id):
        return provider_id == "whatsapp"

    async def connected_app_ids(self):
        return ["whatsapp"]

    async def apps(self):
        return get_apps()

    async def app_connected(self, app):
        return await self.is_connected(app.provider_id)

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
    async def resolve_api_key(self, provider, session_id="default", *, principal=None):
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
    # A new WhatsApp message can start the work; the calendar cannot.
    assert result["apps"]["whatsapp"]["can_trigger"] is True
    assert result["apps"]["google calendar"]["can_trigger"] is False
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


async def test_no_retry_when_too_little_time_is_left_and_none_is_reported(harness, monkeypatch):
    # The whole budget is shorter than a retry needs (MIN_RETRY_SECONDS).
    monkeypatch.setattr("services.employees.llm.CLOUD_BUDGET_SECONDS", setup.MIN_RETRY_SECONDS - 5)
    harness.choice = LLMChoice(provider="openai", model="gpt-x", local=False)
    harness.unifier = FakeUnifier("Sorry, no.", "Still no.")
    result = await generate()
    assert result == {"success": False, "error": "unparseable", "draft_token": "t1", "retried": False}
    assert len(harness.unifier.calls) == 1


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


async def test_with_generated_ui_off_the_server_draws_the_card_from_a_form(harness):
    harness.database.settings["generated_ui"] = False
    harness.unifier = FakeUnifier(FORM["fields"])
    result = await generate()
    assert result["success"] is True and result["retried"] is False
    assert result["reply"] == FORM["reply"]
    assert result["apps"]["whatsapp"]["connected"] is True
    assert result["team"] == preview_from_reply(JOB, FORM["reply"])
    system = harness.unifier.calls[0]["messages"][0].content
    assert fields_prompt() in system and "Components (use ONLY these)" not in system


async def test_with_generated_ui_off_a_form_that_names_no_one_gets_one_retry(harness):
    harness.database.settings["generated_ui"] = False
    harness.unifier = FakeUnifier('{"text":"Hello"}', FORM["fields"])
    result = await generate()
    assert result["success"] is True and result["retried"] is True
    assert result["reply"] == FORM["reply"]
    assert harness.unifier.calls[1]["messages"][-1].content.endswith(FORM_RETRY_NUDGE)


async def test_with_generated_ui_off_a_change_sends_the_latest_setup_as_the_form(harness):
    harness.database.settings["generated_ui"] = False
    harness.unifier = FakeUnifier(FORM["fields"])
    result = await generate(refine="Only weekdays", history=[{"change": None, "reply": FORM["reply"]}])
    assert result["success"] is True
    messages = harness.unifier.calls[0]["messages"]
    assert [m.role for m in messages] == ["system", "user", "assistant", "user"]
    assert json.loads(messages[2].content)["name"] == "Maya"
    assert messages[-1].content == "Change the setup: Only weekdays"


async def test_the_owner_words_are_never_logged(harness, monkeypatch):
    spy = MagicMock()
    monkeypatch.setattr(setup, "logger", spy)
    harness.unifier = FakeUnifier("not json at all", GOOD)
    await generate()
    logged = repr(spy.mock_calls)
    assert JOB not in logged
    assert "Maya" not in logged  # nothing from the reply either
