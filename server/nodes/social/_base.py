"""Social unified-messaging business logic (Wave 11.D.9 inlined).

socialReceive normalizes messages from platform-specific triggers into
a unified format. socialSend routes outbound messages to the correct
platform (WhatsApp, Telegram, Discord, Slack, Signal, SMS, Webchat,
Email, Matrix, Teams).

Imported by :class:`nodes.social.social_receive.SocialReceiveNode` and
:class:`nodes.social.social_send.SocialSendNode`. Calls into
``nodes.whatsapp._service`` for the WhatsApp bridge stay unchanged — moving
them out is a separate refactor.
"""

import time
from datetime import datetime
from typing import Dict, Any
from core.logging import get_logger

logger = get_logger(__name__)

# Platform-specific field mappings to unified format
PLATFORM_FIELD_MAPPINGS = {
    'whatsapp': {
        'sender': 'sender',
        'sender_phone': 'sender_phone',
        'sender_name': 'push_name',
        'chat_id': 'chat_id',
        'text': 'text',
        'message_type': 'message_type',
        'is_group': 'is_group',
        'is_from_me': 'is_from_me',
        'timestamp': 'timestamp',
        'message_id': 'message_id',
    },
    # Add more platform mappings as they are integrated
    'telegram': {},
    'discord': {},
    'slack': {},
    'signal': {},
    'sms': {},
    'webchat': {},
    'email': {},
    'matrix': {},
    'teams': {},
}


def _normalize_to_unified_format(
    input_data: Dict[str, Any],
    source_channel: str
) -> Dict[str, Any]:
    """Normalize platform-specific message data to unified social format.

    Args:
        input_data: Raw message data from platform trigger
        source_channel: Source platform identifier

    Returns:
        Unified message format matching inbound-message.schema.json
    """
    import uuid

    # Generate message_id if not present (e.g., chatTrigger doesn't have one)
    message_id = input_data.get('message_id') or str(uuid.uuid4())[:8]

    # For webchat, use session_id as sender/chat_id if not provided
    session_id = input_data.get('session_id', 'default')
    default_sender = f"webchat_{session_id}" if source_channel == 'webchat' else ''
    default_chat_id = session_id if source_channel == 'webchat' else ''

    # Start with unified structure
    unified = {
        'message_id': message_id,
        'channel': source_channel,
        'sender': input_data.get('sender') or default_sender,
        'sender_phone': input_data.get('sender_phone', ''),
        'sender_name': input_data.get('sender_name') or input_data.get('push_name') or ('User' if source_channel == 'webchat' else ''),
        'sender_username': input_data.get('sender_username', ''),
        'chat_id': input_data.get('chat_id') or default_chat_id,
        'chat_title': input_data.get('chat_title', ''),
        'chat_type': _determine_chat_type(input_data),
        'message_type': input_data.get('message_type', 'text'),
        'text': input_data.get('text') or input_data.get('message', ''),
        'timestamp': input_data.get('timestamp', datetime.now().isoformat()),
        'is_group': input_data.get('is_group', False),
        'is_from_me': input_data.get('is_from_me', False),
        'is_forwarded': input_data.get('is_forwarded', False),
        'is_bot': input_data.get('is_bot', False),
        'is_admin': input_data.get('is_admin', False),
        'thread_id': input_data.get('thread_id'),
        'account_id': input_data.get('account_id'),
        'session_id': input_data.get('session_id'),
    }

    # Copy group_info if present
    if input_data.get('group_info'):
        unified['group_info'] = input_data['group_info']

    # Copy media if present
    if input_data.get('media'):
        unified['media'] = input_data['media']

    # Copy location if present
    if input_data.get('location'):
        unified['location'] = input_data['location']

    # Copy contact if present
    if input_data.get('contact'):
        unified['contact'] = input_data['contact']

    # Copy poll if present
    if input_data.get('poll'):
        unified['poll'] = input_data['poll']

    # Copy reaction if present
    if input_data.get('reaction'):
        unified['reaction'] = input_data['reaction']

    # Copy reply_to if present
    if input_data.get('reply_to'):
        unified['reply_to'] = input_data['reply_to']

    # Copy mentions if present
    if input_data.get('mentions'):
        unified['mentions'] = input_data['mentions']

    # Keep raw data for platform-specific access
    unified['raw'] = input_data.get('raw', input_data)

    return unified


