# Canvas Node — pushed-content display board (panel + docked sidebar)

The `canvas` node is the platform's viewing surface — the Claude/ChatGPT-Canvas
analog. Agents and workflow runs push content onto a per-node **board**
(workspace file references, external URLs, small markdown notes), and the
board renders in three hosts that share one renderer: the node's full-height
parameter panel, a **docked, resizable right-side sidebar** that stays
open while working on the graph, auto-opens when content is pushed, and
doubles as the click-to-preview surface for workspace files, and the Canvas
tab of Home's Workspace dock.

The Dev dock now shares Home's Browser / Canvas / Android workspace tabs.
Canvas retains its board and file-preview behavior; live Chrome sessions use
the separate [Browser workspace](./browser_workspace.md) surface. URL items
on a Canvas still render as sandboxed iframe previews, not browser sessions.

Everything here composes existing patterns; the doc records which one each
piece copies and the few places where a *new* decision had to be made (the
iframe sandbox matrix, the PDF inline exception, the first client-side
file-content fetch).

| Piece | Pattern copied |
|---|---|
| Node shape | `nodes/tool/write_todos/` (ToolNode + `input-main` handle + per-scope store + metadata-only broadcast + panel WS handlers) |
| Durable store | `services/data/mount_store.py` / `services/memory/tool_store.py` (plugin-owned SQLModel tables, `ensure_schema(checkfirst=True)`, zero `core/database.py` edits) |
| WS handler security | `nodes/tool/simple_memory/_handlers.py` preamble, copied verbatim |
| Broadcast | `nodes/context/_events.py` (direct `get_status_broadcaster().broadcast`, identity-only payload) |
| Panel host | `GalleryPanel` (full-height MiddleSection panel via a uiHints flag) |
| Live updates | ContextPanel posture (`canvas_updated` → invalidate `['canvasBoard']` → panel refetches through the authorized handler) |
| Dock resize / prefs | ConsolePanel (`usePanelResize`, now **extracted to `client/src/hooks/usePanelResize.ts`**; zod-validated localStorage prefs) |
| Dock state | `stores/nodeStatusStore.ts` (small Zustand store, slice-selector reads, `getState()` writes) |

## Architecture

```
                agent tool call `canvas(...)`          upstream node output
                (output-tool -> agent input-tools)     (input-main edge)
                          \                             /
                           v                           v
              nodes/tool/canvas/__init__.py  @Operation("display")
                     |  paths -> resolve_media + stat -> FileRef (never bytes)
                     |  url   -> http(s) check
                     |  content -> 64KB truncate_note
                     |  (none) -> structural FileRef scan of connected_outputs
                     v
              _store.py  CanvasStore (canvas_boards / canvas_items, revision++)
                     v
              _events.py  broadcast `canvas_updated` {workflow_id, node_id, revision}
                     v
   WebSocketContext case  ->  invalidate ['canvasBoard']  +  dock notifyPushed
                     v                                   v
        CanvasPanel (parameter panel)          CanvasDock (right sidebar)
                     \_______________  ________________/
                                     \/
                    CanvasContent (one shared renderer)
        media carousel | markdown/code/JSON/text | sandboxed web | PDF | binary
```

Content bodies never ride the board or the broadcast: files are fetched by
the client over the existing `GET /api/workspace/{workflow_id}/files/{path}`
route; note bodies come back only through the authorized `canvas_list`
handler.

## Backend — [`server/nodes/tool/canvas/`](../server/nodes/tool/canvas/__init__.py)

### The node

`CanvasNode(ToolNode)`, `type="canvas"`, `group=("tool","ai")`,
`tool_name="canvas"` with `tool_schema_locked=True` (stale persisted
ToolSchema rows cannot rename or reshape the tool — simpleMemory precedent).
Declared handles are `input-main` (left) + `output-tool` (top): declared
handles win wholesale on the frontend, so no `hide_*` fiddling is needed
despite `component_kind="tool"`.

