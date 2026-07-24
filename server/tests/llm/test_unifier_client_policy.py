import asyncio
from unittest.mock import AsyncMock, MagicMock

import openai
import pytest

from services.llm.protocol import LLMError, LLMErrorCategory, LLMResponse, Message
from services.llm.registry import ProviderSpec
from services.llm.unifier import ChatUnifier
from services.plugin import NodeUserError


@pytest.mark.asyncio
async def test_unifier_caches_by_credential_and_retry_policy_and_closes_clients():
    created = []

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = AsyncMock(return_value=LLMResponse(content="ok"))
            self.closed = False
            created.append(self)

        async def aclose(self):
            self.closed = True

    spec = ProviderSpec(
        name="cache-test",
        factory=Client,
        sdk_exception_refs=("openai:OpenAIError",),
    )
    auth = MagicMock()
    auth.get_api_key = AsyncMock(return_value=None)
    unifier = ChatUnifier(
        defaults={"providers": {}},
        auth_service=auth,
        client_cache_size=2,
    )

    first = await unifier._build_client(
        spec, "secret-a", sdk_max_retries=0
    )
    again = await unifier._build_client(
        spec, "secret-a", sdk_max_retries=0
    )
    different_policy = await unifier._build_client(
        spec, "secret-a", sdk_max_retries=2
    )

    assert first is again
    assert different_policy is not first
    assert len(created) == 2
    assert created[0].kwargs["max_retries"] == 0
    await unifier.aclose()
    assert all(client.closed for client in created)


@pytest.mark.asyncio
async def test_unifier_can_surface_structured_error_for_agent_retry_policy():
    response = MagicMock()
    response.status_code = 429
    response.headers = {"retry-after": "1.5", "x-request-id": "req-1"}
    error = openai.RateLimitError(
        message="rate limited",
        response=response,
        body={"error": {"code": "capacity"}},
    )
    client = MagicMock()
    client.chat = AsyncMock(side_effect=error)

    from services.llm import registry

    name = "structured-error-test"
    spec = ProviderSpec(
        name=name,
        factory=lambda **kwargs: client,
        sdk_exception_refs=("openai:OpenAIError",),
    )
    registry._REGISTRY[name] = spec
    auth = MagicMock()
    auth.get_api_key = AsyncMock(return_value=None)
    unifier = ChatUnifier(
        defaults={"providers": {}},
        auth_service=auth,
        client_cache_size=0,
    )
    try:
        with pytest.raises(LLMError) as caught:
            await unifier.chat(
                provider=name,
                api_key="secret",
                messages=[Message(role="user", content="hello")],
                model="model",
                sdk_max_retries=0,
                translate_errors=False,
            )
    finally:
        registry._REGISTRY.pop(name, None)

    assert caught.value.category == LLMErrorCategory.RATE_LIMIT
    assert caught.value.retryable
    assert caught.value.request_id == "req-1"
    assert caught.value.retry_after == 1.5
    assert caught.value.retry_after_raw == "1.5"
    assert caught.value.provider_code == "capacity"


