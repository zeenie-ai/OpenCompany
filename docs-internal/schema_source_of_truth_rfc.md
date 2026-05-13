# Schema source of truth — RFC

**Status:** ✅ **implemented + extended to full plugin registry** · **Owner:** frontend + backend platform · **Landing:** 2026-04-14 (output schemas) → **Wave 6** (NodeSpec contract) → **Wave 10** (plugin pattern + visual contract + icons, 2026-04-15)

## Landed outcome (Wave 3 — output schemas)

- Backend registry seeded with Pydantic models in [server/services/node_output_schemas.py](../server/services/node_output_schemas.py) — live size via `len(NODE_OUTPUT_SCHEMAS)` — covering every node type the deleted `sampleSchemas` map covered, plus all specialized agents and chat models.
- `GET /api/schemas/nodes/{node_type}.json` serves static JSON Schema (Cache-Control `public, max-age=86400`). 404 for unknown types.
- `get_node_output_schema` WebSocket handler for authenticated editor fetches.
- [InputSection.tsx](../client/src/components/parameterPanel/InputSection.tsx) consumes schemas lazy via `queryClient.fetchQuery(['nodeOutputSchema', nodeType])`, in-memory cache only. **1,673 → 972 LOC** (−701). sampleSchemas map, 20 `isXxxNode` detection constants, and 60-line pattern-match chain all gone.
- `outputSchema` field removed from `INodeTypeDescription` — the frontend type no longer has any shape-describing surface for runtime output.
- Adding a new node type's output shape: one Pydantic model in `node_output_schemas.py`. Zero frontend change.

## Wave 6 extension (input schemas + full NodeSpec)

Wave 3 proved the pattern for output schemas. Wave 6 generalises it to the **full parameter contract** — the business-logic surface the frontend still owned via `nodeDefinitions/*.ts` `properties` arrays, `displayOptions`, validation rules, dynamic-option loaders, and credential mappings.

**Shipped contract:**

- **Input schemas** — [server/services/node_input_schemas.py](../server/services/node_input_schemas.py) registers Pydantic input models across every node type — live count via `len(_DIRECT_MODELS)` — auto-expanding `SpecializedAgentParams` and `AndroidServiceParams` via Literal union introspection.
- **NodeSpec envelope** — [server/services/node_spec.py](../server/services/node_spec.py) assembles `{type, displayName, icon, group, description, version, inputs (JSON Schema), outputs (JSON Schema, reuses Wave 3), credentials, uiHints}` fusing three sources of truth.
- **Display metadata** — [server/models/node_metadata.py](../server/models/node_metadata.py) carries one entry per input-modeled type — live size via `len(NODE_METADATA)`. The only new server-side data file.
- **Endpoints** — alongside the Wave 3 `/nodes/{type}.json` output endpoint: `GET /api/schemas/nodes/{type}/spec.json`, `/nodes/{type}/input.json`, `/nodes/specs` (list), all with 24h `Cache-Control`. WS mirrors: `get_node_spec`, `list_node_specs`. **Icon endpoints (RFC §6.5 + F7):** `GET /api/schemas/nodes/{type}/icon` serves the plugin's co-located `icon.svg` (or per-node-type `icon_<nodeType>.svg` for multi-node folders like whatsapp); `GET /api/schemas/credentials/{provider}/icon` serves the credential brand icon from `server/credentials/icons/<provider>.svg`. Both with long cache headers.
- **loadOptionsMethod dispatch** — [server/services/node_option_loaders/](../server/services/node_option_loaders/) generalises the WhatsApp-only pattern to a registry. Adding a new dynamic-option loader = one-line registration. `POST /api/schemas/nodes/options/{method}` + WS `load_options`.
- **Node-groups index** — `GET /api/schemas/nodes/groups` returns `{group: [node_type, ...]}` derived from every NodeSpec's `group` array — live count via `len(NODE_GROUPS)` — replacing the hand-rolled `*_NODE_TYPES` arrays scattered across 6 frontend files.
- **Pydantic Field hints** — authors encode `displayOptions.show`/`.hide`, `loadOptionsMethod`, `placeholder`, `validation`, `typeOptions` via `Field(json_schema_extra={...})`. Currently ~36 rules encoded across TwitterSend/User, TelegramSend, HttpRequest, WhatsAppSend, SocialSend, Gmail, Calendar.

