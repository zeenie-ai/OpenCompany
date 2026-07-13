"""Shared helpers for the Vertex managed-agent plugins.

Local to ``nodes/agent/`` — same convention as ``_handles.py``. Used by
``vertex_managed_agent`` (Interactions API) and ``vertex_agent_admin``
(Agents API lifecycle).

Auth model (verified live against the enterprise surface):

- ``AIza...`` Gemini API keys -> ``genai.Client(api_key=...)`` on
  generativelanguage.googleapis.com.
- Enterprise Agent Platform (aiplatform.googleapis.com) -> ``genai.Client(
  enterprise=True, project=<id>, location="global")`` with Application
  Default Credentials (``gcloud auth application-default login``). ``AQ.``
  Vertex Express keys do NOT work on the interactions surface (the SDK
  builds an unprojected URL and 404s), so a project id is required there.

The enterprise surface also requires ``background=True`` on agent
interactions ("Agent interactions must set background to true."), so
:func:`create_interaction_and_wait` always creates in background mode and
polls ``interactions.get`` to a terminal status.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from core.logging import get_logger
from services.plugin import NodeUserError

logger = get_logger(__name__)

DEFAULT_MANAGED_AGENT = "antigravity-preview-05-2026"
DEFAULT_LOCATION = "global"

# Interaction statuses that stop the background poll loop.
# "requires_action" is terminal for a single create: the caller decides
# whether to answer pending function calls with a follow-up create.
TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "incomplete",
        "budget_exceeded",
        "requires_action",
    }
)

_POLL_INTERVAL_SECONDS = 3.0

# Set on a client instance after the surface rejects a streaming create
# (400 "Precondition check failed" on the enterprise agent surface) so
# later turns in the same run skip straight to the background+poll path.
_STREAM_UNSUPPORTED_ATTR = "_opencompany_stream_unsupported"


def is_genai_error(exc: BaseException) -> bool:
    """True for any google-genai SDK error, across its two hierarchies.

    The models/agents surfaces raise ``google.genai.errors.APIError``
    while the interactions sub-client raises an OpenAI-style hierarchy
    from a private module — a module-name check covers both without
    importing private paths.
    """
    return type(exc).__module__.startswith("google.genai")


def raise_as_user_error(exc: Exception, *, what: str) -> None:
    """Re-raise a google-genai SDK error as a user-correctable failure."""
    if not is_genai_error(exc):
        raise exc
    detail = str(exc).replace("\n", " ")
    if len(detail) > 400:
        detail = detail[:400] + "..."
    raise NodeUserError(f"{what} failed: {detail}") from exc


def is_expired_environment_error(exc: BaseException) -> bool:
    """Heuristic for a stale environment/interaction chain (7-day TTL)."""
    if not is_genai_error(exc):
        return False
    text = str(exc).lower()
    return ("environment" in text or "interaction" in text) and (
        "not found" in text or "expired" in text or "invalid" in text
    )


def is_precondition_failure(exc: BaseException) -> bool:
    """400 'Precondition check failed' from the enterprise surface.

    Live-verified trigger: creating with ``previous_interaction_id``
    pointing at an interaction that is not in a resumable state (e.g.
    still ``in_progress`` — an orphaned background interaction keeps
    running server-side after a client-side cancel/reload). A chain
    wedged on such a target fails every subsequent run, so callers
    treat this like an expired chain: wipe the stored ids and retry
    fresh.
    """
    if not is_genai_error(exc):
        return False
    return "precondition check failed" in str(exc).lower()


def build_genai_client(api_key: str, project_id: str, location: str = DEFAULT_LOCATION):
    """Build the google-genai client for the managed-agent surfaces."""
    from google import genai

    if project_id:
        # Enterprise Agent Platform surface — ADC auth (gcloud).
        return genai.Client(
            enterprise=True,
            project=project_id,
            location=location or DEFAULT_LOCATION,
        )
    if api_key.startswith("AIza"):
        return genai.Client(api_key=api_key)
    raise NodeUserError(
        "Vertex managed agents need either a GCP Project ID (uses gcloud "
        "Application Default Credentials on the Agent Platform surface) or "
        "a stored Gemini API key starting with 'AIza'. Vertex Express "
        "'AQ.' keys are not supported by the Interactions API."
    )


def resolve_api_key_from_context(raw_context: dict) -> str:
    """Recover the auto-injected gemini key from the raw params.

    ``node_executor._inject_api_keys`` writes ``api_key`` into the raw
    params dict before Pydantic validation strips it. Only prefixes the
    gemini credential can produce are trusted — the injector falls back
    to provider "openai" when saved params lack ``provider``.
    """
    raw_params = (raw_context or {}).get("_raw_parameters") or {}
    key = raw_params.get("api_key") or ""
    if isinstance(key, str) and key.startswith(("AIza", "AQ.")):
        return key
    return ""


async def resolve_gemini_api_key_from_store() -> str:
    """Fetch the stored gemini credential for nodes outside AI_MODEL_TYPES.

    ``vertex_agent_admin`` is not an agent/model type, so
    ``_inject_api_keys`` never runs for it — read the credential store
    directly (same path the delegation injector uses).
    """
    from services.plugin.deps import get_ai_service

    try:
        key = await get_ai_service().auth.get_api_key("gemini", "default")
    except Exception:  # noqa: BLE001 — missing key is handled by the caller
        return ""
    return key or ""


async def create_interaction_and_wait(
    client: Any,
    *,
    on_poll: Optional[Callable[[], Awaitable[None]]] = None,
    poll_interval: float = _POLL_INTERVAL_SECONDS,
    **kwargs: Any,
) -> Any:
    """Create a background interaction and poll it to a terminal status."""
    interaction = await client.aio.interactions.create(background=True, **kwargs)
    while getattr(interaction, "status", None) not in TERMINAL_STATUSES:
        await asyncio.sleep(poll_interval)
        if on_poll is not None:
            await on_poll()
        interaction = await client.aio.interactions.get(interaction.id)
    return interaction


async def stream_interaction(
    client: Any,
    *,
    on_event: Optional[Callable[[Any], Awaitable[None]]] = None,
    poll_interval: float = _POLL_INTERVAL_SECONDS,
    **kwargs: Any,
) -> Any:
    """Create a streamed background interaction; return the FULL resource.

    The SSE stream (``create(stream=True, background=True)``) exists purely
    for LIVE visibility — each event is handed to ``on_event`` best-effort
    (an exception there is logged, never fatal to the turn). The
    ``interaction.completed`` event carries only a PARTIAL interaction
    (no ``output_text`` / ``environment_id``; ``steps`` optional), so after
    the stream closes the authoritative resource is fetched with a
    non-stream ``interactions.get`` and polled to a terminal status as a
    safety net.

    If the streaming create itself is rejected by the surface (e.g. the
    enterprise agent surface answers 400 "Precondition check failed"),
    falls back to the plain background+poll path so the node still works
    — and latches the rejection on the client so the remaining turns of
    the run skip the doomed streaming attempt instead of paying a failed
    round trip + a warning per turn. A fresh run builds a fresh client,
    so streaming is re-probed next run.
    """
    # Identity check on purpose: only an explicit latch counts (mock
    # clients auto-create truthy attributes).
    if getattr(client, _STREAM_UNSUPPORTED_ATTR, False) is True:
        return await create_interaction_and_wait(
            client, poll_interval=poll_interval, **kwargs
        )
    try:
        stream = await client.aio.interactions.create(
            background=True, stream=True, **kwargs
        )
    except Exception as exc:  # noqa: BLE001 — fall back, then error-map there
        if not is_genai_error(exc):
            raise
        setattr(client, _STREAM_UNSUPPORTED_ATTR, True)
        logger.warning(
            "[Vertex] streaming create rejected (%s) — falling back to poll "
            "for the rest of this run",
            str(exc).replace("\n", " ")[:200],
        )
        return await create_interaction_and_wait(
            client, poll_interval=poll_interval, **kwargs
        )

    interaction_id: Optional[str] = None
    async for event in stream:
        if interaction_id is None:
            inner = getattr(event, "interaction", None)
            candidate = getattr(inner, "id", None) or getattr(
                event, "interaction_id", None
            )
            if candidate:
                interaction_id = candidate
        if on_event is not None:
            try:
                await on_event(event)
            except Exception:  # noqa: BLE001 — visibility must never kill the turn
                logger.exception("[Vertex] on_event handler failed (ignored)")

    if interaction_id is None:
        raise NodeUserError(
            "Vertex managed agent stream ended without an interaction id — "
            "no interaction.created event was received."
        )

    interaction = await client.aio.interactions.get(interaction_id)
    while getattr(interaction, "status", None) not in TERMINAL_STATUSES:
        await asyncio.sleep(poll_interval)
        interaction = await client.aio.interactions.get(interaction_id)
    return interaction
