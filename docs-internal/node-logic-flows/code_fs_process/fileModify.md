# File Modify (`fileModify`)

| Field | Value |
|------|-------|
| **Category** | code_fs_process / filesystem |
| **Backend handler** | [`server/nodes/filesystem/file_modify/__init__.py::FileModifyNode.modify`](../../../server/nodes/filesystem/file_modify/__init__.py) (dispatched via `BaseNode.execute()` + `@Operation("modify")`) |
| **Backend** | Native `WorkspaceBackend` plus atomic write helpers in [`server/nodes/filesystem/_backend.py`](../../../server/nodes/filesystem/_backend.py) |
| **Tests** | [`server/tests/nodes/test_code_fs_process.py`](../../../server/tests/nodes/test_code_fs_process.py) |
| **Skill (if any)** | [`server/skills/coding_agent/file-modify-skill/SKILL.md`](../../../server/skills/coding_agent/file-modify-skill/SKILL.md) |
| **Dual-purpose tool** | yes - tool name `file_modify` |

## Purpose

Writes a new file or edits an existing one inside the per-workflow workspace.
Two operations: `write` (create/overwrite) and `edit` (exact string
find-and-replace). Paths are normalized and resolved through
`WorkspaceBackend`, then guarded by a per-path lock. Both operations use a
same-directory temporary file plus `os.replace` so concurrent writes cannot
expose partial content; existing file modes and newline bytes are preserved.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Not consumed by the handler |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `operation` | `write` \| `edit` (Literal) | `write` | no | - | `write` or `edit` |
| `file_path` | string | `""` | yes | - | Target file path |
| `content` | string | `""` | yes (when `operation=write`) | `operation=write` | File content |
| `old_string` | string | `""` | yes (when `operation=edit`) | `operation=edit` | Text to find |
| `new_string` | string | `""` | no | `operation=edit` | Replacement text |
| `replace_all` | boolean | `false` | no | `operation=edit` | Replace every occurrence; when `false` the backend insists `old_string` is unique |

`FileModifyParams` uses `extra="ignore"` — `working_directory` is NOT exposed by
this node (the model drops unknown keys before `model_dump()`).

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Standard envelope payload (node declares only `input-main` / `output-main`; `usable_as_tool=True` exposes the same payload as the `file_modify` tool result) |

### Output payload

Write:
```ts
{ operation: "write"; file_path: string }
```

Edit:
```ts
{ operation: "edit"; file_path: string; occurrences: number }
```

`node_output_schemas.FileModifyOutput` declares `operation` / `file_path` /
`occurrences` (the model's declared `Output` has `written` / `replacements`,
but the operation returns the dicts above; `_OutputBase`/`extra="allow"` keeps
them).

## Logic Flow

```mermaid
flowchart TD
  A[modify] --> B{file_path empty?}
  B -- yes --> E[raise NodeUserError:<br/>file_path is required]
  B -- no --> C[get_backend + normalize_virtual_path]
  C --> D{operation?}
  D -- write --> W[path lock + worker thread:<br/>raise IsADir if dir,<br/>atomic_write_text]
  W -- OSError/ValueError --> Eenv[raise NodeUserError str e]
  W -- result.error --> Eenv
  W -- ok --> Wok[Return dict<br/>operation=write, file_path=result.path]
  D -- edit --> Ei{old_string empty?}
  Ei -- yes --> Ereq[raise NodeUserError:<br/>old_string is required for edit]
  Ei -- no --> Ed[path lock + worker thread:<br/>exact replacement + atomic_write_text]
  Ed -- result.error --> Eenv
  Ed -- ok --> Edok[Return dict<br/>occurrences=result.occurrences]
  D -- other --> Unk[raise NodeUserError:<br/>Unknown operation: <op>]
```

## Decision Logic

- **Validation**:
  - empty `file_path` -> `raise NodeUserError("file_path is required")`.
  - `operation=edit` with empty `old_string` -> `raise NodeUserError("old_string
    is required for edit")`.
  - Anything other than `write` / `edit` -> `raise NodeUserError("Unknown
    operation: <op>")` (Pydantic Literal already constrains it).
- **`write` overwrite handling**: `_do_write` resolves the contained path,
  rejects directories, and atomically replaces or creates the file. `OSError`
  / `ValueError` are caught and re-raised as `NodeUserError`.
- **Backend-level errors**: a non-empty `result.error` from `write`/`edit`
  short-circuits with `raise NodeUserError(result.error)`. Common: non-unique
  `old_string` when `replace_all=False`, path escape with `virtual_mode=True`.
- **`edit` uniqueness constraint**: when `replace_all=False`, the backend
  REQUIRES `old_string` to appear exactly once. Zero or multiple matches
  trigger a backend-level error.
- **`file_path` echoed from `result.path`**: on success the returned
  `file_path` comes from the backend's normalised path, falling back to the
  normalised `file_path` only if `result.path` is empty.

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none.
- **External API calls**: none.
- **File I/O**:
  - `get_backend` ensures the workspace root exists.
  - `write` and `edit` write a same-directory temporary file, flush it, retain
    an existing file's mode, and atomically replace the destination.
- **Subprocess**: none.

## External Dependencies

- **Python packages**: none beyond the standard library.
- **Environment variables**: `WORKSPACE_BASE_DIR`.

## Edge cases & known limits

- **Silent overwrite on `write`**: there is no "fail if exists" flag - every
  `write` call atomically replaces the file.
- **`edit` without a match**: raises `NodeUserError` (backend surfaces the
  message). The handler never distinguishes "not found" from "multiple
  matches" - both are `NodeUserError` strings.
- **No encoding option**: writes are always UTF-8 and preserve caller newline
  bytes (`newline=""`).
- **`working_directory` not exposed**: `extra="ignore"` means the sandbox
  cannot be widened via a node param on this node.
- **`replace_all=true` with empty `new_string`**: effectively deletes every
  occurrence of `old_string`. No safeguard.
- **Symlink-race containment**: POSIX uses root-anchored directory descriptors,
  `O_NOFOLLOW`, and descriptor-relative atomic rename. Windows rejects
  observed reparse points and revalidates directory, temporary-file, and
  target identities before and after `os.replace`.
- **Windows residual**: Python's standard library has no handle-relative
  Windows rename. A hostile process with permission to swap a checked parent
  in the final check-to-`os.replace` interval can redirect the replacement;
  post-validation detects this but cannot safely roll it back.

## Related

- **Skills using this as a tool**: [`file-modify-skill/SKILL.md`](../../../server/skills/coding_agent/file-modify-skill/SKILL.md)
- **Sibling nodes**: [`fileRead`](./fileRead.md), [`fsSearch`](./fsSearch.md), [`shell`](./shell.md)
