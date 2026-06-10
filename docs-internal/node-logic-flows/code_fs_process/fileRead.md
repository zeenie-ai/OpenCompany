# File Read (`fileRead`)

| Field | Value |
|------|-------|
| **Category** | code_fs_process / filesystem |
| **Backend handler** | [`server/services/handlers/filesystem.py::handle_file_read`](../../../server/services/handlers/filesystem.py) |
| **Backend** | [`deepagents.backends.LocalShellBackend`](https://github.com/langchain-ai/deepagents) (third-party) |
| **Tests** | [`server/tests/nodes/test_code_fs_process.py`](../../../server/tests/nodes/test_code_fs_process.py) |
| **Skill (if any)** | [`server/skills/coding_agent/file-read-skill/SKILL.md`](../../../server/skills/coding_agent/file-read-skill/SKILL.md) |
| **Dual-purpose tool** | yes - tool name `file_read` |

## Purpose

Reads a file within the per-workflow workspace. Delegates to
`LocalShellBackend.read()` from the `deepagents` package. The backend is
instantiated with `virtual_mode=True` and a `root_dir` pinned to
`<DATA_DIR>/workspaces/<workflow_slug>/` (or the node-level `working_directory`
override), so path traversal outside the workspace is rejected by the
backend itself.

All calls run inside `asyncio.to_thread()` because the underlying backend
APIs are synchronous file I/O.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Not consumed by the handler |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `file_path` | string | `""` | yes | - | File path relative to the workspace root |
| `offset` | number | `0` | no | - | 0-indexed starting line |
| `limit` | number | `100` | no | - | Max lines to read (1-10000 in UI) |
| `working_directory` | string | `""` | no | - | Overrides the context workspace; resolved before backend creation |

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Standard envelope payload |
| `output-tool` | object | Same payload when wired to an AI agent |

### Output payload

```ts
{
  content: string;     // File content returned by LocalShellBackend.read()
  file_path: string;   // Echo of the requested path
}
```

## Logic Flow

```mermaid
flowchart TD
  A[handle_file_read] --> B{file_path empty?}
  B -- yes --> E[Return error:<br/>file_path is required]
  B -- no --> C[_get_backend:<br/>root = param.working_directory OR<br/>context.workspace_dir OR<br/>Settings().workspace_base_resolved/default]
  C --> D[os.makedirs root, exist_ok=True]
  D --> F[LocalShellBackend root_dir=root, virtual_mode=True]
  F --> G[to_thread backend.read file_path, offset, limit]
  G -- exception --> H[Return error envelope str e]
  G -- ok --> I[Return success envelope<br/>content, file_path]
```

## Decision Logic

- **Validation**: empty `file_path` -> `"file_path is required"` error.
- **Workspace resolution precedence**: node param `working_directory` >
  `context["workspace_dir"]` > `Settings().workspace_base_resolved/default`.
- **Directory creation**: the resolved root is `os.makedirs(... exist_ok=True)`
  BEFORE the backend is built, so read against a fresh workflow works.
- **`virtual_mode=True`**: the backend rejects absolute paths and `..`
  traversal that escape the root. Violations raise an exception which is
  caught by the broad `except` and returned as a string.
- **Broad `except Exception`**: missing files, permission errors, decoding
  errors all collapse to the same shape - users must parse the `error` string
  to distinguish them.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none.
- **External API calls**: none.
- **File I/O**:
  - `os.makedirs(root, exist_ok=True)` on the resolved workspace root.
  - Reads `<root>/<file_path>` via the backend.
- **Subprocess**: none.

## External Dependencies

- **Credentials**: none.
- **Services**: none.
- **Python packages**: `deepagents` (imported lazily inside `_get_backend`),
  `asyncio`.
- **Environment variables**: `WORKSPACE_BASE_DIR` (read by `core.config.Settings`).

## Edge cases & known limits

- **Errors are strings, not structured**: "file not found", "permission
  denied", "path escapes workspace" all return `{success: false, error: <str>}`
  with no error code.
- **Binary files**: `LocalShellBackend.read()` decodes as text; a binary file
  raises a `UnicodeDecodeError` that surfaces as a vague error string.
- **`offset`/`limit` typed as numbers**: negative offsets are NOT validated by
  the handler - they are forwarded to the backend which may return an empty
  string or raise.
- **`working_directory` can point outside the workspace root**: the handler
  trusts it, so setting `working_directory="/"` effectively disables the
  `virtual_mode` sandbox. Dangerous when the node is exposed as an AI tool.
- **No line-count cap**: `limit` default is 100 but the UI allows up to
  10000; very large files with `limit=10000` block the thread longer.

## Related

- **Skills using this as a tool**: [`file-read-skill/SKILL.md`](../../../server/skills/coding_agent/file-read-skill/SKILL.md)
- **Sibling nodes**: [`fileModify`](./fileModify.md), [`fsSearch`](./fsSearch.md), [`shell`](./shell.md)
- **Architecture docs**: [DESIGN.md](../../DESIGN.md)
