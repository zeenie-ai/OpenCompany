"""Asking typed questions (``services.judgment.judge``): which provider
answers, the one attempt it gets, the checks on its answers, and what a
caller gets when none can answer."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from services.judgment import (
    JudgmentImage,
    JudgmentProviderSpec,
    JudgmentReply,
    NotConnected,
    check_questions,
    get_judgment_provider,
    judge,
    judgment_providers,
    register_judgment_provider,
)
from services.judgment import config, registry

NODE = "wf:classify:1"
MESSAGE = "Can we move the call to this afternoon?"
URGENT = {"urgent": {"type": "noul", "description": "The message needs a reply today."}}
SURE = {"urgent": {"type": "noul", "probability": 0.9}}
WHERE = {"where": {"type": "box", "description": "The Send button"}}
PNG = JudgmentImage(data=b"\x89PNG made-up bytes", format="png")


class Provider:
    """A provider whose reply a test sets."""

    def __init__(self, answers=None, *, error=None, delay=0.0, model="model-1"):
        self.answers = SURE if answers is None else answers
        self.error = error
        self.delay = delay
        self.model = model
        self.keys = []
        self.requests = []

    def factory(self, api_key=None):
        self.keys.append(api_key)
        return self

    async def judge(self, request):
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return JudgmentReply(answers=self.answers, model=self.model, cost_usd=0.0001)

    @property
    def called(self):
        return bool(self.requests)


@pytest.fixture
def providers():
    """An empty registry; a test adds its providers by name. Whatever was
    registered before the test is registered again after it."""
    saved = [get_judgment_provider(name) for name in judgment_providers()]
    registry._reset_for_tests()

    def add(name, provider=None, *, refs=("httpx:HTTPError",)):
        provider = provider or Provider()
        register_judgment_provider(JudgmentProviderSpec(name=name, factory=provider.factory, sdk_exception_refs=refs))
        return provider

    yield add
    registry._reset_for_tests()
    for spec in saved:
        register_judgment_provider(spec)


@pytest.fixture
def keys(monkeypatch):
    """The owner's stored keys by credential id (a value that is an exception
    is raised when read), and the reads made, as (credential id, principal)."""
    stored = {}
    reads = []

    class Auth:
        async def resolve_api_key(self, provider, session_id="default", *, principal=None):
            reads.append((provider, principal))
            value = stored.get(provider)
            if isinstance(value, BaseException):
                raise value
            return value

    import core.container as container_module

    monkeypatch.setattr(container_module, "container", SimpleNamespace(auth_service=lambda: Auth()))
    return SimpleNamespace(stored=stored, reads=reads)


def all_three(add):
    return add("typesafe"), add("openrouter"), add("llm")


# ---- which provider answers


async def test_typesafe_answers_when_its_key_is_stored(providers, keys):
    keys.stored.update(typesafe="ts-key", openrouter="or-key")
    typesafe, openrouter, llm = all_three(providers)

    judgment = await judge(MESSAGE, URGENT, node_id=NODE)

    assert judgment.available is True
    assert judgment.provider == "typesafe"
    assert judgment.answers["urgent"].probability == 0.9
    assert typesafe.keys == ["ts-key"]
    assert not openrouter.called and not llm.called


async def test_openrouter_answers_when_only_its_key_is_stored(providers, keys):
    keys.stored["openrouter"] = "or-key"
    typesafe, openrouter, llm = all_three(providers)

    judgment = await judge(MESSAGE, URGENT, node_id=NODE)

    assert judgment.provider == "openrouter"
    assert openrouter.keys == ["or-key"]
    assert not typesafe.called and not llm.called


async def test_jev_answers_are_calibrated(providers, keys):
    keys.stored["typesafe"] = "ts-key"
    all_three(providers)

    judgment = await judge(MESSAGE, URGENT, node_id=NODE)

    assert judgment.calibrated is True


async def test_with_no_key_the_chat_model_answers_uncalibrated(providers, keys):
    _typesafe, _openrouter, llm = all_three(providers)

    judgment = await judge(MESSAGE, URGENT, node_id=NODE)

    assert judgment.available is True
    assert judgment.provider == "llm"
    assert judgment.calibrated is False
    assert llm.keys == [None]


async def test_with_nothing_set_up_the_judgment_is_not_connected(providers, keys):
    providers("typesafe")
    providers("openrouter")
    providers("llm", Provider(error=NotConnected()))

    judgment = await judge(MESSAGE, URGENT, node_id=NODE)

    assert judgment.available is False
    assert judgment.reason == "not_connected"
    assert judgment.answers == {}


async def test_a_provider_that_is_not_registered_is_skipped(providers, keys):
    keys.stored["typesafe"] = "ts-key"
    llm = providers("llm")

    judgment = await judge(MESSAGE, URGENT, node_id=NODE)

    assert judgment.provider == "llm"
    assert llm.called


async def test_a_key_that_cannot_be_read_skips_its_provider(providers, keys):
    keys.stored.update(typesafe=RuntimeError("the vault is locked"), openrouter="or-key")
    typesafe, openrouter, _llm = all_three(providers)

    judgment = await judge(MESSAGE, URGENT, node_id=NODE)

    assert judgment.provider == "openrouter"
    assert not typesafe.called


async def test_the_keys_read_are_the_owners(providers, keys):
    keys.stored["typesafe"] = "ts-key"
    all_three(providers)

    await judge(MESSAGE, URGENT, node_id=NODE, principal="owner-1")

    assert keys.reads == [("typesafe", "owner-1")]


async def test_calibrated_only_never_asks_the_chat_model(providers, keys):
    _typesafe, _openrouter, llm = all_three(providers)

    judgment = await judge(MESSAGE, URGENT, node_id=NODE, calibrated_only=True)

    assert judgment.reason == "not_connected"
    assert not llm.called


async def test_a_box_question_goes_only_to_a_provider_that_takes_images(providers, keys):
    _typesafe, _openrouter, llm = all_three(providers)

    judgment = await judge("A phone screen", WHERE, node_id=NODE, images=[PNG])

    assert judgment.reason == "not_connected"
    assert not llm.called


async def test_the_image_reaches_jev_with_the_questions(providers, keys):
    keys.stored["openrouter"] = "or-key"
    box = {"where": {"type": "box", "top": 0.1, "bottom": 0.2, "left": 0.3, "right": 0.4, "confidence": None}}
    providers("typesafe")
    openrouter = providers("openrouter", Provider(answers=box))
    providers("llm")

    judgment = await judge("A phone screen", WHERE, node_id=NODE, images=[PNG])

    assert judgment.answers["where"].left == 0.3
    assert openrouter.requests[0].images == (PNG,)


async def test_the_request_says_where_the_usage_goes(providers, keys):
    llm = providers("llm")

    await judge({"subject": "Invoice"}, URGENT, node_id=NODE, workflow_id="wf", session_id="chat-1", principal="owner-1")

    request = llm.requests[0]
    assert (request.node_id, request.workflow_id, request.session_id, request.principal) == (NODE, "wf", "chat-1", "owner-1")
    assert request.state == {"subject": "Invoice"}
    assert request.questions["urgent"].type == "noul"


async def test_the_judgment_names_the_model_and_its_cost(providers, keys):
    providers("llm", Provider(model="gpt-x"))

    judgment = await judge(MESSAGE, URGENT, node_id=NODE)

    assert judgment.model == "gpt-x"
    assert judgment.cost_usd == 0.0001


# ---- one attempt


async def test_a_failed_call_is_final(providers, keys):
    keys.stored.update(typesafe="ts-key", openrouter="or-key")
    overloaded = httpx.HTTPStatusError(
        "Server error '529'",
        request=httpx.Request("POST", "https://api.example.test/v1/systemone"),
        response=httpx.Response(529),
    )
    typesafe = providers("typesafe", Provider(error=overloaded))
    openrouter = providers("openrouter")
    llm = providers("llm")

    judgment = await judge(MESSAGE, URGENT, node_id=NODE)

    assert judgment.available is False
    assert judgment.reason == "provider_error"
    assert judgment.provider == "typesafe"
    assert "529" in judgment.detail
    assert len(typesafe.requests) == 1
    assert not openrouter.called and not llm.called


async def test_an_unexpected_error_is_a_provider_error(providers, keys):
    providers("llm", Provider(error=RuntimeError("a bug")))

    judgment = await judge(MESSAGE, URGENT, node_id=NODE)

    assert judgment.reason == "provider_error"
    assert judgment.detail == "a bug"


async def test_a_slow_provider_times_out(providers, keys):
    providers("llm", Provider(delay=5))

    judgment = await judge(MESSAGE, URGENT, node_id=NODE, timeout=0.05)

    assert judgment.available is False
    assert judgment.reason == "timeout"


async def test_without_a_timeout_the_providers_own_limit_applies(providers, keys, monkeypatch):
    providers("llm", Provider(delay=5))
    monkeypatch.setattr(config, "timeout_seconds", lambda name: 0.05)

    judgment = await judge(MESSAGE, URGENT, node_id=NODE)

    assert judgment.reason == "timeout"
    assert judgment.detail == "no answer within 0.05 s"


async def test_cancelling_the_caller_is_not_swallowed(providers, keys):
    providers("llm", Provider(delay=5))
    task = asyncio.ensure_future(judge(MESSAGE, URGENT, node_id=NODE))
    await asyncio.sleep(0.02)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


# ---- the answers are checked


@pytest.mark.parametrize(
    "answers",
    [
        pytest.param({}, id="nothing answered"),
        pytest.param({"urgent": {"type": "choice", "choice": "yes"}}, id="another type"),
        pytest.param({"urgent": {"type": "noul", "probability": 1.5}}, id="out of range"),
        pytest.param({"urgent": {"type": "noul"}}, id="no probability"),
        pytest.param({"urgent": {"type": "refusal"}}, id="a refusal"),
        pytest.param({"urgent": "yes"}, id="not an answer"),
    ],
)
async def test_an_unusable_answer_is_an_invalid_reply(providers, keys, answers):
    providers("llm", Provider(answers=answers))

    judgment = await judge(MESSAGE, URGENT, node_id=NODE)

    assert judgment.available is False
    assert judgment.reason == "invalid_reply"


async def test_a_choice_must_be_one_of_the_options(providers, keys):
    providers("llm", Provider(answers={"topic": {"type": "choice", "choice": "gardening"}}))
    topic = {"topic": {"type": "choice", "description": "What it is about", "options": ["billing", "support"]}}

    judgment = await judge(MESSAGE, topic, node_id=NODE)

    assert judgment.reason == "invalid_reply"


async def test_a_score_must_lie_within_the_levels(providers, keys):
    providers("llm", Provider(answers={"priority": {"type": "score", "score": 3.0}}))
    priority = {"priority": {"type": "score", "description": "How urgent", "levels": ["low", "medium", "high"]}}

    judgment = await judge(MESSAGE, priority, node_id=NODE)

    assert judgment.reason == "invalid_reply"


async def test_a_score_between_two_levels_is_kept(providers, keys):
    answer = {"type": "score", "score": 1.7, "confidence": 0.8, "probabilities": [0.1, 0.1, 0.8]}
    providers("llm", Provider(answers={"priority": answer}))
    priority = {"priority": {"type": "score", "description": "How urgent", "levels": ["low", "medium", "high"]}}

    judgment = await judge(MESSAGE, priority, node_id=NODE)

    assert judgment.answers["priority"].score == 1.7
    assert judgment.answers["priority"].probabilities == [0.1, 0.1, 0.8]


async def test_a_box_must_lie_inside_the_image(providers, keys):
    keys.stored["typesafe"] = "ts-key"
    providers("typesafe", Provider(answers={"where": {"type": "box", "top": 0.1, "bottom": 1.2, "left": 0.3, "right": 0.4}}))

    judgment = await judge("A phone screen", WHERE, node_id=NODE, images=[PNG])

    assert judgment.reason == "invalid_reply"


async def test_answers_to_questions_not_asked_are_left_out(providers, keys):
    providers("llm", Provider(answers={**SURE, "extra": {"type": "noul", "probability": 0.1}}))

    judgment = await judge(MESSAGE, URGENT, node_id=NODE)

    assert list(judgment.answers) == ["urgent"]


# ---- questions that cannot be asked


@pytest.mark.parametrize(
    "questions, problem",
    [
        ({}, "at least one question"),
        ({"2fast": URGENT["urgent"]}, "letters, digits and underscores"),
        ({"a.b": URGENT["urgent"]}, "letters, digits and underscores"),
        ({"topic": {"type": "choice", "description": "About", "options": ["one"]}}, "2 to 255 options"),
        ({"topic": {"type": "choice", "description": "About", "options": ["a", "a "]}}, "must all be different"),
        ({"topic": {"type": "choice", "description": "About", "options": ["a", " "]}}, "must not be empty"),
        ({"priority": {"type": "score", "description": "How", "levels": [str(n) for n in range(11)]}}, "2 to 10 levels"),
        ({"urgent": {"type": "noul", "description": "Urgent", "options": ["a", "b"]}}, "only a choice question has options"),
        ({"urgent": {"type": "noul", "description": "Urgent", "levels": ["a", "b"]}}, "only a score question has levels"),
        ({"urgent": {"type": "noul", "description": "  "}}, "needs a description"),
        ({"urgent": {"type": "maybe", "description": "Urgent"}}, "type"),
        ({"urgent": {"type": "noul", "description": "Urgent", "criteria": {}}}, "criteria"),
    ],
)
def test_a_question_that_cannot_be_asked_is_refused(questions, problem):
    with pytest.raises(ValueError, match=problem):
        check_questions(questions)


def test_choice_options_and_score_levels_are_trimmed():
    asked = check_questions(
        {
            "topic": {"type": "choice", "description": " About ", "options": [" billing", "support "]},
            "priority": {"type": "score", "description": "How", "levels": ["low ", " high"]},
        }
    )

    assert asked["topic"].description == "About"
    assert asked["topic"].options == ["billing", "support"]
    assert asked["priority"].levels == ["low", "high"]


async def test_judge_refuses_a_bad_question_before_calling_anyone(providers, keys):
    llm = providers("llm")

    with pytest.raises(ValueError):
        await judge(MESSAGE, {"a.b": URGENT["urgent"]}, node_id=NODE)

    assert not llm.called


async def test_a_box_question_needs_an_image(providers, keys):
    with pytest.raises(ValueError, match="needs an image"):
        await judge("A phone screen", WHERE, node_id=NODE)


async def test_a_judgment_needs_something_to_judge(providers, keys):
    with pytest.raises(ValueError, match="something to judge"):
        await judge(None, URGENT, node_id=NODE)


def test_an_image_is_png_or_jpg():
    with pytest.raises(ValueError, match="png, jpg"):
        JudgmentImage(data=b"made-up", format="gif")


# ---- the shipped settings


def test_jev_is_tried_first_then_the_chat_model():
    assert config.order() == ["typesafe", "openrouter", "llm"]


@pytest.mark.parametrize("name", ["typesafe", "openrouter"])
def test_both_ways_to_jev_are_calibrated_and_take_images(name):
    assert config.calibrated(name)
    assert config.accepts_images(name)
    assert set(config.question_types(name)) == {"choice", "score", "noul", "box"}
    assert config.credential_id(name) == name
    assert config.base_url(name).startswith("https://")
    assert config.model(name) and config.model(name, images=True)
    low, high = config.image_side_limits(name)
    assert 0 < low < high


def test_the_chat_model_is_keyless_uncalibrated_and_never_shown_images():
    assert config.keyless("llm")
    assert not config.calibrated("llm")
    assert not config.accepts_images("llm")
    assert "box" not in config.question_types("llm")