@pytest.mark.asyncio
async def test_unifier_public_error_does_not_expose_raw_sdk_message():
    response = MagicMock()
    response.status_code = 400
    response.headers = {"x-request-id": "req-private"}
    error = openai.BadRequestError(
        message=(
            "POST https://private-gateway.internal/v1 "
            'payload={"authorization":"Bearer secret"}'
        ),
        response=response,
        body={"error": {"code": "invalid_payload"}},
    )
    client = MagicMock()
    client.chat = AsyncMock(side_effect=error)

    from services.llm import registry

    name = "public-error-test"
    registry._REGISTRY[name] = ProviderSpec(
        name=name,
        factory=lambda **_kwargs: client,
        sdk_exception_refs=("openai:OpenAIError",),
    )
    auth = MagicMock()
    auth.get_api_key = AsyncMock(return_value=None)
    unifier = ChatUnifier(
        defaults={"providers": {}},
        auth_service=auth,
        client_cache_size=0,
    )
    try:
        with pytest.raises(NodeUserError) as caught:
            await unifier.chat(
                provider=name,
                api_key="secret",
                messages=[Message(role="user", content="hello")],
                model="model",
                sdk_max_retries=0,
            )
    finally:
        registry._REGISTRY.pop(name, None)

    assert str(caught.value) == (
        "The language model provider rejected the model request configuration."
    )
    assert "private-gateway" not in str(caught.value)
    assert "Bearer secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_client_constructor_failure_is_structured_and_safe():
    from services.llm import registry

    raw_message = (
        "Invalid proxy URL https://user:secret@private-gateway.internal"
    )
    name = "constructor-error-test"
    registry._REGISTRY[name] = ProviderSpec(
        name=name,
        factory=lambda **_kwargs: (_ for _ in ()).throw(
            ValueError(raw_message)
        ),
        sdk_exception_refs=("openai:OpenAIError",),
    )
    auth = MagicMock()
    auth.get_api_key = AsyncMock(return_value=None)
    unifier = ChatUnifier(
        defaults={"providers": {}},
        auth_service=auth,
        client_cache_size=1,
    )
    try:
        with pytest.raises(NodeUserError) as caught:
            await unifier.chat(
                provider=name,
                api_key="secret",
                messages=[Message(role="user", content="hello")],
                model="model",
            )
        with pytest.raises(LLMError) as structured:
            await unifier.chat(
                provider=name,
                api_key="secret",
                messages=[Message(role="user", content="hello")],
                model="model",
                translate_errors=False,
            )
    finally:
        registry._REGISTRY.pop(name, None)

    assert str(caught.value) == (
        "The language model provider rejected the model request configuration."
    )
    assert "private-gateway" not in str(caught.value)
    assert structured.value.category == LLMErrorCategory.INVALID_REQUEST
    assert structured.value.message == raw_message


@pytest.mark.asyncio
async def test_unifier_closes_ephemeral_client_when_cache_is_disabled():
    client = MagicMock()
    client.chat = AsyncMock(return_value=LLMResponse(content="ok"))
    client.aclose = AsyncMock()

    from services.llm import registry

    name = "ephemeral-client-test"
    registry._REGISTRY[name] = ProviderSpec(
        name=name,
        factory=lambda **_kwargs: client,
        sdk_exception_refs=("openai:OpenAIError",),
    )
    auth = MagicMock()
    auth.get_api_key = AsyncMock(return_value=None)
    unifier = ChatUnifier(
        defaults={"providers": {}},
        auth_service=auth,
        client_cache_size=0,
    )
    try:
        await unifier.chat(
            provider=name,
            api_key="secret",
            messages=[Message(role="user", content="hello")],
            model="model",
        )
    finally:
        registry._REGISTRY.pop(name, None)

    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_lru_eviction_defers_close_until_in_flight_chat_releases_lease():
    started = asyncio.Event()
    release = asyncio.Event()
    created = {}

    class Client:
        def __init__(self, **kwargs):
            self.api_key = kwargs["api_key"]
            self.closed = False
            created[self.api_key] = self

        async def chat(self, *_args, **_kwargs):
            if self.api_key == "secret-a":
                started.set()
                await release.wait()
                assert not self.closed
            return LLMResponse(content="ok")

        async def aclose(self):
            self.closed = True

    from services.llm import registry

    name = "leased-cache-test"
    registry._REGISTRY[name] = ProviderSpec(
        name=name,
        factory=Client,
        sdk_exception_refs=("openai:OpenAIError",),
    )
    auth = MagicMock()
    auth.get_api_key = AsyncMock(return_value=None)
    unifier = ChatUnifier(
        defaults={"providers": {}},
        auth_service=auth,
        client_cache_size=1,
    )

    first_call = asyncio.create_task(
        unifier.chat(
            provider=name,
            api_key="secret-a",
            messages=[Message(role="user", content="first")],
            model="model",
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        await unifier.chat(
            provider=name,
            api_key="secret-b",
            messages=[Message(role="user", content="second")],
            model="model",
        )

        # secret-a was evicted from the size-1 cache, but its active request
        # still owns a lease and must not see a closed SDK transport.
        assert created["secret-a"].closed is False
        release.set()
        await asyncio.wait_for(first_call, timeout=2)
        assert created["secret-a"].closed is True
    finally:
        release.set()
        await asyncio.gather(first_call, return_exceptions=True)
        await unifier.aclose()
        registry._REGISTRY.pop(name, None)
