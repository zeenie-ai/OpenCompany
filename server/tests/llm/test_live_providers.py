"""Live-API integration tests for the ChatUnifier and every native provider.

**Marker**: ``@pytest.mark.live`` — these tests hit real LLM endpoints
and **incur charges** on paid plans. They are excluded from default
pytest runs; opt in explicitly:

    pytest -m live                          # all live providers
    pytest -m live tests/llm/test_live_providers.py  # this file only

**Credentials**: each provider's API key is read from the environment
or from the ``.env`` file at the repo root. Missing keys cause that
provider's tests to skip — they never fail. Recognised env vars:

    ANTHROPIC_API_KEY          → anthropic
    OPENAI_API_KEY             → openai
    GEMINI_API_KEY / GOOGLE_API_KEY → gemini
    OPENROUTER_API_KEY         → openrouter
    XAI_API_KEY / GROK_API_KEY → xai
    DEEPSEEK_API_KEY           → deepseek
    KIMI_API_KEY / MOONSHOT_API_KEY → kimi
    MISTRAL_API_KEY            → mistral
    OLLAMA_BASE_URL            → ollama (override default localhost:11434)
    LMSTUDIO_BASE_URL          → lmstudio (override default localhost:1234)

**Scope**: each enabled provider gets two assertions —
  1. ``ChatUnifier.fetch_models`` returns a non-empty list.
  2. ``ChatUnifier.chat`` returns non-empty content for a 1-token "hi" call.

The tests use the smallest available model + ``max_tokens=16`` to keep
spend negligible. Total cost per full run with all 4 cloud keys
present: well under $0.01 at 2026 prices.

**Why this file is opt-in, not always-on**: the unit/mock tests in
``test_unifier_typed_errors.py`` + ``test_unifier_incompatible_models_filter.py``
+ ``test_wiring.py`` cover the delegation contract without spending
money. Live tests are a smoke check that the SDK shape, auth headers,
and the unifier dispatch still match what the real APIs accept — run
them after dependency bumps or before a release, not on every commit.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pytest
from unittest.mock import AsyncMock, MagicMock

import services.llm  # noqa: F401 — populate the provider registry
from services.llm.config import LLM_DEFAULTS
from services.llm.protocol import Message
from services.llm.registry import get_provider, has_provider
from services.llm.unifier import ChatUnifier


# ---------------------------------------------------------------------------
# .env loading — opt-in (live tests need real keys)
# ---------------------------------------------------------------------------


def _load_dotenv_from_repo_root() -> None:
    """Load ``.env`` from the worktree root so live tests can read API keys.

    Walks up from this test file to find the first ``.env`` and loads
    it via ``python-dotenv``. Existing environment variables win over
    ``.env`` values so CI can override per-run.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return  # python-dotenv missing — fall back to OS env only

    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            load_dotenv(candidate, override=False)
            return


_load_dotenv_from_repo_root()


# ---------------------------------------------------------------------------
# Per-provider env-key resolution
# ---------------------------------------------------------------------------


def _api_key(*names: str) -> Optional[str]:
    """Return the first non-empty env value among ``names`` (priority order)."""
    for n in names:
        val = os.environ.get(n)
        if val and val.strip():
            return val.strip()
    return None


def _local_base_url(env_name: str, default: str) -> str:
    """Resolve a local-server base URL with env override."""
    return (os.environ.get(env_name) or default).strip()


# Provider → (env-var lookup, default smallest model). Local providers
# (ollama / lmstudio) carry no default model — they reflect whatever
# the user has loaded; the live tests skip if the server isn't
# reachable.
_PROVIDER_KEYS = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
    "xai": ("XAI_API_KEY", "GROK_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "kimi": ("KIMI_API_KEY", "MOONSHOT_API_KEY"),
    "mistral": ("MISTRAL_API_KEY",),
}

# Models chosen for low cost + broad availability. Falls back to the
# provider's ``default_model`` from llm_defaults.json if the live API
# rejects the pinned one.
_LIVE_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.5-flash",
    "openrouter": "anthropic/claude-haiku-4.5",
    "xai": "grok-3",
    "deepseek": "deepseek-chat",
    "kimi": "kimi-k2.6",
    "mistral": "mistral-small-latest",
    "ollama": None,    # discovered from server
    "lmstudio": None,
}


def _resolve_live_model(provider: str, fetched: list[str]) -> Optional[str]:
    """Pick a real model name for the chat round-trip.

    If the pinned ``_LIVE_MODELS`` entry is in the fetched list, use it.
    Otherwise fall back to ``providers.<name>.default_model`` from JSON.
    Local providers (ollama/lmstudio) return the first non-empty entry
    in the fetched list.
    """
    pinned = _LIVE_MODELS.get(provider)
    if pinned and pinned in fetched:
        return pinned
    json_default = LLM_DEFAULTS.get("providers", {}).get(provider, {}).get("default_model")
    if json_default and json_default in fetched:
        return json_default
    return fetched[0] if fetched else None


