"""SDK transport failures use the same provider-neutral retry boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from services.llm.protocol import LLMError, LLMErrorCategory, LLMResponse, Message
from services.llm.registry import ProviderSpec
from services.llm.unifier import ChatUnifier
from services.plugin import NodeUserError


@pytest.fixture
def dispatch(monkeypatch):
    from services.llm import registry

    client = SimpleNamespace(chat=AsyncMock(return_value=LLMResponse(content="done")),
                             fetch_models=AsyncMock(return_value=["test"]), aclose=AsyncMock())
    monkeypatch.setitem(registry._REGISTRY, "transport-test", ProviderSpec(
        name="transport-test", factory=lambda **kwargs: client,
        sdk_exception_refs=("google.genai.errors:APIError",),
    ))
    auth = MagicMock()
    auth.get_api_key = AsyncMock(return_value=None)
    return ChatUnifier(defaults={"providers": {}}, auth_service=auth, client_cache_size=0), client


@pytest.mark.asyncio
@pytest.mark.parametrize(("error_type", "category"), [
    (httpx.ReadTimeout, LLMErrorCategory.TIMEOUT),
    (httpx.ConnectError, LLMErrorCategory.CONNECTION),
    (httpx.RemoteProtocolError, LLMErrorCategory.CONNECTION),
])
async def test_sdk_transport_failure_is_retryable_and_releases_client(dispatch, error_type, category):
    unifier, client = dispatch
    client.chat.side_effect = error_type("private endpoint details")
    with pytest.raises(LLMError) as raised:
        await unifier.chat(provider="transport-test", api_key="test", model="test",
                           messages=[Message(role="user", content="go")], translate_errors=False)
    assert raised.value.category == category
    assert raised.value.retryable
    assert "private endpoint" not in raised.value.user_message
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_model_listing_throttle_is_actionable_without_raw_http_error(dispatch):
    unifier, client = dispatch
    request = httpx.Request("GET", "https://private.example/models")
    response = httpx.Response(429, headers={"retry-after": "30"}, request=request)
    client.fetch_models.side_effect = httpx.HTTPStatusError("private endpoint details", request=request, response=response)
    with pytest.raises(NodeUserError) as raised:
        await unifier.fetch_models(provider="transport-test", api_key="test")
    assert "rate-limiting" in str(raised.value)
    assert "private endpoint" not in str(raised.value)
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_programming_error_is_not_retried_as_a_transport_failure(dispatch):
    unifier, client = dispatch
    bug = ValueError("provider implementation bug")
    client.chat.side_effect = bug
    with pytest.raises(ValueError) as raised:
        await unifier.chat(provider="transport-test", api_key="test", model="test", messages=[Message(role="user", content="go")])
    assert raised.value is bug