**Frontend consumer (feature-flagged):**

- `VITE_NODESPEC_BACKEND` flag at [client/src/lib/featureFlags.ts](../client/src/lib/featureFlags.ts). Defaults OFF — legacy `nodeDefinitions/*.ts` wins; ON routes rendering through backend NodeSpec via the adapter.
- [client/src/lib/nodeSpec.ts](../client/src/lib/nodeSpec.ts) — shared `fetchNodeSpec`/`prefetchAllNodeSpecs`/`resolveNodeDescription` helpers following the Wave 3 colocation pattern (no new hook file).
- [client/src/adapters/nodeSpecToDescription.ts](../client/src/adapters/nodeSpecToDescription.ts) — NodeSpec wire shape → legacy `INodeTypeDescription` bridge. Handles `required`, JSON Schema `format` (dateTime/file/binary), `displayOptions`, `validation`, typeOptions lift (`loadOptionsMethod`, `password`, `rows`, `editor`, `editorLanguage`, `accept`, …), and reads hints from both top-level and nested `uiHints` wrapper.
- Dashboard idle-time prefetch warms all 110 NodeSpecs after WebSocket connect (no-op when flag off).

**Adding a new node type's parameter surface:**

1. Define `XyzParams(BaseNodeParams)` in [server/models/nodes.py](../server/models/nodes.py) with `type: Literal["xyz"]` discriminator and Field constraints.
2. Register in `KnownNodeParams` discriminated union.
3. Register in `_DIRECT_MODELS` dict in `node_input_schemas.py`.
4. Add `NODE_METADATA["xyz"] = {displayName, icon, group, description, version}` in `node_metadata.py`.

Zero frontend change for parameter rendering. Icons that are SVG assets still need frontend import until SVG payload migration is its own follow-up.

**What's deferred (Wave 7):**

- Phase 3e flag flip + frontend `nodeDefinitions/*.ts` reductions (~3000 LOC deletion). Adapter is ready; snapshot Playwright tests are the remaining prerequisite.
- Phase 5.b per-component migration from `*_NODE_TYPES` arrays to backend groups.
- Gmail/Calendar/Telegram/Twitter loadOptions loaders (Phase 4 registry is ready for one-line registrations).
- ParameterRenderer → DIY widget registry (the capstone). Now unblocked on backend since NodeSpec's `uiHints` already carry widget routing.

## Problem

The editor's Input panel (`client/src/components/parameterPanel/InputSection.tsx`) needs to show the runtime output shape of every connected source node so users can drag variables into downstream parameters. Today there are two places this shape is declared:

1. A 350-line `sampleSchemas` map inline in `InputSection.tsx` (59 entries).
2. As-of Wave 3 Phase 1 Batch 1 (commit `f1b2813`), a new `outputSchema` field on each `INodeTypeDescription` that the frontend consults before the legacy map.

Both are **frontend duplications of what the backend already owns** — the handler's return type (Pydantic models in `server/services/handlers/*`). Every node addition requires a frontend edit to keep schemas in sync.

## How this scales to 1000+ nodes elsewhere

Raw-GitHub-API research against three schema-driven platforms (n8n ~500 nodes, Activepieces ~400 pieces, Nango 100+ integrations):

**n8n** — the canonical comparison point, React Flow / Vue inspector:
- Node type descriptions come from the backend via `GET /node-types` on boot (`packages/frontend/editor-ui/src/app/stores/nodeTypes.store.ts`).
- The Input panel (`VirtualSchema.vue`) pulls the output shape from two sources in order of preference:
  1. Actual execution data — `getSchemaForExecutionData(props.data)`.
  2. A JSON Schema preview fetched lazy per-node via `GET /schemas/{nodeType}/{version}/{resource?}/{operation?}.json` (`schemaPreview.api.ts`). These are **static JSON files served alongside the backend**, `withCredentials: false`, keyed `${nodeType}_${version}_${resource}_${operation}` in a `Map<string, Result<JSONSchema7, Error>>` in-memory cache.
  3. Empty state when neither is available.

