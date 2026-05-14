# Credentials Panel Architecture (Scalable to 5000 Providers)

The architecture for the MachinaOs credentials panel after scaling work.
Built on the Phase 1 refactor (21-file modular structure) with five additional
layers: server-owned registry, bulk-fetch cache, command palette, lazy panels,
and react-hook-form detail panels.

See also:
- [research_production_platforms.md](./research_production_platforms.md) — evidence base from n8n, Nango, Pipedream, Zapier, Supabase
- [research_react_stack.md](./research_react_stack.md) — library choices and benchmarks
- [typed-splashing-crown.md](../../../../.claude/plans/typed-splashing-crown.md) — execution plan

---

## Five-layer architecture

```
┌─────────────────────────────────────────────────────────────┐
│ 1. BACKEND REGISTRY (Nango-style, JSON for project parity)  │
│                                                              │
│    server/config/credential_providers.json                  │
│    └─ 5000+ entries with `extends` inheritance              │
│                                                              │
│    server/services/credential_registry.py                   │
│    ├─ load_catalogue() parses JSON at startup               │
│    ├─ resolve_extends() deep-merges inherited fields        │
│    ├─ get_all_providers() → list[dict]                      │
│    ├─ get_provider(id) → dict                               │
│    └─ validated with Pydantic                               │
│                                                              │
│    server/routers/websocket.py                              │
│    └─ handle_get_credential_catalogue → one bulk JSON       │
│                                                              │
│    server/routers/credentials.py                            │
│    └─ GET /api/credentials/icon/{id} (cached SVG)           │
└──────────────────────────────┬──────────────────────────────┘
                               ▼ one WebSocket call at first modal open
┌─────────────────────────────────────────────────────────────┐
│ 2. ZUSTAND STORE (cached catalogue)                         │
│                                                              │
│    client/src/stores/useCredentialRegistry.ts               │
│    ├─ providers: ProviderConfig[]                           │
│    ├─ categories: CategoryGroup[]                           │
│    ├─ preparedSearch: Fuzzysort.Prepared[] (memoized)       │
│    ├─ fetchOnce() — idempotent, no double-fetch             │
│    ├─ getById(id) — O(1) Map lookup                         │
│    ├─ search(query) — fuzzysort.go() <5ms on 5000 items     │
│    └─ Cache lives for app session; invalidated on WS event  │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. SEARCH + NAVIGATION (cmdk + fuzzysort + virtuoso)        │
│                                                              │
│    credentials/CredentialsPalette.tsx                        │
│    ├─ <Command shouldFilter={false}>                        │
│    ├─ <Command.Input> — debounced query                     │
│    ├─ <GroupedVirtuoso> — sticky category headers           │
│    │   └─ renders only visible rows at 60fps                │
│    ├─ Keyboard nav (↑↓ Enter Esc) via cmdk built-in         │
│    └─ Ctrl+K opens palette anywhere in the app              │
└──────────────────────────────┬──────────────────────────────┘
                               ▼ selected provider id
┌─────────────────────────────────────────────────────────────┐
│ 4. LAZY PANEL DISPATCH (React.lazy + Suspense)              │
│                                                              │
│    credentials/PanelRenderer.tsx                             │
│    const PANEL_LOADERS = {                                   │
│      apiKey:    () => import('./panels/ApiKeyPanel'),       │
│      oauth:     () => import('./panels/OAuthPanel'),        │
│      qrPairing: () => import('./panels/QrPairingPanel'),    │
│      email:     () => import('./panels/EmailPanel'),        │
│    };                                                        │
│    const Lazy = React.lazy(PANEL_LOADERS[config.kind]);     │
│    <Suspense fallback={<Spin />}><Lazy ... /></Suspense>    │
│                                                              │
│    Vite auto-chunks each panel (<50 KB each).               │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. SCHEMA-DRIVEN FORMS (react-hook-form)                    │
│                                                              │
│    credentials/schema/extends.ts                             │
│    └─ mergeProperties(base, override) — n8n pattern         │
│                                                              │
│    credentials/primitives/FieldRenderer.tsx                  │
│    ├─ <Controller> wrapping antd inputs                     │
│    ├─ Uncontrolled — 5-10× faster than antd Form            │
│    └─ Supports `extends` via merged FieldDef[]              │
│                                                              │
│    panels/{ApiKey,OAuth,Email}Panel.tsx                      │
│    └─ useForm() → RHF, Controller per field                 │
└─────────────────────────────────────────────────────────────┘
```