def _determine_chat_type(data: Dict[str, Any]) -> str:
    """Determine chat type from message data."""
    if data.get('chat_type'):
        return data['chat_type']
    if data.get('is_group'):
        return 'group'
    return 'dm'


def _apply_filters(
    message: Dict[str, Any],
    parameters: Dict[str, Any]
) -> bool:
    """Check if message passes configured filters.

    Args:
        message: Unified message data
        parameters: Node filter parameters

    Returns:
        True if message passes all filters
    """
    # Channel filter
    channel_filter = parameters.get('channel_filter', 'all')
    if channel_filter != 'all' and message.get('channel') != channel_filter:
        return False

    # Message type filter
    type_filter = parameters.get('message_type_filter', 'all')
    if type_filter != 'all' and message.get('message_type') != type_filter:
        return False

    # Sender filter
    sender_filter = parameters.get('sender_filter', 'all')

    if sender_filter == 'any_contact':
        # Non-group messages only
        if message.get('is_group'):
            return False

    elif sender_filter == 'contact':
        # Specific contact
        contact_phone = parameters.get('contact_phone', '')
        if contact_phone and message.get('sender_phone') != contact_phone:
            return False

    elif sender_filter == 'group':
        # Specific group
        group_id = parameters.get('group_id', '')
        if group_id and message.get('chat_id') != group_id:
            return False

    elif sender_filter == 'keywords':
        # Keyword matching
        keywords_str = parameters.get('keywords', '')
        if keywords_str:
            keywords = [k.strip().lower() for k in keywords_str.split(',')]
            text = (message.get('text') or '').lower()
            if not any(kw in text for kw in keywords):
                return False

    # Ignore own messages
    if parameters.get('ignore_own_messages', True) and message.get('is_from_me'):
        return False

    # Ignore bots
    if parameters.get('ignore_bots', False) and message.get('is_bot'):
        return False

    return True


