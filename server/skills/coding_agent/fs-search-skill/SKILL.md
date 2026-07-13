---
name: fs-search-skill
description: Search the filesystem with ls (list directory), glob (pattern match), or grep (search file contents).
allowed-tools: fs_search
metadata:
  author: opencompany
  version: "1.0"
  category: filesystem

---

# FS Search Tool

Search the filesystem: list directories, glob pattern match files, or grep file contents. Uses deepagents filesystem backend.

**Path sandbox:** all paths resolve inside the per-workflow workspace root. Use workspace-relative paths; `..` and `~` segments are rejected, and absolute paths are remapped into the workspace.

## fs_search Tool

### Schema Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| mode | string | No | `ls` (list directory), `glob` (pattern match), `grep` (search contents). Default: `ls` |
| path | string | No | Directory path to search in (default: `.`) |
| pattern | string | If glob/grep | Glob pattern (e.g., `**/*.py`) or grep search text |
| file_filter | string | No | Glob to filter files for grep mode (e.g., `*.py`) |

### Examples

**List directory:**
```json
{"mode": "ls", "path": "/path/to/project"}
```

**Find Python files:**
```json
{"mode": "glob", "path": ".", "pattern": "**/*.py"}
```

**Search for a function:**
```json
{"mode": "grep", "path": ".", "pattern": "def my_function", "file_filter": "*.py"}
```

**Find all config files:**
```json
{"mode": "glob", "path": "/etc", "pattern": "*.conf"}
```

### Response Format

**ls mode:**
```json
{
  "path": ".",
  "entries": [
    {"name": "src", "type": "dir", "size": null},
    {"name": "README.md", "type": "file", "size": 1234}
  ],
  "count": 2
}
```

**glob mode:**
```json
{
  "path": ".",
  "pattern": "**/*.py",
  "matches": [{"path": "src/main.py"}, {"path": "tests/test_main.py"}],
  "count": 2
}
```

**grep mode:**
```json
{
  "path": ".",
  "pattern": "def main",
  "matches": [
    {"path": "src/main.py", "line": 42, "text": "def main():"}
  ],
  "count": 1
}
```

### Guidelines

1. Use `ls` to explore directory structure
2. Use `glob` to find files by name pattern (supports `**` recursive, `*` wildcard, `?` single char)
3. Use `grep` to search file contents (literal text, not regex)
4. Combine `grep` with `file_filter` to limit search scope
5. Results are capped at 500 matches for grep mode