Two deliberate overrides of what the group/base would otherwise derive:

- `ui_hints = {"isCanvasPanel": True, "isConfigNode": False}` —
  the `tool` group auto-derives `isConfigNode: True`, but the Canvas node's
  `input-main` is a real runtime dataflow edge, so it must present as a
  normal node (no parent-input inheritance). Explicit wins over
  auto-derivation, the sanctioned opt-out.
- `annotations = {"destructive": False, "readonly": False, "open_world": False}` —
  overrides ToolNode's `readonly: True` default because `display` writes the
  durable board.

`hideRunButton` is deliberately **not** set: Run with panel-typed params
(including `{{node.field}}` templates) is how the workflow-edge path is
authored and debugged.

### The single `display` op

One `@Operation("display")` serves the agent tool call, the Run button, and
the workflow edge identically (`_pick_operation` runs a single-op node's
lone op regardless of `parameters["operation"]`). Item sources, in order:

1. **`paths`** (≤ 20/call, `NodeUserError` past the cap): each path goes
   through `resolve_media` containment + a `stat` and becomes a serialized
   `FileRef` built gallery-style — **never `coerce_file_param`**, which
   reads full bytes; the board stores a pointer, and reading a 500 MB video
   to register it would be pure waste. `kind` stays `"file"` even for
   media, because `kind="audio"` asserts an `inspect_audio` probe this code
   never ran.
2. **`url`**: scheme must be `http`/`https` (`javascript:`/`file:` are
   user-correctable `NodeUserError`s).
3. **`content`**: markdown note, truncated at 64 KB with a visible
   `… [truncated]` marker — a workflow run must never die on an oversized
   note; the truncation is reported in `message` instead.
4. **Connected-outputs scan** (only when 1–3 produced nothing): a recursive,
   depth-capped structural walk of `ctx.raw["connected_outputs"]` collecting
   serialized `FileRef`s. A dict is a ref iff it *validates* as one —
   `FileRef`'s `extra="forbid"` makes near-misses (e.g. a gallery listing
   row) fail while the `ref` nested inside them is found. Deduped by path,
   capped at 20. This is what makes `tts → canvas` work with a zero-config
   edge. The node is therefore in `NodeExecutor._NEEDS_CONNECTED_OUTPUTS` —
   the one core edit, sanctioned by that frozenset's own comment, and it
   covers direct-run and both Temporal paths because all of them funnel
   through `NodeExecutor._dispatch`.

An empty call raises `NodeUserError("Nothing to display — pass paths, url,
or content …")` so the LLM gets a one-line correctable error.

**Output payload discipline**: the op returns `{message, count, revision,
added: [{id, kind, title}]}` — ids and counts only, never note bodies and
never echoed refs, because a node result is persisted three ways, broadcast,
retained in the status cache, copied into downstream activity inputs, and
replayed into LLM context every turn.

`source` labeling (`"agent"` vs `"workflow"`) keys on `_tool_config` being
present in `ctx.raw` — `execute_as_tool` sets it around the op body and
nothing else does. Cosmetic; a wrong label misleads nobody about security.

### Params coercions (the panel/LLM boundary)

`CanvasParams` is flat on purpose — it **is** the LLM tool schema (nested
models would emit `$defs`, which the tool-schema invariant rejects), and it
deliberately has no docstring because a Pydantic model docstring becomes the
schema description the model reads. Boundary coercions:

- `coerce_blank_params` in a `model_validator(mode="before")` — the panel
  writes `""` for cleared fields whatever the type; blanks drop for the
  non-str fields (`paths`, `mode`) so defaults apply.
- `paths` accepts a JSON-encoded array string (Gemini stringifies array tool
  args), a bare path string, and serialized ref dicts (a `{{node.files}}`
  template resolves to a list of dicts) reduced to their `path` key.
  Malformed JSON passes through so Pydantic raises a correctable error.

### The store — [`_store.py`](../server/nodes/tool/canvas/_store.py)

