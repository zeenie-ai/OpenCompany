"""WhatsApp Personal credential stub.

WhatsApp's "personal" connection is QR-paired session-based — the bot
binary (``edgymeow``) handles the WebSocket session lifecycle and there's
no API key / OAuth token to store. The credential class exists only so
``CREDENTIAL_REGISTRY`` can resolve the ``whatsapp`` provider
key (for catalogue icon lookup via
:meth:`services.plugin.credential.Credential.get_icon_path`). It does
NOT implement ``resolve()`` — connection-state queries go through the
``whatsapp_status`` WebSocket flow + the QR-pairing UI, not through
``auth_service.get_*``.

See ``server/config/credential_providers.json`` (entry
``whatsapp``) for the catalogue-level declaration of the QR
pairing UI / status rows / actions. This module's only job is to make
the icon endpoint find ``credential_whatsapp.svg`` co-located
in this folder.
"""

from __future__ import annotations

from services.plugin.credential import Credential


class WhatsAppCredential(Credential):
    """Catalogue stub for the QR-paired WhatsApp Personal connection.

    Not an :class:`ApiKeyCredential` / :class:`OAuth2Credential` —
    WhatsApp's pairing is session-based and managed entirely by the
    :class:`~nodes.whatsapp._runtime.WhatsAppRuntime` supervisor +
    WebSocket handlers. The class is registered into
    ``CREDENTIAL_REGISTRY`` purely to give the catalogue icon endpoint
    (``GET /api/schemas/credentials/whatsapp/icon``) a class
    to call :meth:`get_icon_path` on. ``resolve()`` is never called
    because no node plugin declares this credential in its
    ``credentials`` tuple — the connection is implicit, mediated by
    ``services.whatsapp_service`` and the runtime supervisor.
    """

    id = "whatsapp"
    display_name = "WhatsApp Personal"
    auth = "custom"
    category = "Social"
    docs_url = "https://faq.whatsapp.com/general/about-linked-devices"
