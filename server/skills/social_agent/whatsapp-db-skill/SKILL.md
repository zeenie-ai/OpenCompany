---
name: whatsapp-db-skill
description: Query WhatsApp database for contacts, groups, channels, and chat history. Look up contact info, search groups, manage channels, retrieve messages.
allowed-tools: whatsapp_db
metadata:
  author: opencompany
  version: "1.0"
  category: messaging

---

# WhatsApp Database Tool

Query WhatsApp database for contacts, groups, channels (newsletters), and message history.

## How It Works

This skill provides instructions for the **WhatsApp DB** tool node. Connect the **WhatsApp DB** node to Zeenie's `input-tools` handle to enable database queries.

## whatsapp_db Tool

Query contacts, groups, channels, and chat history.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Operation type (see below) |
| chat_type | string | For chat_history | `"individual"` or `"group"` |
| phone | string | Varies | Phone number (for chat_history individual, get_contact_info) |
| group_id | string | Varies | Group JID (for chat_history group, get_group_info) |
| query | string | No | Search query (for search_groups, list_contacts) |
| limit | int | No | Max results (varies by operation) |
| offset | int | No | Pagination offset (for chat_history) |
| message_filter | string | No | `"all"` or `"text_only"` (for chat_history) |
| group_filter | string | No | `"all"` or `"contact"` (for group chat_history) |
| sender_phone | string | No | Filter by sender (when group_filter="contact") |
| phones | string | For check_contacts | Comma-separated phone numbers |
| participant_limit | int | No | Max participants (for get_group_info, 1-100) |
| channel_jid | string | For channel ops | Newsletter JID or invite link URL (e.g., `120363198765432101@newsletter` or `https://whatsapp.com/channel/...`) |
| refresh | boolean | No | Force refresh from server (for list_channels, get_channel_info, channel_messages) |
| channel_count | int | No | Number of items to fetch (for channel_messages, channel_stats) |
| before_server_id | int | No | Pagination cursor (for channel_messages) |
| message_offset | int | No | Skip this many messages (for channel_messages) |
| since | string | No | Unix timestamp - messages after this time (for channel_messages) |
| until | string | No | Unix timestamp - messages before this time (for channel_messages) |
| media_type | string | No | Filter by media type: image, video, audio, document, sticker (for channel_messages) |
| search | string | No | Text search within messages (for channel_messages) |
| include_media_data | boolean | No | Download base64 media data for media messages (for chat_history, channel_messages) |
| channel_name | string | For channel_create | Name of the new channel |
| channel_description | string | No | Description (for channel_create) |
| picture | string | No | Base64-encoded profile picture (for channel_create) |
| mute | boolean | For channel_mute | True to mute, false to unmute |
| server_ids | string | For mark_viewed/live_updates | Comma-separated message server IDs |
| react_server_id | int | For newsletter_react | Server ID of message to react to |
| reaction | string | For newsletter_react | Reaction emoji (empty to remove) |
| profile_pic_jid | string | For contact_profile_pic | JID or phone number |
| preview | boolean | No | Get low-res preview (for contact_profile_pic) |

### Operations

| Operation | Description | Required Fields |
|-----------|-------------|-----------------|
| `list_contacts` | List contacts with saved names | query (optional), limit |
| `get_contact_info` | Get full contact details | phone |
| `search_groups` | Search groups by name | query, limit |
| `get_group_info` | Get group details with participants | group_id |
| `chat_history` | Retrieve message history | chat_type, phone/group_id |
| `check_contacts` | Check WhatsApp registration | phones |
| `list_channels` | List subscribed newsletter channels | refresh (optional), limit |
| `get_channel_info` | Get channel details | channel_jid |
| `channel_messages` | Get channel messages with filters (date, media type, search) | channel_jid, channel_count |
| `channel_stats` | Get channel subscriber/view stats | channel_jid |
| `channel_follow` | Follow/subscribe to a channel | channel_jid |
| `channel_unfollow` | Unfollow/unsubscribe from a channel | channel_jid |
| `channel_create` | Create a new newsletter channel | channel_name |
| `channel_mute` | Mute or unmute a channel | channel_jid, mute |
| `channel_mark_viewed` | Mark channel messages as viewed | channel_jid, server_ids |
| `newsletter_react` | React to a channel message | channel_jid, react_server_id, reaction |
| `newsletter_live_updates` | Subscribe to live view/reaction counts | channel_jid, server_ids |
| `contact_profile_pic` | Get contact/group profile picture | profile_pic_jid |

