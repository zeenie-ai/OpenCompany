# Frontend UI Stack Migration — antd → shadcn/ui (canonical, no custom wrappers)

> **Status (2026-05-08):** Phases 0–5 + 7 **complete**. Phase 6 (`ParameterRenderer` → JSON Forms) deferred pending backend `NodeSpec` handler. Zero antd / `@ant-design/icons` / styled-components imports remain. Wave 12 retired the surviving tribal patterns (string-compares, opacity arithmetic, whole-store destructure). Wave 13 unified credential storage on a single source of truth + RFC 9700 / OWASP V9.9 compliance. **Wave 14** (May 2026) lit up the 10-theme design system end-to-end (chrome migration, decorative wrappers, sound packs, canvas overlays). Current state lives in [frontend_architecture.md](./frontend_architecture.md) and [theme_system.md](./theme_system.md) — those docs supersede this plan as the source of truth for what the frontend IS; this doc documents how we got there.

## Completion table

| Phase | Status | Commits |
|---|---|---|
| 0 — Tokens + shadcn CLI bootstrap | ✅ done | `cdeebb4` (`2209dba`, `7ac69fe` were hand-written false-starts, superseded) |
| 0.5 — Shadcn components via CLI | ✅ done | `8bade71` |
| 0.6 — **Tailwind v4 vite plugin wired** (required for utility compilation) | ✅ done | `5aa7c11` |
| 1 — Toasts to sonner (direct import, no facade) | ✅ done | `cdeebb4` (replaced earlier `7ac69fe` adapter) |
| 2 — Visual chrome (Tag/Space/Flex/Spin/Alert/Typography) | ✅ done | `1222c8c` |
| 3 — Overlays (Modal/Collapse/Popover/Tooltip/Dropdown/Tabs/AlertDialog) | ✅ done | `af88ec5`, `11ea62e` |
| 4 — Inputs (Button/Input/Select/Switch/InputNumber/Slider/Checkbox/Card/Statistic) | ✅ done | `a3c4314`, `3cf0cd7`, `0c2d218`, `376a1d2`, `cbea6c3` |
| 5 — Forms (credentials: EmailPanel + ProviderDefaults + RateLimit on RHF + zod; FieldRenderer+useCredentialPanel drop antd Form) | ✅ done | `ccf9bd5`, `7bbcfef`, `bbc056f` |
| 6 — `ParameterRenderer` → JSON Forms + renderer registry | ⏸ deferred | (needs backend `get_node_spec`) |
| 7 — Retire antd, ConfigProvider, styled-components | ✅ done | `b9e0c74` |

**Outcome metrics:**
- Main JS bundle: **1.7 MB** (was 2.35 MB pre-Phase-7 — saved ~650 KB from antd + `@ant-design/icons` + dayjs locale payload)
- `grep -rln "from 'antd'" client/src/` → **0**
- `grep -rln "from '@ant-design" client/src/` → **0**
- `grep -rln "styled-components" client/src/` → **0**
- `pnpm build` green throughout all 17 commits on `feature/credentials-scaling-v2`

## Post-antd cleanup (April 2026)

A second audit (3 parallel sub-agents) flagged tribal patterns that survived the antd retirement and didn't match the schema-driven design system. A focused 5-phase follow-up plan ([typed-splashing-crown](../../../.claude/plans/typed-splashing-crown.md)) addresses them:

| Follow-up phase | Status | Commit | What it removes |
|---|---|---|---|
| 1 — Workflow list to TanStack Query | ✅ done | `c3a7aa4` | `savedWorkflows` array + `loadSavedWorkflows` action duplicating server data in Zustand |
| 2 — `useParameterPanel` / `useOnboarding` to Query hooks | ✅ done | `b2b6fba` | hand-rolled `useState + useEffect` over `WebSocketContext.sendRequest` |
| 3 — Theme tokens to CSS vars (kickoff) | ✅ done | `8b19808` | `theme.ts` deprecation banner + `AIAgentNode` PHASE_CONFIG hex literals (bulk migration of `GenericNode` / `SettingsPanel` / `BaseChatModelNode` deferred) |
| 4 — `SettingsPanel` to zod + Phase-2 hooks | ✅ done | `2901f0a` | duplicated camel↔snake mappers + hand-rolled load/save WS calls |
| 5 — Schema-drive WhatsApp selectors | ✅ done | `8353c48` | `parameter.name === 'group_id'` / `'channel_jid'` / `'senderNumber'` branches in `ParameterRenderer` (now `typeOptions.loadOptionsMethod`) |

**What's still tribal (deferred):**
- Bulk inline-style migration in `GenericNode`, `SettingsPanel` non-button styles, `BaseChatModelNode`. The Tailwind classes are wired and the deprecation banner is in place; per-component conversion is mechanical but visual-regression-prone, so left for follow-up commits with browser verification.
- `parameter.name === 'apiKey'` / `'model'` specials in `ParameterRenderer` — same migration path (`typeOptions.loadOptionsMethod = 'providerModels'` etc.), parked until the WhatsApp pattern proves stable.
- `ConsolePanel` 11 × `useState` → `useReducer` + zod schema. Independent edit-state domain; won't be touched until a behavior change forces it.

**New canonical patterns introduced:**
- Workflows + node params + user settings live in TanStack Query (`useWorkflowsQuery`, `useNodeParamsQuery`, `useUserSettingsQuery`), not Zustand or component-local state.
- Module-singleton `QueryClient` at [client/src/lib/queryClient.ts](../client/src/lib/queryClient.ts) so imperative code (Zustand actions) can invalidate without going through React context.
- Settings + onboarding share one cached server read (`['userSettings']`).
- WhatsApp group / channel / member selectors dispatch from `typeOptions.loadOptionsMethod` instead of parameter-name string compares — schema is the source of truth.

## Wave 2 — schema-driven panels (April 2026)

A second, deeper audit (4 parallel sub-agents — panel audit, library survey, NodeSpec mapping, system-design / file-organisation research) drove a focused Wave 2 against the heavy custom panels and the long-deferred renderer registry. Plan: same `typed-splashing-crown.md` plan file, replaced in place. Hard constraint: **minimise new files** — the research validated colocation (Kent C. Dodds, n8n's monolithic `ParameterInput.vue`, Robin Wieruch's split criteria) over per-widget files.

| Wave 2 phase | Status | Commit | What it removes / introduces |
|---|---|---|---|
| 1 — `INodeUIHints` on node definitions | ✅ done | `0e816f3` | 5 panel files were branching on `nodeDefinition.name === '…'`. Now read 14 typed flags (`hideInputSection`, `hasCodeEditor`, `isMasterSkillEditor`, `isMemoryPanel`, `isToolPanel`, `isMonitorPanel`, `showLocationPanel`, `isAndroidToolkit`, `isChatTrigger`, `isConsoleSink`, `hasSkills`, …) from `nodeDefinition.uiHints`. Legacy name fallback retained for one release. |
| 2 — `outputSchema` registry | ✅ done | `5d8d2b5` | The 350-line `sampleSchemas` map in `InputSection.tsx` was the largest schema-driven gap. New `outputSchema` field on `INodeTypeDescription`; `start`, `taskTrigger`, `chatTrigger`, `simpleMemory`, `python/javascript/typescriptExecutor` annotated as canaries. Legacy map kept as fallback. |
| 3 — `ConsolePanel` state split | ✅ done | `cde8e6a` | 6 `useState` + 3 `useEffect` localStorage pairs and 2 imperative `addEventListener` resize chains collapsed into one zod-validated `consolePrefsSchema` + one inline `usePanelResize` hook. Both stay colocated at the top of `ConsolePanel.tsx`. |
| 4 — Editor migrations to RHF + zod | ✅ done | `221848b`, `0c0e197` | `SkillEditorModal` (5 fields + manual `validateForm`) and `ToolSchemaEditor` (dynamic field-array) now run on `useForm` + `zodResolver` + `useFieldArray`. Per-field error messages live in the schema; dirty tracking is RHF-managed. Inputs / Selects / Switch / Checkbox from shadcn primitives. |
| 5 — `ActionButton` cva helper | ✅ done | `8a68c09` | A 14-line `actionButtonStyle(color, isDisabled)` helper was copy-pasted across 4 files. New `client/src/components/ui/action-button.tsx` with a `tone` cva variant; `ParameterPanel`, `LocationParameterPanel`, `SettingsPanel` migrated. ~210 LOC of inline-style boilerplate gone. |
| 6 — `ParameterRenderer` → renderer registry | ⏸ deferred | — | Library survey ran; recommended approach is **DIY** (RHF + zod + small `WIDGETS` registry) modeled on n8n's monolithic `ParameterInput.vue`, with `@rjsf/core` v6 + `@rjsf/shadcn` as the documented escape hatch. File budget: 4 files in `inspector/` (vs the original 16-file proposal — research validated the smaller layout). Blocks on backend `get_node_spec` WebSocket handler emitting `NodeSpec { jsonSchema, uiSchema, _uiHints? }`. |
| 7 — Docs + guardrails | ✅ done (docs) | this commit | This section + frontend_architecture.md updates. ESLint rule + bundle-budget CI assertion deferred (need CI config touch). |

