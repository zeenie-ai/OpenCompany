---
name: file-modify-skill
description: Write new files or edit existing files with string replacement.
allowed-tools: file_modify
metadata:
  author: opencompany
  version: "1.0"
  category: filesystem

---

# File Modify Tool

Write new files or edit existing files with exact string replacement. Uses the workspace-contained native filesystem backend.

**Path sandbox:** all paths resolve inside the per-workflow workspace root. Use workspace-relative paths (e.g. `reports/summary.md`); `..` and `~` segments are rejected, and absolute paths are remapped into the workspace.

## file_modify Tool

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| operation | string | Yes | `write` (create or wholesale-replace the file) or `edit` (surgical find-and-replace inside an existing file) |
| file_path | string | Yes | Path to the file |
| content | string | If write | Full file content to write — overwrites any existing file at this path |
| old_string | string | If edit | Exact text to find and replace |
| new_string | string | If edit | Replacement text |
| replace_all | boolean | No | Replace all occurrences (default: false, old_string must be unique) |

### Examples

**Write a new file:**
```json
{
  "operation": "write",
  "file_path": "/path/to/new_file.py",
  "content": "print('hello world')"
}
```

**Edit an existing file:**
```json
{
  "operation": "edit",
  "file_path": "/path/to/file.py",
  "old_string": "def old_name():",
  "new_string": "def new_name():"
}
```

**Replace all occurrences:**
```json
{
  "operation": "edit",
  "file_path": "/path/to/file.py",
  "old_string": "TODO",
  "new_string": "DONE",
  "replace_all": true
}
```

### Response Format

**Write:**
```json
{"operation": "write", "file_path": "/path/to/file.py"}
```

**Edit:**
```json
{"operation": "edit", "file_path": "/path/to/file.py", "occurrences": 1}
```

### Guidelines

1. Use `write` to create a new file or replace an existing file's contents wholesale. Provide the full new contents in `content`; any existing file at `file_path` is replaced.
2. Use `edit` for surgical string replacement inside an existing file — it is the right choice when you want to change a small portion of a file rather than rewrite the whole thing.
3. For edit: `old_string` must be unique in the file unless `replace_all` is true.
4. Prefer `edit` over `write` when changing only part of an existing file — it produces a smaller diff and is less destructive.
5. Read the file first before editing to ensure correct context.
