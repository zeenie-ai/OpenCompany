---
name: google-gmail-skill
description: Send, search, and read Gmail emails. Supports composing emails with attachments, searching by query, and reading email content.
allowed-tools: "google_gmail"
metadata:
  author: opencompany
  version: "1.0"
  category: productivity

---

# Gmail Skill

Send, search, and read emails using Gmail API.

## Tool: google_gmail

Consolidated Gmail tool with `operation` parameter.

### Operations

| Operation | Description | Required Fields |
|-----------|-------------|-----------------|
| `send` | Send an email | to, subject, body |
| `search` | Search emails by query | query |
| `read` | Read email by ID | message_id |

### send - Send an email

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"send"` |
| to | string | Yes | Recipient email address |
| subject | string | Yes | Email subject line |
| body | string | Yes | Email body (plain text or HTML) |
| cc | string | No | CC recipients (comma-separated) |
| bcc | string | No | BCC recipients (comma-separated) |
| body_type | string | No | `"text"` or `"html"` (default: text) |

**Example - Send plain text email:**
```json
{
  "operation": "send",
  "to": "recipient@example.com",
  "subject": "Meeting Tomorrow",
  "body": "Hi,\n\nJust a reminder about our meeting tomorrow at 2pm.\n\nBest regards"
}
```

**Example - Send HTML email:**
```json
{
  "operation": "send",
  "to": "recipient@example.com",
  "subject": "Weekly Report",
  "body": "<h1>Weekly Report</h1><p>Here are the highlights...</p>",
  "body_type": "html"
}
```

### search - Search emails

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"search"` |
| query | string | Yes | Gmail search query |
| max_results | integer | No | Maximum results (default: 10, max: 100) |
| include_body | boolean | No | Fetch full message body (default: false) |

**Query Syntax Examples:**
- `from:sender@example.com` - Emails from specific sender
- `to:recipient@example.com` - Emails to specific recipient
- `subject:meeting` - Emails with "meeting" in subject
- `has:attachment` - Emails with attachments
- `is:unread` - Unread emails
- `after:2024/01/01` - Emails after date
- `before:2024/12/31` - Emails before date
- `label:important` - Emails with label
- `"exact phrase"` - Exact phrase match
- `from:boss@company.com is:unread` - Combine multiple filters

**Example:**
```json
{
  "operation": "search",
  "query": "from:client@example.com has:attachment after:2024/01/01",
  "max_results": 20
}
```

### read - Read email content

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"read"` |
| message_id | string | Yes | Gmail message ID from search results |
| format | string | No | `"full"`, `"minimal"`, `"raw"`, `"metadata"` (default: full) |

**Example:**
```json
{
  "operation": "read",
  "message_id": "abc123"
}
```

## Common Workflows

1. **Check unread emails**: Search with `is:unread`, then read important ones
2. **Find emails from someone**: Search with `from:email@example.com`
3. **Reply to email**: Read the email first, then send with same subject prefixed with "Re:"
4. **Forward email**: Read email, send to new recipient with "Fwd:" prefix

## Setup Requirements

1. Connect Gmail node to AI Agent's `input-tools` handle
2. Authenticate with Google Workspace in Credentials Modal
3. Ensure Gmail API scopes are authorized