**Activepieces** (`packages/pieces/framework/src/lib/*`):
- Piece-authors declare Actions in TypeScript inside the piece's backend package. **Actions have no `outputSchema` field at all.** Triggers carry `sampleData: unknown` — a free-form example value the UI renders in the variable picker before first run.
- The backend emits two payloads to the editor: `PieceMetadataSummary` (icon + counts + suggested) for catalog browsing, and the full `PieceMetadata` (actions + triggers records) on select.

**Nango** (`packages/types/lib/nangoYaml/index.ts`):
- YAML config declares `input: ModelName` / `output: ModelName[]` per sync or action. Model definitions in the same YAML are codegen'd into TypeScript types shipped to both backend and frontend.

**Finding:** zero of these platforms keep node-shape declarations on the frontend. n8n explicitly serves per-node JSON Schema files from the backend; Activepieces skips declared schemas entirely in favor of real run data.

## Decision

Adopt n8n's layered pattern.

**Schema source of truth:** backend only. Pydantic models colocated with node handlers.

**Frontend rendering order** (three-tier, mirrors `VirtualSchema.vue`):
1. Real execution data from the most recent run (already wired at [`InputSection.tsx:164-174`](../client/src/components/parameterPanel/InputSection.tsx)).
2. `GET /api/schemas/nodes/{nodeType}.json` — JSON Schema served lazy from the backend, cached in-memory per node type.
3. Empty state / `{ data: 'any' }` fallback.

**What the frontend keeps:**
- `INodeTypeDescription` stays a **UI-only** description: `displayName`, `icon`, `group`, `inputs`, `outputs`, `properties`, `defaults`, `uiHints`, `credentials`. No data schemas.
- `uiHints` (Wave 2 Phase 1) is genuinely UI-owned (panel visibility, editor variants, selector dispatch) and stays.

**What leaves the frontend:**
- `outputSchema` field on `INodeTypeDescription` (added by commit `f1b2813`) — deleted.
- `sampleSchemas` map in `InputSection.tsx` — deleted once the backend endpoint has coverage for the node types currently listed there.

## Backend contract

```
GET /api/schemas/nodes/{node_type}.json
  200 → application/json: JSON Schema 7 describing the node's output shape
  404 → node has no declared schema (frontend falls back to run data / empty)
  headers: Cache-Control: public, max-age=86400 (or similar)
```

WebSocket parallel:
```
request:  { type: "get_node_output_schema", node_type: "whatsappReceive" }
response: { schema: JSONSchema7 | null }
```

Schema generation: each handler's existing Pydantic response model (or a new minimal `NodeOutputSchema` Pydantic model colocated with the handler) emits JSON Schema via `.model_json_schema()`. Registry at `server/services/node_output_schemas.py`: `NODE_OUTPUT_SCHEMAS: dict[str, type[BaseModel]]` mapping node type → model class. Missing entries return 404.

## Frontend changes

1. New `useNodeOutputSchemaQuery(nodeType)` inline at top of `InputSection.tsx` (one consumer — inline per the Wave 2/3 colocation rule):
   ```ts
   function useNodeOutputSchemaQuery(nodeType: string | null) {
     return useQuery<Record<string, any> | null>({
       queryKey: ['nodeOutputSchema', nodeType],
       queryFn: () => nodeType
         ? sendRequest('get_node_output_schema', { node_type: nodeType })
             .then(r => r?.schema ?? null)
         : Promise.resolve(null),
       staleTime: Infinity,
       enabled: !!nodeType,
     });
   }
   ```

2. `InputSection` dispatch becomes three-tier:
   ```ts
   if (executionData?.length) outputSchema = executionData[0][0].json;
   else if (backendSchema)    outputSchema = backendSchema;
   else                       outputSchema = { data: 'any' };
   ```
   Delete the `sampleSchemas` map + pattern-match else-if chain + all `isXxx` constant flags.

3. Revert commit `f1b2813` (frontend `outputSchema` annotations). Remove the `outputSchema` field from `INodeTypeDescription`.

## Migration order

