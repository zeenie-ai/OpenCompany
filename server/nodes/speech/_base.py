"""Shared helpers for the two speech nodes.

Credential resolution, audio input handling, and cost attribution -- the
parts both nodes need and neither should own.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence, Tuple

from services.media import coerce_file_param, read_media_bytes, resolve_media
from services.media.inspect import inspect_audio
from services.plugin import NodeContext, NodeUserError, coerce_blank_params  # noqa: F401

from . import _config as speech_config

if TYPE_CHECKING:
    from services.gateway.routing import GatewayRoute


def gateway_serves(provider: str) -> bool:
    """Whether the OpenCompany Gateway serves this provider's speech
    (``"gateway": true`` in speech_defaults.json)."""
    return speech_config.provider_block(provider).get("gateway") is True


async def provider_key(ctx: NodeContext, provider: str) -> Tuple[str, Optional[GatewayRoute]]:
    """Resolve the key for ``provider`` through its credential, and the
    OpenCompany Gateway's route when the gateway serves the call.

    Goes through ``ctx.connection(...)`` rather than reading the auth
    service directly so a missing key raises the framework's annotated
    ``PermissionError`` -- which ``BaseNode.execute`` turns into a
    credential envelope plus a CloudEvents broadcast, so the Credentials
    modal lights up the right provider.

    Own keys first: only with no key of the install's own does a provider
    marked ``"gateway": true`` in speech_defaults.json run on the gateway.
    """
    credential_id = speech_config.credential_id(provider)
    if gateway_serves(provider):
        from services.gateway.routing import key_or_route

        api_key, route = await key_or_route(ctx, credential_id, provider)
    else:
        async with ctx.connection(credential_id) as conn:
            secrets = await conn.credentials()
        api_key, route = str(secrets.get("api_key") or ""), None
    if not api_key:
        raise NodeUserError(
            f"No API key stored for '{credential_id}'. Add one in the "
            "Credentials modal."
        )
    return api_key, route


def require_provider(provider: str, available: Sequence[str], direction: str) -> str:
    """Validate a provider selection against what is actually registered."""
    if provider in available:
        return provider
    raise NodeUserError(
        f"'{provider}' is not a {direction} provider. Available: "
        f"{', '.join(available)}."
    )


def read_audio_input(
    value: Any, ctx: NodeContext, *, max_bytes: Optional[int] = None
) -> Tuple[str, bytes, Optional[Path]]:
    """Resolve an audio parameter to ``(filename, bytes, path_or_None)``.

    The path comes back when one exists because it is what makes real
    duration billing possible -- ``inspect_audio`` needs a file. A legacy
    base64 upload has no path, and rather than inventing a duration for it
    the node bills nothing. That is the fix for the old Sarvam node, which
    charged every clip as 30 seconds because it never measured.

    All three input shapes route through ``services.media``, so the
    traversal that let ``audio_file="../../credentials.db"`` read the
    credential store is closed here by construction.
    """
    kwargs: Dict[str, Any] = {"ctx": ctx}
    if max_bytes:
        kwargs["max_bytes"] = max_bytes

    path: Optional[Path] = None
    if isinstance(value, str) and value.strip():
        # A path or workspace-relative string: resolve it so we keep the
        # location for probing, then read through the contained reader.
        path = resolve_media(value, ctx=ctx)
        filename, blob = read_media_bytes(value, **kwargs)
    elif isinstance(value, dict) and value.get("kind") == "audio":
        from services.media import AudioRef

        ref = AudioRef.model_validate(value)
        path = resolve_media(ref, ctx=ctx)
        filename, blob = read_media_bytes(ref, **kwargs)
    else:
        filename, blob = coerce_file_param(value, **kwargs)

    return filename, blob, path


def measure_seconds(path: Optional[Path], declared_format: str = "") -> Optional[float]:
    """Real duration for billing, or ``None`` when it cannot be measured.

    Never raises and never guesses: ``inspect_audio`` degrades to an empty
    probe on an unknown container, and a missing duration means the caller
    skips per-second attribution rather than inventing a figure.
    """
    if path is None:
        return None
    return inspect_audio(path, declared_format=declared_format).duration_seconds


async def track_usage(
    ctx: NodeContext,
    *,
    provider: str,
    operation: str,
    units: Optional[float],
    unit: str,
) -> None:
    """Record an API usage metric. Never raises.

    ``service`` is the provider id rather than a generic ``"speech"`` so
    per-provider dashboards group correctly, and the unit is whatever that
    provider actually bills in -- characters, seconds or minutes. Both come
    from the provider module, because only it knows.

    A provider with no ``operation_map`` entry in ``pricing.json`` yields a
    zero cost silently, which is why every provider added here needs both
    an ``api_pricing`` and an ``operation_map`` block.
    """
    if units is None or units <= 0:
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


__all__ = [
    "coerce_blank_params",
    "gateway_serves",
    "measure_seconds",
    "provider_key",
    "read_audio_input",
    "require_provider",
    "track_usage",
]
