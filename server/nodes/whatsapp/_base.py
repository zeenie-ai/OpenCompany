"""WhatsApp send + DB business logic (Wave 11.D.9 inlined).

Imported by nodes.whatsapp.whatsapp_send and nodes.whatsapp.whatsapp_db.
RPC dispatch still flows through nodes.whatsapp._service (HTTP to the Go
bridge) — extracting that is a separate refactor, not required for the
plugin migration.
"""

import asyncio
import time
from datetime import datetime
from typing import Dict, Any, List
from core.logging import get_logger

logger = get_logger(__name__)


def _is_invite_link(value: str) -> bool:
    """Check if a channel identifier is an invite link URL."""
    return value.strip().startswith("http://") or value.strip().startswith("https://")


def _resolve_channel_identifier(value: str) -> Dict[str, str]:
    """Route a channel identifier to the correct RPC parameter key.

    For RPC methods that accept both 'jid' and 'invite' params
    (newsletter_info, newsletter_stats).
    """
    if not value:
        return {}
    value = value.strip()
    if _is_invite_link(value):
        return {"invite": value}
    return {"jid": value}


async def _resolve_to_jid(value: str, rpc_call) -> str:
    """Resolve a channel identifier to a JID string.

    If the value is an invite link, calls newsletter_info to look up the JID.
    For RPC methods that only accept 'jid' (newsletter_messages,
    newsletter_follow, newsletter_unfollow, newsletter_mute,
    newsletter_mark_viewed).
    """
    if not value:
        raise ValueError("Channel JID is required")
    value = value.strip()
    if not _is_invite_link(value):
        return value
    # Resolve invite link to JID via newsletter_info
    data = await rpc_call("newsletter_info", {"invite": value})
    if isinstance(data, dict):
        result = data.get("result", data)
        jid = result.get("jid") or result.get("id")
        if jid:
            return jid
    raise ValueError(f"Could not resolve invite link to channel JID: {value}")


# Media types that can be downloaded via the media RPC
MEDIA_MESSAGE_TYPES = frozenset({"image", "video", "audio", "document", "sticker"})


async def _download_single_media(message: Dict[str, Any], rpc_call) -> None:
    """Download media for a single message in-place. Mutates the message dict."""
    message_id = message.get("message_id") or message.get("id")
    if not message_id:
        return
    try:
        data = await rpc_call("media", {"message_id": message_id})
        if isinstance(data, dict):
            result = data.get("result", data)
            if result.get("data"):
                message["media_data"] = result["data"]
                message["media_mime_type"] = result.get("mime_type", "")
            else:
                message["media_error"] = result.get("error", "No media data returned")
        else:
            message["media_error"] = "Unexpected response format"
    except Exception as e:
        message["media_error"] = str(e)


