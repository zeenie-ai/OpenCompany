"""Telegram event filter builder.

Moved from ``services/event_waiter.build_telegram_filter`` so the
generic ``event_waiter`` module no longer imports anything telegram-
specific.  The builder is published into
``event_waiter.FILTER_BUILDERS`` via ``register_filter_builder()`` from
this package's ``__init__.py``.

Lazy ``owner_chat_id`` lookup goes through the local
:class:`TelegramService` -- no import path leaks outside the plugin
folder.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict

logger = logging.getLogger(__name__)


def build_telegram_filter(params: Dict) -> Callable[[Dict], bool]:
    """Build a filter function for Telegram messages.

    Sender filter values:
        all, self, private, group, supergroup, channel,
        specific_chat, specific_user, keywords

    Legacy parameters (back-compat): chatTypeFilter, chat_id, from_user,
    keywords, ignoreBots.

    Event data fields originate from
    :meth:`TelegramService._format_message`.
    """
    sender_filter = params.get("sender_filter", "")

    # Legacy fallback
    if not sender_filter:
        chat_type_filter = params.get("chat_type_filter", "all")
        old_chat_id = params.get("chat_id", "")
        old_from_user = params.get("from_user", "")
        old_keywords = params.get("keywords", "")

        if old_chat_id:
            sender_filter = "specific_chat"
        elif old_from_user:
            sender_filter = "specific_user"
        elif old_keywords:
            sender_filter = "keywords"
        elif chat_type_filter != "all":
            sender_filter = chat_type_filter
        else:
            sender_filter = "all"

    content_type_filter = params.get("content_type_filter", "all")
    chat_id_filter = params.get("chat_id", "")
    from_user_filter = params.get("from_user", "")
    keywords = [k.strip().lower() for k in params.get("keywords", "").split(",") if k.strip()]
    ignore_bots = params.get("ignore_bots", True)
    owner_chat_id = params.get("_owner_chat_id")

    logger.debug(f"[TelegramFilter] Built: sender={sender_filter}, " f"content_type={content_type_filter}, owner_chat_id={owner_chat_id}")

    def _get_owner_chat_id() -> object:
        """Lazy lookup from the local service -- handles owner detected
        after the filter was built (first private message captures it)."""
        if owner_chat_id:
            return owner_chat_id
        try:
            from ._service import get_telegram_service

            return get_telegram_service().owner_chat_id
        except Exception:
            return None

    def matches(m: Dict) -> bool:
        if content_type_filter != "all":
            if m.get("content_type", "") != content_type_filter:
                return False

        if sender_filter == "self":
            current_owner = _get_owner_chat_id()
            if not current_owner:
                logger.debug("[TelegramFilter] Rejecting: owner_chat_id not available")
                return False
            if str(m.get("from_id", "")) != str(current_owner):
                return False

        elif sender_filter in ("private", "group", "supergroup", "channel"):
            if m.get("chat_type", "") != sender_filter:
                return False

        elif sender_filter == "specific_chat":
            if chat_id_filter and str(m.get("chat_id", "")) != str(chat_id_filter):
                return False

        elif sender_filter == "specific_user":
            if from_user_filter and str(m.get("from_id", "")) != str(from_user_filter):
                return False

        elif sender_filter == "keywords":
            if keywords:
                text = (m.get("text") or "").lower()
                if not any(kw in text for kw in keywords):
                    return False

        # Ignore bot messages (skip for 'self' filter)
        if sender_filter != "self" and ignore_bots and m.get("is_bot", False):
            return False

        logger.debug(f"[TelegramFilter] Matched message from " f"{m.get('from_username', m.get('from_id'))}")
        return True

    return matches
