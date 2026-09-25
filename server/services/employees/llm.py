"""Which AI model writes a setup screen, and calling it.

``resolve_llm_choice`` picks, in order:

1. a local model server (Ollama, LM Studio: providers the credential
   catalogue marks ``runs_locally``), when the owner keeps
   "Keep everything on this computer" on;
2. the global default provider and model from Settings, when that
   provider is usable;
3. the first usable provider, then any saved OpenAI-compatible endpoint.

A provider is usable when its credential is stored (the same check the
credential catalogue serves). The model is the provider's saved default,
else (for a local server) the first model it reported, else the JSON
default.

``employees_chat`` runs one completion through the ChatUnifier under a time
budget; ``record_llm_usage`` books its tokens and cost like every other
LLM call.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List, Optional

from core.logging import get_logger
from services.credential_registry import get_credential_registry
from services.employees.connections import Connections
from services.employees.context import SETTINGS_USER_ID
from services.llm.protocol import LLMResponse, Message

logger = get_logger(__name__)

#: Time a model may take to write one setup screen.
CLOUD_BUDGET_SECONDS = 90.0
LOCAL_BUDGET_SECONDS = 240.0

#: Room for a 12-element minified spec with every string near its cap.
SETUP_MAX_TOKENS = 6000
SETUP_TEMPERATURE = 0.4

#: Where setup calls appear in the usage tables.
USAGE_NODE_ID = "employee_setup"


@dataclass(frozen=True)
class LLMChoice:
    provider: str
    model: str
    local: bool

    @property
    def budget_seconds(self) -> float:
        return LOCAL_BUDGET_SECONDS if self.local else CLOUD_BUDGET_SECONDS


def runs_locally(provider: str) -> bool:
    entry = get_credential_registry().get_provider(provider) or {}
    return bool(entry.get("runs_locally"))


async def _model_for(provider: str, database: Any, auth_service: Any, *, local: bool, endpoint_models: Optional[List[str]] = None) -> str:
    from services.llm.config import get_default_model

    try:
        saved = await database.get_provider_defaults(provider)
    except Exception:
        saved = None
    if saved and saved.get("default_model"):
        return str(saved["default_model"])
    if endpoint_models:
        return endpoint_models[0]
    if local:
        try:
            models = await auth_service.get_stored_models(provider)
        except Exception:
            models = []
        if models:
            return str(models[0])
    return get_default_model(provider)


async def resolve_llm_choice(
    database: Any,
    auth_service: Any,
    connections: Connections,
    *,
    user_id: str = SETTINGS_USER_ID,
) -> Optional[LLMChoice]:
    """The model to use, or None when no AI model is set up."""
    usable = await connections.ai_providers()
    endpoints = []
    try:
        from services.llm.endpoints import list_endpoints

        endpoints = await list_endpoints(auth_service)
    except Exception:
        logger.warning("Could not list OpenAI-compatible endpoints", exc_info=True)
    endpoint_models = {endpoint.ref: list(endpoint.models) for endpoint in endpoints}
    if not usable and not endpoints:
        return None

    try:
        settings = await database.get_user_settings(user_id) or {}
    except Exception:
        settings = {}

    async def choose(provider: str, model: Optional[str] = None) -> LLMChoice:
        local = runs_locally(provider)
        chosen = model or await _model_for(
            provider, database, auth_service, local=local, endpoint_models=endpoint_models.get(provider)
        )
        return LLMChoice(provider=provider, model=chosen, local=local)

    if settings.get("prefer_local_ai") is not False:
        for provider in usable:
            if runs_locally(provider):
                return await choose(provider)

    default_provider = str(settings.get("default_llm_provider") or "")
    if default_provider and (default_provider in usable or default_provider in endpoint_models):
        return await choose(default_provider, str(settings.get("default_llm_model") or "") or None)

    if usable:
        return await choose(usable[0])
    return await choose(endpoints[0].ref)


async def employees_chat(
    chat_unifier: Any,
    auth_service: Any,
    choice: LLMChoice,
    messages: List[Message],
    *,
    max_tokens: int = SETUP_MAX_TOKENS,
    timeout: Optional[float] = None,
) -> LLMResponse:
    """One completion on the chosen model. Raises ``asyncio.TimeoutError``
    past the budget and ``NodeUserError`` for provider failures."""
    api_key = await auth_service.get_api_key(choice.provider) or ""
    try:
        from services.model_registry import get_model_registry

        ceiling = get_model_registry().get_max_output_tokens(choice.model, choice.provider)
        if ceiling > 0:
            max_tokens = min(max_tokens, int(ceiling))
    except Exception:
        logger.debug("No output-token ceiling for the setup model", provider=choice.provider, exc_info=True)
    return await asyncio.wait_for(
        chat_unifier.chat(
            provider=choice.provider,
            api_key=api_key,
            messages=messages,
            model=choice.model,
            temperature=SETUP_TEMPERATURE,
            max_tokens=max_tokens,
            sdk_max_retries=1,
        ),
        timeout=timeout if timeout is not None else choice.budget_seconds,
    )


async def record_llm_usage(database: Any, choice: LLMChoice, response: LLMResponse, *, session_id: str) -> None:
    """Book a setup call's tokens and cost (Settings > usage reads these).
    Best effort: a failed write never fails the setup."""
    from services.pricing import get_pricing_service

    usage = response.billing_usage or response.usage
    tokens = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "cache_creation_tokens": usage.cache_creation_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
    }
    try:
        cost = get_pricing_service().calculate_cost(
            provider=choice.provider,
            model=choice.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            reasoning_tokens=usage.reasoning_tokens,
        )
        await database.save_token_metric(
            {
                "session_id": session_id,
                "node_id": USAGE_NODE_ID,
                "provider": choice.provider,
                "model": choice.model,
                "iteration": 1,
                **tokens,
                "input_cost": cost["input_cost"],
                "output_cost": cost["output_cost"],
                "cache_cost": cost["cache_cost"],
                "total_cost": cost["total_cost"],
            }
        )
    except Exception:
        logger.warning("Could not record setup usage", provider=choice.provider, model=choice.model, exc_info=True)


__all__ = [
    "CLOUD_BUDGET_SECONDS",
    "LLMChoice",
    "LOCAL_BUDGET_SECONDS",
    "SETUP_MAX_TOKENS",
    "USAGE_NODE_ID",
    "employees_chat",
    "record_llm_usage",
    "resolve_llm_choice",
    "runs_locally",
]