Plugin-owned tables, mount_store mechanism: importing the plugin registers
`CanvasBoard` (`canvas_boards`: board id, identity columns, `revision`) and
`CanvasItemRow` (`canvas_items`: kind/title/`ref` JSON/url/`content`
Text/language/source/position) into SQLModel metadata before
`Database.startup()`'s `create_all`; `ensure_schema()` (per-engine set +
lock + `checkfirst=True`) covers late importers. No migration framework, no
`core/database.py` edits.

Scope is `CanvasScope(owner_id, workflow_id, node_id)` with the
`"unsaved"` workflow fallback; the board id is
`"canv_" + sha256("canvas:v1\0owner\0workflow\0node")[:48]` — the version
string is baked into the hash material from day one (the
`todo_session_key` lesson: keys that aren't versioned eventually need to
be, retroactively).

Every mutation is one transaction that bumps `revision`. Caps:
`CANVAS_MAX_ITEMS = 200` (FIFO eviction by `position`),
`CANVAS_NOTE_MAX_BYTES = 64 KB`, `CANVAS_MAX_PATHS_PER_CALL = 20`.
`_serialize` emits the fixed wire shape with a stable, null-filled key set.

### Panel WS handlers — [`_handlers.py`](../server/nodes/tool/canvas/_handlers.py)

`canvas_list` / `canvas_remove` / `canvas_clear`, all `@ws_response` (never
`@ws_handler` — a user-correctable failure must be one WARN line, not an
ERROR with a traceback), self-registered via `register_ws_handlers` from the
package `__init__`. Security preamble copied verbatim from simple_memory:

1. `_require_external_socket` — the unauthenticated `/ws/internal` worker
   socket is refused (defence in depth behind the deny-by-default
   `services/authz/ws_surface.py` allowlist, which new handlers never join).
2. `_authenticated_owner` — identity from `websocket.state`/`scope` only,
   never from request data; `"owner"` fallback mirrors
   `NodeContext.user_id`'s trusted default.
3. Graph-ownership resolution — `workflow_id` + `node_id` required, the
   persisted workflow must exist, its `owner_id` must match, and **exactly
   one** node with that id AND `type == "canvas"` must be in the graph.
   Neither the client nor the model can supply a scope identifier.

Trade-off shipped knowingly (same as simpleMemory): the op can write to an
`"unsaved"`-scoped board, but the panel handlers require a **saved**
workflow (the ownership check needs the DB row) — an unsaved canvas's board
becomes readable after the first save.

Mutating handlers broadcast `canvas_updated` so every open surface (other
tabs included) refetches.

### Events — [`_events.py`](../server/nodes/tool/canvas/_events.py)

`canvas_updated` is a CloudEvents `WorkflowEvent`
(`source: opencompany://nodes/canvas`,
`type: com.opencompany.canvas.updated`, `subject: node_id`) whose data is
**identity + revision only** — `{workflow_id, node_id, revision}`. It is
broadcast **directly** via `get_status_broadcaster().broadcast(...)`, not
`dispatch.emit`: no node type registers a canary consumer for
`com.opencompany.canvas.*`, so `emit` would run a Temporal Visibility query
guaranteed to match nothing, once per `display` call. No item content rides
the broadcast because it fans out to every connected socket; content flows
only through the authorized `canvas_list`.

## Wire contract

```ts
// One board item — server/nodes/tool/canvas/_store.py::_serialize
interface CanvasItem {
  id: string;
  kind: 'file' | 'url' | 'note';
  title: string | null;
  ref: WorkspaceFileRef | null;   // kind 'file'
  url: string | null;             // kind 'url'
  content: string | null;         // kind 'note' (64KB-capped markdown)
  language: string | null;
  source: 'agent' | 'workflow' | string;
  created_at: string | null;
}
```

