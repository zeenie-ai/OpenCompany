"""Stripe WebSocket handlers.

Login is a thin wrap around the Stripe CLI's two machine-friendly
flags:

* ``stripe login --non-interactive`` prints
  ``{browser_url, verification_code, next_step}`` JSON and exits.
* ``stripe login --complete <next_step>`` polls Stripe until the
  user authorises in the browser, then writes credentials to
  ``~/.config/stripe/config.toml`` and exits 0.

The ``stripe_login`` handler runs step 1 synchronously, returns the
URL and verification code to the frontend (same shape as Twitter /
Google ``oauth_login`` handlers), then fires step 2 as a background
task. When step 2 finishes, we kick the broadcaster's stripe-status
refresh callback so the modal updates reactively.

Every other lifecycle command (connect/disconnect/reconnect/status)
comes from :func:`services.events.make_lifecycle_handlers`.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from typing import Any, Dict

from fastapi import WebSocket

from core.logging import get_logger
from services.events import make_lifecycle_handlers, run_cli_command

from ._install import ensure_stripe_cli, stripe_cli_path
from ._source import (
    get_listen_source,
    is_logged_in,
    stripe_config_path,
)

logger = get_logger(__name__)


_LOGIN_TIMEOUT_SECONDS = 600


async def _resolved_binary() -> str | None:
    """Resolve the stripe binary path, downloading on first use.
    Returns None on install failure (caller should surface error)."""
    try:
        return str(await ensure_stripe_cli())
    except Exception as e:
        logger.warning("[Stripe] CLI install failed: %s", e)
        return None


async def _status_snapshot() -> Dict[str, Any]:
    """Compose the daemon-status + login-state dict the modal renders."""
    src = get_listen_source()
    status = await src.status()
    status["logged_in"] = is_logged_in()
    status["connected"] = bool(status.get("running")) and status["logged_in"]
    return status


# --- Catalogue-stored marker -------------------------------------------------
#
# The catalogue handler in routers/websocket.py keys its "stored" check off
# `auth_service.get_oauth_tokens(status_hook)` for any provider with
# `status_hook` set (the Google / Twitter pattern). The Stripe CLI manages
# its own auth at ~/.config/stripe/config.toml — there are no real OAuth
# tokens for us to store. We persist a synthetic marker via the same API
# Google/Twitter use, so the catalogue's existing logic flips
# `stored: true` after login without any node-specific code in the
# catalogue handler.

_MARKER_TOKEN = "cli-managed"


async def _mark_logged_in() -> None:
    from services.plugin.deps import get_auth_service
    await get_auth_service().store_oauth_tokens(
        provider="stripe",
        access_token=_MARKER_TOKEN,
        refresh_token=_MARKER_TOKEN,
    )


async def _mark_logged_out() -> None:
    from services.plugin.deps import get_auth_service
    await get_auth_service().remove_oauth_tokens("stripe")


async def _broadcast_credential_event(event_type: str) -> None:
    """Emit a CloudEvents-shaped credential mutation broadcast.

    Wraps :class:`services.events.envelope.WorkflowEvent` via the canonical
    helper :func:`StatusBroadcaster.broadcast_credential_event` — same
    invariant that ``handle_save_api_key`` / ``handle_twitter_logout`` are
    locked to in ``tests/credentials/test_credential_broadcasts.py``. The
    frontend listens on ``case 'credential_catalogue_updated'`` (the
    helper's outer wire-format type) and invalidates the catalogue query.
    """
    from services.status_broadcaster import get_status_broadcaster
    await get_status_broadcaster().broadcast_credential_event(
        event_type, provider="stripe",
    )


async def handle_stripe_trigger(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Run ``stripe trigger <event>`` for synthetic test events."""
    event = data.get("event")
    if not event:
        return {"success": False, "error": "event required (e.g. 'charge.succeeded')"}
    binary = await _resolved_binary()
    if not binary:
        return {"success": False, "error": "Stripe CLI install failed"}
    return await run_cli_command(binary=binary, argv=["trigger", event])


async def handle_stripe_login(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Step 1 of CLI OAuth: get the browser URL + verification code."""
    logger.info("[Stripe] login flow: step 1/2 (--non-interactive) starting")
    binary = await _resolved_binary()
    if not binary:
        logger.warning("[Stripe] login failed: CLI binary unavailable")
        return {
            "success": False,
            "error": "Stripe CLI install failed. Manual install: https://stripe.com/docs/stripe-cli#install",
        }
    logger.info("[Stripe] using binary: %s", binary)
    result = await run_cli_command(
        binary=binary, argv=["login", "--non-interactive"], timeout=10.0,
    )
    if not result["success"]:
        logger.warning(
            "[Stripe] login step 1 CLI failure: %s | stderr=%r",
            result.get("error"), (result.get("stderr") or "")[:300],
        )
        return result
    try:
        info = json.loads(result["stdout"])
    except json.JSONDecodeError as e:
        logger.warning("[Stripe] login step 1 unparseable stdout=%r", result["stdout"][:300])
        return {"success": False, "error": f"unparseable stripe login response: {e}"}

    next_step_raw = info.get("next_step")
    url = info.get("browser_url") or info.get("url")
    if not (url and next_step_raw):
        logger.warning("[Stripe] login response missing url/next_step: keys=%s", list(info.keys()))
        return {"success": False, "error": "stripe login response missing browser_url / next_step"}

    # ``next_step`` is the LITERAL shell command the user would otherwise
    # type, e.g.:
    #     stripe login --complete 'https://dashboard.stripe.com/stripecli/auth/…?secret=…'
    # Feeding the whole string into ``--complete`` makes the CLI try to
    # URL-parse it and bail with "first path segment in URL cannot contain
    # colon". Tokenise it and pass just the auth URL (the last argument)
    # to ``--complete``.
    try:
        complete_url = shlex.split(next_step_raw)[-1]
    except (ValueError, IndexError):
        complete_url = next_step_raw
    if not complete_url.startswith("http"):
        logger.warning(
            "[Stripe] could not extract auth URL from next_step=%r — falling back to raw value",
            next_step_raw,
        )
        complete_url = next_step_raw

    logger.info(
        "[Stripe] login step 1 ok: code=%s, browser_url issued — opening on frontend; "
        "spawning step 2 (--complete) in background (timeout=%ss)",
        info.get("verification_code"), _LOGIN_TIMEOUT_SECONDS,
    )
    asyncio.create_task(_complete_login(binary, complete_url))
    return {
        "success": True,
        "url": url,
        "verification_code": info.get("verification_code"),
    }


async def _complete_login(binary: str, next_step: str) -> None:
    """Step 2: block on ``stripe login --complete`` until the user
    authorises (or the 10-min timeout fires). On success, write the
    same kind of marker the Google / Twitter callbacks write so the
    catalogue's stored-check flips, then auto-start the listen
    daemon and trigger the generic catalogue refresh on the frontend."""
    logger.info("[Stripe] login step 2/2 polling for browser confirmation")
    try:
        result = await run_cli_command(
            binary=binary, argv=["login", "--complete", next_step],
            timeout=_LOGIN_TIMEOUT_SECONDS,
        )
    except Exception as e:
        # ``asyncio.create_task`` swallows exceptions silently — log them.
        # Nothing was persisted, so no broadcast: catalogue state is unchanged.
        logger.exception("[Stripe] login step 2 raised unexpectedly: %s", e)
        return

    if not result.get("success"):
        logger.warning(
            "[Stripe] login step 2 CLI failure: %s | stderr=%r",
            result.get("error"), (result.get("stderr") or "")[:300],
        )

    if not is_logged_in():
        logger.warning(
            "[Stripe] step 2 finished but ``is_logged_in()`` is False — config file %s missing/empty; "
            "user likely closed the browser before authorising",
            stripe_config_path(),
        )
        return

    logger.info(
        "[Stripe] auth successful — credentials written to %s; persisting catalogue marker + starting listen daemon",
        stripe_config_path(),
    )
    await _mark_logged_in()
    logger.info("[Stripe] catalogue marker token persisted (auth_service.store_oauth_tokens)")
    start_result = await get_listen_source().start()
    if start_result.get("success"):
        logger.info("[Stripe] listen daemon started (pid=%s)", start_result.get("status", {}).get("pid"))
    else:
        logger.warning("[Stripe] listen daemon failed to start: %s", start_result.get("error"))
    await _broadcast_credential_event("credential.oauth.connected")


async def handle_stripe_logout(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Stop the daemon (if running) and run ``stripe logout --all`` to
    clear ``~/.config/stripe/config.toml``. Mirror the Google/Twitter
    logout shape: drop the catalogue marker token + broadcast the
    generic catalogue invalidation so the modal flips immediately."""
    logger.info("[Stripe] logout starting: stopping daemon + running 'stripe logout --all'")
    await get_listen_source().stop()
    cached = stripe_cli_path()
    if cached is None:
        cfg = stripe_config_path()
        if cfg.exists():
            cfg.unlink(missing_ok=True)
        result: Dict[str, Any] = {"success": True, "message": "Logged out (CLI not yet installed; cleared config file)"}
        logger.info("[Stripe] logout fallback: CLI not installed; deleted %s", cfg)
    else:
        result = await run_cli_command(binary=str(cached), argv=["logout", "--all"], timeout=10.0)
        logger.info(
            "[Stripe] logout CLI result: success=%s err=%s",
            result.get("success"), result.get("error"),
        )
    await _mark_logged_out()
    await _broadcast_credential_event("credential.oauth.disconnected")
    logger.info("[Stripe] logout complete: marker token removed + catalogue broadcast sent")
    return result


async def handle_stripe_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Augments the stock daemon-status with login-state."""
    return {"success": True, "status": await _status_snapshot()}


WS_HANDLERS = make_lifecycle_handlers(
    prefix="stripe",
    source=get_listen_source(),
    extra={
        "stripe_login": handle_stripe_login,
        "stripe_logout": handle_stripe_logout,
        "stripe_trigger": handle_stripe_trigger,
    },
)
WS_HANDLERS["stripe_status"] = handle_stripe_status