### Limits

| Operation | Default | Max |
|-----------|---------|-----|
| chat_history | 50 | 500 |
| search_groups | 20 | 50 |
| list_contacts | 50 | 100 |
| get_group_info participants | 50 | 100 |
| list_channels | 20 | 50 |
| channel_messages | 20 | 100 |
| channel_stats | 10 | 100 |
| channel_mute | - | - |
| channel_mark_viewed | - | - |

**Important:** Always use small limits and specific queries to avoid context overflow.

## Operation Examples

### list_contacts

Find contacts by name.

```json
{
  "operation": "list_contacts",
  "query": "mom",
  "limit": 10
}
```

**Response:**
```json
{
  "success": true,
  "operation": "list_contacts",
  "contacts": [
    {"phone": "919876543210", "name": "Mom", "jid": "919876543210@s.whatsapp.net"}
  ],
  "total": 1
}
```

### get_contact_info

Get full contact details for sending/replying.

```json
{
  "operation": "get_contact_info",
  "phone": "919876543210"
}
```

**Response:**
```json
{
  "success": true,
  "operation": "get_contact_info",
  "phone": "919876543210",
  "name": "Mom",
  "jid": "919876543210@s.whatsapp.net",
  "profile_picture": "https://..."
}
```

### search_groups

Search groups by name.

```json
{
  "operation": "search_groups",
  "query": "family",
  "limit": 10
}
```

**Response:**
```json
{
  "success": true,
  "operation": "search_groups",
  "groups": [
    {"group_id": "120363123456789@g.us", "name": "Family Group", "participant_count": 5}
  ],
  "total": 1
}
```

### get_group_info

Get group details with participant list.

```json
{
  "operation": "get_group_info",
  "group_id": "120363123456789@g.us",
  "participant_limit": 20
}
```

**Response:**
```json
{
  "success": true,
  "operation": "get_group_info",
  "name": "Family Group",
  "jid": "120363123456789@g.us",
  "participants": [
    {"phone": "919876543210", "name": "Mom", "is_admin": true},
    {"phone": "919876543211", "name": "Dad", "is_admin": false}
  ],
  "total_participants": 5
}
```

### chat_history (Individual)

Get messages from an individual chat.

```json
{
  "operation": "chat_history",
  "chat_type": "individual",
  "phone": "919876543210",
  "limit": 20
}
```

### chat_history (Group)

Get messages from a group chat.

```json
{
  "operation": "chat_history",
  "chat_type": "group",
  "group_id": "120363123456789@g.us",
  "message_filter": "text_only",
  "limit": 50
}
```

**Response:**
```json
{
  "success": true,
  "operation": "chat_history",
  "messages": [
    {
      "index": 1,
      "message_id": "ABC123",
      "sender": "919876543210@s.whatsapp.net",
      "sender_name": "Mom",
      "text": "Hello!",
      "timestamp": "2025-01-30T12:00:00",
      "is_from_me": false
    }
  ],
  "total": 50,
  "has_more": true
}
```

### check_contacts

Check if phone numbers have WhatsApp.

```json
{
  "operation": "check_contacts",
  "phones": "1234567890,0987654321"
}
```

**Response:**
```json
{
  "success": true,
  "operation": "check_contacts",
  "results": [
    {"phone": "1234567890", "registered": true, "jid": "1234567890@s.whatsapp.net"},
    {"phone": "0987654321", "registered": false}
  ]
}
```

