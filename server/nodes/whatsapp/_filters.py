"""WhatsApp event-trigger filter builder (Wave 11.I, milestone K).

Moved verbatim from ``services/event_waiter.build_whatsapp_filter``.
The plugin's ``__init__.py`` self-registers via
``event_waiter.register_filter_builder("whatsappReceive", build_filter)``;
the central event_waiter dispatch table no longer hardcodes this
plugin's filter.

The filter shape mirrors the event payload shipped by the Go RPC's
``handleIncomingMessage()`` (see ``service.go``). Snake_case
parameters throughout — matches the plugin Params with no camelCase
aliases.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict

logger = logging.getLogger(__name__)


def build_filter(params: Dict) -> Callable[[Dict], bool]:
    """Build filter function for WhatsApp messages.

    Based on Go RPC ``handleIncomingMessage()`` event fields:

    - ``message_id`` -- unique message ID
    - ``sender`` -- Sender JID (may be LID for groups)
    - ``sender_phone`` -- RESOLVED phone number (LID already resolved)
    - ``chat_id`` -- Chat JID (same as sender for DMs, group JID for groups)
    - ``timestamp`` -- message timestamp
    - ``is_from_me`` -- true if sent by connected account
    - ``is_group`` -- true if message is in a group chat
    - ``message_type`` -- text / image / video / audio / document /
      sticker / location / contact / contacts
    - ``text`` -- text content (for text messages)
    - ``is_forwarded`` -- true if message is forwarded
    - ``forwarding_score`` -- forwarding count
    - ``group_info`` -- present for group messages, with ``group_jid``
      / ``sender_jid`` / ``sender_phone`` / ``sender_name``
    """
    msg_type = params.get('message_type_filter', 'all')
    sender_filter = params.get('filter', 'all')
    contact_phone = params.get('phone_number', '')
    group_id = params.get('group_id', '')
    sender_number = params.get('sender_number', '')
    keywords = [
        k.strip().lower()
        for k in params.get('keywords', '').split(',')
        if k.strip()
    ]
    ignore_own = params.get('ignore_own_messages', True)
    forwarded_filter = params.get('forwarded_filter', 'all')

    logger.debug(
        "[WhatsAppFilter] Built: type=%s, filter=%s, group_id=%r, forwarded=%s",
        msg_type, sender_filter, group_id, forwarded_filter,
    )

    def matches(m: Dict) -> bool:
        msg_chat_id = m.get('chat_id', '')
        is_group = m.get('is_group', False)
        group_info = m.get('group_info', {})

        # Use sender_phone directly -- Go RPC already resolves LIDs.
        # For group messages, prefer group_info.sender_phone, fall back
        # to root sender_phone.
        if is_group:
            sender_phone = group_info.get('sender_phone', '') or m.get('sender_phone', '')
        else:
            sender_phone = m.get('sender_phone', '')

        # Fallback: extract phone from sender JID if sender_phone absent.
        if not sender_phone:
            sender = m.get('sender', '')
            sender_phone = sender.split('@')[0] if '@' in sender else sender

        # Message type filter (schema field: message_type).
        if msg_type != 'all' and m.get('message_type') != msg_type:
            return False

        # Sender filter -- for ``contact`` filter, use actual phone.
        if sender_filter == 'self':
            # Self-chat (notes-to-self) only -- must be from me AND in
            # a chat with myself.
            if not m.get('is_from_me'):
                return False
            chat_id = m.get('chat_id', '')
            sender = m.get('sender', '')
            if chat_id != sender:
                return False

        if sender_filter == 'any_contact':
            if is_group:
                return False

        if sender_filter == 'contact':
            if contact_phone not in sender_phone:
                return False

        if sender_filter == 'group':
            if not is_group:
                return False
            if msg_chat_id != group_id:
                return False
            # Optional: filter by specific sender within group.
            if sender_number:
                if sender_number not in sender_phone:
                    return False

        if sender_filter == 'channel':
            if not msg_chat_id.endswith('@newsletter'):
                return False
            channel_jid = params.get('channel_jid', '')
            if channel_jid and msg_chat_id != channel_jid:
                return False

        if sender_filter == 'keywords':
            text = (m.get('text') or '').lower()
            if not any(kw in text for kw in keywords):
                return False

        # Ignore own messages -- but not when filtering for ``self``.
        if ignore_own and sender_filter != 'self' and m.get('is_from_me'):
            return False

        is_forwarded = m.get('is_forwarded', False)
        if forwarded_filter == 'only_forwarded' and not is_forwarded:
            return False
        if forwarded_filter == 'ignore_forwarded' and is_forwarded:
            return False

        logger.debug("[WhatsAppFilter] Matched message from %s", sender_phone)
        return True

    return matches
