"""Stripe event source — supervised ``stripe listen`` daemon + the
companion ``WebhookSource`` that receives forwarded events.

Two cooperating sources:

* :class:`StripeListenSource` (DaemonEventSource) keeps the CLI alive,
  captures the ``whsec_…`` signing secret from its stderr banner.
* :class:`StripeWebhookSource` (WebhookSource) is the actual event
  producer — verifies the Stripe-Signature header on each forwarded
  POST and turns the payload into a :class:`WorkflowEvent`.

Module-level singletons (``get_listen_source`` / ``get_webhook_source``)
plug into the framework registries from ``__init__.py``.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Request

from core.logging import get_logger
from services.events import (
    DaemonEventSource,
    StripeVerifier,
    WebhookSource,
    WorkflowEvent,
)

from ._credentials import StripeCredential

logger = get_logger(__name__)

_SECRET_RE = re.compile(r"whsec_[A-Za-z0-9_]+")
_SECRET_FIELD = "stripe_webhook_secret"


def stripe_config_path() -> Path:
    """Default location for the Stripe CLI's credentials file
    (``~/.config/stripe/config.toml``, ``XDG_CONFIG_HOME``-aware)."""
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "stripe" / "config.toml"


def is_logged_in() -> bool:
    """Detect Stripe CLI login by sniffing its config file. Cheap
    filesystem check — the CLI writes ``test_mode_api_key`` /
    ``live_mode_api_key`` under a profile section on success."""
    cfg = stripe_config_path()
    if not cfg.exists():
        return False
    try:
        return "_api_key" in cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


class StripeListenSource(DaemonEventSource):
    """Supervises ``stripe listen`` and persists the captured webhook
    signing secret. Emits no events itself — Stripe events arrive over
    HTTP via :class:`StripeWebhookSource`."""

    type = "stripe.listen"
    process_name = "stripe-listen"
    # Empty so DaemonEventSource skips its built-in PATH check; we
    # do install + verification ourselves in start() via
    # ensure_stripe_cli (which falls back to system PATH first, then
    # workspace-local download).
    binary_name = ""
    workflow_namespace = "_stripe"
    install_hint = "https://stripe.com/docs/stripe-cli#install"
    credential = StripeCredential

    def build_command(self, secrets: Dict) -> str:
        # The CLI uses credentials it stored at ~/.config/stripe/config.toml
        # (populated by `stripe login`). No --api-key flag here.
        # Binary path is resolved by `ensure_stripe_cli` (called from
        # the start() override below) and cached for sync access here.
        # ``shlex.quote`` so Windows backslashes survive ProcessService's
        # ``shlex.split`` round-trip (POSIX-mode parser eats raw ``\``).
        from core.config import Settings
        from ._install import stripe_cli_path

        port = int(Settings().port)
        binary = shlex.quote(str(stripe_cli_path() or "stripe"))
        return f"{binary} listen --forward-to http://localhost:{port}/webhook/stripe " f"--print-secret"

    async def start(self) -> Dict[str, Any]:
        """Ensure the CLI binary is downloaded before delegating to the
        framework's daemon-start path (which calls ``build_command``)."""
        from ._install import ensure_stripe_cli

        try:
            path = await ensure_stripe_cli()
            logger.info("[Stripe] daemon start: CLI binary resolved at %s", path)
        except Exception as e:
            logger.warning("[Stripe] daemon start failed at install step: %s", e)
            return {"success": False, "error": f"Stripe CLI install failed: {e}"}
        return await super().start()

    async def has_credential(self) -> bool:
        """The Stripe CLI manages auth state in its own config file —
        that's our 'credential' for the daemon's start gate."""
        return is_logged_in()

    def parse_line(self, stream: str, line: str) -> Optional[WorkflowEvent]:
        if stream == "stderr" and (m := _SECRET_RE.search(line)):
            secret = m.group(0)
            logger.info("[Stripe] whsec_… signing secret detected in daemon stderr — persisting")
            asyncio.create_task(self._persist_secret(secret))
        return None

    async def _persist_secret(self, secret: str) -> None:
        try:
            from services.plugin.deps import get_auth_service

            await get_auth_service().store_api_key(_SECRET_FIELD, secret, models=[])
            logger.info(
                "[Stripe] webhook signing secret persisted (key=%s, len=%d)",
                _SECRET_FIELD,
                len(secret),
            )
        except Exception as e:
            logger.warning("[Stripe] persist secret failed: %s", e)


class StripeWebhookSource(WebhookSource):
    """HTTP receiver for Stripe-forwarded events."""

    type = "stripe.webhook"
    path = "stripe"
    verifier = StripeVerifier
    secret_field = _SECRET_FIELD
    credential = StripeCredential

    async def shape(self, request: Request, body: bytes, payload: dict) -> WorkflowEvent:
        created = payload.get("created")
        try:
            time = datetime.fromtimestamp(int(created), tz=timezone.utc) if created else datetime.now(timezone.utc)
        except (TypeError, ValueError):
            time = datetime.now(timezone.utc)
        account = payload.get("account") or "default"
        return WorkflowEvent(
            id=payload.get("id") or "",
            type=f"stripe.{payload.get('type', 'unknown')}",
            source=f"stripe://{account}",
            time=time,
            data=payload.get("data") or {},
            subject=payload.get("type"),
        )


_listen: Optional[StripeListenSource] = None
_webhook: Optional[StripeWebhookSource] = None


def get_listen_source() -> StripeListenSource:
    global _listen
    if _listen is None:
        _listen = StripeListenSource()
    return _listen


def get_webhook_source() -> StripeWebhookSource:
    global _webhook
    if _webhook is None:
        _webhook = StripeWebhookSource()
    return _webhook
