# treg (`tregAction`)

| Field | Value |
|------|-------|
| **Category** | web_automation / api / tool (dual-purpose) |
| **Backend handler** | [`server/nodes/treg/treg_action.py::TregActionNode`](../../../server/nodes/treg/treg_action.py) - dispatch via `BaseNode.execute()` -> `@Operation("search" / "describe" / "balance")`; the CLI's environment and runs in [`_service.py`](../../../server/nodes/treg/_service.py), its install in [`_install.py`](../../../server/nodes/treg/_install.py), catalog parameters in [`_request.py`](../../../server/nodes/treg/_request.py), large answers in [`_result.py`](../../../server/nodes/treg/_result.py) |
| **Tests** | [`server/tests/test_treg_plugin.py`](../../../server/tests/test_treg_plugin.py) |
| **Skill (if any)** | none yet |
| **Dual-purpose tool** | yes - tool name `treg_action` (`usable_as_tool = True`; both canvas handles explicitly kept visible) |
| **Direct Run** | supported; `search` and `describe` work before sign-in, `balance` needs the treg sign-in (Credentials -> treg) |
| **Controlled execution** | ActionNode defaults on the `rest-api` queue; a broken-off `search`, `describe` or `balance` may run again (`repeats.py`) |
| **Workspace task** | not applicable |

## Purpose

treg (treg.to) puts thousands of paid third-party tools (search results,
SEO data, social profiles and posts, people and company data, web pages,
image, video and voice generation) behind one sign-in, each billed per
call to the team's prepaid balance. This node runs treg's own CLI, the
PyPI package `tools-registry`, pinned and installed by OpenCompany with uv
into its own tool folder. `search` finds tools for a job with their
prices, `describe` lists one tool's parameters and where each goes, and
`balance` reads the team balance.

## Inputs (handles)

