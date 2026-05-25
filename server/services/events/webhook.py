"""WebhookSource — push source backed by an HTTP POST to /webhook/{path}.

Plugins subclass and declare:

    class MyWebhook(WebhookSource):
        path = "myprovider"
        verifier = MyVerifier        # WebhookVerifier subclass (optional)
        secret_field = "my_webhook_secret"  # extra field on the credential

        async def shape(self, request, body, payload) -> WorkflowEvent: ...

A single edit to ``routers/webhook.py`` consults :data:`WEBHOOK_SOURCES`
before the legacy generic dispatch path. No plugin name is hardcoded
in the router.
"""

from __future__ import annotations

import json
from typing import ClassVar, Dict, Optional, Type

from fastapi import HTTPException, Request

from core.logging import get_logger

from .envelope import WorkflowEvent
from .push import PushEventSource
from .verifiers import WebhookVerifier

logger = get_logger(__name__)


WEBHOOK_SOURCES: Dict[str, "WebhookSource"] = {}


def register_webhook_source(source: "WebhookSource") -> None:
    """Idempotent: same instance for the same path is a no-op; conflicts raise."""
    existing = WEBHOOK_SOURCES.get(source.path)
    if existing is not None and existing is not source:
        raise ValueError(f"Webhook path {source.path!r} already registered to {type(existing).__name__}")
    WEBHOOK_SOURCES[source.path] = source


class WebhookSource(PushEventSource):
    """Base for HTTP-webhook event sources.

    Subclass contract:
        path:           URL fragment under /webhook/
        verifier:       WebhookVerifier subclass (or None to skip verification)
        secret_field:   credential extra-field name holding the signing secret
        shape():        turn the verified request into a WorkflowEvent
    """

    path: ClassVar[str] = ""
    verifier: ClassVar[Optional[Type[WebhookVerifier]]] = None
    secret_field: ClassVar[Optional[str]] = None

    async def _resolve_secret(self) -> Optional[str]:
        if self.secret_field is None or self.credential is None:
            return None
        try:
            secrets = await self.credential.resolve()
        except PermissionError:
            return None
        return secrets.get(self.secret_field)

    async def shape(self, request: Request, body: bytes, payload: dict) -> WorkflowEvent:
        """Override to map the verified payload into a CloudEvent."""
        return WorkflowEvent(
            source=f"webhook://{self.path}",
            type=f"webhook.{self.path}",
            data=payload,
        )

    async def handle(self, request: Request) -> WorkflowEvent:
        """Called by the webhook router. Verifies signature, parses the
        body, shapes the event, dispatches into ``event_waiter``, and
        returns the event for logging / response shaping."""
        body = await request.body()
        if self.verifier is not None:
            secret = await self._resolve_secret()
            if not secret:
                logger.warning(
                    "[%s] no signing secret available; accepting unverified event",
                    self.type or self.path,
                )
            else:
                try:
                    self.verifier.verify(dict(request.headers), body, secret)
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=str(e))

        try:
            payload = json.loads(body.decode() or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise HTTPException(status_code=400, detail=f"invalid JSON body: {e}")

        event = await self.shape(request, body, payload)
        await self.receive(event)

        from services import event_waiter

        event_waiter.dispatch(self.type, event)
        return event