### list_channels

List subscribed newsletter channels.

```json
{
  "operation": "list_channels",
  "limit": 10
}
```

**Response:**
```json
{
  "success": true,
  "operation": "list_channels",
  "channels": [
    {"jid": "120363198765432101@newsletter", "name": "Tech Updates", "subscriber_count": 5000}
  ],
  "total": 3
}
```

### get_channel_info

Get channel details.

```json
{
  "operation": "get_channel_info",
  "channel_jid": "120363198765432101@newsletter"
}
```

### channel_messages

Get channel message history.

```json
{
  "operation": "channel_messages",
  "channel_jid": "120363198765432101@newsletter",
  "channel_count": 10
}
```

### channel_follow

Follow/subscribe to a channel.

```json
{
  "operation": "channel_follow",
  "channel_jid": "120363198765432101@newsletter"
}
```

### channel_create

Create a new newsletter channel.

```json
{
  "operation": "channel_create",
  "channel_name": "My Channel",
  "channel_description": "A channel about interesting topics"
}
```

### channel_mute

Mute or unmute a newsletter channel.

```json
{
  "operation": "channel_mute",
  "channel_jid": "120363198765432101@newsletter",
  "mute": true
}
```

### channel_mark_viewed

Mark channel messages as viewed by server ID.

```json
{
  "operation": "channel_mark_viewed",
  "channel_jid": "120363198765432101@newsletter",
  "server_ids": "100, 101, 102"
}
```

### get_channel_info (with invite link)

Get channel details using an invite link instead of JID.

```json
{
  "operation": "get_channel_info",
  "channel_jid": "https://whatsapp.com/channel/0029Va12345abcdef"
}
```

### channel_messages (with filters)

Get channel messages filtered by media type and date range.

```json
{
  "operation": "channel_messages",
  "channel_jid": "120363198765432101@newsletter",
  "channel_count": 20,
  "media_type": "image",
  "since": "1704067200",
  "include_media_data": true
}
```

### channel_messages (text search)

Search channel messages by text content.

```json
{
  "operation": "channel_messages",
  "channel_jid": "120363198765432101@newsletter",
  "search": "announcement",
  "channel_count": 10
}
```

### newsletter_react

React to a channel message with an emoji.

```json
{
  "operation": "newsletter_react",
  "channel_jid": "120363198765432101@newsletter",
  "react_server_id": 42,
  "reaction": "\ud83d\udc4d"
}
```

### contact_profile_pic

Get a contact's profile picture.

```json
{
  "operation": "contact_profile_pic",
  "profile_pic_jid": "919876543210"
}
```

## Common Workflows

### Find and message someone by name

1. Use `list_contacts` with query to find the person
2. Get their phone number from the result
3. Use `whatsapp_send` tool with the phone number

### Get recent messages from a contact

1. Use `chat_history` with chat_type="individual"
2. Set appropriate limit for desired history depth

### Find and message a group

1. Use `search_groups` to find the group by name
2. Get the group_id from the result
3. Use `whatsapp_send` tool with recipient_type="group"

### See who's in a group

1. Use `get_group_info` with the group_id
2. Set participant_limit to control output size

## Guidelines

1. **Phone numbers**: Always use without + prefix, just digits
2. **Group IDs**: JID format ending in `@g.us`
3. **Limits**: Use small limits to avoid context overflow
4. **Queries**: Be specific to narrow results
5. **Pagination**: Use offset to page through chat_history
6. **Channel JIDs**: JID format ending in `@newsletter`, or pass an invite link URL
7. **Invite links**: `channel_jid` accepts both JIDs and `https://whatsapp.com/channel/...` URLs

## Error Responses

```json
{
  "error": "Phone number is required for chat_type='individual'"
}
```

```json
{
  "error": "group_id is required for get_group_info"
}
```

## Setup Requirements

1. Connect the **WhatsApp DB** node to Zeenie's `input-tools` handle
2. Ensure WhatsApp is connected (green status indicator in Credentials)
