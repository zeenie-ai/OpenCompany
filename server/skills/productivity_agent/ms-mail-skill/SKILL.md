---
name: ms-mail-skill
description: Send, read, search, reply to, and handle attachments of Outlook email via Microsoft Graph — for your own mailbox or a shared mailbox. Compose messages, list recent mail, search by text, reply/reply-all, and list or download file attachments (e.g. PDFs) for parsing.
allowed-tools: "ms_mail"
metadata:
  author: opencompany
  version: "1.2"
  category: productivity

---

# Outlook Mail Skill

Send, read, search, reply to, and handle attachments of email using the Microsoft Graph API (Outlook / Microsoft 365).

## Tool: ms_mail

Consolidated Outlook Mail tool with an `operation` parameter.

### Operations

| Operation | Description | Required Fields |
|-----------|-------------|-----------------|
| `send` | Send an email | to, subject, body |
| `read` | Read a message by ID, or list recent mail when no ID is given | (message_id optional) |
| `search` | Search messages by text | query |
| `reply` | Reply to a message | reply_message_id, comment |
| `list_attachments` | List a message's attachments (metadata only, no download) | message_id |
| `download_attachments` | Download file attachments into the workspace | message_id |

Every `read`/`search` result includes a `has_attachments` boolean per message —
check it before calling `list_attachments` / `download_attachments`.

### send - Send an email

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"send"` |
| to | string | Yes | Recipient address(es), comma-separated |
| subject | string | Yes | Email subject line |
| body | string | Yes | Email body (plain text or HTML) |
| cc | string | No | CC recipients (comma-separated) |
| bcc | string | No | BCC recipients (comma-separated) |
| body_type | string | No | `"text"` or `"html"` (default: text) |

**Example - Send plain text email:**
```json
{
  "operation": "send",
  "to": "alice@contoso.com",
  "subject": "Meeting Tomorrow",
  "body": "Hi,\n\nJust a reminder about our meeting tomorrow at 2pm.\n\nBest regards"
}
```

**Example - Send to multiple recipients with CC:**
```json
{
  "operation": "send",
  "to": "alice@contoso.com, bob@contoso.com",
  "cc": "manager@contoso.com",
  "subject": "Weekly Report",
  "body": "<h1>Weekly Report</h1><p>Highlights...</p>",
  "body_type": "html"
}
```

### read - Read a message or list recent mail

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"read"` |
| message_id | string | No | Message ID to fetch (with body). Omit to list recent messages. |
| max_results | integer | No | Max messages when listing (default: 10, max: 100) |

Each returned message includes `has_attachments` (boolean). When `true`, use
`list_attachments` / `download_attachments` with that `message_id`.

**Example - Read a specific message:**
```json
{
  "operation": "read",
  "message_id": "AAMkAGI2..."
}
```

**Example - List the 20 most recent messages:**
```json
{
  "operation": "read",
  "max_results": 20
}
```

### search - Search messages

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"search"` |
| query | string | Yes | Free-text search (Microsoft Graph `$search`) |
| search_max_results | integer | No | Max results (default: 10, max: 100) |

Microsoft Graph search matches across subject, body, sender, and recipients.
Use natural keywords (e.g. `invoice`, `from:jane quarterly plan`); it does not
use Gmail-style operators.

**Example:**
```json
{
  "operation": "search",
  "query": "quarterly plan",
  "search_max_results": 20
}
```

### reply - Reply to a message

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"reply"` |
| reply_message_id | string | Yes | ID of the message to reply to (from search/read) |
| comment | string | Yes | The reply text |
| reply_all | boolean | No | Reply to all recipients (default: false) |

**Example:**
```json
{
  "operation": "reply",
  "reply_message_id": "AAMkAGI2...",
  "comment": "Thanks - looks good to me.",
  "reply_all": true
}
```