---

## Data flow (reading credentials)

```
User opens CredentialsModal
  ↓
CredentialsModal.tsx calls useCredentialRegistry().fetchOnce()
  ↓
Zustand store: if not fetched, sendRequest('get_credential_catalogue')
  ↓
WebSocket backend: credential_registry.get_all_providers()
  ↓
YAML loaded (cached at startup) → resolve_extends() → Pydantic-validated list
  ↓
{ providers: [...5000], categories: [...20], version: "abc123" }
  ↓
Zustand stores providers + prepares fuzzysort index
  ↓
CredentialsPalette renders GroupedVirtuoso
  ↓
User presses Ctrl+K or clicks sidebar → search input focus
  ↓
User types "whats" → fuzzysort.go() → 3ms → filtered list
  ↓
User selects WhatsApp → setSelectedId('whatsapp')
  ↓
PanelRenderer looks up config by id → determines kind='qrPairing'
  ↓
React.lazy loads QrPairingPanel chunk (first time) → Suspense spinner
  ↓
QrPairingPanel renders → calls useCredentialPanel(config, visible)
  ↓
useForm() initializes RHF → fields hydrate from stored credentials
```

---

## Data flow (saving credentials)

```
User edits a field
  ↓
RHF Controller → onChange → form state (uncontrolled, no re-render)
  ↓
User clicks Save
  ↓
handleSubmit(data) → panel.actions.save(key, value)
  ↓
useCredentialPanel execute('save', ...) → setLoading('save')
  ↓
useApiKeys.saveApiKey(key, value) → WebSocket → AuthService.store_api_key()
  ↓
EncryptedAPIKey table (Fernet + PBKDF2 + PBKDF2HMAC)
  ↓
Response → setStored(true), clear password field
```

---

## JSON schema with `extends` inheritance

MachinaOs uses JSON for all backend config (`email_providers.json`, `llm_defaults.json`,
`pricing.json`, `google_apis.json`). Credential registry follows the same convention.

```json
{
  "version": "2026.04",
  "providers": {
    "_ai_base": {
      "_abstract": true,
      "category": "ai",
      "category_label": "AI Providers",
      "kind": "apiKey",
      "has_defaults": true,
      "fields": [
        { "key": "apiKey", "label": "API Key", "type": "password", "required": true }
      ]
    },
    "openai": {
      "extends": "_ai_base",
      "name": "OpenAI",
      "color": "green",
      "fields": [
        { "key": "apiKey", "placeholder": "sk-..." }
      ]
    },
    "anthropic": {
      "extends": "_ai_base",
      "name": "Anthropic",
      "color": "orange",
      "fields": [
        { "key": "apiKey", "placeholder": "sk-ant-..." }
      ]
    },
    "gemini": {
      "extends": "_ai_base",
      "name": "Gemini",
      "color": "cyan",
      "fields": [
        { "key": "apiKey", "placeholder": "AIza..." }
      ]
    }
  }
}
```

**`credential_registry.resolve_extends()` behavior**:
1. Walk providers in YAML order
2. If `extends:` field set, deep-merge parent first, then override with child fields
3. For array fields (`fields`, `statusRows`, `actions`): merge by `key` — child
   entries replace parent entries with the same key; unmatched child entries append