| Channel | Shape |
|---|---|
| `canvas_list {workflow_id, node_id}` | `{success, items: CanvasItem[], revision}` |
| `canvas_remove {workflow_id, node_id, item_id}` | `{success, removed, revision}` + broadcast |
| `canvas_clear {workflow_id, node_id}` | `{success, cleared, revision}` + broadcast |
| broadcast `canvas_updated` | `{type: 'canvas_updated', data: <WorkflowEvent>}`, data.data = `{workflow_id, node_id, revision}` |
| tool `canvas(title?, paths?, url?, content?, language?, mode)` | flat schema, no `$defs`; `mode: 'append' \| 'replace'` |

Client-side types + query keys live in
[`client/src/lib/canvasBoard.ts`](../client/src/lib/canvasBoard.ts):
`canvasBoardQueryKey(workflowId ?? 'unsaved', nodeId)` (todoQuery shape),
prefix `['canvasBoard']` for broadcast-driven invalidation.

## Frontend — three hosts, one renderer

### Hosts

- **[`CanvasPanel.tsx`](../client/src/components/parameterPanel/CanvasPanel.tsx)**
  — the full-height parameter-panel host, dispatched by
  `uiHints.isCanvasPanel` in `MiddleSection` (gallery pattern: flag read +
  description-suppression chain + ternary-ladder branch). Header carries the
  item-count badge and Clear; body is the shared renderer. Items are
  references — removing one never deletes the underlying file, which is why
  Clear is a plain ghost button and not an `AlertDialog`.
- **[`CanvasDock.tsx`](../client/src/components/ui/CanvasDock.tsx)** — the
  new docked surface, mounted as the last flex child of Dashboard's
  main-content row (rightmost edge). Left-edge drag handle via the shared
  `usePanelResize` (dragging left widens; floor 280 px, ceiling nearly the
  full window — `innerWidth - 160` so the handle stays reachable, with a
  4000 px sanity bound on persisted values), `transition-[width]` disabled
  while dragging. Header: node
  selector (a Select when the workflow has more than one Canvas node —
  ConsolePanel's node-selector precedent; nodes are found by
  `resolveNodeDescription(type)?.uiHints?.isCanvasPanel`, never by the type
  string), ephemeral "Preview" badge + Back, close. TopToolbar carries the
  toggle button (Monitor icon, `aria-pressed`, action-tools tokens),
  reading the dock store directly — the state is not Dashboard's to thread.