async def handle_social_receive(
    node_id: str,
    node_type: str,
    parameters: Dict[str, Any],
    context: Dict[str, Any],
    outputs: Dict[str, Any] = None,
    source_nodes: list = None
) -> Dict[str, Any]:
    """Handle Social Receive node - normalizes and filters incoming messages.

    This node connects to platform-specific triggers (WhatsApp Receive, etc.)
    and normalizes their output into unified social format.

    Args:
        node_id: The node ID
        node_type: The node type (socialReceive)
        parameters: Node parameters (filters)
        context: Execution context
        outputs: Connected upstream node outputs (passed from node_executor)
        source_nodes: Source node info with id, type, label

    Returns:
        Normalized message in unified format, or filtered out result
    """
    start_time = time.time()

    # Use outputs passed from node_executor, fall back to context for backwards compatibility
    if outputs is None:
        outputs = context.get('outputs', {})
    if source_nodes is None:
        source_nodes = []

    logger.debug(f"[handle_social_receive] Processing with {len(outputs)} outputs from {len(source_nodes)} source nodes")

    try:
        input_data = None
        source_channel = 'unknown'

        # Find message data from connected outputs
        # Outputs are keyed by node type (e.g., 'whatsappReceive') and contain raw data directly
        for source_node in source_nodes:
            source_type = source_node.get('type', '')
            source_output = outputs.get(source_type)

            if source_output and isinstance(source_output, dict):
                # Check if this output has message data (message_id, text, or message field)
                if source_output.get('message_id') or source_output.get('text') or source_output.get('message'):
                    input_data = source_output
                    # Detect source channel from node type
                    if 'whatsapp' in source_type.lower():
                        source_channel = 'whatsapp'
                    elif 'telegram' in source_type.lower():
                        source_channel = 'telegram'
                    elif 'discord' in source_type.lower():
                        source_channel = 'discord'
                    elif 'slack' in source_type.lower():
                        source_channel = 'slack'
                    elif 'chat' in source_type.lower():
                        source_channel = 'webchat'
                    elif source_output.get('channel'):
                        source_channel = source_output['channel']
                    logger.debug(f"[handle_social_receive] Found message from {source_type}, channel={source_channel}")
                    break

        # Fallback: iterate through outputs by key if source_nodes didn't match
        if not input_data:
            for upstream_key, upstream_output in outputs.items():
                if isinstance(upstream_output, dict):
                    if upstream_output.get('message_id') or upstream_output.get('text') or upstream_output.get('message'):
                        input_data = upstream_output
                        # Detect source channel from key or data
                        if 'whatsapp' in upstream_key.lower() or upstream_output.get('sender', '').endswith('@s.whatsapp.net'):
                            source_channel = 'whatsapp'
                        elif 'chat' in upstream_key.lower():
                            source_channel = 'webchat'
                        elif upstream_output.get('channel'):
                            source_channel = upstream_output['channel']
                        logger.debug(f"[handle_social_receive] Found message via fallback from key={upstream_key}")
                        break

        if not input_data:
            logger.warning(f"[handle_social_receive] No message data found. outputs keys={list(outputs.keys())}, source_nodes={source_nodes}")
            return {
                "success": False,
                "node_id": node_id,
                "node_type": "socialReceive",
                "error": f"No message data received from upstream trigger. Available sources: {list(outputs.keys())}",
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat()
            }

        # Normalize to unified format
        unified_message = _normalize_to_unified_format(input_data, source_channel)

        # Apply filters
        if not _apply_filters(unified_message, parameters):
            return {
                "success": True,
                "node_id": node_id,
                "node_type": "socialReceive",
                "result": None,  # Filtered out
                "filtered": True,
                "reason": "Message did not pass filters",
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat()
            }

        # Build result with four outputs:
        # - message: Text content for LLM input
        # - media: Media data and metadata
        # - contact: Sender/contact info
        # - metadata: Message-related metadata
        media_data = unified_message.get('media', {})
        result = {
            # Output 1: Just the text message
            "message": unified_message.get('text', ''),

            # Output 2: Media data and related metadata
            "media": {
                "url": media_data.get('url', '') if media_data else '',
                "type": media_data.get('type', '') if media_data else '',
                "mimetype": media_data.get('mimetype', '') if media_data else '',
                "caption": media_data.get('caption', '') if media_data else '',
                "size": media_data.get('size', 0) if media_data else 0,
                "thumbnail": media_data.get('thumbnail', '') if media_data else '',
                "filename": media_data.get('filename', '') if media_data else '',
            } if media_data else {},

            # Output 3: Contact/sender info
            "contact": {
                "sender": unified_message.get('sender'),
                "sender_phone": unified_message.get('sender_phone'),
                "sender_name": unified_message.get('sender_name'),
                "sender_username": unified_message.get('sender_username'),
                "channel": unified_message.get('channel'),
                "is_group": unified_message.get('is_group'),
                "group_info": unified_message.get('group_info'),
                "chat_title": unified_message.get('chat_title'),
            },

            # Output 4: Message-related metadata
            "metadata": {
                "message_id": unified_message.get('message_id'),
                "chat_id": unified_message.get('chat_id'),
                "timestamp": unified_message.get('timestamp'),
                "message_type": unified_message.get('message_type'),
                "is_from_me": unified_message.get('is_from_me'),
                "is_forwarded": unified_message.get('is_forwarded'),
                "reply_to": unified_message.get('reply_to'),
                "thread_id": unified_message.get('thread_id'),
            },

            # Backwards compatibility: include full data at top level
            **unified_message
        }

        return {
            "success": True,
            "node_id": node_id,
            "node_type": "socialReceive",
            "result": result,
            "execution_time": time.time() - start_time,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error("Social receive failed", node_id=node_id, error=str(e))
        return {
            "success": False,
            "node_id": node_id,
            "node_type": "socialReceive",
            "error": str(e),
            "execution_time": time.time() - start_time,
            "timestamp": datetime.now().isoformat()
        }


async def handle_social_send(
    node_id: str,
    node_type: str,
    parameters: Dict[str, Any],
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle Social Send node - routes outbound messages to platform.

    This node sends messages to the configured platform (WhatsApp, Telegram, etc.)
    Also works as an AI Agent tool.

    Args:
        node_id: The node ID
        node_type: The node type (socialSend)
        parameters: Message parameters (channel, recipient, content)
        context: Execution context

    Returns:
        Send result with message_id and status
    """
    start_time = time.time()

    try:
        channel = parameters.get('channel', 'whatsapp')
        recipient_type = parameters.get('recipient_type', 'phone')
        message_type = parameters.get('message_type', 'text')

        # Get recipient based on type
        recipient = None
        if recipient_type == 'phone':
            recipient = parameters.get('phone')
        elif recipient_type == 'group':
            recipient = parameters.get('group_id')
        elif recipient_type == 'channel':
            recipient = parameters.get('channel_id')
        elif recipient_type == 'user':
            recipient = parameters.get('user_id')
        elif recipient_type == 'chat':
            recipient = parameters.get('chat_id')

        if not recipient:
            raise ValueError(f"Recipient ({recipient_type}) is required")

        # Route to platform-specific handler
        if channel == 'whatsapp':
            result = await _send_via_whatsapp(parameters, recipient, recipient_type, message_type)
        else:
            # Stub for other platforms - will be implemented as they are integrated
            result = {
                "success": False,
                "error": f"Platform '{channel}' is not yet implemented",
                "message_id": None
            }

        if not result.get('success'):
            raise Exception(result.get('error', 'Send failed'))

        return {
            "success": True,
            "node_id": node_id,
            "node_type": "socialSend",
            "result": {
                "success": True,
                "message_id": result.get('message_id'),
                "channel": channel,
                "recipient": recipient,
                "recipient_type": recipient_type,
                "message_type": message_type,
                "timestamp": datetime.now().isoformat()
            },
            "execution_time": time.time() - start_time,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error("Social send failed", node_id=node_id, error=str(e))
        return {
            "success": False,
            "node_id": node_id,
            "node_type": "socialSend",
            "error": str(e),
            "execution_time": time.time() - start_time,
            "timestamp": datetime.now().isoformat()
        }


async def _send_via_whatsapp(
    parameters: Dict[str, Any],
    recipient: str,
    recipient_type: str,
    message_type: str
) -> Dict[str, Any]:
    """Route message to WhatsApp via the social-provider registry.

    Maps socialSend parameters to whatsappSend parameters, then
    dispatches through :func:`services.plugin.social_provider_registry.
    get_social_send_handler`. The whatsapp plugin self-registers as the
    ``"whatsapp"`` platform from its own ``__init__.py`` — no
    cross-plugin import from this module.
    """
    from services.plugin.social_provider_registry import get_social_send_handler

    whatsapp_send_handler = get_social_send_handler("whatsapp")
    if whatsapp_send_handler is None:
        raise RuntimeError(
            "social: 'whatsapp' platform not registered. "
            "Check that nodes/whatsapp/__init__.py is imported at startup "
            "and calls register_social_send_handler('whatsapp', ...). "
        )

    # Map socialSend params to whatsappSend format
    whatsapp_params = {
        'recipient_type': recipient_type,
        'message_type': message_type,
    }

    # Set recipient
    if recipient_type == 'phone':
        whatsapp_params['phone'] = recipient
    else:
        whatsapp_params['group_id'] = recipient

    # Map message content based on type
    if message_type == 'text':
        whatsapp_params['message'] = parameters.get('message', '')

    elif message_type in ('image', 'video', 'audio', 'document', 'sticker'):
        media_source = parameters.get('media_source', 'url')
        whatsapp_params['media_source'] = media_source

        if media_source == 'url':
            whatsapp_params['media_url'] = parameters.get('media_url', '')
        elif media_source == 'base64':
            whatsapp_params['media_data'] = parameters.get('media_data', '')
        elif media_source == 'file':
            whatsapp_params['file_path'] = parameters.get('file_path', '')

        if parameters.get('mime_type'):
            whatsapp_params['mime_type'] = parameters['mime_type']
        if parameters.get('caption'):
            whatsapp_params['caption'] = parameters['caption']
        if parameters.get('filename'):
            whatsapp_params['filename'] = parameters['filename']

    elif message_type == 'location':
        whatsapp_params['latitude'] = parameters.get('latitude', 0)
        whatsapp_params['longitude'] = parameters.get('longitude', 0)
        whatsapp_params['location_name'] = parameters.get('location_name', '')
        whatsapp_params['address'] = parameters.get('address', '')

    elif message_type == 'contact':
        whatsapp_params['contact_name'] = parameters.get('contact_name', '')
        whatsapp_params['vcard'] = parameters.get('vcard', '')

    # Reply options
    if parameters.get('reply_to_message'):
        whatsapp_params['is_reply'] = True
        whatsapp_params['reply_message_id'] = parameters.get('reply_message_id', '')

    # Call WhatsApp handler
    return await whatsapp_send_handler(whatsapp_params)