4. `_abstract: true` providers are resolved but excluded from `get_all_providers()`
5. Cycle detection: throw on self-reference or loops

---

## Why this architecture?

### Why server-owned JSON?
- **Zero per-provider TypeScript.** Adding a new provider = editing one JSON entry.
- Git diffs stay readable as the catalogue grows.
- Non-engineers (support team, partners) can PR new providers without touching code.
- Parsed once at server startup, cached in memory. No runtime parsing cost per request.
- **Matches existing project convention**: every other backend config file is JSON
  (`email_providers.json`, `llm_defaults.json`, `pricing.json`, `google_apis.json`).
  No new dependencies, stdlib `json` only.

### Why one bulk WebSocket fetch?
- At 5000 providers × ~200 bytes/entry = ~1 MB JSON. Transferable in one call, loadable
  in <500ms over a local WebSocket.
- Cached in Zustand for the app session — no re-fetch on modal reopen.
- Matches n8n's proven pattern (they do it at 400 providers; we scale to 5000).

### Why `react-virtuoso` over antd Menu?
- **antd Menu has no virtualization**. At 1000+ items it mounts every `<Menu.Item>`
  to the DOM, killing initial render performance.
- `react-virtuoso`'s `GroupedVirtuoso` gives sticky category headers for free.
- 18.8 KB gz — acceptable cost for the scalability win.

### Why cmdk + fuzzysort over antd Select `filterable`?
- antd Select's substring filter is O(N) per keystroke with no ranking — at 5000
  items users can't find anything useful.
- cmdk provides keyboard navigation + accessibility out of the box.
- fuzzysort is the fastest JS fuzzy search (3× faster than Fuse.js).
- Combined cost: ~21 KB gz.

### Why React.lazy per panel?
- Each panel (ApiKeyPanel, OAuthPanel, QrPairingPanel, EmailPanel) is 40-80 lines
  but pulls in different deps (e.g., QrPairingPanel depends on `QRCodeDisplay` +
  `useWhatsApp`; EmailPanel depends on provider preset logic).
- Lazy-loading them drops initial credentials modal JS by ~60%.
- Vite handles chunking automatically — no config needed.

### Why react-hook-form over antd Form.useForm()?
- Uncontrolled by default → ~2ms per keystroke vs 18ms for antd Form at 200 fields.
- Critical for the `ProviderDefaultsSection` (AI provider defaults with 8+ fields).
- Keeps antd components via `<Controller>` — we're swapping the state engine,
  not the UI kit.

### Why `extends` inheritance?
- Today, 9 AI providers duplicate the same `fields: [{key: 'apiKey', ...}]` config.
- With `extends: _ai_base`, each entry is 4 lines instead of 12.
- At 5000 providers, inheritance is the difference between a 1 MB YAML and a 3 MB YAML.

---

## Performance targets

| Metric | Current (monolith) | Phase 1 refactor | Phase 2 (after scaling) | Target |
|--------|--------------------|--------------------|--------------------------|---------|
| CredentialsModal initial JS | ~180 KB | ~180 KB | ~100 KB | <150 KB |
| Per-panel chunk | N/A (monolithic) | N/A | <50 KB each | <50 KB |
| Sidebar initial render (20 providers) | 50 ms | 40 ms | 30 ms | <50 ms |
| Sidebar initial render (5000 providers) | crashes | 3000 ms (DOM bloat) | <200 ms (virtualized) | <300 ms |
| Search latency (5000 items) | N/A (no search) | N/A | <5 ms | <10 ms |
| Scroll at row 2000 | N/A | ~20 fps (jank) | 60 fps | ≥60 fps |
| Catalogue WebSocket fetch (5000 entries) | N/A | N/A | ~300 ms | <500 ms |

---

## Migration safety

1. **Worktree**: `feature/credentials-scaling` branched from main with Phase 1
   refactor cherry-picked on top. No changes to main until verified.