1. Write this RFC. (this file)
2. Frontend: add the `useNodeOutputSchemaQuery` hook + three-tier dispatch. **Keep** the legacy `sampleSchemas` map as a temporary final fallback below the backend call so nothing breaks before the backend endpoint exists. The `nodeDef.outputSchema` branch goes away in this commit (revert + remove the type field).
3. Backend: implement `/api/schemas/nodes/{node_type}.json` + `get_node_output_schema` WS handler. Seed the registry from existing Pydantic response models (start with the high-traffic nodes: `chatTrigger`, `webhookTrigger`, `whatsappReceive`, `aiAgent`/`chatAgent` family, `httpRequest`, code executors, Google Workspace).
4. Frontend: once the backend has coverage for every type in the legacy `sampleSchemas` map, **delete the map**. Verify via manual smoke of the 20 node categories.

## What this unblocks

Phase 6 (`ParameterRenderer` → DIY widget registry) was blocked on the backend emitting `NodeSpec { jsonSchema, uiSchema, _uiHints? }`. The endpoint defined here is the `jsonSchema` slice of that same `NodeSpec` — Phase 6 extends it with `uiSchema` + `_uiHints`. Phase 1-REVISED is therefore the on-ramp to Phase 6, not a detour.

## Non-goals

- Build-time codegen of TypeScript types from the backend schemas (Nango's pattern) — deferred. The frontend reads schemas as plain JSON; typed inference on top would be nice-to-have but isn't load-bearing.
- Versioning of schemas per node-type version — deferred. All current node types are v1; add `{version}` to the URL path the first time we bump.

## Wave 10 — plugin pattern + visual contract

Wave 6 made the backend authoritative for node schemas. Wave 10 closed the
remaining tribal-code paths so **adding a new node = one Python file, zero
frontend edits**.

### 10.A — Visual contract extended

`NodeMetadata` TypedDict in [server/models/node_metadata.py](../server/models/node_metadata.py)
gains the fields the frontend previously hardcoded:

- `color` (hex / dracula token)
- `componentKind` (`square` / `circle` / `trigger` / `start` / `agent` / `chat` / `tool` / `model` / `generic`)
- `handles: NodeHandle[]` — full React Flow topology (replaces the 400-line
  `AGENT_CONFIGS` map in `AIAgentNode.tsx`)
- `credentials`, `hideOutputHandle`, `visibility`

Each field flows through `get_node_spec()` into the `/api/schemas/nodes/{type}/spec.json`
envelope, so every consumer (React Flow dispatch, parameter panel, palette)
reads from one source.

### 10.C — `@register_node` decorator

[server/services/node_registry.register_node(...)](../server/services/node_registry.py)
writes to four registries atomically: `NODE_METADATA`, `_DIRECT_MODELS`,
`NODE_OUTPUT_SCHEMAS`, `_HANDLER_REGISTRY`. `server/nodes/__init__.py` walks
`server/nodes/*.py` submodules at import time via `pkgutil.iter_modules`,
so plugin registration is side-effect at startup. 106/111 node types migrated
to this path (the remaining 5 are output-only legacy aliases).

**New node checklist (post-Wave-10):**

```python
# server/nodes/<group>/<plugin>/__init__.py
from typing import Literal
from pydantic import Field
from services.plugin import ActionNode, NodeContext, Operation

class MyNodeParams(ActionNode.Params):
    query: str = Field(default="", json_schema_extra={"placeholder": "Search..."})

class MyNodeOutput(ActionNode.Output):
    result: dict = {}

class MyNode(ActionNode):
    type = "myNode"
    display_name = "My Node"
    group = ("tool",)
    component_kind = "square"
    Params = MyNodeParams
    Output = MyNodeOutput

    @Operation("run")
    async def run(self, ctx: NodeContext, params: MyNodeParams) -> MyNodeOutput:
        return MyNodeOutput(success=True, result={...})
```

Drop `icon.svg` and `meta.json` (`{"color": "#xxx"}`) into the plugin folder. Zero edits elsewhere.

### 10.B — Icon + color: per-plugin folder + visuals.json fallback

Icons live as `icon.svg` (or `icon_<nodeType>.svg` for multi-node folders like whatsapp) in the plugin folder; colors live in `meta.json`. `visuals.json` is the fallback registry for emoji / `lobehub:<brand>` icons + the skill reverse-map. Full resolution chain + wire format + frontend rendering all documented in [server/nodes/README.md → Icon + color](../server/nodes/README.md). Credential brand icons via `Credential.get_icon_path()` resolve to `server/credentials/icons/<provider>.svg` or co-located `credential_<id>.svg`; served by `GET /api/schemas/credentials/{provider}/icon`.

### 10.G — Parameter panel fully spec-driven

- `ParameterRenderer.tsx` gains `case 'code'` + `case 'dateTime'` + generic
  `loadOptionsMethod` dispatch via the backend `load_options` WS handler
  (unlocking the 4 Google Workspace dynamic-option loaders that were
  previously unreachable). `displayOptions.show` now propagates into nested
  `fixedCollection` renders. Password masking wins over multi-row textarea.
- `MiddleSection` / `InputSection` / `SquareNode` / `ParameterPanel` retire
  the last 14 hardcoded type-array fallbacks (`TRIGGER_NODE_TYPES`,
  `AGENT_WITH_SKILLS_TYPES`, `SKILL_NODE_TYPES`, etc.). Every widget
  decision reads a uiHint or handle-topology fact declared by the node's
  own plugin module.
- `nodeDefinitions/*.ts` files strip every `icon:` field (−272 LOC) — 
  backend is sole declaration site. `INodeTypeDescription.icon` narrowed
  to `icon?: string`.

### Contract invariants (108 pytest in `tests/test_node_spec.py`)

`TestWave10GContractInvariants` enforces:

- every agent-kind node has `uiHints.hasSkills`
- every tool-kind node has `uiHints.isToolPanel`
- every Google Workspace node has a field gated by
  `displayOptions.show.operation`
- every code executor emits `editor: "code"` on its `code` field
- every `api_key` / `apiKey` field emits `password: True`
- every `asset:<key>` icon resolves to a real SVG under
  `client/src/assets/icons/`
- every palette group carries non-empty label + icon

## Wave 11 — plugin-first class hierarchy

Wave 10 shipped the `@register_node` decorator and per-file plugin
layout but kept handlers as standalone async functions. Wave 11
promotes nodes to a class-based plugin model, extracts runtime
infrastructure into `services/plugin/`, and unifies the node-creation
API around a single `BaseNode` hierarchy. See
**[plugin_system.md](./plugin_system.md)** for the canonical reference.

### What changed

- **`BaseNode` class hierarchy.** Three concrete kinds —
  `ActionNode` / `TriggerNode` / `ToolNode`. Each subclass declares
  everything on its class object: `type`, metadata, `Params` +
  `Output` Pydantic models, `credentials`, `handles`, `task_queue`,
  `retry_policy`, operations. `__init_subclass__` writes to the four
  legacy registries on import, so existing consumers are unchanged.
- **`@Operation` decorator** for multi-op dispatch.
- **Declarative `Routing` DSL** + **`Connection` facade (Nango
  pattern).** Plugins never see tokens; routing owns templating + HTTP.
- **Credential classes** (`ApiKeyCredential`, `OAuth2Credential`)
  with declarative `inject()` methods. Auto-registry via
  `__init_subclass__`.
- **Per-plugin Temporal activities** (Wave 11.F). Every subclass
  exposes `cls.as_activity()` with name `node.{type}.v{version}`.
  `TemporalWorkerPool` spawns one worker per declared `task_queue`
  with tuned concurrency (ai-heavy=4, rest-api=50, triggers-poll=100).
- **Folder layout mirrors palette groups.** `server/nodes/<group>/<name>.py`.
- **Auto-populate trigger registries** (Wave 11.D.11). Plugins
  declaring `event_type` + `build_filter` auto-register into
  `event_waiter.TRIGGER_REGISTRY` + `FILTER_BUILDERS`.
- **Shared helpers under `services/plugin/`**: `edge_walker.py`
  (agent connection discovery), `base.py` (BaseNode lifecycle),
  `context.py` (typed NodeContext), `scaling.py` (TaskQueue +
  RetryPolicy), `operation.py` (@Operation decorator + collector),
  `routing.py` (declarative REST DSL), `connection.py` (authed
  httpx wrapper), `credential.py`, `interceptor.py`.

### Contract invariants

Adds 16 invariants to the 108 Wave 10 suite → **124 total**:

- Every `BaseNode` subclass has `type`, `display_name`, `group`.
- `Params` + `Output` are Pydantic `BaseModel` subclasses.
- Every declared credential resolves to `CREDENTIAL_REGISTRY`.
- `@Operation` names unique per class; `routing=…` requires credentials.
- `task_queue` ∈ `TaskQueue.ALL`; `retry_policy` is `RetryPolicy`.
- Every `ToolNode` JSON schema has no `$defs` / `$ref`.
- Every event-mode `TriggerNode` declares `event_type`.
- Fast-path covers every AI-tool-usable plugin.
- Trigger registry auto-populates for every event-mode plugin.

### Status

Plugins (live count via `glob server/nodes/**/__init__.py`) live across 9 Temporal task queues (`rest-api`,
`ai-heavy`, `code-exec`, `triggers-poll`, `triggers-event`, `android`,
`browser`, `messaging`, `machina-default`). Handler bodies are fully
inlined into plugin files — `services/handlers/` shrank from
**12.8K LOC / 16 files → 1.1K LOC / 4 files**. Only cross-cutting
orchestration remains: `tools.py` (AI-tool dispatch + agent
delegation through `BaseNode.execute()` via the node registry),
`google_auth.py`, `triggers.py`. Sunset of the empty
`nodes/{agents,services,triggers,tools,utilities}.py` bulk files +
dead dispatcher fallbacks is complete (Wave 11.D.13).

### Sub-waves shipped

- **11.A / B / B.1** — `BaseNode` hierarchy, `ActionNode` /
  `TriggerNode` / `ToolNode`, `__init_subclass__` registry writes.
- **11.C** — `@Operation` decorator + multi-op dispatch.
- **11.D.0–13** — handler inlining, bulk-file sunset, trigger
  registry auto-population, dispatcher cleanup.
- **11.E** — declarative `Credential` subclasses
  (`ApiKeyCredential`, `OAuth2Credential`) with `inject()` methods
  and auto-registry via `Credential.__init_subclass__`.
- **11.E.1** — credentials modularised into per-domain
  `server/nodes/<group>/_credentials.py` files (or inline for
  single-use). **Central `server/credentials/` directory deleted.**
  18 `Credential` subclasses total.
- **11.E.2 / E.3 / E.4** — credential polish + doc sync.
- **11.F** — per-plugin Temporal activities
  (`cls.as_activity()` named `node.{type}.v{version}`),
  `TemporalWorkerPool` per task queue with tuned concurrency
  (ai-heavy=4, rest-api=50, triggers-poll=100, etc.).
- **11.G** — nodes cookbook (`server/nodes/README.md`) + CLAUDE.md
  sync.

## References

- [n8n `schemaPreview.api.ts`](https://github.com/n8n-io/n8n/blob/master/packages/frontend/editor-ui/src/features/ndv/runData/schemaPreview.api.ts)
- [n8n `VirtualSchema.vue`](https://github.com/n8n-io/n8n/blob/master/packages/frontend/editor-ui/src/features/ndv/runData/components/VirtualSchema.vue)
- [n8n `nodeTypes.store.ts`](https://github.com/n8n-io/n8n/blob/master/packages/frontend/editor-ui/src/app/stores/nodeTypes.store.ts)
- [n8n node-icon conventions](https://docs.n8n.io/integrations/creating-nodes/build/reference/node-base-files/icons/) — `file:` + `fa:` prefix-dispatch which Wave 10.B generalises
- [Activepieces `piece-metadata.ts`](https://github.com/activepieces/activepieces/blob/main/packages/pieces/framework/src/lib/piece-metadata.ts)
- [Activepieces `action.ts`](https://github.com/activepieces/activepieces/blob/main/packages/pieces/framework/src/lib/action/action.ts)
- [Nango `nangoYaml/index.ts`](https://github.com/NangoHQ/nango/blob/master/packages/types/lib/nangoYaml/index.ts)
- [@lobehub/icons](https://github.com/lobehub/lobe-icons) — the default `<lib>:<brand>` target for AI provider brand logos