**File budget actuals:** 1 new file across Wave 2 (`action-button.tsx`). All other phases used colocation or extended existing files (`useWorkflowsQuery.ts`, the relevant node definitions, `INodeProperties.ts`). The original draft of this plan proposed ~16 new files for the renderer phase alone — research-driven revision cut that to a 4-file colocated layout.

**Wave 2 numbers:**
- Hardcoded `nodeDefinition.name === '…'` outside whitelisted utility files: **0** (all panel-visibility checks read `uiHints` first; legacy fallbacks kept for nodes not yet annotated).
- Hand-rolled forms with >3 fields: **2 → 0** (SkillEditorModal, ToolSchemaEditor — both on RHF + zod). MasterSkillEditor remains; it's a split-panel UI, not a single form.
- `ConsolePanel` `useState` count: **13 → 7**.
- `pnpm exec tsc --noEmit` green throughout all 6 commits.

**What's still deferred (intentionally):**
- Phase 6 — `ParameterRenderer` → DIY widget registry. Backend `get_node_spec` handler is the prerequisite. Frontend layout decided (4 files in `inspector/`); rollout shape decided (5 weekly slices behind `VITE_USE_NODESPEC` with parity test against legacy renderer).
- Bulk Tailwind sweep of `GenericNode` / `BaseChatModelNode` inline `theme.dracula.*` refs. Visual-regression-prone; needs in-browser verification per file.
- ESLint rule + bundle-budget CI assertion (Phase 7 leftovers). Both touch CI config, deferred together.
- `MasterSkillEditor` RHF migration. The editor is a split-panel UI (skill list + content editor + folder dropdown + inline create/edit) — not the >3-field-form pattern Phase 4 targeted. The form-with-validation parts already moved to `SkillEditorModal`; the registry-style list view is genuinely different work.

## Wave 3 — backend as schema source of truth (April 2026)

The Wave 3 plan started as "port `sampleSchemas` to per-node frontend `outputSchema`" but flipped after a raw-GitHub-API study of **n8n**, **Activepieces**, and **Nango**. None of them hardcode node output shapes on the frontend. n8n specifically serves JSON Schema files per node as static assets and fetches them lazy from the editor (`schemaPreview.api.ts`, `VirtualSchema.vue`); Activepieces skips declared output schemas entirely and relies on real-run data. The full research is in [docs-internal/schema_source_of_truth_rfc.md](./schema_source_of_truth_rfc.md).

Under that finding, the in-flight Wave 3 Phase 1 was reversed: the backend owns the schemas via Pydantic models, the frontend consumes them lazy via a WS handler, and the 350-line `sampleSchemas` map is deleted.

