"""The charge a provider reports for a call (``LLMResponse.reported_cost``).

Responses go through the OpenAI SDK's real parsing, because the charge
arrives in ``usage`` fields the SDK does not declare.
"""

import httpx
import openai
import pytest

from services.llm.protocol import Message
from services.llm.providers.openai import OpenAIProvider
from services.llm.providers.openrouter import OpenRouterProvider

HELLO = [Message(role="user", content="Hello")]
MODEL = "openai/gpt-6.1-terra"


@pytest.fixture
def answering(monkeypatch):
    """Make every chat completion answer with the given extra ``usage`` fields."""

    def answer_with(usage):
        body = {
            "id": "gen-1",
            "object": "chat.completion",
            "created": 0,
            "model": MODEL,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, **usage},
        }
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        sdk_client = openai.AsyncOpenAI
        monkeypatch.setattr(
            openai, "AsyncOpenAI", lambda **kwargs: sdk_client(**kwargs, http_client=httpx.AsyncClient(transport=transport))
        )

    return answer_with


async def test_openrouter_reports_what_it_charged(answering):
    answering({"cost": 0.0123})

    response = await OpenRouterProvider("sk-or-test", max_retries=0).chat(HELLO, model=MODEL)

    assert response.reported_cost == 0.0123


async def test_openrouter_adds_the_providers_charge_on_a_bring_your_own_key_call(answering):
    answering({"cost": 0.001, "is_byok": True, "cost_details": {"upstream_inference_cost": 0.02}})

    response = await OpenRouterProvider("sk-or-test", max_retries=0).chat(HELLO, model=MODEL)

    assert response.reported_cost == pytest.approx(0.021)


async def test_openrouter_never_adds_an_upstream_cost_to_a_call_paid_with_credits(answering):
    # What a credit-paid call carried on 2026-11-20: the same money in both fields.
    answering({"cost": 0.0015, "is_byok": False, "cost_details": {"upstream_inference_cost": 0.0015}})

    response = await OpenRouterProvider("sk-or-test", max_retries=0).chat(HELLO, model=MODEL)

    assert response.reported_cost == 0.0015


async def test_openrouter_adds_no_upstream_cost_when_the_response_does_not_say_whose_key(answering):
    answering({"cost": 0.0015, "cost_details": {"upstream_inference_cost": 0.0015}})

    response = await OpenRouterProvider("sk-or-test", max_retries=0).chat(HELLO, model=MODEL)

    assert response.reported_cost == 0.0015


async def test_openrouter_reports_a_free_call_as_zero(answering):
    answering({"cost": 0, "cost_details": {"upstream_inference_cost": None}})

    response = await OpenRouterProvider("sk-or-test", max_retries=0).chat(HELLO, model=MODEL)

    assert response.reported_cost == 0


async def test_openrouter_reports_nothing_when_the_response_has_no_cost(answering):
    answering({})

    response = await OpenRouterProvider("sk-or-test", max_retries=0).chat(HELLO, model=MODEL)

    assert response.reported_cost is None


async def test_openrouter_reports_nothing_for_a_cost_that_is_not_a_number(answering):
    answering({"cost": "0.0123"})

    response = await OpenRouterProvider("sk-or-test", max_retries=0).chat(HELLO, model=MODEL)

    assert response.reported_cost is None


async def test_openrouter_adds_nothing_for_cost_details_of_another_shape(answering):
    answering({"cost": 0.001, "is_byok": True, "cost_details": ["upstream_inference_cost", 0.02]})

    response = await OpenRouterProvider("sk-or-test", max_retries=0).chat(HELLO, model=MODEL)

    assert response.reported_cost == 0.001


async def test_another_provider_reports_nothing_even_when_its_usage_has_a_cost(answering):
    answering({"cost": 0.0123})

    response = await OpenAIProvider("sk-test", max_retries=0).chat(HELLO, model="gpt-6-luna")

    assert response.reported_cost is None