# ---------------------------------------------------------------------------
# Unifier fixture — wired against a stub auth_service that returns the
# user-configured {provider}_proxy URL from the environment (so the
# Ollama / LM Studio paths still work when the user has a custom
# endpoint). Cloud providers ignore proxy_url and use their default
# base_url + the api_key passed to chat().
# ---------------------------------------------------------------------------


@pytest.fixture
def live_unifier():
    """ChatUnifier wired against real env credentials."""

    async def _get_api_key(provider_proxy_key: str):
        # provider_proxy_key looks like "ollama_proxy" / "lmstudio_proxy"
        if provider_proxy_key == "ollama_proxy":
            return _local_base_url("OLLAMA_BASE_URL", "")
        if provider_proxy_key == "lmstudio_proxy":
            return _local_base_url("LMSTUDIO_BASE_URL", "")
        return None  # cloud providers don't use proxy_url

    auth = MagicMock()
    auth.get_api_key = AsyncMock(side_effect=_get_api_key)
    return ChatUnifier(defaults=LLM_DEFAULTS, auth_service=auth)


# ---------------------------------------------------------------------------
# Cloud-provider live tests
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.parametrize("provider", sorted(_PROVIDER_KEYS.keys()))
@pytest.mark.asyncio
async def test_live_fetch_models_returns_non_empty(live_unifier, provider):
    """``ChatUnifier.fetch_models`` returns at least one model from the live API."""
    api_key = _api_key(*_PROVIDER_KEYS[provider])
    if not api_key:
        pytest.skip(f"no API key set for {provider} ({' / '.join(_PROVIDER_KEYS[provider])})")

    assert has_provider(provider), f"provider {provider!r} not in registry"

    models = await live_unifier.fetch_models(provider=provider, api_key=api_key)
    assert isinstance(models, list), f"{provider} fetch_models returned non-list: {type(models)}"
    assert models, f"{provider} fetch_models returned an empty list"


@pytest.mark.live
@pytest.mark.parametrize("provider", sorted(_PROVIDER_KEYS.keys()))
@pytest.mark.asyncio
async def test_live_chat_round_trip_returns_content(live_unifier, provider):
    """``ChatUnifier.chat`` round-trip returns non-empty content from the live API."""
    api_key = _api_key(*_PROVIDER_KEYS[provider])
    if not api_key:
        pytest.skip(f"no API key set for {provider} ({' / '.join(_PROVIDER_KEYS[provider])})")

    models = await live_unifier.fetch_models(provider=provider, api_key=api_key)
    model = _resolve_live_model(provider, models)
    if model is None:
        pytest.skip(f"{provider}: live model list empty — nothing to call")

    response = await live_unifier.chat(
        provider=provider,
        api_key=api_key,
        messages=[Message(role="user", content="Say hi in 3 words.")],
        model=model,
        temperature=0.0,
        max_tokens=16,
    )
    assert response.content, f"{provider} ({model}) returned empty content: {response!r}"


# ---------------------------------------------------------------------------
# Local-server live tests (ollama / lmstudio)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.parametrize("provider,env_var,default_url", [
    ("ollama", "OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    ("lmstudio", "LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
])
@pytest.mark.asyncio
async def test_live_local_server_fetch_models(live_unifier, provider, env_var, default_url):
    """Local servers (ollama / lmstudio) — fetch the user's loaded models."""
    base_url = _local_base_url(env_var, default_url)
    # Probe whether the server is actually reachable; skip cleanly if not.
    try:
        import httpx

        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{base_url}/models")
            r.raise_for_status()
    except Exception as e:
        pytest.skip(f"{provider} server not reachable at {base_url}: {e}")

    # Local providers don't need a real API key — pass a placeholder.
    models = await live_unifier.fetch_models(provider=provider, api_key="placeholder")
    assert isinstance(models, list)
    # We do NOT assert non-empty — a running server with zero loaded
    # models is a legitimate state (e.g. user just started Ollama and
    # hasn't pulled anything). The unifier path is exercised either way.


# ---------------------------------------------------------------------------
# Cross-cutting smoke: incompatible_models filter on gemini live
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_gemini_filters_antigravity_from_dropdown(live_unifier):
    """Live gemini fetch_models hides the JSON-declared antigravity model.

    This is the end-to-end verification of the Phase A4 hotfix — even
    if the upstream Gemini Models API surfaces the preview, our unifier
    must drop it from the dropdown so the chat-model node can't select
    an Interactions-API-only model and crash.
    """
    api_key = _api_key(*_PROVIDER_KEYS["gemini"])
    if not api_key:
        pytest.skip("no API key set for gemini (GEMINI_API_KEY / GOOGLE_API_KEY)")

    models = await live_unifier.fetch_models(provider="gemini", api_key=api_key)
    incompatible = set(
        LLM_DEFAULTS.get("providers", {}).get("gemini", {}).get("incompatible_models", [])
    )
    if not incompatible:
        pytest.skip("no gemini.incompatible_models declared in llm_defaults.json")

    for forbidden in incompatible:
        assert forbidden not in models, (
            f"gemini fetch_models returned a model that llm_defaults.json "
            f"marks as incompatible: {forbidden!r}. The filter is not "
            f"applied — the user would be able to pick it from the "
            f"dropdown and the chat call would surface a typed APIError."
        )
