"""Adapter mapping ``socialSend``'s generic shape onto whatsapp params.

This body used to live in ``nodes/social/_base.py`` as ``_send_via_whatsapp``,
which meant the platform-neutral social node carried one platform's parameter
names — and would have grown a branch per platform. It lives here now, so the
knowledge of what whatsapp calls a caption stays inside the whatsapp plugin
and ``handle_social_send`` names no platform at all.

Registered as the ``"whatsapp"`` handler from this package's ``__init__.py``.
The registry contract is documented on
:func:`services.plugin.social_provider_registry.register_social_send_handler`:
the payload is ``socialSend``-shaped, and normalising it is the adapter's job.
"""

from __future__ import annotations

from typing import Any, Dict

from ._service import handle_whatsapp_send

# Message types that carry a media payload rather than a text body. Kept as a
# frozenset rather than an inline tuple so the membership test reads as a
# capability check, not a magic list.
_MEDIA_MESSAGE_TYPES = frozenset({"image", "video", "audio", "document", "sticker"})

# socialSend's media_source -> the whatsapp param that carries the payload for
# that source. A dict rather than an if/elif chain so an unknown source is a
# lookup miss (no key written) instead of a silently-skipped branch.
_MEDIA_SOURCE_FIELDS = {
    "url": ("media_url", "media_url"),
    "base64": ("media_data", "media_data"),
    "file": ("file_path", "file_path"),
}

# Optional passthroughs: copied only when truthy, because whatsappSend
# distinguishes "absent" from "empty string" for these.
_OPTIONAL_MEDIA_FIELDS = ("mime_type", "caption", "filename")


async def social_send_adapter(payload: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    """Translate a ``socialSend`` payload into ``whatsappSend`` params.

    Args:
        payload: ``socialSend``'s own parameters, plus the resolved
            ``recipient`` and the normalised ``recipient_type`` /
            ``message_type`` that :func:`nodes.social._base.handle_social_send`
            guarantees are present.
        ctx: The node's NodeContext.

    Returns:
        whatsapp's native result dict, passed back to the social node
        untouched.
    """
    recipient = payload.get("recipient", "")
    recipient_type = payload.get("recipient_type", "phone")
    message_type = payload.get("message_type", "text")

    params: Dict[str, Any] = {
        "recipient_type": recipient_type,
        "message_type": message_type,
    }

    # whatsapp addresses individuals by phone and everything else by group jid.
    if recipient_type == "phone":
        params["phone"] = recipient
    else:
        params["group_id"] = recipient

    if message_type == "text":
        params["message"] = payload.get("message", "")

    elif message_type in _MEDIA_MESSAGE_TYPES:
        media_source = payload.get("media_source", "url")
        params["media_source"] = media_source
        source_field = _MEDIA_SOURCE_FIELDS.get(media_source)
        if source_field:
            target, source = source_field
            params[target] = payload.get(source, "")
        for field in _OPTIONAL_MEDIA_FIELDS:
            if payload.get(field):
                params[field] = payload[field]

    elif message_type == "location":
        params["latitude"] = payload.get("latitude", 0)
        params["longitude"] = payload.get("longitude", 0)
        params["location_name"] = payload.get("location_name", "")
        params["address"] = payload.get("address", "")

    elif message_type == "contact":
        params["contact_name"] = payload.get("contact_name", "")
        params["vcard"] = payload.get("vcard", "")

    if payload.get("reply_to_message"):
        params["is_reply"] = True
        params["reply_message_id"] = payload.get("reply_message_id", "")

    return await handle_whatsapp_send(params, ctx=ctx)


__all__ = ["social_send_adapter"]