### list_attachments - List a message's attachments (metadata only)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"list_attachments"` |
| message_id | string | Yes | The message whose attachments to list |
| include_inline | boolean | No | Include inline body images (e.g. signature logos). Default false. |

Returns `attachments: [{attachment_id, name, content_type, size, is_inline, kind}]`
and `count`. `kind` is `"file"` for a downloadable file attachment; other values
(`itemAttachment`, `referenceAttachment`) are shown so you know they cannot be
downloaded as bytes. Inline images are excluded unless `include_inline: true`.
This op does NOT download bytes — it is a cheap metadata lookup.

**Example:**
```json
{
  "operation": "list_attachments",
  "message_id": "AAMkAGI2..."
}
```

### download_attachments - Download file attachments into the workspace

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"download_attachments"` |
| message_id | string | Yes | The message whose attachments to download |
| attachment_id | string | No | Download only this attachment. Omit to download all file attachments. |
| include_inline | boolean | No | Include inline body images. Default false. |

Saves each file attachment into the workflow workspace and returns:
- `attachments: [{filename, path, mime_type, size, ref}]` — `path` is the absolute
  file path on disk (feed it to the Document Parser's `file_path`).
- `download_dir` — the absolute directory the files landed in (feed it to the
  Document Parser's `input_dir` to parse all of them).
- `count`, and `skipped: [{name, reason}]` for anything not downloaded
  (inline images, item/reference attachments, or files over the size limit).

Only real file attachments are downloaded. Item/reference attachments and (by
default) inline images are skipped. Bytes are never returned inline — you get a
path/reference, not the file contents.

**Example - download all file attachments:**
```json
{
  "operation": "download_attachments",
  "message_id": "AAMkAGI2..."
}
```

**Example - download one specific attachment:**
```json
{
  "operation": "download_attachments",
  "message_id": "AAMkAGI2...",
  "attachment_id": "AAMkAGI2...=="
}
```

## Parsing an attachment (e.g. a PDF)

`ms_mail` downloads the file; it does NOT extract text. To read a PDF's contents,
pair it with the **Document Parser** node on the canvas:

1. `ms_mail` `download_attachments` → returns `download_dir` (and per-file `path`).
2. Document Parser with `input_dir = {{msMail.download_dir}}` and
   `file_pattern = *.pdf` (or `file_path = {{msMail.attachments[0].path}}` for one file).
3. Document Parser returns `documents[].content` — the extracted text.

## Common Workflows

1. **Triage recent mail**: `read` with no ID to list recent messages, then `read` a specific `message_id` for full content.
2. **Find a thread**: `search` by keyword, take the `message_id`, then `reply`.
3. **Send an update**: `send` to one or more recipients, optionally as HTML.
4. **Process an attachment**: on a message with `has_attachments: true`, call
   `download_attachments`, then run the Document Parser over `download_dir` to get the text.

## Shared mailboxes

Every operation accepts an optional `mailbox` field. Leave it empty to use the
signed-in user's own mailbox (default). Set it to a shared/other mailbox address
(e.g. `support@contoso.com`) to operate on that mailbox instead — the node then
calls `/users/{address}/…` rather than `/me/…`.

Requirements for a shared mailbox:
- The signed-in account must have **Full Access** on the mailbox (and **Send As**
  / **Send on Behalf** to send from it).
- The `Mail.ReadWrite.Shared` / `Mail.Send.Shared` scopes must be granted
  (reconnect in the Credentials Modal after these are added to re-consent).

**Example - read a shared mailbox:**
```json
{
  "operation": "read",
  "mailbox": "support@contoso.com",
  "max_results": 20
}
```

## Setup Requirements

1. Connect the Outlook Mail node to an AI Agent's `input-tools` handle.
2. Authenticate with Microsoft Graph in the Credentials Modal (Work/School account).
3. Ensure the Mail.Send and Mail.ReadWrite scopes are granted (plus the
   `.Shared` variants if you use the `mailbox` field for a shared mailbox).
