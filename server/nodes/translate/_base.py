"""Shared helpers for the three text-language nodes."""

from __future__ import annotations

from typing import Optional, Sequence

from services.plugin import NodeContext, NodeUserError

from . import _config as translate_config


async def provider_api_key(ctx: NodeContext, provider: str) -> str:
    """Resolve the stored key through the provider's credential.

    Goes through ``ctx.connection(...)`` rather than the auth service so a
    missing key raises the framework's annotated ``PermissionError``, which
    ``BaseNode.execute`` turns into a credential envelope plus a CloudEvents
    broadcast — lighting up the right provider in the Credentials modal.
    """
    from services.gateway.routing import gateway_covers

    credential_id = translate_config.credential_id(provider)
    try:
        async with ctx.connection(credential_id) as conn:
            secrets = await conn.credentials()
    except PermissionError:
        # No key of the install's own: an LLM provider the OpenCompany
        # Gateway serves still runs, through ChatUnifier (services/gateway).
        if await gateway_covers(provider):
            return ""
        raise
    api_key = str(secrets.get("api_key") or "")
    if not api_key and await gateway_covers(provider):
        return ""
    if not api_key:
        raise NodeUserError(
            f"No API key stored for '{credential_id}'. Add one in the "
            "Credentials modal."
        )
    return api_key


def require_provider(provider: str, available: Sequence[str], capability: str) -> str:
    if provider in available:
        return provider
    raise NodeUserError(
        f"'{provider}' does not do {capability}. Available: {', '.join(available)}."
    )


def check_length(provider: str, capability: str, model: str, text: str) -> None:
    """Refuse over-long input before spending a paid call on it."""
    cap = translate_config.max_input_chars(provider, capability, model)
    if cap and len(text) > cap:
        raise NodeUserError(
            f"Text is {len(text)} characters; {provider}"
            f"{'/' + model if model else ''} accepts at most {cap}. Shorten it "
            "or split it across several runs."
        )


async def track_usage(
    ctx: NodeContext,
    *,
    provider: str,
    operation: str,
    units: Optional[float],
    unit: str,
) -> None:
    """Record an API usage metric. Never raises.

    Skipped entirely for token-billed providers: an LLM-backed translation is
    already costed by the LLM layer, and recording a character count against
    ``api_pricing`` as well would double-count the same call.
    """
    if unit == "tokens" or units is None or units <= 0:
        return
    from services.usage import record_api_usage

    await record_api_usage(
        provider,
        operation,
        units,
        node_id=ctx.node_id,
        session_id=ctx.raw.get("session_id", "default"),
        workflow_id=ctx.workflow_id,
    )


__all__ = ["check_length", "provider_api_key", "require_provider", "track_usage"]
