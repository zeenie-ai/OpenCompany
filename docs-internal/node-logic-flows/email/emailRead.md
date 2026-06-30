# Email Read (`emailRead`)

| Field | Value |
|------|-------|
| **Category** | email / tool (dual-purpose) |
| **Backend handler** | [`server/nodes/email/email_read/__init__.py`](../../../server/nodes/email/email_read/__init__.py) — dispatched via `BaseNode.execute()` -> `@Operation("query")` -> `EmailService.read` ([`_service.py`](../../../server/nodes/email/_service.py)) |
| **Tests** | [`server/tests/nodes/test_email.py`](../../../server/tests/nodes/test_email.py) |
| **Skill (if any)** | none shipped |
| **Dual-purpose tool** | yes - connect to `input-tools` as the node's own name |

## Purpose

Read and manage IMAP mail via the `himalaya` CLI: list envelopes, search,
fetch a full message, list folders, move/delete, and add/remove flags. One
node exposes seven operations selected by the `operation` parameter.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream data; not consumed directly - all inputs come from `parameters` |

## Parameters

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `provider` | options | `gmail` | no | - | Preset key in `email_providers.json` |
| `operation` | options | `list` | no | - | `list` / `search` / `read` / `folders` / `move` / `delete` / `flag` |
| `folder` | string | `INBOX` | no | `operation: ['list','search']` | IMAP folder name |
| `query` | string | `""` | no (`search`) | `operation: ['search']` | Himalaya search expression, e.g. `from:x subject:y` |
| `message_id` | string | `""` | no (used by `read`/`move`/`delete`/`flag`) | `operation: ['read','move','delete','flag']` | IMAP message UID |
| `target_folder` | string | `""` | no (`move`) | `operation: ['move']` | Destination folder for `move` |
| `flag` | options | `""` | no | `operation: ['flag']` | One of `Seen` / `Answered` / `Flagged` / `Draft` / `Deleted` (empty default) |
| `flag_action` | options | `add` | no | `operation: ['flag']` | `add` or `remove` |
| `limit` | number | `20` | no | `operation: ['list','search']` | Max envelopes per page (1-500) |
| `page` | number | `1` | no | `operation: ['list','search']` | 1-based page number |
| `page_size` | number | `20` | no | `operation: ['list','search']` | Items per page (1-500); overrides `limit` when paginating |
| `offset` | number | `0` | no | `operation: ['list','search']` | Skip this many messages (alternative to page-based pagination) |

> Note: no parameter is Pydantic-required (`operation` defaults to `list`); the
> operation router in `EmailService.read` passes empty strings straight to
> Himalaya, which fails with a non-zero exit for ops that genuinely need
> `message_id` / `query` / `target_folder`.

Credential params (`email`, `password`, `imap_host`, etc.) follow the same
resolution rules as [`emailSend`](./emailSend.md#decision-logic).

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | Operation result |

When wired to an AI agent's `input-tools` handle (`usable_as_tool = True`, tool name `email_read`), the same payload is returned to the LLM via the tool-dispatch path — there is no separate `output-tool` handle.

### Output payload

```ts
{
  operation: string;            // echoed from params
  folder: string;               // echoed from params
  // When Himalaya returns a JSON object, its keys are merged in:
  ...dictFromHimalaya;
  // When Himalaya returns a list (e.g. list/search envelopes), it's wrapped:
  data?: unknown;
  // When stdout isn't JSON, HimalayaService falls back to:
  raw_output?: string;
}
```

Wrapped in the standard envelope: `{ success: true, result: <payload>, execution_time, node_id, node_type, timestamp }`.

## Logic Flow

```mermaid
flowchart TD
  A[BaseNode.execute -> Operation query] --> B[EmailService.read params]
  B --> C[resolve_credentials]
  C -->|missing email/password| Ec[ValueError -> error envelope]
  C --> D{operation in router?}
  D -- no --> En[ValueError: Unknown operation -> error envelope]
  D -- yes --> E[Dispatch to HimalayaService method]
  E --> F[ensure_binary + write TOML config tempfile]
  F -->|missing| Eb[RuntimeError -> error envelope]
  F --> G[subprocess: himalaya -c cfg -a acct --output json ...<br/>timeout 60s]
  G -->|returncode != 0| Er[RuntimeError: himalaya error ... -> error envelope]
  G --> H[json.loads stdout; fallback {raw_output: str}]
  H --> I[Merge operation + folder into result]
  I --> J[Return success envelope]
```

## Decision Logic

- **Operation router** in `EmailService.read` maps each operation to a
  `HimalayaService` method + kwargs. See the table in the handler:

  | operation | Himalaya subcommand |
  |-----------|---------------------|
  | `list`    | `envelope list -f <folder> --page <page> --page-size <n>` |
  | `search`  | `envelope list -f <folder> --query <query>` |
  | `read`    | `message read <id> -f <folder>` |
  | `folders` | `folder list` |
  | `move`    | `message move <id> <target_folder> -f <folder>` |
  | `delete`  | `message delete <id> -f <folder>` |
  | `flag`    | `flag add|remove <id> --flag <flag> -f <folder>` |

- **Unknown operation** raises `ValueError("Unknown operation: ...")` before
  any subprocess is spawned.
- **Missing required params for an operation are not pre-validated** - e.g.
  calling `read` without `message_id` passes an empty string to Himalaya,
  which returns a non-zero exit code and produces a generic error envelope.
- **Result shape depends on Himalaya output**:
  - Dict stdout (most commands) -> merged into `{operation, folder, ...}`.
  - List stdout -> wrapped as `{operation, folder, data: [...]}`.
  - Non-JSON stdout -> `HimalayaService.execute` returns `{raw_output: str}`
    which is then merged into `{operation, folder, raw_output}`.
- **`folders` operation** ignores `folder`, but the echoed value will still be
  whatever the caller passed (defaulting to `INBOX`).

## Side Effects

- **Database writes**: none.
- **Broadcasts**: none.
- **External API calls**: none direct - IMAP traffic flows through Himalaya.
- **File I/O**: one `himalaya_*.toml` tempfile per call (containing the
  plaintext password), deleted in `finally`.
- **Subprocess**: one `himalaya` invocation per call with 60s timeout.

## External Dependencies

- **Binary**: `himalaya` on `PATH`.
- **Credentials**: same as [`emailSend`](./emailSend.md#external-dependencies).
- **Config**: `server/config/email_providers.json`.
- **Python packages**: stdlib only.

## Edge cases & known limits

- **Pagination uses `page`/`page_size` only**: the router in `EmailService.read`
  reads `page` + `page_size`; the `limit` and `offset` params exist on the model
  but are not forwarded to Himalaya for `list`/`search`. `page_size` is Pydantic-
  validated to `1-500`; the IMAP server's own limits still apply.
- **No pagination metadata**: `list` returns whatever Himalaya gives; there
  is no `has_more` / `total` helper.
- **Deletion is permanent** (no undo), and the handler does not prompt or
  double-check. This is particularly load-bearing when used as an AI tool.
- **Search syntax** is Himalaya's, not RFC 3501 IMAP SEARCH; queries are
  passed through verbatim.
- **Plaintext password on disk** for the tempfile window (inherited from
  `HimalayaService.execute`).

## Related

- **Companion nodes**: [`emailSend`](./emailSend.md), [`emailReceive`](./emailReceive.md)
- **Architecture docs**: [Email Service](../../email_service.md)
