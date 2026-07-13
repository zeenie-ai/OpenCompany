---
name: whatsapp-send-skill
description: Send WhatsApp messages to contacts, groups, or channels. Supports text, images, videos, audio, documents, stickers, locations, and contacts.
allowed-tools: whatsapp_send
metadata:
  author: opencompany
  version: "1.0"
  category: messaging

---

# WhatsApp Send Tool

Send messages to WhatsApp contacts, groups, or newsletter channels.

## How It Works

This skill provides instructions for the **WhatsApp Send** tool node. Connect the **WhatsApp Send** node to Zeenie's `input-tools` handle to enable message sending.

## whatsapp_send Tool

Send messages to individual contacts, groups, or channels.

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| recipient_type | string | Yes | `"phone"` for individual, `"group"` for group chat, or `"channel"` for newsletter |
| phone | string | If phone | Phone number without + prefix (e.g., `1234567890`) |
| group_id | string | If group | Group JID (e.g., `123456789@g.us`) |
| channel_jid | string | If channel | Newsletter JID (e.g., `120363198765432101@newsletter`) |
| message_type | string | Yes | Message type (see below) |
| message | string | If text | Text message content |
| media_url | string | If media | URL for image/video/audio/document/sticker |
| caption | string | No | Caption for media messages |
| latitude | float | If location | Latitude coordinate |
| longitude | float | If location | Longitude coordinate |
| location_name | string | No | Display name for location |
| address | string | No | Address text for location |
| contact_name | string | If contact | Contact display name |
| vcard | string | If contact | vCard 3.0 format string |

### Message Types

| Type | Required Fields | Description |
|------|-----------------|-------------|
| `text` | message | Plain text message |
| `image` | media_url | Image file (JPG, PNG, GIF) |
| `video` | media_url | Video file (MP4) |
| `audio` | media_url | Audio file (MP3, OGG, WAV) |
| `document` | media_url | Any file type |
| `sticker` | media_url | Sticker image (WebP) |
| `location` | latitude, longitude | GPS coordinates |
| `contact` | contact_name, vcard | Contact card |

### Examples

**Send text message to contact:**
```json
{
  "recipient_type": "phone",
  "phone": "1234567890",
  "message_type": "text",
  "message": "Hello! How are you?"
}
```

**Send image with caption:**
```json
{
  "recipient_type": "phone",
  "phone": "1234567890",
  "message_type": "image",
  "media_url": "https://example.com/photo.jpg",
  "caption": "Check out this photo!"
}
```

**Send to group:**
```json
{
  "recipient_type": "group",
  "group_id": "123456789012345678@g.us",
  "message_type": "text",
  "message": "Hello everyone!"
}
```

**Send to channel (newsletter):**
```json
{
  "recipient_type": "channel",
  "channel_jid": "120363198765432101@newsletter",
  "message_type": "text",
  "message": "Channel update: new features released!"
}
```

> **Channel limitations:** Channels only support `text`, `image`, `video`, `audio`, and `document` message types. Sticker, location, and contact are NOT supported. You must be an admin/owner of the channel to send.

**Send video:**
```json
{
  "recipient_type": "phone",
  "phone": "1234567890",
  "message_type": "video",
  "media_url": "https://example.com/video.mp4",
  "caption": "Check this video"
}
```

**Send document:**
```json
{
  "recipient_type": "phone",
  "phone": "1234567890",
  "message_type": "document",
  "media_url": "https://example.com/report.pdf",
  "caption": "Here's the report"
}
```

**Send location:**
```json
{
  "recipient_type": "phone",
  "phone": "1234567890",
  "message_type": "location",
  "latitude": 37.7749,
  "longitude": -122.4194,
  "location_name": "San Francisco",
  "address": "San Francisco, CA, USA"
}
```

**Send contact card:**
```json
{
  "recipient_type": "phone",
  "phone": "1234567890",
  "message_type": "contact",
  "contact_name": "John Doe",
  "vcard": "BEGIN:VCARD\nVERSION:3.0\nFN:John Doe\nTEL:+1234567890\nEND:VCARD"
}
```

### Response Format

```json
{
  "success": true,
  "recipient": "1234567890",
  "recipient_type": "phone",
  "message_type": "text",
  "details": {
    "status": "sent",
    "preview": "Hello! How are you?",
    "timestamp": "2025-01-30T12:00:00"
  }
}
```

### Error Response

```json
{
  "error": "Phone number is required for recipient_type='phone'"
}
```

## Guidelines

1. **Phone numbers**: Always use without + prefix, just digits (e.g., `919876543210`)
2. **Group IDs**: Use JID format ending in `@g.us` (e.g., `123456789@g.us`)
3. **Channel JIDs**: Use JID format ending in `@newsletter` (e.g., `120363198765432101@newsletter`)
4. **Media URLs**: Must be publicly accessible URLs (https://)
5. **vCard format**: Use vCard 3.0 specification for contact cards
6. **Message length**: Text messages can be up to 4096 characters
7. **Media size**: Check WhatsApp limits for media file sizes

## Common Use Cases

- Send automated notifications to contacts
- Forward messages to groups
- Share media files
- Send location information
- Share contact information

## Setup Requirements

1. Connect the **WhatsApp Send** node to Zeenie's `input-tools` handle
2. Ensure WhatsApp is connected (green status indicator in Credentials)
3. The recipient must have WhatsApp installed