- **[`WorkspaceCanvas.tsx`](../client/src/features/home/workspace/WorkspaceCanvas.tsx)**
  — the Canvas tab of Normal mode's Workspace dock
  ([normal_mode.md](./normal_mode.md#the-workspace)). It shows one
  employee's board, named by the employee summary's `canvas_node_id`,
  loads lazily so the renderer's viewers stay out of Home's chunk, and
  passes an owner-facing empty hint. Hires get a Canvas node from the
  Hire builder.

### Dock state — [`stores/canvasDockStore.ts`](../client/src/stores/canvasDockStore.ts)

A small Zustand store (slice-selector reads; `getState()` writes from the WS
handler and click-to-preview call sites) — deliberately not on `useAppStore`
or `WebSocketContext.value`, both context-fan-out traps. Prefs
(`open`, `widthPx`, `autoOpen`, `followMode`) persist to localStorage under
`canvas_dock_prefs_v1` via a zod-validated envelope (ConsolePanel idiom);
node selection and ephemeral items are session-only because node ids are
workflow-scoped.

`notifyPushed(nodeId)` rules (invoked from the `canvas_updated` case for the
**current** workflow only): closed + `autoOpen` → open on the pushed node;
open in node mode → follow the pushed node; **ephemeral mode → never yank a
deliberate preview** (the invalidation still freshens data for later).

### Live updates

One switch case in `WebSocketContext.tsx` beside the `context.*` cases:
invalidate the `['canvasBoard']` prefix (a no-op unless a surface is
mounted) + the workflow-scoped `notifyPushed`. **Because a switch case
exists, the default-case `addEventListener` fan-out never fires for this
type — never `addEventListener('canvas_updated')` anywhere.**

### The shared renderer — [`parameterPanel/canvas/CanvasContent.tsx`](../client/src/components/parameterPanel/canvas/CanvasContent.tsx)

The item list **is** the carousel: one active item (only the active item
mounts — no N live videos/iframes), prev/next in the footer,
`pinnedId: null` meaning "follow newest" (the chat stick-to-bottom rule: a
pushed item surfaces automatically unless the user deliberately navigated
back; navigating onto the last item resumes following). Arrow/Home/End keys
work on the focused `role="group"` only — no document-level listeners,
which would fight React Flow node nudging.

**Follow-latest** (the browser-automation live view): when the active item
is a workspace image, a Switch enables a visibility-gated 5 s poll of the
existing `list_workspace_files` handler on the image's folder, rendering the
newest image entry in place — zero new backend. Persisted as the dock's
`followMode` pref.

Renderer dispatch is a pure function
([`canvasKinds.ts`](../client/src/components/parameterPanel/canvas/canvasKinds.ts)),
mime before extension, script-bearing/document verdicts before the generic
text tiers:

| Verdict | Condition | Renders |
|---|---|---|
| `note` | `kind === 'note'` | ReactMarkdown prose (no fetch) |
| `web-external` | `kind === 'url'` | sandboxed iframe (below) |
| `media-image/video/audio` | mime prefix or ref.kind | native element, FilePreviewDialog's honest-fallback idiom |
| `pdf` | `application/pdf` / `.pdf` | plain same-origin iframe (browser viewer) |
| `web-srcdoc` | `text/html` / `.html` | capped fetch → `srcDoc` sandbox iframe; over-cap → refusal + Download (a half-rendered page misleads) |
| `markdown` / `json` / `code` / `text` | ext/mime tiers (code set reuses gallery's `fileIcons` family) | [`TextFileView.tsx`](../client/src/components/parameterPanel/canvas/TextFileView.tsx): prose / JsonView / Prism on the `--code-*` tokens (Prism skipped > 100 KB) |
| `binary` | everything else | glyph + meta + Download |

### First client-side file-content fetch — [`hooks/useWorkspaceText.ts`](../client/src/hooks/useWorkspaceText.ts)

Text bodies (markdown/code/JSON/HTML) come over the existing workspace route
with **no backend change**: `Content-Disposition: attachment` blocks
navigation/embedding but not `fetch()` reading the body. Bounded by
construction — a streamed reader cancelled at `TEXT_FETCH_CAP_BYTES`
(512 KB) so a 100 MB log never lands in memory; `truncated` renders a
banner. Non-ok throws with the status (a 401 must surface as an error state,
never a blank pane). The query key includes `size_bytes` + `modified_at`,
so a re-pushed file self-busts with no invalidation plumbing.

### Click-to-preview

`FilePreviewDialog` (gallery) gained an "Open in side panel" button: it
builds an ephemeral `CanvasItem` from the entry's server-built ref, calls
`showEphemeral`, closes the dialog **and clears the selected node** — the
dock sits behind the 95vw parameter modal, so moving the preview to the
persistent surface must close the modal; that is the point of the
affordance. Ephemeral items live in no board and are never persisted.

## Security decisions

The iframe surfaces are zero-precedent territory (there was no `<iframe>`
anywhere in `client/src`); these attributes are the decisions, locked by
regression tests asserting the exact sandbox values:

| Surface | Attributes | Why |
|---|---|---|
| External URL | `sandbox="allow-scripts allow-forms"`, `referrerPolicy="no-referrer"` | No `allow-same-origin` (opaque origin), no `allow-top-navigation` (a framed page must never navigate the app away), no `allow-popups` — the permanent "Open in new tab" button is the escape hatch, and it must always be visible because X-Frame-Options / `frame-ancestors` denial is undetectable cross-origin. |
| Workspace HTML | fetch text → `srcDoc` + `sandbox="allow-scripts"` | Never `allow-same-origin`: a stored-XSS payload in a workspace file executes with no origin, no cookies, no app storage. Workspace HTML never enters the app DOM (also why no sanitizer dependency exists). The backend's `NEVER_INLINE` rule is **respected, not worked around** — HTML still serves `attachment` from the route. |
| PDF | plain same-origin iframe | Browser-native viewer; sandboxing breaks it in several engines. Served inline because `application/pdf` joined a new `INLINE_EXACT` frozenset in [`services/media/preview.py`](../server/services/media/preview.py) — an **exact-match** addition (no other `application/*` type gains inline serving as a side effect), `NEVER_INLINE` untouched, `PreviewKind` gains `"pdf"` on both sides of the wire, locked by `tests/routers/test_workspace.py`. The exposure class is the same as images, not the markup-from-app-origin class the deny-set guards. |

Plus: broadcasts carry no content (ownership is enforced at `canvas_list`);
the model cannot choose a scope (trusted `NodeContext` only); handler scope
resolves from the persisted graph, not the request.

## Browser screenshot persistence — [`server/nodes/browser/_screenshots.py`](../server/nodes/browser/_screenshots.py)

Shipped alongside the node because it is what gives the board its most
interesting content. Both browser plugins historically leaked screenshots in
unusable shapes: `browser` returned whatever agent-browser printed (a base64
blob — a media-contract violation that also hits the 100 KB CLI-output
truncation), `browserHarness` an absolute path under
`<DATA_DIR>/daemons/browser-harness/tmp/` that no HTTP route can serve.

The shared helper lands the bytes in the workflow workspace via
`write_media(kind="image")` so a screenshot becomes a ~400 B `FileRef`:

- `persist_screenshot_from_payload(data, ctx, fmt)` (the `browser` node's
  `screenshot` op): probes the known inline-base64 keys (only `"base64"` is
  evidenced in-repo; the rest are tolerated probes) then saved-file-path
  keys, returns `(ref, consumed_key)` so the caller drops exactly the bulky
  field from the payload.
- `persist_screenshot_file(path, ctx, contained_under)` (the harness
  `screenshot` op): reads **only** files contained under the harness runtime
  dir — printed process output is not a licence to read arbitrary files.

**Tolerance is the contract**: an unrecognized payload shape, a missing
workspace, or a write failure logs one warning and returns `None`; the
browser operation itself never breaks over persistence. Sanity bounds
(128 B – 25 MB) refuse garbage. Tests:
`server/tests/nodes/test_browser_screenshots.py`.

## Edits outside the plugin folder (the complete list)

| File | Edit | Justification |
|---|---|---|
| `server/services/node_executor.py` | `"canvas"` in `_NEEDS_CONNECTED_OUTPUTS` | The frozenset's own comment sanctions it; covers direct-run + both Temporal paths |
| `server/config/node_allowlist.json` | `"canvas"` in `enabled_nodes` | Normal-mode palette visibility (positive list) |
| `server/tests/test_node_spec.py` | `"isCanvasPanel"` in the uiHints `known` set | The test's own failure message instructs this |
| `server/services/media/preview.py` + `server/tests/routers/test_workspace.py` | `INLINE_EXACT` / `"pdf"` | The PDF surface; `NEVER_INLINE` untouched |
| `server/nodes/browser/{browser,browser_harness}/__init__.py` | screenshot post-process | Plugin-folder edits of the browser plugins |
| `client/src/{types/INodeProperties.ts, types/workspaceFiles.ts, components/parameterPanel/MiddleSection.tsx, contexts/WebSocketContext.tsx, Dashboard.tsx, components/ui/TopToolbar.tsx, components/ui/ConsolePanel.tsx, components/parameterPanel/gallery/FilePreviewDialog.tsx}` | hint type, `'pdf'` PreviewKind, panel dispatch, `canvas_updated` case, dock mount, toggle, `usePanelResize` extraction, click-to-preview | The standard new-panel checklist + the dock integration |

Notably **not** edited: `routers/websocket.py`, `main.py`,
`services/authz/ws_surface.py`, `core/database.py`,
`test_plugin_self_containment.py` (its `_PLUGINS_WITH_HANDLERS` drift check
covers group-level folders only — nested plugin folders like this one, and
write_todos/simple_memory/gallery before it, are out of its scope).

## Tests

| Surface | File | Locks |
|---|---|---|
| Store | [`server/tests/nodes/test_canvas_node.py`](../server/tests/nodes/test_canvas_node.py) | scope isolation, append/replace + revision monotonicity, FIFO eviction, kind rejection, stable wire key set, note truncation, versioned board id |
| Display op | same | contained ref build (`../` rejected), url scheme, truncation notice, empty-call `NodeUserError`, connected-outputs scan (near-miss dicts skipped, nested refs found), agent/workflow source labeling, replace mode, output payload discipline |
| Handlers | same | internal-socket denial, owner mismatch, wrong-node-type, list/remove/clear round trip + broadcast-per-mutation |
| Event + schema | same | identity-only envelope (`subject`, type, exact data keys), `as_tool_schema()["name"] == "canvas"`, structurally no `$defs`/`$ref`, locked hints/annotations |
| Screenshots | [`test_browser_screenshots.py`](../server/tests/nodes/test_browser_screenshots.py) | base64/path/unrecognized shapes, size bounds, missing-workspace tolerance, harness containment |
| FE dispatch | `canvas/__tests__/canvasKinds.test.ts` | full verdict matrix, mime-over-extension, language override |
| FE carousel + sandbox | `canvas/__tests__/CanvasContent.test.tsx` | follow-newest vs pinned semantics, keyboard, remove; **sandbox regression**: external iframe exactly `allow-scripts allow-forms`, srcDoc lacks `allow-same-origin` and has no `src` |
| FE dock store | `stores/__tests__/canvasDockStore.test.ts` | notifyPushed truth table, width clamp, prefs round-trip + corrupt-JSON fallback, session-only fields never persisted |
| FE text fetch | `hooks/__tests__/useWorkspaceText.test.ts` | cap + reader cancel, status error, no-body fallback, cookie + buildApiUrl |
| FE panel | `__tests__/CanvasPanel.test.tsx` | `canvas_list/remove/clear` round trip, no-workflow guard, denied-listing surfacing |

## Pitfalls & known limits

- **NodeSpec cache**: `isCanvasPanel` reaches an open canvas only after a
  hard browser reload (specs are session-sticky, revision-busted at page
  load). Symptom: the plain params list renders instead of the board.
- **Unsaved workflows**: the board is writable under the `"unsaved"` scope
  but the panel reads it only after the workflow is saved (ownership needs
  the DB row).
- `connected_outputs` is keyed by source node **type** (engine behavior
  shared with console/executors): two same-type upstreams collide and the
  scan sees one output.
- Framing denial by external sites is undetectable — the "Open in new tab"
  affordance is permanent, not error-triggered. Site content that declares
  no background renders transparent over dark themes (accepted; no token is
  invented for third-party content).
- Removing a board item never deletes the underlying workspace file;
  deletion stays human-only in the gallery.
- No GC of board rows on workflow delete (future work; `canvas_clear`
  covers the manual case). Other explicit non-goals for v1: thumbnails
  strip, OutputPanel/chat click-to-preview integrations, CDP screencast
  live view (the follow-latest poll covers the need), a `display` skill
  (would need a `visuals.json` alias for tool name `canvas`).

## Related

[media_transport.md](./media_transport.md) (FileRef contract, the serving
rule this extends) · [plugin_system.md](./plugin_system.md) (the plugin
recipe) · [node-logic-flows/ai_tools/canvas.md](./node-logic-flows/ai_tools/canvas.md)
(the frozen behavioural card) · [frontend_architecture.md](./frontend_architecture.md)
(uiHints catalogue, strict frontend rules).
