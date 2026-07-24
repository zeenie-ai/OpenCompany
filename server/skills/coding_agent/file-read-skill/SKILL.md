---
name: file-read-skill
description: Read file contents with line numbers and pagination support.
allowed-tools: file_read
metadata:
  author: opencompany
  version: "1.0"
  category: filesystem

---

# File Read Tool

Read file contents with line-numbered output and pagination. Uses the workspace-contained native filesystem backend.

**Path sandbox:** all paths resolve inside the per-workflow workspace root. Use workspace-relative paths (e.g. `reports/data.csv`); `..` and `~` segments are rejected, and absolute paths are remapped into the workspace. Use `fs_search` with `mode: "ls"` to discover what exists.

## file_read Tool

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| file_path | string | Yes | Path to the file to read |
| offset | int | No | Line number to start from, 0-indexed (default: 0) |
| limit | int | No | Maximum lines to read (default: 100, max: 10000) |

### Examples

**Read a file:**
```json
{"file_path": "/path/to/file.py"}
```

**Read with pagination:**
```json
{"file_path": "/path/to/large_file.py", "offset": 100, "limit": 50}
```

### Response Format

```json
{
  "content": "1\tline one\n2\tline two\n...",
  "file_path": "/path/to/file.py",
  "encoding": "utf-8"
}
```

### Guidelines

1. Use offset/limit for large files instead of reading everything
2. File content is returned with line numbers (tab-separated)
3. Binary files return base64-encoded content
