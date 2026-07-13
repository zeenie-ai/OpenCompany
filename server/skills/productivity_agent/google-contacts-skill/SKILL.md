---
name: google-contacts-skill
description: Create, list, and search Google Contacts. Supports names, emails, phone numbers, and organizations.
allowed-tools: "google_contacts"
metadata:
  author: opencompany
  version: "1.0"
  category: productivity

---

# Google Contacts Skill

Manage Google Contacts - create, list, and search contacts.

## Tool: google_contacts

Consolidated Google Contacts tool with `operation` parameter.

### Operations

| Operation | Description | Required Fields |
|-----------|-------------|-----------------|
| `create` | Create a new contact | first_name |
| `list` | List contacts | (none) |
| `search` | Search contacts by query | query |
| `get` | Get contact details | resource_name |
| `update` | Update existing contact | resource_name |
| `delete` | Delete a contact | resource_name |

### create - Create a new contact

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"create"` |
| first_name | string | Yes | Contact's first name |
| last_name | string | No | Contact's last name |
| email | string | No | Email address |
| phone | string | No | Phone number |
| company | string | No | Company/organization name |
| job_title | string | No | Job title |
| notes | string | No | Notes about contact |

**Example - Basic contact:**
```json
{
  "operation": "create",
  "first_name": "John",
  "last_name": "Smith",
  "email": "john.smith@example.com",
  "phone": "+1-555-123-4567"
}
```

**Example - Full contact:**
```json
{
  "operation": "create",
  "first_name": "Jane",
  "last_name": "Doe",
  "email": "jane.doe@company.com",
  "phone": "+1-555-987-6543",
  "company": "Acme Corporation",
  "job_title": "Senior Engineer",
  "notes": "Met at tech conference 2024"
}
```

### list - List contacts

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"list"` |
| page_size | integer | No | Results per page (default: 20, max: 100) |
| page_token | string | No | Token for next page |
| sort_order | string | No | LAST_MODIFIED_ASCENDING, LAST_MODIFIED_DESCENDING, FIRST_NAME_ASCENDING, LAST_NAME_ASCENDING |

**Example:**
```json
{
  "operation": "list",
  "page_size": 50,
  "sort_order": "FIRST_NAME_ASCENDING"
}
```

### search - Search contacts

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"search"` |
| query | string | Yes | Search query (name or email) |
| page_size | integer | No | Results per page (default: 10, max: 30) |

**Example - Search by name:**
```json
{
  "operation": "search",
  "query": "John"
}
```

**Example - Search by email domain:**
```json
{
  "operation": "search",
  "query": "@company.com"
}
```

### get - Get contact details

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"get"` |
| resource_name | string | Yes | Contact resource name (e.g., "people/c1234567890") |

**Example:**
```json
{
  "operation": "get",
  "resource_name": "people/c1234567890"
}
```

### update - Update a contact

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"update"` |
| resource_name | string | Yes | Contact resource name |
| update_first_name | string | No | New first name |
| update_last_name | string | No | New last name |
| update_email | string | No | New email |
| update_phone | string | No | New phone |
| update_company | string | No | New company |
| update_job_title | string | No | New job title |

**Example:**
```json
{
  "operation": "update",
  "resource_name": "people/c1234567890",
  "update_email": "new.email@example.com",
  "update_job_title": "Director of Engineering"
}
```

### delete - Delete a contact

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"delete"` |
| resource_name | string | Yes | Contact resource name |

**Example:**
```json
{
  "operation": "delete",
  "resource_name": "people/c1234567890"
}
```

## Contact Fields

| Field | Description |
|-------|-------------|
| `resource_name` | Unique identifier (people/cXXX) |
| `display_name` | Full formatted name |
| `first_name` | Given name |
| `last_name` | Family name |
| `email` | Primary email address |
| `phone` | Primary phone number |
| `company` | Organization name |
| `job_title` | Position/title |
| `notes` | Free-form notes |

## Phone Number Formats

Accepted formats:
- `+1-555-123-4567` (international)
- `(555) 123-4567` (US format)
- `555-123-4567` (simple)
- `5551234567` (digits only)

## Common Workflows

1. **Add new contact**: Create with basic info after meeting someone
2. **Find contact**: Search by name to get their details
3. **Build mailing list**: List contacts, filter by company
4. **Update CRM**: Export contacts for business use

## Tips

- Search is case-insensitive
- Partial matches work for names
- Email search matches any part of address
- Use page_token for pagination through large lists

## Setup Requirements

1. Connect Contacts node to AI Agent's `input-tools` handle
2. Authenticate with Google Workspace in Credentials Modal
3. Ensure People API (Contacts) scopes are authorized