| Handle | Connection type | Required | Purpose |
|--------|-----------------|----------|---------|
| `input-main` | main | no | Upstream data; not consumed directly - inputs come from `parameters` (or the LLM's tool args when used as a tool) |

## Parameters

(Pydantic `TregActionParams`, `extra="ignore"`; a `model_validator(mode="before")`
runs `coerce_blank_params` so the panel's `""` for a cleared number falls
back to the default.)

| Name | Type | Default | Required | displayOptions.show | Description |
|------|------|---------|----------|---------------------|-------------|
| `operation` | options | `search` | no | - | `search` / `describe` / `balance` |
| `query` | string | `""` | yes for `search` | `operation` = `search` | The job in plain words; may not start with `-` |
| `limit` | int (`ge=1, le=100`) | `10` | no | `operation` = `search` | The most tools to list |
| `tool_id` | string | `""` | yes for `describe` | `operation` = `describe` | A catalog id; must match `[A-Za-z0-9][A-Za-z0-9._@-]*` |

## Outputs (handles)

| Handle | Shape | Description |
|--------|-------|-------------|
| `output-main` | object | `_shape(operation, **fields)` payload (see below); the same payload goes to the LLM when wired to `input-tools` |
| `output-tool` | - | Auto-appended for `usable_as_tool` |

### Output payload

`TregActionOutput` (`extra="allow"`, every field Optional; `_shape` drops
`None`):

```ts
// operation = "search"
{
  operation: 'search';
  query: string;
  count: number;             // tools returned
  total: number;             // tools that matched
  verdict?: string;          // treg's judged search: strong | closest | none | keyword | name
  reason?: string;
  tools: Array<{
    id: string; name?: string; summary?: string; provider?: string;
    price?: { type?: string; usd?: number; usd_min?: number; unit?: string; varies?: boolean };
    task?: true;             // runs as an asynchronous task (generation)
    routed?: true;           // treg picks the provider
  }>;
}

// operation = "describe"
{
  operation: 'describe';
  tool_id: string; name?: string; summary?: string; note?: string;
  provider?: string; method?: string; price?: object;
  parameters: Array<{ name: string; in: 'path' | 'query' | 'body' | 'header';
                      type?: string; required?: boolean; default?: unknown;
                      enum?: unknown[]; example?: unknown; note?: string }>;
  parameters_note?: string;
  example_request?: object;  // the catalog's known-good request
  example_response?: unknown;// its example answer, previewed (first element of each list)
  task?: true; status?: string; superseded_by?: string; docs_url?: string;
}
// describe of a job id (what several providers do): { operation, tool_id, job: <treg's providers list> }

// operation = "balance"
{
  operation: 'balance';
  balance_usd: number;
  holds: Array<{ tool_id: string; reserved_usd: number; call_id: string }>;
}
```

## Logic Flow

```mermaid
flowchart TD
  A[BaseNode.execute -> TregActionNode.&lt;operation&gt;] --> I[get_installer.ensure: pinned tools-registry via uv, waits up to 300 s]
  I -- uv missing / install failed / still installing --> Ei[NodeUserError]
  I -- ok --> B{operation}
  B -- search --> S1{query given and not starting with -?}
  S1 -- no --> Eq[NodeUserError]
  S1 -- yes --> S2[treg --json catalog search QUERY --limit=N]
  S2 --> S3[rows trimmed: id, name, summary, provider, price, task, routed]
  B -- describe --> D1[check_tool_id] --> D2[treg --json catalog get ID]
  D2 -- endpoint --> D3[parameters_of input blocks + price + example request + previewed example answer]
  D2 -- job id --> D4[job: treg's providers list]
  B -- balance --> B1[treg --json balance --limit=1] --> B2[balance_usd + holds]
  S2 & D2 & B1 -- exit != 0 --> Ew[NodeUserError in treg's words: detail, message or the CLI's last stderr line]
```

## Decision Logic

- **Every run** goes through `_service.run_treg`: `treg --json <argv>`
  with the environment `treg_env` builds (`TREG_CONFIG` under
  `<DATA_DIR>/treg`, inherited `TREG_*` removed, analytics off,
  `TREG_CLIENT=opencompany`, a `BROWSER` that opens nothing), stdin a
  pipe closed at once, from the `<DATA_DIR>/treg` folder. Values are
  passed as separate arguments; no shell.
- **Validation**: `search` needs a query that does not start with `-` (the
  CLI removes any `--org=` argument wherever it stands); `describe` needs
  a tool id of catalog shape.
- **Error paths**: a failed read becomes `NodeUserError` with treg's own
  words (`detail`, or its `message` / `error` plus `did_you_mean`), else
  the CLI's last stderr line. `balance` adds a hint to sign in again.
- **Approval class** (`services/approvals/classes.py`): `search`,
  `describe` and `balance` are `read`, and each may run again after a
  broken-off attempt (`repeats.py`).

## Side Effects

- **Database writes**: none.
- **Broadcasts**: per-node status via `BaseNode.execute`.
- **External API calls**: through the CLI only: `GET https://treg.to/catalog/search`,
  `GET /catalog/endpoints/{id}`, and for `balance` `GET /orgs` then
  `GET /orgs/{id}/balance` (with the CLI's stored key).
- **File I/O**: the CLI reads and writes its config under `<DATA_DIR>/treg`.
- **Subprocess**: one `treg` run per operation; the first use also runs
  `uv tool install`.

## External Dependencies

- **Credentials**: `TregCredential` (id `treg`, `auth = "custom"`) is a
  marker: the key stays in the CLI's own config; sign-in is relayed by
  the Credentials modal (`treg_login` / `treg_status` / `treg_logout` in
  [`_handlers.py`](../../../server/nodes/treg/_handlers.py)).
- **Services**: uv at run time (bundled in the desktop app; absent from the
  default Docker image).
- **Python packages**: none in the server; the CLI is `tools-registry==0.30.0`
  in its own uv tool environment.
- **Environment variables**: `UV_TOOL_DIR` / `UV_TOOL_BIN_DIR` (desktop
  app), `OPENCOMPANY_UV_BIN`.

## Edge cases & known limits

- **Search and describe need no sign-in**: treg's catalog is open; the
  judged search is capped per caller (per IP address when signed out).
- **`balance` may trigger an automatic top-up** when the team turned
  automatic top-ups on and the balance is under its threshold (treg's own
  rule on its balance route).
- **A newer `tools-registry`** is not used until the pin in `_install.py`
  is bumped.

## Related

- **Companion nodes**: [`tikhubAction`](./tikhubAction.md), [`apifyActor`](./apifyActor.md)
- **Architecture docs**: [Plugin System](../../plugin_system.md), [CLI Services Integration](../../cli_services_integration.md)
