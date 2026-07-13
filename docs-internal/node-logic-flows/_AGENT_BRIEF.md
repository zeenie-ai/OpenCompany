# Agent Brief - Per-Category Rollout

You are documenting and writing contract tests for one category of OpenCompany
workflow nodes. Phase 1 (test infrastructure) and Phase 2 (search-node pilot)
are already done. Your job is to repeat the pilot pattern for your category.

## Pilot example (study before starting)

- **Docs**: [`docs-internal/node-logic-flows/search/`](./search/) - 3 files
  (`braveSearch.md`, `serperSearch.md`, `perplexitySearch.md`).
- **Test**: [`server/tests/nodes/test_search.py`](../../server/tests/nodes/test_search.py)
  - 15 tests across 3 classes.
- **Doc template**: [`./_TEMPLATE.md`](./_TEMPLATE.md) - copy verbatim,
  fill every section.

## Reusable infra you must use

| Path | What it gives you |
|------|------------------|
| `server/tests/nodes/_harness.py` | `NodeTestHarness` - drives any handler through `NodeExecutor` with stubbed services |
| `server/tests/nodes/_mocks.py` | `patched_container`, `patched_pricing`, `patched_broadcaster`, `patched_event_waiter`, `patched_subprocess` |
| `server/tests/conftest.py` | Provides `harness` fixture; pre-stubs `core.logging`, `core.container`, `services.pricing` |

## What to read

1. The handler file(s) for your category (paths in your prompt).
2. The backend plugin at `server/nodes/<category>/<node>.py` (the NodeSpec SSOT).
3. Any matching skill at `server/skills/<folder>/<skill>/SKILL.md` - link to it from the doc, do not duplicate.
4. The pilot files above so you copy the structure exactly.

## What to write

1. **Per-node docs**: one Markdown file per node at `docs-internal/node-logic-flows/<category>/<nodeName>.md`. Use camelCase matching the registry key.
2. **One test file** at `server/tests/nodes/test_<category>.py` with one test class per node, covering at minimum:
   - happy path (assert envelope success + payload shape)
   - one validation/error short-circuit (e.g. empty required param)
   - one external-error path (HTTP 4xx/5xx, missing credential, subprocess failure)
3. **Update the docs index**: run `node scripts/build-node-docs-index.js` after writing docs.

## Doc style rules

- Use the Mermaid `flowchart TD` syntax shown in the search docs.
- Every doc must have **Side Effects** listing every DB write, broadcast, subprocess spawn, file I/O, and HTTP call. Be precise (URL, table name, method).
- Every doc must have **Edge cases & known limits** with at least one entry. Look for swallowed exceptions, silent fallbacks, hard-coded clamps, undocumented branches.
- Cross-link sibling docs in **Related**.
- Do NOT use emojis anywhere (project rule).

## Test style rules

- Use `respx.mock` for httpx calls (already in pyproject dev deps).
- Use `patched_container(auth_api_keys={"<provider>": "tk_test"})` to inject API keys without touching the encryption pipeline.
- For trigger nodes, use `patched_event_waiter(canned_event={...})` so the future resolves immediately.
- For subprocess-based handlers (browser, shell, process_manager, code executors), use `patched_subprocess(stdout=b"...")`.
- Run `cd server && uv run pytest tests/nodes/test_<category>.py -v` and confirm all green before reporting done.

## Reporting back

After all docs are written and tests pass, report:
- Count of docs created.
- Test count + pass status.
- Any nodes you skipped + why.
- Any handler bugs you found while documenting (do NOT fix - just list).
