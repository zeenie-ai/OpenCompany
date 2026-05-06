"""Telegram bot-token credential (Wave 11.E.1 — per-domain).

Used by the two telegram plugins in this folder (telegram_send, telegram_receive).
"""

from __future__ import annotations

import httpx

from services.plugin.credential import ApiKeyCredential, ProbeResult


# Telegram embeds the bot token in the URL path (``/bot<token>/getMe``)
# rather than in a header / query / Authorization slot, so the
# declarative ``probe_url`` shape doesn't fit. We override ``_probe``
# directly but reuse :meth:`_handle_probe_response` from the base for
# the 200-with-failure-envelope case.
_TELEGRAM_API = "https://api.telegram.org"


class TelegramCredential(ApiKeyCredential):
    id = "telegram_bot_token"
    display_name = "Telegram Bot"
    category = "Social"
    icon = "asset:telegram"
    key_name = ""  # not used — python-telegram-bot takes the token directly
    key_location = "header"
    extra_fields = ("telegram_owner_chat_id",)
    docs_url = "https://core.telegram.org/bots"

    @classmethod
    async def _probe(cls, api_key: str) -> ProbeResult:
        """Validate via ``GET /bot<token>/getMe``.

        Telegram returns 401 for revoked tokens and 404 for malformed
        tokens; both surface as :class:`httpx.HTTPStatusError` and the
        base :meth:`Credential.validate` translates them via
        :func:`classify_credential_error`. A 200 with ``{ok: false}`` is
        rare but possible (legacy token shapes) and is caught in
        :meth:`_handle_probe_response`.
        """
        url = f"{_TELEGRAM_API}/bot{api_key}/getMe"
        async with httpx.AsyncClient(timeout=cls.probe_timeout_seconds) as client:
            response = await client.get(url)
        response.raise_for_status()
        return cls._handle_probe_response(response)

    @classmethod
    def _handle_probe_response(cls, response: httpx.Response) -> ProbeResult:
        payload = response.json()
        if not payload.get("ok"):
            return ProbeResult(
                valid=False,
                message=payload.get("description", "Telegram rejected the bot token"),
            )
        bot = payload.get("result") or {}
        username = bot.get("username")
        return ProbeResult(
            valid=True,
            message=(
                f"Telegram bot token is valid (@{username})"
                if username
                else "Telegram bot token is valid"
            ),
            extra={
                "bot_id": bot.get("id"),
                "bot_username": username,
                "bot_name": bot.get("first_name"),
            },
        )