2. **Original import site preserved**: `client/src/components/CredentialsModal.tsx`
   remains a 1-line re-export. All existing usages unchanged.
3. **Backwards-compatible YAML**: ~~can serve the current 20 providers from YAML while
   the old `providers.tsx` is still present. Flip the switch when the YAML path is
   proven. Delete `providers.tsx` only after manual verification.~~ **DONE (Wave 12 follow-up)** — `providers.tsx` deleted; the server-driven catalogue (`server/config/credential_providers.json` → `useCatalogueQuery`) is the single source of truth. Cold-boot renders a `<Skeleton>` palette; server-unreachable shows an explicit error state.
4. **Feature flag** (optional): `VITE_USE_YAML_CREDENTIALS=true` — gates the new
   Zustand store fetch vs the old inline array. Enables A/B testing in dev.
5. **No main branch touches** until the full verification checklist passes.

---

## Files touched

See [typed-splashing-crown.md](../../../../.claude/plans/typed-splashing-crown.md)
section "Critical files to modify/create" for the exhaustive list.

Summary:
- **New**: 9 files (3 research MDs, 1 YAML, 4 Python, 2 TypeScript stores, 1 palette, 1 schema helper)
- **Modified**: 8 files (worktree credentials module + package.json + CLAUDE.md)
- **Deleted**: 1 file (`credentials/providers.tsx`) — done in Wave 12 follow-up commit `c94a610`. Server-driven catalogue is now the single source of truth.

---

## Status: plan approved, execution in progress

This document is authoritative for the architecture. If implementation deviates,
update this file and the plan file together.

---

## Addendum — Revised architecture (IndexedDB + TanStack Query + runtime targets)

The base 5-layer architecture above is correct but incomplete. Runtime/memory research revealed a sixth layer (IndexedDB persistence) and refined Phase 3 (TanStack Query owns the catalogue, Zustand holds UI state only). The revised architecture is **6 layers**:

```
┌─────────────────────────────────────────────────────────────┐
│ 1. BACKEND REGISTRY                                          │
│    server/config/credential_providers.json                   │
│    server/services/credential_registry.py                    │
│      - load_catalogue() parses JSON once at startup          │
│      - resolve_extends() deep-merges inherited fields        │
│      - content-sha256 version hash                           │
│    server/routers/websocket.py                               │
│      - handle_get_credential_catalogue                       │
│      - handle_save_credential (+ idempotency key dedupe)     │
│    server/routers/credentials.py                             │
│      - GET /api/credentials/icon/{id} (long-lived cache)     │
└──────────────────────────────┬──────────────────────────────┘
                               ▼  WebSocket bulk fetch
┌─────────────────────────────────────────────────────────────┐
│ 2. INDEXEDDB PERSISTENCE  (NEW)                              │
│    idb-keyval — key `credentials:catalogue:v<sha>`           │
│    Warm-start read on app open (<50 ms); background          │
│      revalidate via WebSocket version-hash comparison        │
│    Hydration write deferred via requestIdleCallback          │
│      (avoids 50–200 ms main-thread block at app open)        │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. TANSTACK QUERY (server cache) + ZUSTAND (UI state)        │
│    useCatalogueQuery()                                       │
│      - queryKey ['credentialCatalogue']                      │
│      - staleTime Infinity, gcTime 10 min                     │
│      - persister experimental_createPersister(idb-keyval)    │
│    useCredentialRegistry (Zustand, UI state ONLY)            │
│      - selectedId, paletteOpen, query                        │
│      - NO catalogue, NO derived state, NO preparedSearch     │
│      - Prevents the #1 memory trap: selector closures        │
│        capturing the whole 1.5–2.5 MB catalogue array        │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. PALETTE (cmdk + fuzzysort + GroupedVirtuoso)              │
│    CredentialsPalette.tsx                                    │
│      - prepared fuzzysort index via useMemo(providers)       │
│      - startTransition wrapping filter updates (measured    │
│        ~30–70 ms shorter long task vs useDeferredValue)      │
│      - GroupedVirtuoso for sticky category headers           │
│      - DOM pool: 10–50 live nodes regardless of item count   │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. LAZY PANEL DISPATCH (React.lazy + Suspense)               │
│    PanelRenderer.tsx — unchanged from base plan              │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. SCHEMA-DRIVEN FORMS + React Compiler                      │
│    credentials/primitives/FieldRenderer.tsx                  │
│      - RHF Controller wrapping antd inputs (uncontrolled)    │
│    React Compiler auto-memoizes the whole credentials module │
│      (scoped via babel-plugin-react-compiler in vite.config) │
└─────────────────────────────────────────────────────────────┘
```

