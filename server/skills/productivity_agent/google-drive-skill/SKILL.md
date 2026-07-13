---
name: google-drive-skill
description: Upload, download, list, and share Google Drive files. Supports folders, file search, and permission management.
allowed-tools: "google_drive"
metadata:
  author: opencompany
  version: "1.0"
  category: productivity

---

# Google Drive Skill

Manage Google Drive files - upload, download, list, and share.

## Tool: google_drive

Consolidated Google Drive tool with `operation` parameter.

### Operations

| Operation | Description | Required Fields |
|-----------|-------------|-----------------|
| `upload` | Upload a file to Drive | filename + (file_url or file_content) |
| `download` | Download a file by ID | file_id |
| `list` | List/search files | (none, defaults to root) |
| `share` | Share a file with a user | file_id, email |

### upload - Upload a file

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"upload"` |
| file_url | string | Yes* | URL of file to upload |
| file_content | string | Yes* | Base64 encoded file content |
| filename | string | Yes | Name for the uploaded file |
| folder_id | string | No | Destination folder ID (default: root) |
| mime_type | string | No | File MIME type (auto-detected if not provided) |

*Either `file_url` OR `file_content` is required.

**Example - Upload from URL:**
```json
{
  "operation": "upload",
  "file_url": "https://example.com/report.pdf",
  "filename": "Q4_Report.pdf",
  "folder_id": "1abc123def456"
}
```

### download - Download a file

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"download"` |
| file_id | string | Yes | File ID to download |
| output_format | string | No | `"base64"` or `"url"` (default: base64) |

**Example:**
```json
{
  "operation": "download",
  "file_id": "1xyz789abc"
}
```

### list - List/search files

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"list"` |
| folder_id | string | No | Folder ID (default: root) |
| query | string | No | Drive search query |
| max_results | integer | No | Maximum results (default: 20, max: 1000) |
| file_types | string | No | Filter: `"all"`, `"folder"`, `"document"`, `"spreadsheet"`, `"image"` |
| order_by | string | No | Sort order (default: `"modifiedTime desc"`) |

**Query Syntax:**
- `name contains 'report'` - Files with "report" in name
- `mimeType = 'application/pdf'` - PDF files only
- `modifiedTime > '2024-01-01'` - Recently modified
- `trashed = false` - Exclude trashed files

**Example - Search for PDFs:**
```json
{
  "operation": "list",
  "query": "name contains 'invoice' and mimeType = 'application/pdf'",
  "max_results": 50
}
```

### share - Share a file

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | Must be `"share"` |
| file_id | string | Yes | File ID to share |
| email | string | Yes | Email address to share with |
| role | string | No | `"reader"`, `"commenter"`, `"writer"` (default: reader) |
| send_notification | boolean | No | Send email notification (default: true) |
| message | string | No | Custom notification message |

**Example:**
```json
{
  "operation": "share",
  "file_id": "1xyz789abc",
  "email": "colleague@example.com",
  "role": "writer"
}
```

## Common MIME Types

| Type | MIME Type |
|------|-----------|
| PDF | `application/pdf` |
| Word | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| Excel | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| PowerPoint | `application/vnd.openxmlformats-officedocument.presentationml.presentation` |
| Google Doc | `application/vnd.google-apps.document` |
| Google Sheet | `application/vnd.google-apps.spreadsheet` |
| Folder | `application/vnd.google-apps.folder` |

## Common Workflows

1. **Backup files**: Upload local files to Drive folder
2. **Share report**: Upload file, then share with team
3. **Find files**: List with search query
4. **Download for processing**: Download, process locally, re-upload

## Setup Requirements

1. Connect Drive node to AI Agent's `input-tools` handle
2. Authenticate with Google Workspace in Credentials Modal
3. Ensure Drive API scopes are authorized