async def _enrich_messages_with_media(messages: List[Dict[str, Any]], rpc_call, max_concurrent: int = 5) -> List[Dict[str, Any]]:
    """Download media for messages that contain media types.

    Uses asyncio.Semaphore for concurrency control and asyncio.gather
    for parallel downloads. Gracefully handles per-message failures.

    Args:
        messages: List of message dicts (mutated in-place)
        rpc_call: The whatsapp_rpc_call function
        max_concurrent: Max parallel media downloads

    Returns:
        The same messages list (mutated with media_data/media_error fields)
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def download_with_semaphore(msg: Dict[str, Any]) -> None:
        async with semaphore:
            await _download_single_media(msg, rpc_call)

    # Filter to only media messages
    media_messages = [msg for msg in messages if msg.get("message_type", msg.get("type", "")) in MEDIA_MESSAGE_TYPES]

    if not media_messages:
        return messages

    logger.info(f"Downloading media for {len(media_messages)} messages (max concurrent: {max_concurrent})")

    await asyncio.gather(*(download_with_semaphore(msg) for msg in media_messages), return_exceptions=True)

    return messages


async def handle_whatsapp_send(node_id: str, node_type: str, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Handle WhatsApp send message node via Go RPC service.

    Supports all message types: text, image, video, audio, document, sticker, location, contact
    Recipients: phone number, group_id, or channel_jid (newsletter)
    Media sources: base64, file path, or URL

    Args:
        node_id: The node ID
        node_type: The node type (whatsappSend)
        parameters: Resolved parameters
        context: Execution context

    Returns:
        Execution result dict
    """
    from nodes.whatsapp._service import handle_whatsapp_send as whatsapp_send_handler

    start_time = time.time()

    try:
        # Determine recipient (snake_case parameters)
        recipient_type = parameters.get("recipient_type", "self")
        message_type = parameters.get("message_type", "text")

        # Determine recipient based on type
        if recipient_type == "self":
            # Self will be resolved by the router using connected phone
            recipient = "self"
        elif recipient_type == "channel":
            recipient = parameters.get("channel_jid")
            if not recipient:
                raise ValueError("Channel JID is required")
            # Validate channel-supported message types
            channel_types = {"text", "image", "video", "audio", "document"}
            if message_type not in channel_types:
                raise ValueError(f"Channels only support: {', '.join(sorted(channel_types))}. Got: {message_type}")
        elif recipient_type == "group":
            recipient = parameters.get("group_id")
            if not recipient:
                raise ValueError("Group ID is required")
        else:  # phone
            recipient = parameters.get("phone")
            if not recipient:
                raise ValueError("Phone number is required")

        # For text messages, validate message content
        if message_type == "text" and not parameters.get("message"):
            raise ValueError("Message content is required for text messages")

        # Convert GFM markdown to WhatsApp-native formatting if enabled
        if message_type == "text" and parameters.get("format_markdown", False):
            from services.markdown_formatter import to_whatsapp

            parameters["message"] = to_whatsapp(parameters["message"])

        # Call WhatsApp Go RPC service via handler - pass full params
        data = await whatsapp_send_handler(parameters)

        success = data.get("success", False)
        if not success:
            raise Exception(data.get("error", "Send failed"))

        # Build informative result based on message type (snake_case output)
        result = {
            "status": "sent",
            "recipient": recipient,
            "recipient_type": recipient_type,
            "message_type": message_type,
            "timestamp": datetime.now().isoformat(),
        }

        # Add type-specific details using match statement
        match message_type:
            case "text":
                msg_content = parameters.get("message", "")
                result["preview"] = msg_content[:100] + "..." if len(msg_content) > 100 else msg_content
            case "image" | "video" | "audio" | "document" | "sticker":
                media_source = parameters.get("media_source", "base64")
                result["media_source"] = media_source
                if parameters.get("caption"):
                    result["caption"] = parameters.get("caption")
                if parameters.get("filename"):
                    result["filename"] = parameters.get("filename")
                if parameters.get("mime_type"):
                    result["mime_type"] = parameters.get("mime_type")
                # For file uploads, include the uploaded filename
                file_param = parameters.get("file_path")
                if isinstance(file_param, dict) and file_param.get("type") == "upload":
                    result["uploaded_file"] = file_param.get("filename")
                    result["mime_type"] = file_param.get("mimeType")
            case "location":
                result["location"] = {
                    "latitude": parameters.get("latitude"),
                    "longitude": parameters.get("longitude"),
                    "name": parameters.get("location_name"),
                    "address": parameters.get("address"),
                }
            case "contact":
                result["contact_name"] = parameters.get("contact_name")

        return {
            "success": success,
            "node_id": node_id,
            "node_type": "whatsappSend",
            "result": result,
            "execution_time": time.time() - start_time,
            "timestamp": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error("WhatsApp send failed", node_id=node_id, error=str(e))
        return {
            "success": False,
            "node_id": node_id,
            "node_type": "whatsappSend",
            "error": str(e),
            "execution_time": time.time() - start_time,
            "timestamp": datetime.now().isoformat(),
        }


async def handle_whatsapp_db(node_id: str, node_type: str, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Handle WhatsApp DB node - query contacts, groups, messages.

    Operations:
    - chat_history: Retrieve messages from history store
    - search_groups: Search groups by name
    - get_group_info: Get group details with participant names
    - get_contact_info: Get full contact info (name, phone, photo)
    - list_contacts: List contacts with saved names
    - check_contacts: Check WhatsApp registration
    - list_channels: List subscribed newsletter channels
    - get_channel_info: Get channel details
    - channel_messages: Get channel message history
    - channel_stats: Get channel subscriber/view stats
    - channel_follow: Follow/subscribe to a channel
    - channel_unfollow: Unfollow/unsubscribe from a channel
    - channel_create: Create a new newsletter channel
    - channel_mute: Mute/unmute a newsletter channel
    - channel_mark_viewed: Mark channel messages as viewed

    Channel operations accept both JIDs (120363...@newsletter) and
    invite links (https://whatsapp.com/channel/...). Methods that support
    both params directly (newsletter_info, newsletter_stats) use
    _resolve_channel_identifier(). Other methods use _resolve_to_jid()
    which resolves invite links to JIDs via newsletter_info first.

    Args:
        node_id: The node ID
        node_type: The node type (whatsappDb)
        parameters: Resolved parameters including operation and operation-specific params
        context: Execution context

    Returns:
        Execution result dict
    """
    from nodes.whatsapp._service import handle_whatsapp_chat_history as whatsapp_chat_history_handler, whatsapp_rpc_call

    start_time = time.time()

    try:
        operation = parameters.get("operation", "chat_history")

        if operation == "chat_history":
            return await _handle_chat_history(node_id, parameters, start_time, whatsapp_chat_history_handler, whatsapp_rpc_call)
        elif operation == "search_groups":
            return await _handle_search_groups(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "get_group_info":
            return await _handle_get_group_info(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "get_contact_info":
            return await _handle_get_contact_info(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "list_contacts":
            return await _handle_list_contacts(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "check_contacts":
            return await _handle_check_contacts(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "list_channels":
            return await _handle_list_channels(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "get_channel_info":
            return await _handle_get_channel_info(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "channel_messages":
            return await _handle_channel_messages(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "channel_stats":
            return await _handle_channel_stats(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "channel_follow":
            return await _handle_channel_follow(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "channel_unfollow":
            return await _handle_channel_unfollow(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "channel_create":
            return await _handle_channel_create(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "channel_mute":
            return await _handle_channel_mute(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "channel_mark_viewed":
            return await _handle_channel_mark_viewed(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "newsletter_react":
            return await _handle_newsletter_react(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "newsletter_live_updates":
            return await _handle_newsletter_live_updates(node_id, parameters, start_time, whatsapp_rpc_call)
        elif operation == "contact_profile_pic":
            return await _handle_contact_profile_pic(node_id, parameters, start_time, whatsapp_rpc_call)
        else:
            raise ValueError(f"Unknown operation: {operation}")

    except Exception as e:
        logger.error("WhatsApp DB failed", node_id=node_id, operation=parameters.get("operation"), error=str(e))
        return {
            "success": False,
            "node_id": node_id,
            "node_type": "whatsappDb",
            "error": str(e),
            "execution_time": time.time() - start_time,
            "timestamp": datetime.now().isoformat(),
        }


async def _handle_chat_history(node_id: str, parameters: Dict[str, Any], start_time: float, handler, rpc_call=None) -> Dict[str, Any]:
    """Handle chat_history operation."""
    chat_type = parameters.get("chat_type", "individual")
    rpc_params: Dict[str, Any] = {}

    if chat_type == "individual":
        phone = parameters.get("phone")
        if not phone:
            raise ValueError("Phone number is required for individual chats")
        rpc_params["phone"] = phone
    else:
        group_id = parameters.get("group_id")
        if not group_id:
            raise ValueError("Group ID is required for group chats")
        rpc_params["group_id"] = group_id

        group_filter = parameters.get("group_filter", "all")
        if group_filter == "contact":
            sender_phone = parameters.get("sender_phone")
            if sender_phone:
                rpc_params["sender_phone"] = sender_phone

    message_filter = parameters.get("message_filter", "all")
    rpc_params["text_only"] = message_filter == "text_only"
    rpc_params["limit"] = parameters.get("limit", 50)
    rpc_params["offset"] = parameters.get("offset", 0)

    data = await handler(rpc_params)

    if not data.get("success", False):
        raise Exception(data.get("error", "Failed to retrieve chat history"))

    messages = data.get("messages", [])
    base_offset = rpc_params.get("offset", 0)
    for i, msg in enumerate(messages):
        msg["index"] = base_offset + i + 1

    # Enrich with media data if requested
    if parameters.get("include_media_data") and rpc_call and messages:
        await _enrich_messages_with_media(messages, rpc_call)

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {
            "operation": "chat_history",
            "messages": messages,
            "total": data.get("total", 0),
            "has_more": data.get("has_more", False),
            "count": len(messages),
            "chat_type": chat_type,
            "timestamp": datetime.now().isoformat(),
        },
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_search_groups(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle search_groups operation."""
    query = parameters.get("query", "")
    limit = parameters.get("limit", 20)  # Default limit to prevent context overflow
    data = await rpc_call("groups", {})

    if not data.get("success", True):
        raise Exception(data.get("error", "Failed to get groups"))

    groups = data if isinstance(data, list) else data.get("result", [])

    # Filter by query if provided
    if query:
        query_lower = query.lower()
        groups = [g for g in groups if query_lower in g.get("name", "").lower()]

    total_found = len(groups)

    # Apply limit to prevent context overflow (51 groups * ~4KB = 200KB+ tokens)
    # Only return essential fields: jid and name
    groups_limited = [{"jid": g.get("jid", ""), "name": g.get("name", "")} for g in groups[:limit]]

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {
            "operation": "search_groups",
            "groups": groups_limited,
            "total": total_found,
            "returned": len(groups_limited),
            "has_more": total_found > limit,
            "query": query,
            "hint": f"Showing {len(groups_limited)} of {total_found} groups. Use a more specific query or get_group_info for details."
            if total_found > limit
            else None,
            "timestamp": datetime.now().isoformat(),
        },
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_get_group_info(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle get_group_info operation."""
    group_id = parameters.get("group_id_for_info") or parameters.get("group_id")
    if not group_id:
        raise ValueError("Group ID is required")

    participant_limit = parameters.get("participant_limit", 50)  # Limit participants to prevent overflow

    data = await rpc_call("group_info", {"group_id": group_id})

    if not data.get("success", True) if isinstance(data, dict) else True:
        raise Exception(data.get("error", "Failed to get group info") if isinstance(data, dict) else "Failed")

    result = data if not isinstance(data, dict) or "result" not in data else data.get("result", data)

    # Limit participants and return only essential fields
    participants = result.get("participants", [])
    total_participants = len(participants)
    participants_limited = [
        {"phone": p.get("phone", ""), "name": p.get("name", ""), "is_admin": p.get("is_admin", False)}
        for p in participants[:participant_limit]
    ]

    # Build limited result
    limited_result = {
        "name": result.get("name", ""),
        "jid": result.get("jid", group_id),
        "participants": participants_limited,
        "total_participants": total_participants,
        "participants_shown": len(participants_limited),
    }

    if total_participants > participant_limit:
        limited_result["hint"] = f"Showing {participant_limit} of {total_participants} participants."

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {"operation": "get_group_info", **limited_result, "timestamp": datetime.now().isoformat()},
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_get_contact_info(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle get_contact_info operation."""
    phone = parameters.get("contact_phone") or parameters.get("phone")
    if not phone:
        raise ValueError("Phone number is required")

    data = await rpc_call("contact_info", {"phone": phone})

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to get contact info"))

    result = data if not isinstance(data, dict) or "result" not in data else data.get("result", data)

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {"operation": "get_contact_info", **result, "timestamp": datetime.now().isoformat()},
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_list_contacts(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle list_contacts operation."""
    query = parameters.get("query", "")
    limit = parameters.get("limit", 50)  # Default limit to prevent context overflow

    data = await rpc_call("contacts", {"query": query})

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to list contacts"))

    result = data if not isinstance(data, dict) or "result" not in data else data.get("result", data)
    contacts = result.get("contacts", []) if isinstance(result, dict) else result

    total_found = len(contacts)

    # Apply limit and return only essential fields: phone, name, jid
    contacts_limited = [{"phone": c.get("phone", ""), "name": c.get("name", ""), "jid": c.get("jid", "")} for c in contacts[:limit]]

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {
            "operation": "list_contacts",
            "contacts": contacts_limited,
            "total": total_found,
            "returned": len(contacts_limited),
            "has_more": total_found > limit,
            "query": query,
            "hint": f"Showing {len(contacts_limited)} of {total_found} contacts. Use a more specific query to narrow results."
            if total_found > limit
            else None,
            "timestamp": datetime.now().isoformat(),
        },
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_check_contacts(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle check_contacts operation."""
    phones_str = parameters.get("phones", "")
    if not phones_str:
        raise ValueError("Phone numbers are required")

    # Parse comma-separated phones
    phones = [p.strip() for p in phones_str.split(",") if p.strip()]
    if not phones:
        raise ValueError("At least one phone number is required")

    data = await rpc_call("contact_check", {"phones": phones})

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to check contacts"))

    results = data if isinstance(data, list) else data.get("result", [])

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {"operation": "check_contacts", "results": results, "total": len(results), "timestamp": datetime.now().isoformat()},
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_contact_profile_pic(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle contact_profile_pic operation - get profile picture for a contact/group."""
    jid = parameters.get("profile_pic_jid") or parameters.get("phone")
    if not jid:
        raise ValueError("JID or phone number is required")

    rpc_params: Dict[str, Any] = {"jid": jid}
    preview = parameters.get("preview", False)
    if preview:
        rpc_params["preview"] = True

    data = await rpc_call("contact_profile_pic", rpc_params)

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to get profile picture"))

    result = data if not isinstance(data, dict) or "result" not in data else data.get("result", data)

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {
            "operation": "contact_profile_pic",
            **(result if isinstance(result, dict) else {"url": result}),
            "timestamp": datetime.now().isoformat(),
        },
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_list_channels(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle list_channels operation - list subscribed newsletter channels."""
    refresh = parameters.get("refresh", False)
    limit = parameters.get("limit", 20)

    rpc_params: Dict[str, Any] = {}
    if refresh:
        rpc_params["refresh"] = True

    data = await rpc_call("newsletters", rpc_params)

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to list channels"))

    channels = data if isinstance(data, list) else data.get("result", [])
    total_found = len(channels)

    # Return essential fields only
    channels_limited = [
        {"jid": c.get("jid", ""), "name": c.get("name", ""), "subscriber_count": c.get("subscriber_count", 0)} for c in channels[:limit]
    ]

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {
            "operation": "list_channels",
            "channels": channels_limited,
            "total": total_found,
            "returned": len(channels_limited),
            "has_more": total_found > limit,
            "hint": f"Showing {len(channels_limited)} of {total_found} channels." if total_found > limit else None,
            "timestamp": datetime.now().isoformat(),
        },
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_get_channel_info(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle get_channel_info operation - get channel details."""
    channel_jid = parameters.get("channel_jid")
    if not channel_jid:
        raise ValueError("Channel JID is required")

    rpc_params: Dict[str, Any] = _resolve_channel_identifier(channel_jid)
    if parameters.get("refresh"):
        rpc_params["refresh"] = True

    data = await rpc_call("newsletter_info", rpc_params)

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to get channel info"))

    result = data if not isinstance(data, dict) or "result" not in data else data.get("result", data)

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {"operation": "get_channel_info", **result, "timestamp": datetime.now().isoformat()},
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_channel_messages(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle channel_messages operation - get channel message history.

    Schema params (newsletter_messages RPC):
      jid (required), count, offset, before, since, until, media_type, search, refresh
    """
    channel_jid = parameters.get("channel_jid")
    jid = await _resolve_to_jid(channel_jid, rpc_call)

    count = parameters.get("channel_count", 20)
    rpc_params: Dict[str, Any] = {"jid": jid, "count": count}

    before_server_id = parameters.get("before_server_id")
    if before_server_id:
        rpc_params["before"] = int(before_server_id)

    # Pagination offset
    msg_offset = parameters.get("message_offset")
    if msg_offset:
        rpc_params["offset"] = int(msg_offset)

    # Date range filters (unix timestamps as strings)
    since = parameters.get("since")
    if since:
        rpc_params["since"] = str(since)

    until = parameters.get("until")
    if until:
        rpc_params["until"] = str(until)

    # Media type filter
    media_type = parameters.get("media_type")
    if media_type and media_type != "all":
        rpc_params["media_type"] = media_type

    # Text search
    search = parameters.get("search")
    if search:
        rpc_params["search"] = search

    # Force refresh bypassing cache
    if parameters.get("refresh"):
        rpc_params["refresh"] = True

    data = await rpc_call("newsletter_messages", rpc_params)

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to get channel messages"))

    messages = data if isinstance(data, list) else data.get("result", [])

    # Enrich with media data if requested
    if parameters.get("include_media_data") and messages:
        await _enrich_messages_with_media(messages, rpc_call)

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {
            "operation": "channel_messages",
            "messages": messages,
            "count": len(messages),
            "channel_jid": channel_jid,
            "timestamp": datetime.now().isoformat(),
        },
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_channel_stats(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle channel_stats operation - get channel subscriber/view stats."""
    channel_jid = parameters.get("channel_jid")
    if not channel_jid:
        raise ValueError("Channel JID is required")

    count = parameters.get("channel_count", 10)
    rpc_params: Dict[str, Any] = {**_resolve_channel_identifier(channel_jid), "count": count}

    data = await rpc_call("newsletter_stats", rpc_params)

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to get channel stats"))

    result = data if not isinstance(data, dict) or "result" not in data else data.get("result", data)

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {"operation": "channel_stats", **result, "channel_jid": channel_jid, "timestamp": datetime.now().isoformat()},
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_channel_follow(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle channel_follow operation - follow/subscribe to a channel."""
    channel_jid = parameters.get("channel_jid")
    jid = await _resolve_to_jid(channel_jid, rpc_call)

    data = await rpc_call("newsletter_follow", {"jid": jid})

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to follow channel"))

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {
            "operation": "channel_follow",
            "channel_jid": channel_jid,
            "status": "followed",
            "timestamp": datetime.now().isoformat(),
        },
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_channel_unfollow(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle channel_unfollow operation - unfollow/unsubscribe from a channel."""
    channel_jid = parameters.get("channel_jid")
    jid = await _resolve_to_jid(channel_jid, rpc_call)

    data = await rpc_call("newsletter_unfollow", {"jid": jid})

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to unfollow channel"))

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {
            "operation": "channel_unfollow",
            "channel_jid": channel_jid,
            "status": "unfollowed",
            "timestamp": datetime.now().isoformat(),
        },
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_channel_create(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle channel_create operation - create a new newsletter channel."""
    channel_name = parameters.get("channel_name")
    if not channel_name:
        raise ValueError("Channel name is required")

    rpc_params: Dict[str, Any] = {"name": channel_name}
    description = parameters.get("channel_description")
    if description:
        rpc_params["description"] = description
    picture = parameters.get("picture")
    if picture:
        rpc_params["picture"] = picture

    data = await rpc_call("newsletter_create", rpc_params)

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to create channel"))

    result = data if not isinstance(data, dict) or "result" not in data else data.get("result", data)

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {"operation": "channel_create", **result, "timestamp": datetime.now().isoformat()},
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_channel_mute(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle channel_mute operation - mute/unmute a newsletter channel."""
    channel_jid = parameters.get("channel_jid")
    jid = await _resolve_to_jid(channel_jid, rpc_call)

    mute = parameters.get("mute", True)
    rpc_params = {"jid": jid, "mute": bool(mute)}

    data = await rpc_call("newsletter_mute", rpc_params)

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to mute/unmute channel"))

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {
            "operation": "channel_mute",
            "channel_jid": channel_jid,
            "muted": mute,
            "status": "muted" if mute else "unmuted",
            "timestamp": datetime.now().isoformat(),
        },
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_channel_mark_viewed(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle channel_mark_viewed operation - mark channel messages as viewed."""
    channel_jid = parameters.get("channel_jid")
    jid = await _resolve_to_jid(channel_jid, rpc_call)

    server_ids_raw = parameters.get("server_ids", "")
    if not server_ids_raw:
        raise ValueError("server_ids is required (comma-separated message IDs)")

    server_ids = [int(sid.strip()) for sid in str(server_ids_raw).split(",") if sid.strip()]
    if not server_ids:
        raise ValueError("At least one server_id is required")

    rpc_params = {"jid": jid, "server_ids": server_ids}

    data = await rpc_call("newsletter_mark_viewed", rpc_params)

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to mark channel as viewed"))

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {
            "operation": "channel_mark_viewed",
            "channel_jid": channel_jid,
            "server_ids": ",".join(str(s) for s in server_ids),
            "status": "marked_viewed",
            "timestamp": datetime.now().isoformat(),
        },
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_newsletter_react(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle newsletter_react operation - react to a channel message."""
    channel_jid = parameters.get("channel_jid")
    jid = await _resolve_to_jid(channel_jid, rpc_call)

    server_id = parameters.get("react_server_id")
    if not server_id:
        raise ValueError("Message server ID is required")

    reaction = parameters.get("reaction", "")

    rpc_params: Dict[str, Any] = {"jid": jid, "server_id": int(server_id), "reaction": reaction}

    data = await rpc_call("newsletter_react", rpc_params)

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to react to channel message"))

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {
            "operation": "newsletter_react",
            "channel_jid": channel_jid,
            "server_id": int(server_id),
            "reaction": reaction,
            "timestamp": datetime.now().isoformat(),
        },
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }


async def _handle_newsletter_live_updates(node_id: str, parameters: Dict[str, Any], start_time: float, rpc_call) -> Dict[str, Any]:
    """Handle newsletter_live_updates operation - subscribe to live view/reaction counts."""
    channel_jid = parameters.get("channel_jid")
    jid = await _resolve_to_jid(channel_jid, rpc_call)

    server_ids_raw = parameters.get("server_ids", "")
    if not server_ids_raw:
        raise ValueError("server_ids is required (comma-separated message IDs)")

    server_ids = [int(sid.strip()) for sid in str(server_ids_raw).split(",") if sid.strip()]
    if not server_ids:
        raise ValueError("At least one server_id is required")

    rpc_params = {"jid": jid, "server_ids": server_ids}

    data = await rpc_call("newsletter_live_updates", rpc_params)

    if isinstance(data, dict) and not data.get("success", True):
        raise Exception(data.get("error", "Failed to subscribe to live updates"))

    result = data if not isinstance(data, dict) or "result" not in data else data.get("result", data)

    return {
        "success": True,
        "node_id": node_id,
        "node_type": "whatsappDb",
        "result": {
            "operation": "newsletter_live_updates",
            "channel_jid": channel_jid,
            **(result if isinstance(result, dict) else {}),
            "timestamp": datetime.now().isoformat(),
        },
        "execution_time": time.time() - start_time,
        "timestamp": datetime.now().isoformat(),
    }