### Runtime + memory targets (what verification must prove)

| Metric | Target | Measurement method |
|---|---|---|
| V8 heap — catalogue retained size | ~1.5–2.5 MB, exactly one retainer (TanStack Query cache) | Chrome DevTools → Memory → Heap Snapshot → filter Array, sort by Retained Size |
| DOM node count at any scroll position | 10–50 (virtuoso pool) | Elements panel count inside the virtuoso container |
| Modal 50-cycle heap delta (open/close 50×) | < 1 MB | Allocation timeline before vs after |
| INP p75 during rapid search typing | < 200 ms (stretch: < 100 ms) | `web-vitals` library `onINP` attribution mode |
| Search latency on 5000 items | < 10 ms (fuzzysort.go only) | `performance.measure()` around the filter |
| Catalogue cold fetch (5000 providers, first ever open) | < 500 ms | Network panel timing |
| Catalogue warm-start (second open, IndexedDB hit) | < 50 ms | Network panel: zero calls; React Profiler commit time |
| Scroll FPS at row 2000 | 60 fps | DevTools Performance tab |

### Runtime traps to avoid (the non-negotiable list)

1. **Zustand selector capturing the whole catalogue in a closure** — retains 1.5–2.5 MB permanently. Fix: keep derived data in `useMemo` inside components, not in the store.
2. **`useState(catalogue.filter(...))`** on every render — 1–5 MB GC churn/cycle. Fix: `useMemo`.
3. **Non-virtualized list** — 5–20 MB DOM + detached-node leaks. Fix: react-virtuoso.
4. **IndexedDB main-thread blocking write** — 50–200 ms stall. Fix: defer via `requestIdleCallback` after first paint.
5. **Fuse.js with `shouldSort + includeMatches`** on 5000 items — 10–50 MB transient per query. Fix: stay on fuzzysort.

### Referenced modern architecture sites

- **oboe.com** (inspected at user request, 2026): React + TanStack Router + TanStack Query + dehydrated→hydrated query client + SSR streaming. Our Vite SPA analog is IndexedDB persister + WebSocket version-hash invalidation. Same pattern, different transport.
- **Sanity Studio** + **Wakelet**: production React Compiler rollouts — 15% INP improvement on already-memoized code, 50–80% on fresh code.
- **n8n**: `credentials.store.ts` Pinia bulk fetch + `mergeNodeProperties` extends inheritance. Our Phase 3 + Phase 6 directly port this.
- **Nango**: content-addressed provider manifests with version-keyed delta fetches. We adopt the same version-hash approach but with a single-file catalogue.

### Addendum sources

- React Compiler 1.0 release: https://react.dev/blog/2025/10/07/react-compiler-1
- TanStack Query `experimental_createPersister`: https://tanstack.com/query/v5/docs/react/plugins/createPersister
- V8 memory optimization: https://v8.dev/blog/optimizing-v8-memory
- Chrome DevTools heap snapshots: https://developer.chrome.com/docs/devtools/memory-problems/heap-snapshots
- web-vitals library: https://github.com/GoogleChrome/web-vitals
- MemLab (Meta): https://engineering.fb.com/2022/09/12/open-source/memlab/