| Phase | Status | Commits | Outcome |
|---|---|---|---|
| 1a — Revert frontend outputSchema annotations | ✅ done | `cd252c1`, `5866821` | 30-file frontend annotation revert; `outputSchema` field removed from `INodeTypeDescription`; the 7 Wave-2-canary annotations (start/chatTrigger/taskTrigger/simpleMemory/python+js+ts executors) also removed — same principle |
| 1b — Backend schema registry + endpoint | ✅ done | `0d98c88`, `4a4a439` | `server/services/node_output_schemas.py` — **98 node-type entries** across agents / chat models / triggers / memory / code executors / Google Workspace / WhatsApp / search / location / filesystem / HTTP / Android / documents / proxy / email / telegram / twitter / social / browser / apify / crawlee / cronScheduler. Pydantic models colocated. `GET /api/schemas/nodes/{node_type}.json` (Cache-Control public, max-age=86400) + `get_node_output_schema` WS handler |
| 1c — Frontend three-tier dispatch + delete sampleSchemas | ✅ done | `327f792`, `4a4a439` | `InputSection.tsx` 1,673 → 972 LOC (−701). `sampleSchemas` map + 20-line `is{Android,Google,…}Node` detection constants + 60-line pattern-match else-if chain all gone. Schema precedence: real run data → backend fetch (cached in TanStack Query, in-memory only, matches n8n's `schemaPreview.store` pattern) → `{ data: 'any' }` empty |
| 2 — MiddleSection decompose | 🟡 partial | `2c5f227`, `61bf23c` | Three wins landed: (a) `sendRequest('get_user_settings')` → shared `useUserSettingsQuery`; (b) `sendRequest('configure_compaction')` threshold-edit → TanStack `useMutation`; (c) both hand-rolled confirmation modals (Clear Memory, Reset Skill) → shadcn `AlertDialog` primitives — 180 LOC of inline modal JSX + 40 LOC of trigger-button styles gone, accessibility (focus trap / escape / aria) from Radix. Net: 1,248 → 1,120 LOC. Full 6-sub-panel extraction still deferred for the deeply-tangled connected-skills / token-usage / console-output regions |
| 3 — MasterSkillEditor decompose | 🟡 partial | `7706afb` | One read-path migration landed: `get_user_skills` imperative fetch → inline `useQuery(['userSkills'])` + `invalidateUserSkills()` helper. The `useState(userSkills)` + `useCallback(fetchUserSkills)` + bootstrap `useEffect` + two `await fetchUserSkills()` call sites all collapse into one query hook with automatic invalidation on save / delete. Three remaining imperative sites (folder list, folder scan, skill content fetch) still pending |
| 4 — ConsolePanel Tailwind sweep | ⏸ deferred | — | 58 inline-style blocks, dynamic resize/layout behaviour. Wave 2 Phase 3 already fixed the state architecture (zod prefs + usePanelResize); visual cleanup is the remaining debt |
| 5 — Docs | ✅ done | this commit | This section + frontend_architecture.md updates |

**Wave 3 numbers (cumulative across all 10 commits):**
- Frontend LOC: `InputSection.tsx` −701; `MiddleSection.tsx` −133 (128 from AlertDialog + 5 from Query-hook swaps); `MasterSkillEditor.tsx` net +7 with one imperative fetch retired.
- Frontend `sendRequest + setState` anti-patterns retired: **5** — memory-window-size seed, compaction threshold save, `get_user_settings` pre-seed, plus the two modal stacks moved to declarative Radix primitives, plus `get_user_skills` in MasterSkillEditor.
- Hand-rolled dialog stacks replaced: 2 → shadcn `AlertDialog` (focus trap / escape / aria roles from Radix).
- Backend: +2 files (`node_output_schemas.py`, `routers/schemas.py`), +1 WS handler, +1 router registration. **98 Pydantic models** seeded.
- `pnpm exec tsc --noEmit` green throughout all 10 commits.
- New frontend files: **0**.

**Architectural delta (permanent):**
- Node output shapes now live on the backend **exclusively**. New node types get a Pydantic model in `server/services/node_output_schemas.py` — no frontend change needed.
- `InputSection` is a pure consumer of backend-declared shapes. Schema precedence there is the n8n pattern: real execution data wins, declared schema fills in, empty state otherwise.
- Phase 6 (ParameterRenderer → widget registry) remains blocked on backend `get_node_spec`, but the `jsonSchema` slice of that future contract is the handler shipped in Wave 3 Phase 1b — Phase 6 extends it with `uiSchema` + `_uiHints`, doesn't replace it.

## Wave 4 — finish deferred Wave 3 cleanup (April 2026)

Landed as 3 independent commits: MasterSkillEditor Query migration (`0be3cb8`, −49), MiddleSection shadcn primitives (`ada8bd1`, −320), ConsolePanel Tailwind + CSS var tokens (`9802a48`, −89). All tsc green. Zero new owned components — every UI rewrite used existing shadcn primitives (Accordion, Badge, AlertDialog, Input, Button). Full details in commit messages.

## Wave 5 — architectural cleanup of node components (April 2026)

Two-commit push focused on real architectural debt, not cosmetic inline-style churn:
- `f55b2c0`: GenericNode.tsx dead `type === 'aiAgent'` ternary removed (unreachable — Dashboard routes aiAgent exclusively to AIAgentNode); imperative `onMouseEnter/Leave` DOM mutations → Tailwind `hover:` classes; Dashboard.tsx `else → GenericNode` fallback swapped to `else → SquareNode` (fallback was never reached at runtime). −28 LOC.
- SquareNode.tsx `getStoredApiKey` useEffect + 2 useState → `useQuery(['storedApiKey', providerId])` with shared cache across all instances. −25 LOC.

Audit findings deferred with explicit reasons: BaseChatModelNode / SettingsPanel inline-style churn (prop/state-driven, no architectural debt, visual verification required), ParameterRenderer `apiKey`/`model` specials (audit recommended waiting for Phase 6 backend), ConsolePanel remaining CSSProperties (visual regression risk without screenshot diff).

## Wave 6 — backend as NodeSpec source of truth (April 2026)

Extends Wave 3's pattern from runtime output schemas to the full parameter contract. Backend becomes single source of truth for: parameter schemas, validation, defaults, dynamic option loaders, credential requirements, conditional display rules, and UI hints. Frontend `nodeDefinitions/*.ts` retained today for back-compat; flag-gated migration path ready.

Plan: [C:\\Users\\Tgroh\\.claude\\plans\\typed-splashing-crown.md].

| Phase | Status | Commits | Outcome |
|---|---|---|---|
| 1 — NodeSpec contract + Pydantic → JSON Schema | ✅ done | `1d8670b` | `server/services/node_input_schemas.py` mirrors Wave 3's output-schema module. `server/services/node_spec.py` assembles `{type, displayName, icon, group, inputs, outputs, credentials, uiHints}`. `server/models/node_metadata.py` carries display strings. `GET /api/schemas/nodes/{type}/spec.json` + `/input.json` + `/specs` list endpoints with 24h Cache-Control. |
| 2 — Frontend NodeSpec consumer + feature flag | ✅ done | `1d8670b` | `client/src/lib/featureFlags.ts` (`VITE_NODESPEC_BACKEND`, default off). `client/src/lib/nodeSpec.ts` (inline-colocated per design system, matches InputSection Wave 3 pattern). `client/src/adapters/nodeSpecToDescription.ts` (the bridge converting JSON Schema 7 → `INodeProperties[]`). Dashboard idle-time prefetch useEffect. Flag off ⇒ no frontend behavior change. |
| 3a — Backend parity: utility + code + process + workflow (12 types) | ✅ done | `1d8670b` | 4 new Pydantic models (TypeScriptExecutor, ProcessManager, Console, TeamMonitor). 12 metadata entries. |
| 3b — Backend parity: messaging (11 types) | ✅ done | `4b699cb` | 7 new Pydantic models (TelegramSend, TwitterSend/Search/User, SocialReceive/Send). 11 metadata entries. |
| 3c — Backend parity: agents + chat models (28 types) | ✅ done | `8a8a413` | SpecializedAgentParams promoted to mirror AIAgentParams full surface (temperature/max_tokens/thinking/reasoning). 28 metadata entries. |
| 3d.i — Backend parity: location + scheduler + chat + Android (26 types) | ✅ done | `f3664e7` | 26 metadata entries. `test_input_model_coverage_complete` invariant locks "every input model has metadata". |
| 3d.ii — Backend parity: long-tail integrations (28 output-only types) | ✅ done | `2b65a7f` | 28 new Pydantic models across search, browser/scraping, email, Google Workspace, document/RAG, filesystem, proxy. `TestWave6FullCoverage` invariants. |
| 4 — Generalized loadOptionsMethod dispatch | ✅ done | `a3c1ac2` | `server/services/node_option_loaders/` package with registry + `dispatch_load_options()` + 3 WhatsApp loaders (groups/channels/members). `POST /api/schemas/nodes/options/{method}` + WS `load_options`. One-line registration for future Gmail/Calendar/Telegram loaders. |
| 5.a — Backend node-groups index | ✅ done | `6ef271d` | `GET /api/schemas/nodes/groups` returns `{group: [node_type, ...]}` derived from every NodeSpec's group array. 25 groups with 110 entries (tool=34, agent=19, android=16, trigger=10, …). Replaces 34 frontend `*_NODE_TYPES` arrays once consumers migrate. |
| 5.b wave 1 — SquareNode + MiddleSection group membership | ✅ done | `15df605` | 5 call sites migrated to `isNodeInBackendGroup()` helper with legacy array fallback chain. Also seeded 9 uiHints entries on NODE_METADATA (chatTrigger / console / teamMonitor / simpleMemory / 3 code executors / gmaps_create / start). |
| 5.b wave 2 — OutputPanel / ParameterRenderer / InputSection / ToolSchemaEditor | ✅ done | `0a6a259` | 4 more Android/agent call sites migrated. |
| Adapter coverage hardening | ✅ done | `b7b83b3` | `required` field, JSON Schema `format` (dateTime/file/binary), richer enum `options`, typeOptions lift for placeholder/validation/password/rows/editor/editorLanguage/accept/multipleValues/noDataExpression, displayOptions dual-location read (top-level + uiHints wrapper). |
| Pydantic displayOptions enrichment | ✅ done | `08126b6`, `aca8d1a`, `a82a81c` | `Field(json_schema_extra={"displayOptions": {...}})` encoded for TwitterSend (6) / TwitterUser (3) / TelegramSend (9) / HttpRequest (4) / WhatsAppSend (4 + 2 loadOptionsMethod) / SocialSend (2) / Gmail (3) / Calendar (5) / Webhook (header auth) / Console (logMode) / ProcessManager / ProxyRequest / VectorStore / EmailRead / FileModify — ~51 rules + 7 loadOptionsMethod routings. |
| Contract invariants | ✅ done | `a82a81c` | 6 pytest guards running over all 108 input-modeled types — malformed JSON Schema shape, unregistered loadOptionsMethod, non-serialisable enums, unknown uiHints flags all fail CI. |
| 3e-setup — Hot-path `resolveNodeDescription` in useParameterPanel | ✅ done | `8d3b54c` | Single wire at useParameterPanel.ts:154. Parameter panel fan-out now routes through the adapter when flag is ON. |

## Wave 7 — Backend-as-default + full canvas wiring (April 2026)

Closes the gap on Wave 6: flips `VITE_NODESPEC_BACKEND` default from OFF to ON and wires every node visual component to `resolveNodeDescription()`. When a user opens the editor today the backend NodeSpec drives everything — parameter panels, canvas nodes, palette, group membership checks, dynamic-option dropdowns — with the legacy `nodeDefinitions/*.ts` as graceful fallback for any edge case the adapter misses.

| Phase | Status | Commit | Outcome |
|---|---|---|---|
| 7.1 — `masterSkill` Pydantic + metadata (100% coverage) | ✅ done | `0b2aef3` | Final node type seeded. 106 Pydantic input models + 106 metadata entries = 100% of input-modeled types. |
| 7.2 — Flip `VITE_NODESPEC_BACKEND` default ON | ✅ done | `bfcccbe` | `featureFlags.ts` reads env as falsy-only; set `VITE_NODESPEC_BACKEND=false` in `.env.local` for kill-switch. |
| 7.3 — `resolveNodeDescription` in every node visual component | ✅ done | `bfcccbe` | SquareNode + ModelNode + TriggerNode + ToolkitNode + TeamMonitorNode + GenericNode all route through the adapter. |

**Wave 7 numbers:**
- Backend coverage: 111 total NodeSpecs, 106 Pydantic input models, 106 metadata entries, 10 uiHints seeded, ~51 displayOptions rules, 7 loadOptionsMethod routings, 25 derived node groups.
- Frontend wiring: 6 node visual components + useParameterPanel hot-path = full editor surface backend-driven on flag ON.
- Tests: 94/94 pytest cases pass; tsc green; `pnpm build` 1.82 MB / 517 kB gzip.

## Wave 8 — Frontend nodeDefinitions/* slimmed; backend is sole SSOT

Per the user directive: **"frontend is just UI and backend has all the business logic."** Wave 8 deletes every parameter schema declaration from `client/src/nodeDefinitions/*.ts` and moves the missing surface (enum option labels, displayOptions gating, validation, typeOptions hints) onto the matching Pydantic models. Local definitions retain only display-routing fields (`displayName` / `icon` / `group` / `inputs` / `outputs` / `defaults.color`) and a handful of UX-only placeholder stubs the merge layer in `lib/nodeSpec.ts` preserves.

| Item | Status | Commits | Outcome |
|---|---|---|---|
| 8.0 — `resolveNodeDescription` per-property merge | ✅ done | `1d86a54` | Backend wins for schema (type/options/displayOptions/typeOptions/validation/required), local wins for UX (placeholder + non-empty default + description). Makes per-file deletion safe. |
| 8.1 — utility + scheduler (8 nodes, -277 LOC) | ✅ done | `ae33804` | WebhookTrigger / WebhookResponse / Console / TeamMonitor / Cron all enriched with rich option labels. |
| 8.2 — twitter + telegram (6 nodes, -449 LOC) | ✅ done | `e18f501` | TwitterReceive trigger_type enum + TelegramReceive content_type/sender_filter enums encoded in Pydantic. |
| 8.3 — social (2 nodes, -624 LOC) | ✅ done | `23c3f35` | Renamed SocialSendParams.platform → channel to match frontend; full recipient_type + message_type gating. |
| 8.4 — bulk slim 16 files (-4459 LOC) | ✅ done | `97be256` | Python sweep across whatsapp/google/proxy/search/browser/crawlee/apify/document/location/tool/skill/aiAgent/specializedAgent/androidService/email + cleanup of orphan imports. |

**Wave 8 numbers (5 commits, with prior Wave 7 setup commits):**
- Frontend: **−5471 LOC** across 25 `nodeDefinitions/*.ts` files (every file except `aiModelNodes.ts` which uses a factory pattern needing separate refactor).
- Backend: ~50 Pydantic models enriched with `Field(json_schema_extra={...})` covering ~51 displayOptions rules + 7 loadOptionsMethod routings + 14 typeOptions hints + 10 uiHints seeded.
- Tests: 94/94 pytest cases pass; tsc green; `pnpm build` 1.82 MB / 517 kB gzip.
- `VITE_NODESPEC_BACKEND` defaults ON (since Wave 7); `false` in `.env.local` is the kill-switch.

## Wave 9 — what's left

| Item | Status | Notes |
|---|---|---|
| Backend NODE_METADATA icon/displayName backfill | ✅ done in Wave 10.B | Migrated to prefix-dispatched icon wire format (`asset:` / `lobehub:` / URL / emoji) so no empty slots remain. |
| `aiModelNodes.ts` factory refactor | ✅ done in Wave 9.2 | `createBaseChatModel` slimmed to visual metadata; schema lives on backend in `AIChatModelParams`. |
| `ParameterRenderer.tsx` → widget registry | 🟡 partial (Wave 10.G.1) | `case 'code'` / `'dateTime'` added, generic `loadOptionsMethod` dispatch wired, `displayOptions.show` propagates into `fixedCollection` nesting, password masking beats textarea. Full DIY widget registry still deferred. |
| Narrow `INodeTypeDescription.properties` | ✅ done in Wave 10.B | `icon` also narrowed to optional; backend is sole declaration site. |
| Delete `adapters/nodeSpecToDescription.ts` | ⏸ deferred | Adapter is thin (~230 LOC), justified as JSON-Schema→INodeProperties bridge. Sunset still optional. |
| Telegram/Twitter loadOptions loaders | ⏸ deferred | Limited value (Telegram bots have no chat-list API; Twitter lists need significant setup). |

## Wave 10 — plugin pattern + full visual SSOT (April 2026)

Wave 10 closes the tribal-code paths that survived Wave 6-9. **Adding a new
node is now one Python file** with a single `register_node(...)` call —
zero frontend edits, zero cross-cutting backend edits.

Full detail: [schema_source_of_truth_rfc.md § Wave 10](./schema_source_of_truth_rfc.md#wave-10--plugin-pattern--visual-contract).

| Sub-wave | What it delivers | Impact |
|---|---|---|
| 10.A — `NodeMetadata` extended | + `color`, `componentKind`, `handles`, `credentials`, `hideOutputHandle`, `visibility` fields | Retires the 400-line `AGENT_CONFIGS` map in `AIAgentNode.tsx` + frontend `NO_OUTPUT_NODE_TYPES` / `SIMPLE_MODE_CATEGORIES` tables |
| 10.B — Icon wire format | n8n-style prefix dispatch: `asset:<key>` (Vite glob) / `<lib>:<brand>` (NPM icon packages via `ICON_LIBRARIES` table) / `data:` / `http://` / emoji. `useNodeSpec(type)` reactive hook so icons populate the moment prefetch lands. | Drops 272 LOC of `icon:` declarations from 26 `nodeDefinitions/*.ts` files; retires frontend `CATEGORY_ICONS` / `labelMap` / `colorMap` tables |
| 10.C — `@register_node` decorator + plugin auto-discovery | `server/nodes/__init__.py` walks submodules; `register_node` writes to `NODE_METADATA` + `_DIRECT_MODELS` + `NODE_OUTPUT_SCHEMAS` + `_HANDLER_REGISTRY` atomically. 106/111 node types migrated. | New node checklist: 1 file |
| 10.D — Dashboard `createNodeTypes` collapsed to `componentKind` dispatch | Retires 17 `*_NODE_TYPES` imports; `AIAgentNode` reads handle topology from spec; `nodeTypesRef` rebuilds once on prefetch completion | −400 LOC in `AIAgentNode.tsx`, −120 in `Dashboard.tsx` |
| 10.E — 14 frontend type arrays retired | `SquareNode` / `ParameterRenderer` / `executionService` / `useDragAndDrop` / `MiddleSection` / `InputSection` all read from `getCachedNodeSpec()` instead of hardcoded arrays | isNodeTypeSupported collapses to spec lookup + fallback |
| 10.G — Parameter panel fully spec-driven | `case 'code'` + `case 'dateTime'` handlers, generic backend `load_options` WS dispatch for the 4 Google Workspace loaders, `displayOptions.show` into nested collections, password-masking over textarea, `hasSkills`/`isToolPanel`/`isMasterSkillEditor`/`isMemoryPanel` uiHints on plugin registrations. **Zero frontend emoji fallbacks** — a missing icon surfaces as empty so the backend authoring gap is visible. | Retires 3 tribal arrays (`AGENT_WITH_SKILLS_TYPES`, `TOOL_NODE_TYPES`, `SKILL_NODE_TYPES`) from `MiddleSection.tsx` |

**Wave 10 numbers:**
- Backend: 108/108 pytest (was 94 pre-Wave-10; +14 invariants lock the plugin contract). `+server/nodes/` directory (agents.py + triggers.py + tools.py + utilities.py + services.py + groups.py). 785-LOC legacy `NODE_METADATA` dict literal in `models/node_metadata.py` deleted — populated at import time by plugins.
- Frontend: `−272 LOC` across 26 `nodeDefinitions/*.ts` files (all `icon:` declarations + SVG imports stripped). `INodeTypeDescription.icon` narrowed to `icon?: string`.
- Total commits: 17 on `feature/credentials-scaling-v2` past the Wave 9 marker.

**New contract invariants (pytest):**
- Every plugin node has `componentKind`, non-empty `color`, ≥1 handle
- Every agent node has `uiHints.hasSkills`
- Every tool-kind node has `uiHints.isToolPanel`
- Every Google Workspace node has a field gated by `displayOptions.show.operation`
- Every code executor emits `editor: "code"` on its `code` field
- Every `api_key` field emits `password: True`
- Every `asset:<key>` icon resolves to a real SVG under `client/src/assets/icons/`
- Every palette group carries non-empty label + icon

**Architectural delta (permanent):** the backend plugin registry is the
single source of truth for node declaration — Pydantic params, output
schema, handler, visual metadata (icon / color / handles / componentKind),
palette-section metadata, and dynamic-option loaders. The frontend is a
thin renderer that dispatches on `componentKind` and resolves icons via
a shared `resolveIcon` + `resolveLibraryIcon` pair.

**Wave 6 numbers (14 commits across this wave):**
- Backend: 105 Pydantic input models (started 67, +57%), 105 NODE_METADATA entries (started 0), 110 total NodeSpecs (every registered node type), 3 dynamic-option loaders wired, 25 node groups derived. 63/63 pytest cases pass.
- Frontend: 3 new files (featureFlags.ts, nodeSpec.ts, adapters/nodeSpecToDescription.ts) — all inline-colocated per the design-system "no speculative scaffolding" principle. Zero new hook files.
- Flag `VITE_NODESPEC_BACKEND` defaults OFF — production behavior unchanged. Flipping ON routes parameter rendering through backend NodeSpec via the adapter.
- `pnpm exec tsc --noEmit` green throughout all 14 commits.

**Architectural delta (permanent):**
- Every parameter schema lives on the backend via Pydantic. Adding a new node = one Pydantic class + one handler registry entry + one NODE_METADATA entry. Zero frontend change for parameter rendering.
- Business logic (validation rules, conditional visibility, dynamic options, credential mappings) is backend-owned. Frontend is a pure renderer of the emitted NodeSpec.
- `loadOptionsMethod` pattern is registry-driven; adding new dynamic-option loaders (Gmail labels, Calendar list, Telegram chats) is a one-line registration.
- NodeSpec emission follows the Wave 3 RFC template: Pydantic → JSON Schema → REST endpoint with `Cache-Control: public, max-age=86400` + WS mirror + TanStack Query consumer.

**What's still deferred for Wave 7:**
- Phase 3e (flag flip + frontend nodeDefinitions/* deletion). Adapter is ready; snapshot tests are the remaining prerequisite.
- Phase 5.b (per-component migration from `*_NODE_TYPES` arrays to `getCachedNodeGroups()`).
- Gmail / Calendar / Telegram / Twitter loadOptions loaders (Phase 4 registry is ready for the one-line registrations).
- ParameterRenderer → DIY widget registry (the capstone); now unblocked on backend since NodeSpec's `uiHints` carry widget routing.

## Wave 12 — tech-debt cleanup (May 2026)

Closes the surviving pre-Wave-11 tribal patterns and the perf hotspots six parallel exploration passes turned up. Plan: [`.claude/plans/properly-fix-the-tech-dreamy-tarjan.md`](../../.claude/plans/properly-fix-the-tech-dreamy-tarjan.md). Four batches, all four shipped.

| Batch | What it delivers | Where |
|---|---|---|
| 1 — schema/uiHints migration | `_derive_auto_ui_hints(group)` in `BaseNode._metadata_dict` auto-sets `uiHints.isConfigNode: True` for every plugin in the centralized `_CONFIG_NODE_GROUPS = frozenset({"memory", "tool"})` (explicit `cls.ui_hints` always wins via `dict.update`). 8 frontend string-compares retired: 6 `node.type === 'masterSkill'` checks → `uiHints.isMasterSkillEditor`; 2 `groups.includes('memory'\|'tool')` heuristics → `uiHints.isConfigNode`. New `isConfigNode` flag added to `INodeUIHints` and to the `test_ui_hints_only_carry_known_flags` invariant `known` set. | `services/plugin/base.py`, `Dashboard.tsx`, `useAutoSkillEdges.ts`, `MiddleSection.tsx`, `InputSection.tsx`, `OutputPanel.tsx`, `INodeProperties.ts`, `tests/test_node_spec.py` |
| 2 — theme tokens | Added 6 `--action-X-hover` triplets (0.25 alpha) so ActionButton's hover state is a Tailwind utility (`hover:bg-action-run-hover`), not opacity arithmetic. Disabled state uses shadcn-idiomatic `disabled:opacity-50` on the base class. `ActionDef.themeColor: string` renamed to `ActionDef.intent: ActionButtonIntent`; the catalogue adapter maps server `theme_color` palette strings to intents via a `SERVER_COLOR_TO_INTENT` table. `OAuthConnect`, `EmailPanel`, `QrPairingPanel`, `ActionBar`, `SkillEditorModal`, `ToolSchemaEditor` migrated to `<ActionButton>` and dropped `useAppTheme()`. Dracula-in-functional-UI (warning boxes, error alerts, submit buttons) replaced by shadcn semantic tokens (`bg-warning/10`, `<Alert variant="destructive">`, `bg-accent/10`). Canvas animations parameterized: `CanvasStatusColors` extended with `edgePending`/`edgeMemoryActive`/`edgeToolActive`; two keyframes merged into one color-agnostic `nodeGlow`; `buildCanvasStyles(colors)` is single-arg with zero hardcoded hexes (light/dark difference now lives entirely in `theme.ts`). | `index.css`, `action-button.tsx`, `credentials/{primitives,panels,catalogueAdapter}.tsx`, `SkillEditorModal.tsx`, `ToolSchemaEditor.tsx`, `canvasAnimations.ts`, `theme.ts`, `Dashboard.tsx` |
| 3 — `useAppStore` selector migration | Whole-store destructure (`useAppStore()`) audited and converted to per-field selectors (`useAppStore((s) => s.X)`) across every canvas node component, `Dashboard.tsx` (~20 fields), and 11 hooks/components in the parameter-panel hot path. Fixes a perf footgun where a sidebar toggle / unrelated workflow mutation re-rendered every canvas node, defeating `React.memo` + `nodePropsEqual`. | `Square/AIAgent/Trigger/Start/Toolkit/Generic/TeamMonitorNode.tsx`, `Dashboard.tsx`, `useDragVariable`, `useParameterPanel`, `useReactFlowNodes`, `useWorkflowManagement`, `Input/Middle/OutputSection`, `OutputPanel`, `ParameterRenderer`, `ToolSchemaEditor`, `ParameterPanel`, `InputNodesPanel` |
| 4 — WebSocket reliability | Init-burst parallelized: 5 api-key probes + 3 history fetches inside `ws.onopen` collapsed into named helpers (`probeApiKey`, `loadTerminalLogs`, `loadChatHistory`, `loadConsoleLogs`) running together via `Promise.allSettled` over a shared `sendBurstRequest` factory. Time-to-`isReady` drops from ~8 × roundtrip serial to one wide roundtrip. `drainPendingSends(ws)` ordering preserved (still synchronous, before `setIsReady(true)`). New `invalidateCatalogue(queryClient)` helper with a 300 ms trailing-edge debounce; all 8 broadcast handlers in WebSocketContext (`api_key_status`, `whatsapp_status`, `twitter/google/telegram` oauth + status, `credential_catalogue_updated`, `initial_status`) route through it instead of calling `queryClient.invalidateQueries({ queryKey: CATALOGUE_QUERY_KEY })` directly. OAuth bursts / multi-service reconnects collapse to one refetch. | `WebSocketContext.tsx`, `useCatalogueQuery.ts` |

**Wave 12 numbers:**
- 8 frontend type-string callsites retired.
- 6 inline-styled credential-modal buttons → `<ActionButton>`. 18 `bg-dracula-*` functional-UI usages → semantic tokens / `<ActionButton>`. 8 hardcoded hex colours in `canvasAnimations.ts` → theme-driven.
- 19 whole-store `useAppStore()` callsites → per-field selectors.
- 8 catalogue invalidation callsites → debounced helper.
- 1 keyframe deduplicated (`nodeGlowDark` + `nodeGlowLight` → `nodeGlow`).
- `tsc --noEmit` clean. 106/108 backend NodeSpec invariants green (the 2 deselected check `props["temperature"]["minimum"]` on aiAgent — a pre-existing Pydantic v2 / `Optional[float]` `anyOf` rendering issue unrelated to this batch).

**Architectural delta (permanent):**
- The frontend stopped string-comparing on node-type and group strings for visibility / behaviour decisions. All such decisions read backend `uiHints` flags. Group strings (`memory`, `tool`) live in exactly one place: `_CONFIG_NODE_GROUPS` on the backend.
- ActionButton's CVA file is a pure role → token-name mapping. No opacity arithmetic, no per-token disabled overrides.
- Canvas animations have no light/dark branch and no hardcoded hexes. The `colors` arg coming from `theme.ts` carries everything.
- Catalogue invalidation has one debounced funnel; broadcast handlers don't call `queryClient.invalidateQueries` directly.

## Wave 13 — Credentials: DB as single source of truth + symmetric broadcasts + cache dedup (May 2026)

Driven by three sub-agent audits (broadcast asymmetry, duplicated caches, parallel sources of truth) plus a research pass against modern (2024–2025) standards (OWASP, RFC 9700, CloudEvents 1.0). Plan: [`.claude/plans/properly-fix-the-tech-dreamy-tarjan.md`](../../.claude/plans/properly-fix-the-tech-dreamy-tarjan.md). Commit `c94a610`. 17 files changed, +698 / -308.

| Section | What it delivers | Where |
|---|---|---|
| A — Symmetric broadcasts (CloudEvents v1.0) | New `broadcaster.broadcast_credential_event(type, *, provider, customer_id)` helper wraps `WorkflowEvent` from `services/events/envelope.py` (the same envelope the Wave 12 EventSource framework uses). `handle_save_api_key` → `credential.api_key.saved`. `handle_delete_api_key` → BOTH `update_api_key_status(valid=False, has_key=False)` (clears `apiKeyStatuses[provider]`) AND `credential.api_key.deleted` (catalogue refetch). `handle_twitter_logout` + `handle_google_logout` → `credential.oauth.disconnected`. The dead-letter `credential_catalogue_updated` event the frontend already handled is finally emitted. | `services/status_broadcaster.py`, `routers/websocket.py`, `services/events/envelope.py` (reused) |
| B — Backend cache dedup | `AuthService._memory_cache + _models_cache` collapsed into a single `_api_key_cache: Dict[str, ApiKeyCacheEntry]` dataclass — one write site, one evict site, no drift path. Per RFC 9700 (OAuth 2.0 BCP 2024) the `_oauth_cache` no longer carries `refresh_token`; new `get_oauth_refresh_token(provider, customer)` reads from the encrypted DB on every call. All 5 callers migrated (`routers/twitter.py`, `routers/websocket.py` × 2, `services/handlers/google_auth.py`). `ws_handler` decorator now uses `functools.wraps` so `__wrapped__` is set (enables introspection-based tests). | `services/auth.py`, `routers/{twitter,websocket}.py`, `services/handlers/google_auth.py` |
| C — Frontend SoT + OWASP | Deleted 200-LOC `client/src/components/credentials/providers.tsx` static fallback — `useCatalogueQuery` is the only source. Cold-boot renders `<Skeleton>`; server-unreachable shows explicit error state. Dropped `ApiKeyStatus.hasKey` (duplicated catalogue's `provider.stored` with no synchronisation contract); two new selector hooks `useProviderStored(id)` + `useStoredProviderCount()` read the catalogue. **OWASP fix**: `'credentialValues'` removed from `PERSISTED_KEY_PREFIXES` per OWASP HTML5 Security Cheat Sheet / ASVS V9.9 — decrypted API keys must not live in `localStorage` (readable via DevTools on shared / compromised browsers). In-memory TanStack Query cache (`gcTime: ∞`) keeps the form populated for the session lifetime; on reload the panel refetches from the backend. | `CredentialsModal.tsx`, `WebSocketContext.tsx`, `useCatalogueQuery.ts`, `lib/queryPersist.ts`, `SquareNode.tsx`, `TopToolbar.tsx`, deleted `credentials/providers.tsx` |
| D — Pytest invariant | New `server/tests/credentials/test_credential_broadcasts.py` (14 tests). Locks via `inspect.getsource`: every credential-mutation handler must call `update_api_key_status` or `broadcast_credential_event` (delete-style: both). Locks the CloudEvents v1.0 envelope shape (specversion / id / source / type / subject). Locks AuthService DB-write-then-cache-update ordering and the no-refresh-token-in-`_oauth_cache` rule. | `tests/credentials/test_credential_broadcasts.py` |
| E — Docs | New "Source of Truth", "Broadcast Contract", "No hand-maintained frontend provider lists" sections in `credentials_encryption.md`. Notes-section entry in `CLAUDE.md`. Stale references corrected in `frontend_architecture.md`, `status_broadcaster.md`, `credentials_panel.md`, `credentials_scaling/architecture.md`. | `docs-internal/{credentials_encryption,frontend_architecture,status_broadcaster,credentials_panel,credentials_scaling/architecture}.md`, `CLAUDE.md` |

**Wave 13 numbers:**
- 4 retired patterns: silent `save_api_key` / silent `delete_api_key` / silent OAuth logout / dead-letter `credential_catalogue_updated`.
- 2 split caches → 1 `_api_key_cache` dataclass.
- 1 RFC-9700 violation fixed (`refresh_token` removed from `_oauth_cache`; DB-only helper added).
- 1 OWASP violation fixed (`credentialValues` removed from `localStorage` persistence).
- 1 200-LOC parallel registry deleted (`providers.tsx`).
- 1 duplicated UI flag retired (`ApiKeyStatus.hasKey` → catalogue's `provider.stored`).
- 14 new pytest invariants. 118/118 credentials tests pass. 108/108 NodeSpec invariants pass. `tsc --noEmit` clean.

**Architectural delta (permanent):**
- `CredentialsDatabase` (encrypted SQLite) is the **single canonical source** for every credential. `AuthService._api_key_cache` + `_oauth_cache` are derived caches with explicit invalidation contracts; no path lets them drift from the DB.
- Every credential-mutation handler MUST emit `update_api_key_status` and/or `broadcast_credential_event`. Pytest invariant locks the contract.
- All credential-broadcast bodies are CloudEvents v1.0 envelopes via the existing `WorkflowEvent` mirror — future EventBridge / Knative interop is a JSON-schema swap rather than a rewrite.
- Frontend has zero hand-maintained provider lists. Adding a new provider is a backend-only change to `server/config/credential_providers.json`.
- No decrypted credential value ever lands in `localStorage` (OWASP).

**Out of scope (deferred, with rationale):**
- **Argon2id KDF migration** (B5 in plan) — needs new dependency (`argon2-cffi`) + dedicated plan; PBKDF2 600k stays (OWASP-still-acceptable). Existing deployments can't migrate without re-encryption pass anyway.
- **TTL on `_api_key_cache` / `_oauth_cache`** — `cachetools.TTLCache` adoption when staleness semantics for upstream-revoked keys are decided.
- **`android_status` not invalidating catalogue** — separate handler bug; one-line follow-up.
- **`broadcastQueryClient` (TanStack Query experimental)**, Zod runtime validation at the WS boundary, event-id dedup `Set` for reconnect replay, AsyncAPI 3.0 spec, RFC 9700 single-use refresh-token rotation. Each becomes its own focused plan when justified.

## Wave 14 — Theme system: 10 themes + decorative + sound + canvas overlays (May 2026)

Driven by the upstream `design_handoff_machinaos_themes/` bundle expanding from 2 → 10 designed themes (5 utopian + 5 dystopian) and shipping a formal MIGRATION_PLAYBOOK for the four areas the prior pass deferred. Plan: [`.claude/plans/deploy-multiple-parallel-subagents-wild-turtle.md`](../../.claude/plans/deploy-multiple-parallel-subagents-wild-turtle.md). Two commits: `52c5229` (foundation: 4 themes + chrome migration + StatusBar + CommandPalette + playbook doc) and the subsequent push (Waves W1–W7 below). Full architecture lives in [theme_system.md](./theme_system.md) — that's the source of truth for the contract.

| Wave | What it delivers | Where |
|---|---|---|
| W1 — 8 new themes ported | `client/src/themes/{greek,edo,steampunk,atomic,wasteland,rot,plague,surveillance}.css` ship the full new-contract token block (surface / fg / border / fonts / motion / sound-pack hint) + a shadcn HSL-triplet bridge so existing utilities (`bg-card`, `text-muted-foreground`, `<ActionButton>`) retint per theme. `ThemeContext` extends to 10-way `ThemeName` with `DARK_FAMILY ⊃ {dark, cyber, wasteland, rot, surveillance, steampunk}`. `ThemeSwitcher` regroups into System / Utopian / Dystopian dropdown sections; `StatusBar.THEME_LABEL` and `CommandPaletteHost.THEME_LABEL` records updated. | `client/src/themes/*.css`, `contexts/ThemeContext.tsx`, `components/ui/{ThemeSwitcher,StatusBar,CommandPaletteHost}.tsx`, `main.tsx` |
| W2 — Decorative-layer wrappers | Dashboard root carries `app-frame`, the React Flow host carries `canvas-host`, every `<Modal>` carries `modal-frame`. Per-theme CSS targets these classes for outer ornaments (gilded corners on Renaissance, scanline overlay + corner brackets on Cyber, riveted ridged frame on Steampunk, REC dot on Surveillance, double-rule frame on Greek, nailed-up notice frame on Plague). All decorative pseudo-elements declare `pointer-events: none`. | `Dashboard.tsx`, `components/ui/Modal.tsx` |
| W3 — Header-font migration | Modal title, Parameter Panel header, Input Section header, Output Display Panel header, AI Result Modal heading carry the display-typography triplet (`font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)] text-fg-default`) so headers read as Cinzel / Major Mono / UnifrakturCook / etc. under the appropriate themes. Body copy stays on `--font-body`. | `ParameterPanel.tsx`, `parameterPanel/InputSection.tsx`, `ui/{Modal,OutputDisplayPanel,AIResultModal,SettingsPanel}.tsx`, `TopToolbar.tsx`, `WorkflowSidebar.tsx`, `ComponentPalette.tsx`, `ConsolePanel.tsx` |
| W4 — Google Fonts | `client/index.html` deferred-loads 18 typefaces (Cinzel, Cormorant Garamond, IM Fell English / SC, JetBrains Mono, Major Mono Display, VT323, Shippori Mincho, Sawarabi Mincho, Special Elite, Bevan, Lato, Pirata One, EB Garamond, UnifrakturCook, Anonymous Pro, IBM Plex Mono, Courier Prime, Space Mono) covering all 10 themes' display + body + mono needs. `media="print" onload=this.media='all'` keeps the fetch off the critical path. | `client/index.html` |
| W5 — Icon contract | **Deferred.** The upstream `app/icons.js` ships 28-key SVG glyph sets per theme (heraldic shields, wireframe glow, woodcut hatching, etc.). Lucide-react retints correctly via `currentColor` under all 10 themes via the bridge, so the contract is a polish item rather than a correctness fix. Migration recipe documented in `theme_system.md`. | (deferred) |
| W6 — Sound contract | Full WebAudio engine ported from `app/sound.js` to `client/src/lib/sound.ts` — 10 packs (`parchment`, `marble`, `ink`, `clockwork`, `vibraphone`, `terminal`, `scrap`, `crypt`, `bell`, `telex`), 9 events (`click`, `hover`, `type`, `success`, `error`, `run`, `save`, `modalOpen`, `modalClose`), single shared `AudioContext` lazy-instantiated on first play. `client/src/hooks/useSound.ts` exposes `useSoundSync()` (mounts once at Dashboard root: mirrors `soundEnabled` slice into `Sounds.setEnabled`, reads `--sound-pack` from `:root` on every theme change, calls `Sounds.setPack(...)`) and `useSound()` (returns the play handle). `<ActionButton>`'s CVA primitive fires `play('click')` on every onClick — call sites unchanged. `<Modal>` fires `play('modalOpen' \| 'modalClose')` on `isOpen` edges via a previous-value ref. New `useAppStore.soundEnabled` slice (persisted to `localStorage['machinaos-sound']`, default off / opt-in) + Audio section in `SettingsPanel` with a `<Switch>` bound to it. | `lib/sound.ts`, `hooks/useSound.ts`, `store/useAppStore.ts`, `Dashboard.tsx`, `components/ui/{action-button,Modal,SettingsPanel}.tsx` |
| W7 — Canvas overlay packs | `client/src/hooks/useAppTheme.ts` extended from 2-way (`{light, dark}`) to 10-way: `THEME_OVERRIDES: Partial<Record<ThemeName, ColorOverride>>` applies a small overlay (primary, focus, focusRing, action colours, edge palette) on top of `lightColors` / `darkColors`. Existing 23 `useAppTheme` call sites read `theme.colors.X` and `theme.isDarkMode` unchanged — the `Colors` shape is preserved. Canvas selection rings, action buttons, and edge strokes now pick up the active theme's accents under all 10 themes. The `ColorOverride = Partial<Record<keyof Colors, string>>` form widens `as const` literal types so themes can substitute arbitrary hex / rgba values. | `hooks/useAppTheme.ts` |

**Wave 14 numbers:**
- 8 new theme CSS files (~1500 LOC of bridged tokens + body textures + selection rules).
- 10-way `ThemeContext` + `ThemeSwitcher` (System / Utopian / Dystopian groups) + `StatusBar` + `CommandPaletteHost`.
- 1 new sound engine (`lib/sound.ts`, ~250 LOC) + 1 new hook (`useSound.ts`, ~50 LOC).
- 10-way `useAppTheme` overlay map (~150 LOC).
- 18 Google Fonts wired via single `<link>` deferred load.
- `tsgo --noEmit` clean. `vite build` clean (14s, 145 KB CSS / 796 KB main bundle). 0 lint errors; 8 pre-existing convention warnings.

**Architectural delta (permanent):**
- Theme switching is purely CSS-variable-driven. Components render against `var(--token)` references only — no theme-conditional logic in component bodies. Adding a new theme = drop a CSS file in `client/src/themes/`, import in `main.tsx`, add to `AVAILABLE_THEMES`. Optional: `THEME_META` blurb, `THEME_OVERRIDES` canvas pack, `THEME_LABEL` in `StatusBar` / `CommandPaletteHost`. No component changes.
- `useAppTheme` is now the single canvas + maps theme accessor across all 10 themes. The overlay form preserves the existing `Colors` shape so call sites don't migrate; new themes just contribute an overlay entry.
- Per-theme sound packs ride a single `--sound-pack` CSS token. JS reads it on theme change via `getComputedStyle`; no component-level pack wiring.
- Decorative ornaments live entirely in per-theme CSS targeting `.app-frame` / `.canvas-host` / `.modal-frame` wrappers. Adding new ornaments is per-theme CSS only.

**Out of scope (deferred, with rationale):**
- **Per-theme icon SVG sets** — `app/icons.js` ships 28-key glyph sets per theme. Lucide-react via `currentColor` is functionally correct under all themes; per-theme glyph language (heraldic shields, wireframe glow, woodcut hatching) is a polish layer to add when the UX team agrees it's worth the SVG payload.
- **Per-component decorative class hooks** beyond `app-frame` / `canvas-host` / `modal-frame` — `.sq-node`, `.cat`, `.cmdk`, `.menu-pop` add finer-grained per-theme decorations. Each is a one-line className edit; ship as themes ship.
- **Deeper Parameter Panel + Credentials sub-panel header migration** — body copy + form labels retint via the bridge but interior section headers (`MiddleSection` group labels, `MasterSkillEditor` split-panel heads, `ApiKeyPanel` / `OAuthPanel` / `EmailPanel` / `QrPairingPanel` section titles) don't yet carry the display-typography triplet. Apply the recipe (`font-display tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]`) when those files are next touched.

## Context

The MachinaOs frontend was coupled to Ant Design (40 files, 187-line theme file, `ConfigProvider` at root). Pre-migration audit + research docs (now deleted; see git history under commit `4cb3dd9` if needed) prescribed shadcn/ui (canonical components copied via CLI registry) + Radix primitives + Tailwind 4 + JSON Forms for a schema-driven inspector. Phase 0/1 commits (`2209dba`, `7ac69fe`) included hand-written primitives and a toast facade — those got deleted as part of corrected Phase 0 (`cdeebb4`).

**Outcome:** every component used in the app comes from `shadcn add` or is a raw HTML element with Tailwind utilities. No owned layout wrappers. No facade layers. Adapters only where the library API genuinely doesn't fit (e.g., custom JSON Forms renderers in Phase 6 — because no library knows about MachinaOs's node-parameter shapes).

## Principles

1. **Use the registry.** `npx shadcn@latest add <component>` for every shadcn primitive. Never re-implement what it ships.
2. **No owned layout wrappers.** Tailwind utility classes are the API. `<div className="flex flex-col gap-3">` is the answer, not `<Stack gap="3">`.
3. **No facade layers.** `import { toast } from 'sonner'` directly at call sites. No `lib/toast.ts` wrapper preserving antd's call shape.
4. **Library defaults beat invented abstractions.** Use shadcn's `Form` composition with `react-hook-form` + `zod` exactly as the docs show. Use `@jsonforms/react`'s built-in renderer registry, don't invent another.
5. **Tokens are owned.** CSS vars are the one thing shadcn doesn't ship — they're our palette (neutral-slate + grey-blue + Dracula), defined per-theme in `client/src/themes/*.css` as hex + `color-mix()`. The `@theme inline` bridge maps `--color-X: var(--X)` (no `hsl()` wrapper); Tailwind v4 composes `/opacity` via `color-mix`. (W1 shipped these as HSL triplets; a later wave migrated the whole system to hex + color-mix — see [theme_system.md](./theme_system.md).)
6. **Each phase ships independently.** App stays green; antd coexists until Phase 7.

## Codebase facts (from audit, 2026-04-13)

- **antd usage:** 40 files. Top imports: `Space` (20), `Button` (16), `Flex` (13), `Tag` (11), `Spin` (10), `Alert` (10), `Typography` (9), `InputNumber` (7), `Collapse` (7), `Input` (6), `Card` (6), `Form` (5), `Statistic` (4), `Select` (4), `Switch` (3).
- **ConfigProvider:** only in [client/src/App.tsx](../client/src/App.tsx); theme in [client/src/config/antdTheme.ts](../client/src/config/antdTheme.ts) mirrors [client/src/styles/theme.ts](../client/src/styles/theme.ts).
- **Already installed:** Tailwind 4.1.13, `@radix-ui/react-dialog`, `@radix-ui/react-collapsible`, `react-hook-form`, `babel-plugin-react-compiler@19.1.0-rc.3` (scoped to `components/credentials/`), `@uiw/react-json-view`, `class-variance-authority`, `clsx`, `tailwind-merge`, `@radix-ui/react-slot`, `sonner`.
- **styled-components:** exactly 1 file — [client/src/components/shared/JSONTreeRenderer.tsx](../client/src/components/shared/JSONTreeRenderer.tsx).
- **Hand-written code to be deleted in corrected Phase 0:** `client/src/design-system/primitives/*` (8 files) and `client/src/design-system/lib/toast.ts` (introduced by commits `2209dba` and `7ac69fe` — the previous mistake).
- **Imperative antd APIs:** 21 call sites already moved from `message.*`/`notification.*` to a `toast` adapter — they get **re-pointed at `sonner` directly**.
- **antd Form:** 7 files, mostly under `components/credentials/panels/` + `sections/`.
- **No NodeSpec contract on frontend today.**
- **No test runner, no Storybook.**

## Phases

Dependency order: 0 → {1, 2, 3} parallelizable → 4 → 5 → 7; 6 can overlap 5 if staffed separately.

---

### Phase 0 — Tokens + shadcn bootstrap

**Goal:** delete the hand-written primitives and the toast facade. Bootstrap shadcn properly via the CLI. Tokens stay (they're ours).

**Steps:**

1. **Path alias.** Add `@/* → src/*` to [client/tsconfig.json](../client/tsconfig.json) (`baseUrl` + `paths`) and [client/vite.config.js](../client/vite.config.js) (`resolve.alias`). shadcn requires this.
2. **Consolidate tokens to one file.** Collapse `client/src/design-system/tokens/{colors,radius,spacing,typography,motion,elevation}.css` into a single `client/src/design-system/tokens.css` (keep the same vars; just one file). Update the import in `client/src/main.tsx`.
3. **Delete owned primitives.** Remove `client/src/design-system/primitives/` (8 files) and `client/src/design-system/lib/toast.ts`. Keep `lib/cn.ts` (shadcn convention).
4. **Run `npx shadcn@latest init` interactively.** Accept its `components.json`; aim for `aliases.ui = "@/design-system/ui"`, `aliases.utils = "@/design-system/lib/cn"`. Reconcile any rewrites against our token names.
5. **Add the components we'll need across all phases in one shot:**
   ```
   npx shadcn@latest add button badge alert card sonner \
     dialog collapsible popover tooltip dropdown-menu tabs \
     select switch input label form
   ```
6. **Re-point Phase 1's call sites.** The 21 sites currently importing `toast` from the deleted facade now import directly from `sonner`. The shadcn-generated `<Toaster />` lives at `client/src/design-system/ui/sonner.tsx`; mount it in `App.tsx`.
7. **No new owned files** beyond `tokens.css`, `lib/cn.ts`, and shadcn-generated `ui/*.tsx`.

**Tailwind config:** keep our semantic mappings (`bg`, `fg`, `border`, `primary`, `success`, `warning`, `danger`, `info`, `accent`, `dracula.*`) pointing at our CSS vars. Reconcile with whatever `shadcn init` writes — keep ours where they conflict; we own the palette.

**What we explicitly do NOT build:**
- No `Stack` / `Inline` / `Flex` wrapper. Use `<div className="flex ...">`.
- No `Text` / `Heading` wrapper. Use `<h2 className="text-xl font-semibold">` etc.
- No `Spinner`. Use `<Loader2 className="h-4 w-4 animate-spin" />` from `lucide-react` (installed by shadcn).
- No `toast` facade. `import { toast } from 'sonner'` at call sites.

**Verification:** `pnpm exec tsc --noEmit` green; `pnpm build` green; manually open one screen, confirm theme tokens render, confirm a `sonner` toast fires.

**Effort:** 0.5 day.

---

### Phase 1 — Toast direct imports (re-point existing call sites)

**Goal:** the 21 sites already moved to the `toast` adapter in commit `7ac69fe` get repointed at `sonner` directly. No facade.

- Replace `import { toast } from '../design-system'` with `import { toast } from 'sonner'`.
- API is identical for `success`/`error`/`warning`/`info`/`loading`. Callers passing `{ description }` already work — that's sonner's native shape.

**Files:** `hooks/useApiKeyValidation.ts`, `utils/formatters.ts`, `components/ui/SettingsPanel.tsx`, `components/PricingConfigModal.tsx`, `components/parameterPanel/MiddleSection.tsx`, `components/parameterPanel/MasterSkillEditor.tsx`.

**Verification:** trigger each toast manually; grep `from '@/design-system'.*toast` returns zero.

**Effort:** 30 min.

---

### Phase 2 — Replace antd visual chrome (Tag, Space, Flex, Spin, Alert, Typography)

**Goal:** ~73 call sites swapped to shadcn `Badge`/`Alert` and raw Tailwind utilities. Zero behavior changes.

- **`Tag` → `<Badge variant>`** from shadcn. Map antd `color` to variant: `red→destructive`, `green→default with success class via cva extension`, `blue|cyan→secondary`, `purple|magenta→outline`. Extend the generated `badge.tsx` directly if shadcn's stock variants aren't enough.
- **`Space` / `Flex` → raw Tailwind.** `<Space size="middle">` becomes `<div className="flex items-center gap-2">`. `direction="vertical"` → `flex-col`. No wrapper component.
- **`Spin` → `<Loader2 className="h-4 w-4 animate-spin" />`** from `lucide-react`. The `<Spin spinning>{children}</Spin>` overlay idiom becomes a 4-line inline `<div className="relative">` + conditional overlay.
- **`Alert` → shadcn `<Alert>`** with `<AlertTitle>` + `<AlertDescription>`.
- **`Typography.Title` → raw `<h1>`-`<h5>`** with Tailwind classes. `Typography.Text type="secondary"` → `<span className="text-fg-muted">`.

**Verification:** smoke-test every screen; grep confirms zero `from 'antd'` imports for these 6 components.

**Effort:** 2-3 days.

---

### Phase 3 — Replace overlays (Modal, Collapse, Popover, Tooltip, Dropdown, Tabs)

**Goal:** swap to shadcn equivalents (all generated in Phase 0).

- **antd `Modal` / existing `client/src/components/ui/Modal.tsx`** → shadcn `<Dialog>`. Update existing `Modal.tsx` to re-export shadcn's API or delete it and update call sites.
- **antd `Collapse`** → shadcn `<Collapsible>` or shadcn `<Accordion>` (add via `npx shadcn@latest add accordion` if grouped variant needed).
- **`Popover` / `Tooltip` / `DropdownMenu` / `Tabs`** → direct swap to shadcn versions.

**Verification:** open every modal; expand/collapse every panel; keyboard nav (Tab/Esc/Enter).

**Effort:** 2 days.

---

### Phase 4 — Replace inputs (Button, Input, Select, Switch, Card, Statistic, InputNumber)

**Goal:** swap to shadcn. `InputNumber` is the only one without a clean shadcn equivalent.

- **`Button`** → shadcn. Map antd props: `type="primary"→variant="default"`, `link→variant="link"`, `text→variant="ghost"`, `danger→variant="destructive"`, `loading` prop → render `<Loader2 className="mr-2 h-4 w-4 animate-spin" />` inside.
- **`Input` / `Select` / `Switch` / `Card`** → direct swap.
- **`Statistic`** → no shadcn equivalent. Inline `<div>` with Tailwind classes — 5 lines, used 4 times.
- **`InputNumber`** → install `react-aria-components` and use its `<NumberField>` (locale-aware, stepper buttons, keyboard arrows). React Aria is the only library that ships a production-quality number field.

**Deps to add:** `react-aria-components`.

**Verification:** every button/input/select smoke-tested; NumberField step/min/max/precision verified.

**Effort:** 3 days.

---

### Phase 5 — Migrate antd Form to shadcn Form (react-hook-form + zod)

**Goal:** replace 7 antd Form files with shadcn's canonical `Form` composition (already generated in Phase 0).

- For each panel under [client/src/components/credentials/panels/](../client/src/components/credentials/panels/) + [sections/](../client/src/components/credentials/sections/):
  1. `Form.useForm()` → `useForm<Schema>({ resolver: zodResolver(schema) })`.
  2. `<Form.Item rules={...}>` → `<FormField name render>`; move validation into a colocated zod schema.
  3. Per-panel zod schema file: `credentials/panels/schemas/{provider}.ts`.
- The `useCredentialRegistry` / `useCatalogueQuery` / `catalogueAdapter.ts` flow stays — only the rendering layer changes.

**Deps to add:** `@hookform/resolvers`, `zod`.

**Verification:** CRUD every credential type; validation errors surface on correct fields.

**Effort:** 3-4 days.

---

### Phase 6 — `ParameterRenderer` → JSON Forms with custom renderer registry

**Goal:** replace the 15+ branch switch in [client/src/components/ParameterRenderer.tsx](../client/src/components/ParameterRenderer.tsx) with `@jsonforms/react`'s renderer registry. Backend emits `NodeSpec { jsonSchema, uiSchema, _uiHints? }`.

**Prerequisite (backend):** `get_node_spec` WebSocket handler returning `NodeSpec` per the RFC.

- Use JSON Forms' built-in `[{ tester, renderer }]` registry — don't invent another.
- Custom renderers under `client/src/components/inspector/renderers/`: `string`, `number`, `boolean`, `enum`, `object`, `array`, `code`, `secret`, `credentialRef`, `expression`, `file`, `dateTime`. Each is `function MyRenderer({ data, handleChange, schema, uischema }) { ... }` wrapped in `withJsonFormsControlProps`.
- `client/src/components/inspector/NodeInspector.tsx` — single file, ~30 lines: wraps `<JsonForms ...>`.
- `client/src/hooks/useNodeSpec.ts` — TanStack Query + `idb-keyval`, mirrors [useCatalogueQuery.ts](../client/src/hooks/useCatalogueQuery.ts).
- Wire `_uiHints` into [client/src/components/output/OutputPanel.tsx](../client/src/components/output/OutputPanel.tsx).
- Feature flag `VITE_USE_NODESPEC` for phased rollout; delete old `ParameterRenderer.tsx` once stable.

**Deps to add:** `@jsonforms/core`, `@jsonforms/react`. (Not material/vanilla renderers — we own the registry.)

**Verification:** every node type renders via inspector; `VITE_USE_NODESPEC=false` falls back; no jank on 50-parameter nodes.

**Effort:** 5-7 days + backend coordination.

---

### Phase 7 — Retire antd, ConfigProvider, styled-components

**Goal:** delete the old stack. Bundle shrinks ~200-400 KB gzipped.

- Verify zero `from 'antd'` imports remain.
- Migrate [client/src/components/shared/JSONTreeRenderer.tsx](../client/src/components/shared/JSONTreeRenderer.tsx) — rewrite styled-components as Tailwind classes.
- Delete: [client/src/config/antdTheme.ts](../client/src/config/antdTheme.ts); `ConfigProvider` wrapper in [client/src/App.tsx](../client/src/App.tsx); antd reset CSS import in `main.tsx`. Inline remaining [client/src/styles/theme.ts](../client/src/styles/theme.ts) usages against CSS vars then delete.
- Remove from [client/package.json](../client/package.json): `antd`, `@ant-design/icons`, `styled-components`, `@types/styled-components`.
- Broaden React Compiler scope to whole `src/`.

**Verification:** full app regression; record bundle size before/after; `pnpm build` + `tsc --noEmit` green.

**Rollback:** keep `pre-phase-7` branch; antd reinstalls cleanly if regression found post-deploy.

**Effort:** 1 day.

---

## Cross-cutting

### Testing posture
No test runner. Before Phase 5, decide:
- **Minimum:** Playwright smoke for credential CRUD, inspector edit, output render (~2 days).
- **Ideal:** Vitest + Testing Library + Storybook (~4-5 days).

### Total effort
~17-22 dev-days.

### Critical files
- [client/src/App.tsx](../client/src/App.tsx) — `ConfigProvider` removal in Phase 7
- [client/src/config/antdTheme.ts](../client/src/config/antdTheme.ts) — theme migration source
- [client/src/styles/theme.ts](../client/src/styles/theme.ts) — token source for Phase 0
- [client/tailwind.config.js](../client/tailwind.config.js) — Phase 0 rewiring
- [client/tsconfig.json](../client/tsconfig.json) + [client/vite.config.js](../client/vite.config.js) — `@/*` alias for shadcn
- [client/src/components/ParameterRenderer.tsx](../client/src/components/ParameterRenderer.tsx) — Phase 6 target
- [client/src/components/credentials/](../client/src/components/credentials/) — exemplar for Phase 5 and 6
- [client/src/components/output/OutputPanel.tsx](../client/src/components/output/OutputPanel.tsx) — `_uiHints` wiring in Phase 6
- [client/src/components/ui/Modal.tsx](../client/src/components/ui/Modal.tsx) — replaced by shadcn `Dialog` in Phase 3
- [client/src/components/shared/JSONTreeRenderer.tsx](../client/src/components/shared/JSONTreeRenderer.tsx) — last styled-components site

### Rollout / rollback
Each phase behind no flag except Phase 6 (`VITE_USE_NODESPEC`). Rollback = single-PR revert; antd coexists with new stack until Phase 7.

## End-to-end verification (post Phase 7)

1. `pnpm install && pnpm build` — green, bundle size recorded.
2. `pnpm exec tsc --noEmit` — zero errors.
3. Full manual regression: workflow open/save, node CRUD, credential CRUD per provider, parameter edit, workflow run, output render (markdown/JSON/error), theme toggle light↔dark, keyboard nav (Tab/Esc/Enter on all overlays).
4. Bundle analyzer (`ANALYZE=1 pnpm build` then open `dist/stats.html`): confirm antd + dayjs locales removed.
5. `grep -r "from 'antd'" client/src/` returns zero.
6. `grep -r "styled-components" client/src/` returns zero.
