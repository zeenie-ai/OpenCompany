"""An OpenRouter key is checked where OpenRouter checks it.

The LLM probe validates a key by listing the provider's models, but
OpenRouter's model list is public and answers any key with 200. A key
OpenRouter refuses was therefore stored as validated, and only the first
call that used it failed with 401. The probe now asks /api/v1/key first,
which answers 401 to a key OpenRouter does not know.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from nodes.model._credentials import OpenAICredential, OpenRouterCredential

pytestmark = pytest.mark.credentials

KEY_URL = "https://openrouter.ai/api/v1/key"
MODELS = ["anthropic/claude-sonnet-5.5"]


@pytest.fixture
def ai(monkeypatch) -> SimpleNamespace:
    service = SimpleNamespace(fetch_models=AsyncMock(return_value=MODELS))
    monkeypatch.setattr("services.plugin.deps.get_ai_service", lambda: service)
    return service


@pytest.fixture
def auth(auth_service, monkeypatch):
    from core.container import container

    monkeypatch.setattr(container, "auth_service", lambda: auth_service)
    monkeypatch.setattr(
        "services.status_broadcaster.get_status_broadcaster",
        lambda: SimpleNamespace(update_api_key_status=AsyncMock()),
    )
    return auth_service


@respx.mock
async def test_a_key_openrouter_refuses_is_not_stored(auth, ai):
    respx.get(KEY_URL).mock(return_value=httpx.Response(401, json={"error": {"message": "User not found.", "code": 401}}))

    result = await OpenRouterCredential.validate({"api_key": "sk-or-v1-unknown"})

    assert result["valid"] is False
    assert result["message"] == "OpenRouter rejected the API key."
    assert await auth.get_api_key("openrouter") is None
    ai.fetch_models.assert_not_awaited()


@respx.mock
async def test_a_key_openrouter_knows_is_stored_with_its_models(auth, ai):
    route = respx.get(KEY_URL).mock(return_value=httpx.Response(200, json={"data": {"label": "sk-or-v1-kno...own", "usage": 0}}))

    result = await OpenRouterCredential.validate({"api_key": "sk-or-v1-known"})

    assert result["valid"] is True
    assert route.calls.last.request.headers["Authorization"] == "Bearer sk-or-v1-known"
    assert await auth.get_api_key("openrouter") == "sk-or-v1-known"
    assert await auth.get_stored_models("openrouter") == MODELS


@respx.mock
async def test_a_provider_whose_model_list_checks_the_key_makes_no_other_request(ai):
    result = await OpenAICredential._probe("sk-test")

    assert result.valid is True and result.models == MODELS
    assert respx.calls.call_count == 0
