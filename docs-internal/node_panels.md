# Node Parameter Panel — Logic Flow Documentation

> Reference for the three-section node configuration UI (Input / Parameters / Output).
> Companion test suite in [client/src/hooks/__tests__/](../client/src/hooks/__tests__) and
> [client/src/components/parameterPanel/__tests__/](../client/src/components/parameterPanel/__tests__).

The Parameter Panel is the modal that opens when a node is selected on the canvas. It has three
columns that can be hidden independently depending on the node type:

```
+---------------------------------------------------------------+
| header: icon + name + Run / Save / Cancel                     |
+----------------+--------------------------+-------------------+
| Input section  | Middle section            | Output section    |
| (left)         | (parameters / config)     | (right)           |
| flex 0.7       | flex 1.6                  | flex 0.7          |
+----------------+--------------------------+-------------------+
```

Files:
- [client/src/ParameterPanel.tsx](../client/src/ParameterPanel.tsx) — modal shell
- [client/src/components/parameterPanel/ParameterPanelLayout.tsx](../client/src/components/parameterPanel/ParameterPanelLayout.tsx) — flex layout
- [client/src/components/parameterPanel/InputSection.tsx](../client/src/components/parameterPanel/InputSection.tsx)
- [client/src/components/parameterPanel/MiddleSection.tsx](../client/src/components/parameterPanel/MiddleSection.tsx)
- [client/src/components/parameterPanel/OutputSection.tsx](../client/src/components/parameterPanel/OutputSection.tsx)
- [client/src/components/OutputPanel.tsx](../client/src/components/OutputPanel.tsx) — drag source for connected outputs
- [client/src/components/ParameterRenderer.tsx](../client/src/components/ParameterRenderer.tsx) — universal widget
- [client/src/hooks/useParameterPanel.ts](../client/src/hooks/useParameterPanel.ts)
- [client/src/hooks/useDragVariable.ts](../client/src/hooks/useDragVariable.ts)

## 1. Lifecycle

1. User clicks a node on the canvas → `selectedNode` set in Zustand store.
2. `ParameterPanel` mounts → `useParameterPanel()` fires.
3. Hook reads defaults from `nodeDefinition.properties[].default`, then asks backend for any saved
   parameters via WebSocket `get_node_parameters`. Saved values overlay defaults.
4. Modal renders three sections; `MiddleSection` filters parameters via `displayOptions.show`
   (see §4 invariants), then renders each visible parameter through `ParameterRenderer`.
5. User edits → `handleParameterChange(name, value)` updates local state. `hasUnsavedChanges`
   flips true (computed via `JSON.stringify` equality with original).
6. Save → WebSocket `save_node_parameters` → DB; on success `originalParameters` updated.
7. Run → if `hasUnsavedChanges` save first, then `executeNodeViaWebSocket`.
8. Cancel → revert pending edits, clear selection, close modal.

## 2. Section Visibility Rules

| Node type bucket | Input | Middle | Output |
|---|---|---|---|
| Start | hidden | shown | hidden |
| Skill (e.g. masterSkill, single skill nodes) | hidden | shown | hidden |
| Monitor (`teamMonitor`) | hidden | shown | hidden |
| Everything else | shown | shown | shown |

`ParameterPanel.tsx` lines 119–122 compute `showInputSection` / `showOutputSection` and pass them
to `ParameterPanelLayout`.

## 3. Template Variable Naming (drag-and-drop contract)

When the user drags a value from `OutputPanel` into a parameter input, the dragged payload is a
template string `{{name.path}}` plus a JSON sidecar with metadata.

`name` is resolved by `useDragVariable.getTemplateVariableName(sourceNodeId)` with this strict
priority:

1. `node.data.label` — user-renamed label
2. `nodeDefinition.displayName` — built-in display name
3. `nodeType` — registered type name
4. `nodeId` — final fallback

In every case the result is **lowercased and whitespace-stripped** (`'My  Cron  Scheduler'` →
`'mycronscheduler'`).

The drag payload is set on both MIME types:
- `text/plain` → the template string `{{name.path}}` (used by simple text inputs)
- `application/json` → `{type: 'nodeVariable', nodeId, nodeName, key, variableTemplate, dataType}`

`effectAllowed` is `'copy'`.

## 4. Parameter Visibility (`displayOptions.show`)

Each `INodeProperties` entry can include a `displayOptions.show` map. Values can be arrays
(allowed-values list) or scalars (single allowed value). All conditions must hold:

```ts
displayOptions: {
  show: {
    operation: ['create', 'update'],   // operation must be one of these
    useProxy: [true],                  // AND useProxy must be true
  }
}
```

When ALL conditions match the parameter renders; otherwise it's hidden.

A parameter without `displayOptions.show` always renders.

`MiddleSection.shouldShowParameter` (lines 59–81) implements this. The function is internal so
tests assert it indirectly via component rendering — see
[client/src/components/parameterPanel/__tests__/MiddleSection.test.tsx](../client/src/components/parameterPanel/__tests__/MiddleSection.test.tsx).

## 5. Connection Discovery (Input + Output)

Both `InputSection` and `OutputPanel` walk the workflow's edges to figure out which other nodes
are linked to the current one. They classify handles into two buckets:

| Handle bucket | Examples | Effect |
|---|---|---|
| Data flow | `input-main`, `input-chat`, `input-task`, `input-teammates` | shown as connected nodes |
| Config / auxiliary | `input-memory`, `input-tools`, `input-skill`, `input-model` | hidden — they belong to the dedicated UI in `MiddleSection` |

Plus a special case for **config nodes themselves** (e.g. `simpleMemory`, any node whose group
includes `'memory'` or `'tool'`): when the user is viewing a config node, the panel inherits the
parent agent's main inputs and labels them `(via Agent Name)` so the user can still drag those
upstream variables into the config node's parameters.

## 6. Output Display (`OutputSection`)

`OutputSection` combines two sources of execution data:

1. `executionResults` — local results from in-modal Run button.
2. `nodeStatuses[selectedNode.id]` — push updates from workflow runs via WebSocket.

The WebSocket result is folded in at the front (newest-first) **only when** its `outputs` field
isn't already present in `executionResults` (deduplicated via `JSON.stringify`). Statuses other
than `success`/`error` (e.g. `running`) are ignored.

## 7. Refactor Invariants

Locked in by the test suite at:
- [client/src/hooks/__tests__/useDragVariable.test.ts](../client/src/hooks/__tests__/useDragVariable.test.ts)
- [client/src/components/parameterPanel/__tests__/MiddleSection.test.tsx](../client/src/components/parameterPanel/__tests__/MiddleSection.test.tsx)
- [client/src/components/parameterPanel/__tests__/InputSection.test.tsx](../client/src/components/parameterPanel/__tests__/InputSection.test.tsx)
- [client/src/components/parameterPanel/__tests__/OutputSection.test.tsx](../client/src/components/parameterPanel/__tests__/OutputSection.test.tsx)

1. **Defaults loaded** from `nodeDefinition.properties[].default`; missing default ⇒ `null`.
2. **Saved params win** over defaults when merged (DB is source of truth).
3. **`hasUnsavedChanges`** is a deep-equal check against the original snapshot loaded from DB.
4. **Save** routes to `save_node_parameters` and updates the original snapshot on success.
5. **Cancel** restores the pending edits and clears `selectedNode`.
6. **Drag template variable** uses the priority `label > displayName > nodeType > nodeId`,
   normalised to lowercase + no whitespace.
7. **Drag payload** sets both `text/plain` and `application/json`; `effectAllowed = 'copy'`.
8. **`displayOptions.show`** hides a parameter unless ALL keyed conditions match. Array values
   are membership checks; scalar values are equality checks.
9. **Config handles** (`input-memory|tools|skill|model`) are SKIPPED by both `InputSection` and
   `OutputPanel` for agent nodes — those dependencies surface in MiddleSection.
10. **`input-main|chat|task|teammates`** are NEVER skipped — they are data flow.
11. **Memory / tool config nodes** inherit their parent agent's main inputs and label them
    `via <Agent Name>` so upstream variables remain draggable.
12. **OutputSection deduplication** compares result `outputs` via `JSON.stringify` before folding
    in WebSocket status into local results. Only `success` / `error` statuses fold in.

## 8. Run / Save / Cancel Buttons

- **Run** disabled while `isExecuting`. Prefixed by an autosave when `hasUnsavedChanges`.
- **Save** disabled when `!hasUnsavedChanges`.
- **Cancel/Stop** acts as Stop (cancels event-wait via WebSocket) when the node is in `waiting`
  state; otherwise plain Cancel that reverts edits and closes the modal.

## 9. Test Run

```bash
cd client
npm install
npm run test:run -- src/hooks/__tests__/useDragVariable.test.ts \
                    src/components/parameterPanel/__tests__
```

Or use the dedicated script (added in `client/package.json`):

```bash
npm run test:nodepanels
```
