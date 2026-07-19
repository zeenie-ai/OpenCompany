# Frontend Architecture

Post-migration (2026-04-14). Single source of truth for the current frontend.

> The pre-migration audit / research / RFC docs (`frontend_architecture_analysis.md`, `frontend_component_functionality_and_design.md`, `frontend_system_design_rfc.md`, `frontend_ui_framework_research.md`, `frontend_ui_stack_recommendation.md`) were deleted on 2026-04-14 — they're preserved in git history under commit `4cb3dd9` if you ever need to reference them. The migration log lives at [ui_migration_plan.md](./ui_migration_plan.md).

## TL;DR

- **React 19 + Vite 7 + TypeScript 5.9** with the **React Compiler** (`babel-plugin-react-compiler@19`, scoped to all of `src/` except `components/ui/`).
- **Tailwind v4** via `@tailwindcss/vite` + `@import "tailwindcss"` in [src/index.css](../client/src/index.css). Tokens defined in the same CSS file via `@theme inline` (no `tailwind.config.js` colors block).
- **shadcn/ui** via the canonical CLI (`npx shadcn@latest add`). All primitives live under [client/src/components/ui/](../client/src/components/ui/) as first-class repo files we can edit.
- **Radix UI** is the primitive engine shadcn uses (Dialog, Accordion, Select, Switch, Tabs, Tooltip, Popover, Dropdown, AlertDialog, Collapsible, Progress, Slider, Label, Checkbox).
- **Forms**: react-hook-form + zod via shadcn's `Form` composition. Per-form schemas live colocated with the form (e.g. `credentials/panels/schemas/email.ts`); tiny forms use inline zod.
- **Toasts**: `sonner` imported directly at call-sites. The shadcn `<Toaster />` wrapper (at [components/ui/sonner.tsx](../client/src/components/ui/sonner.tsx)) is patched to read our `ThemeContext` instead of `next-themes`.
- **State**: TanStack Query for server state, Zustand for UI-only state, plain `useState`/`useReducer` for local. No global redux store.
- **WebSocket realtime** via `WebSocketContext`; chat/node/workflow events are push-based, never polled.
- **antd is gone.** `styled-components` is gone. `@ant-design/icons` is gone. Icons come from `lucide-react`.

## Tech stack (current)

| Concern | Library | Where |
|---|---|---|
| Bundler | Vite 7 | [client/vite.config.js](../client/vite.config.js) |
| Framework | React 19 | [client/src/main.tsx](../client/src/main.tsx) |
| Compiler | `babel-plugin-react-compiler@19.1.0-rc.3` | [vite.config.js](../client/vite.config.js) (scoped: all of `/src/` except `components/ui/`) |
| Styling | Tailwind v4 + `@tailwindcss/vite` | [index.css](../client/src/index.css) + [tailwind.config.js](../client/tailwind.config.js) |
| Component library | shadcn/ui (CLI `npx shadcn@latest add`) | [components/ui/](../client/src/components/ui/) |
| Primitives | Radix UI | Pulled as transitive deps by shadcn |
| Icons | `lucide-react` | Everywhere. No more `@ant-design/icons`. |
| Typography | `@tailwindcss/typography` (`prose`) | Activated via `@plugin` in index.css |
| Markdown | `react-markdown` + `remark-gfm` + `remark-breaks` | Output panel, memory display, skill instructions |
| Code highlighting | `prismjs` | Code editor |
| JSON tree | `@uiw/react-json-view` (`githubDarkTheme` / `githubLightTheme`) | `OutputPanel` |
| Forms | `react-hook-form@7` + `zod@4` + `@hookform/resolvers` | Credential panels + sections |
| Toasts | `sonner` | Direct imports; shadcn `<Toaster />` wrapper mounted in `App.tsx` |
| Server state | `@tanstack/react-query@5` | `useCatalogueQuery`, `useProviderStatus`, etc. |
| UI state | `zustand@5` | `useAppStore`, `useCredentialRegistry` (UI-only, never holds catalogue data) |
| Realtime | native WebSocket wrapped by `WebSocketContext` | `contexts/WebSocketContext.tsx` |
| Search palette | `cmdk@1` + `fuzzysort@3` | `CredentialsPalette` |
| Virtualization | `react-virtuoso@4` | `GroupedVirtuoso` in `CredentialsPalette` (grouped, variable-height) |
| IndexedDB | `idb-keyval@6` | Warm-start cache for the credentials catalogue |
| Canvas | `reactflow@11` | Workflow editor |
| Code editor | `react-simple-code-editor` + `prismjs` | Python/JS/TS node editors |

Not present (intentionally): antd, `@ant-design/icons`, styled-components, emotion, moment/dayjs user imports, `next-themes`.

## Directory layout

```
client/src/
├── App.tsx                  # Root: syncs ThemeContext -> <html data-theme>/class, mounts Toaster
├── main.tsx                 # Providers (QueryClient, Theme, Auth, WebSocket) + renders <App/>
├── Dashboard.tsx            # Canvas workspace (React Flow + top-level panels)
├── ParameterPanel.tsx       # Per-node inspector (Phase 6 will schema-drive this)
│
├── index.css                # Tailwind v4 @import + @theme inline tokens + RF/scrollbar chrome
│
├── components/
│   ├── ui/                  # shadcn-generated primitives (editable, ours)
│   │   ├── button.tsx       # CVA variants: default/secondary/ghost/outline/destructive/link
│   │   ├── badge.tsx        # + success/warning/info variants we added
│   │   ├── alert.tsx        # + success/warning/info variants we added
│   │   ├── accordion.tsx    # Radix accordion
│   │   ├── dialog.tsx       # Radix dialog (Modal.tsx re-exports via thin wrapper)
│   │   ├── popover.tsx / tooltip.tsx / dropdown-menu.tsx
│   │   ├── select.tsx       # Radix select (no search; grouped items via SelectGroup/SelectLabel)
│   │   ├── input.tsx / textarea.tsx / switch.tsx / checkbox.tsx / label.tsx / slider.tsx
│   │   ├── collapsible.tsx / tabs.tsx / alert-dialog.tsx / card.tsx / progress.tsx
│   │   ├── form.tsx         # react-hook-form + FormField/FormItem/FormControl/FormMessage
│   │   └── sonner.tsx       # Patched to read ThemeContext (not next-themes)
│   │
│   ├── Modal.tsx (src/components/ui/Modal.tsx)
│   │                        # Thin wrapper over shadcn Dialog; preserves the pre-migration
│   │                        # API (isOpen/onClose/title/maxWidth/maxHeight/autoHeight/
│   │                        # headerActions) so call sites didn't churn.
│   │
│   ├── credentials/         # EXEMPLAR SUBSYSTEM — see "Credentials" section below
│   │   ├── CredentialsModal.tsx    # Shell — palette + PanelRenderer
│   │   ├── CredentialsPalette.tsx  # cmdk + fuzzysort + GroupedVirtuoso
│   │   ├── PanelRenderer.tsx       # Lazy-loads panel by kind
│   │   ├── catalogueAdapter.ts     # Server JSON -> ProviderConfig
│   │   ├── types.ts                # ProviderConfig, FieldDef, PanelKind, etc.
│   │   ├── useCredentialPanel.ts   # State hook (useState + form shim)
│   │   ├── panels/
│   │   │   ├── ApiKeyPanel.tsx           # Generic api-key providers
│   │   │   ├── OAuthPanel.tsx            # Twitter / Google / Telegram
│   │   │   ├── QrPairingPanel.tsx        # WhatsApp / Android
│   │   │   ├── EmailPanel.tsx            # IMAP/SMTP (RHF + zod)
│   │   │   └── schemas/email.ts          # Email zod schema w/ superRefine
│   │   ├── sections/
│   │   │   ├── ApiUsageSection.tsx       # Per-service usage/cost
│   │   │   ├── LlmUsageSection.tsx       # Per-provider token/cost
│   │   │   ├── ProviderDefaultsSection.tsx  # Default model params (RHF + zod)
│   │   │   └── RateLimitSection.tsx      # WhatsApp rate limits (RHF + zod)
│   │   └── primitives/
│   │       ├── StatusCard.tsx            # Config-driven status rows
│   │       ├── ActionBar.tsx             # Config-driven action buttons
│   │       ├── FieldRenderer.tsx         # Schema-driven simple-field renderer
│   │       └── OAuthConnect.tsx          # Composes status + fields + actions
│   │
│   ├── output/
│   │   └── OutputPanel.tsx         # Execution results. Single file, ~150 lines.
│   │                               # antd Collapse replaced with composable
│   │                               # `<Collapsible>` sections + ChevronDown;
│   │                               # Markdown via ReactMarkdown + prose.
│   │                               # JSON via @uiw/react-json-view.
│   │
│   ├── parameterPanel/             # Per-node inspector (pre-Phase-6)
│   │   ├── MiddleSection.tsx       # Parameters + console + skills + token usage
│   │   ├── OutputSection.tsx       # Wraps output/OutputPanel
│   │   ├── InputSection.tsx        # Connected node outputs
│   │   └── MasterSkillEditor.tsx   # Skill enable/disable + instructions editor
│   │
│   ├── onboarding/
│   │   ├── OnboardingWizard.tsx    # Custom step indicator (no antd Steps)
│   │   └── steps/*.tsx             # Welcome / Concepts / ApiKey / Canvas / GetStarted
│   │
│   ├── ui/
│   │   ├── ApiKeyInput.tsx         # Composite: input + eye toggle + save/delete buttons
│   │   ├── SettingsPanel.tsx       # Shadcn Switch + Slider + Input
│   │   ├── PricingConfigModal.tsx  # (client/src/components/PricingConfigModal.tsx)
│   │   ├── ConsolePanel.tsx        # Chat + console + terminal + output
│   │   ├── Modal.tsx               # Shadcn Dialog wrapper
│   │   ├── NodeOutputPanel.tsx     # Deleted (superseded by output/OutputPanel)
│   │   └── TopToolbar.tsx          # File menu + model picker + action buttons
│   │
│   ├── icons/                      # AI provider icons (SVG data URIs)
│   ├── auth/                       # Login page + protected route
│   ├── shared/
│   │   └── JSONTreeRenderer.tsx    # Recursive JSON tree (no styled-components)
│   ├── SquareNode.tsx, StartNode.tsx, TriggerNode.tsx, GenericNode.tsx, AIAgentNode.tsx, WhatsAppNode.tsx, ModelNode.tsx
│   │                               # React Flow nodes with lucide icons
│   └── APIKeyValidator.tsx         # Shadcn Input + Button + Tooltip composition
│
├── contexts/
│   ├── ThemeContext.tsx            # isDarkMode + toggleTheme
│   ├── AuthContext.tsx             # JWT user state
│   └── WebSocketContext.tsx        # Single source of truth for WS state + handlers
│
├── hooks/
│   ├── useAppTheme.ts              # Bridges ThemeContext + light/dark base packs + per-theme overlays
│   ├── useCatalogueQuery.ts        # TanStack Query + idb-keyval warm-start (exemplar)
│   ├── useWorkflowsQuery.ts        # Workflow list + save/delete mutations (Query)
│   ├── useNodeParamsQuery.ts       # Per-node parameter Query + save mutation
│   ├── useUserSettingsQuery.ts     # user_settings row Query + save mutation
│   ├── useApiKeys.ts               # WS-based API key CRUD
│   ├── useApiKeyValidation.ts      # Provider-specific validation helpers
│   ├── useComponentPalette.ts / useDragAndDrop.ts / useExecution.ts
│   ├── useOnboarding.ts            # Reads via useUserSettingsQuery; writes via mutation
│   ├── useParameterPanel.ts        # Thin orchestrator over useNodeParamsQuery + save mutation
│   ├── usePricing.ts / useToolSchema.ts / useWhatsApp.ts / useAndroidOperations.ts
│   └── useCopyPaste.ts / useRename.ts
│
├── store/
│   ├── useAppStore.ts              # UI state (sidebar, palette, pro mode, persisted)
│   └── useCredentialRegistry.ts    # UI-only: selectedId + paletteOpen + query
│
├── lib/
│   ├── queryClient.ts              # Module-singleton QueryClient so imperative
│   │                               # code (Zustand actions) can invalidate without
│   │                               # going through React context.
│   └── utils.ts                    # cn() = clsx + tailwind-merge (shadcn convention)
│
├── styles/
│   └── theme.ts                    # `lightColors` / `darkColors` base packs +
│                                   # `dracula` / `solarized` constants. Read
│                                   # exclusively by `useAppTheme` (which
│                                   # overlays per-theme accents on top of the
│                                   # base pack — see hooks/useAppTheme.ts) and
│                                   # by canvas node components for inline
│                                   # gradients tied to per-definition node
│                                   # colors. Not imported anywhere else.
│
├── services/
│   ├── executionService.ts         # ExecutionResult shape + node-execution plumbing
│   ├── apiKeyManager.ts            # LangChain API key utilities
│   └── dynamicParameterService.ts  # Remote options loaders for ParameterRenderer
│
├── adapters/
│   └── nodeSpecToDescription.ts    # Backend NodeSpec -> legacy INodeTypeDescription shape
├── lib/
│   ├── nodeSpec.ts                 # TanStack-Query spec fetch, resolveNodeDescription, listCachedNodeSpecs
│   ├── aiModelProviders.ts         # Frontend-only AI provider icon/credential map
│   ├── queryClient.ts / queryConfig.ts / featureFlags.ts
│   └── utils.ts
├── types/                          # INodeProperties, NodeTypes, etc.
└── utils/                          # formatters, apiKeySecurity, workflowExport, parameterSanitizer
```

## Tokens + theming

The frontend ships **12 themes** organised base / utopian / dystopian: `light` · `dark` · `renaissance` · `greek` · `edo` · `steampunk` · `atomic` · `cyber` · `wasteland` · `rot` · `plague` · `surveillance`. Active theme is `<html data-theme="...">` set by `<ThemeProvider>` (see [contexts/ThemeContext.tsx](../client/src/contexts/ThemeContext.tsx)). Per-theme blocks live in [client/src/themes/](../client/src/themes/) and own all colour VALUES as **hex + `color-mix()`** (never HSL triplets); [src/index.css](../client/src/index.css) is **plumbing-only** (the `@theme inline` `var()` bridge — no literal colours). Full token contract, sound + decorative-layer wiring, and the 12-theme migration playbook in **[theme_system.md](./theme_system.md)** — read that before adding a theme or migrating a component.

The bridge ([src/index.css](../client/src/index.css)) maps each Tailwind colour token to a raw `var(--X)`.

```css
@import "tailwindcss";
@import "shadcn/tailwind.css";
@import "@fontsource-variable/geist";
@plugin "@tailwindcss/typography";
@custom-variant dark (&:is(.dark *));

/* Colour VALUES live in client/src/themes/*.css as hex + color-mix() — NOT here.
 * light.css defines them on bare :root (global); dark.css + the 10 skins override. */
:root, :root[data-theme="light"] {       /* client/src/themes/light.css */
  --background: #f5f7fa; --foreground: #1a1d21; --primary: #2563eb;
  --destructive: #dc2626; --success: #059669; --border: #d1d5db; --radius: 0.5rem;

  /* Dracula action palette, same across themes */
  --dracula-green: #50fa7b; --dracula-purple: #bd93f9;  /* …pink, cyan, red, orange, yellow */

  /* Node + action role tokens: color-mix over the dracula base + the shared
   * --tint-* alpha scale (base.css). Call sites use bg-node-X / -soft /
   * border-node-X-border + text-action-X-ink directly — no opacity arithmetic. */
  --node-agent:        var(--dracula-purple);
  --node-agent-soft:   color-mix(in srgb, var(--dracula-purple) var(--tint-soft), transparent);
  --node-agent-border: color-mix(in srgb, var(--dracula-purple) var(--tint-border), transparent);
  --action-run:        var(--dracula-green);
  --action-run-soft:   color-mix(in srgb, var(--dracula-green) var(--tint-action-soft), transparent);
  --action-run-ink:    #15803d;   /* readable label; dark themes use var(--action-run) */
}

[data-theme="dark"] {                     /* client/src/themes/dark.css — colour-hex overrides */
  --background: #0d0f13;   /* neutral slate */
  --foreground: #e8eaed;   /* near-white neutral */
  --primary: #3b82f6;      /* standard blue */
  /* …etc; node/action tokens inherit light.css's :root (identical in dark) + -ink overrides */
}

@theme inline {                           /* client/src/index.css — bridge ONLY, no values */
  --color-background: var(--background);   /* NO hsl() wrapper */
  --color-primary: var(--primary);
  --color-node-agent-soft: var(--node-agent-soft);
  --color-action-run-ink: var(--action-run-ink);
  /* …every --color-X maps a raw var(--X) */
}
```

Rules:
1. **Hex + `color-mix()`; bridge maps `--color-X: var(--X)`** (no `hsl()` wrapper). Tailwind v4 still composes `/opacity` (`bg-primary/50`) via `color-mix` for any colour format.
2. **shadcn's variable names win** (`--background`, `--primary`, `--destructive`, etc.) so every shadcn-generated file resolves against our palette with no re-wiring.
3. Theme switches via `[data-theme="<name>"]` set by `<ThemeProvider>`. Themes whose backgrounds are dark (`dark`, `cyber`, `wasteland`, `rot`, `surveillance`, `steampunk`) also flip Tailwind's `.dark` class so legacy `dark:` variants resolve correctly. The 10-way `THEME_OVERRIDES` map in [hooks/useAppTheme.ts](../client/src/hooks/useAppTheme.ts) layers per-theme accents (primary, focus, action colours, edge palette) on top of `lightColors` / `darkColors` for canvas surfaces.
4. `styles/theme.ts` exports `lightColors` / `darkColors` base packs + raw `dracula` / `solarized` palette constants. Consumed only by `useAppTheme` (overlay merge) and canvas node components for inline per-definition gradients. New code uses Tailwind classes (`bg-primary`, `bg-action-run-soft`, `bg-node-agent-soft`, `bg-bg-app`, `text-fg-default`) or `var(--...)` inline. Palette names (`text-dracula-green`) are forbidden in components — go through the matching `--action-X` or `--node-X` semantic role token.

### Token tier — pick the most specific that fits

| Tier | Tokens | Use for |
|---|---|---|
| **shadcn semantic** | `background`, `foreground`, `card`, `popover`, `primary`, `secondary`, `muted`, `accent`, `destructive`, `success`, `warning`, `info`, `border`, `input`, `ring` | App-wide chrome, status colors, generic actions. Each rotates per theme. |
| **Node-type role** | `node-agent`, `node-model`, `node-skill`, `node-tool`, `node-trigger`, `node-workflow` (+ paired `-soft` and `-border` variants) | Anywhere a node type's identity should drive color: palette icons, parameter-panel sections, draggable variable cards, status badges, edge label tints. |
| **Action role** | `action-run`, `action-stop`, `action-save`, `action-config`, `action-secret`, `action-tools` (each with `-soft` resting bg, `-hover` hover bg, and `-border` outline variants) | Toolbar icon buttons, File menu items, and the underlying tokens behind `<ActionButton intent="...">`. Semantic role names (run / stop / save / ...), never palette colors. The `-hover` triplet means ActionButton's hover state composes via `hover:bg-action-X-hover` instead of opacity arithmetic; disabled state is the shadcn-idiomatic `disabled:opacity-50` on the base class. Themes redefine without touching call sites. |
| **Dracula raw** | `dracula-green`, `dracula-purple`, `dracula-pink`, `dracula-cyan`, `dracula-red`, `dracula-orange`, `dracula-yellow` | Constant across themes by design. Used as the underlying palette that `--action-X` and `--node-X` reference; do not consume directly in components. |
| **Code & syntax** | `code-bg`, `code-gutter-bg`/`-fg`, `code-caret`, `code-border`, `code-text`, `code-comment`, `code-keyword`, `code-string`, `code-number`, `code-boolean`, `code-function`, `code-property`, `code-operator`, `code-punctuation`, `code-tag` | The code editor, console/output JSON viewers, and chat code blocks. Per-theme `--code-*` (one block per theme file; skins derive syntax from their role hues — keyword→trigger, string→success, number→agent, function→model). Consumed via `var(--code-*)` in `index.css` + `text-code-*` / `bg-code-bg` Tailwind utilities; the `OutputPanel` `@uiw/react-json-view` reads them too. Replaced the global dracula `--prism-*` block + dead `getPrismTokenCSS()`. |

### No opacity arithmetic at call sites

`bg-primary/10` and `border-node-agent/30` are forbidden in new code. Themes own the exact tint per role:

```tsx
// ❌ Don't
<Card className="bg-node-agent/10 border-node-agent/30" />

// ✅ Do — themes can redefine --node-agent-soft / -border independently
<Card className="bg-node-agent-soft border-node-agent-border" />
```

Add a new `-soft` / `-border` (or other named) variant to `--node-X` if a unique opacity is needed; never inline the math at the call site.

## Component primitives

All under [components/ui/](../client/src/components/ui/). Editable — add variants as needed (we extended `Badge` and `Alert` with `success/warning/info`).

| Concern | Primitive | Notes |
|---|---|---|
| Button | `Button` (CVA) | Variants: `default | secondary | ghost | outline | destructive | link`. Sizes: `default | xs | sm | lg | icon | icon-xs | icon-sm | icon-lg` |
| Badge | `Badge` | + `success | warning | info` (ours) |
| Alert | `Alert + AlertTitle + AlertDescription` | + `success | warning | info` (ours) |
| Overlay | `Dialog`, `AlertDialog`, `Popover`, `Tooltip`, `DropdownMenu` | Radix |
| Disclosure | `Accordion`, `Collapsible`, `Tabs` | Radix |
| Inputs | `Input`, `Textarea`, `Select`, `Switch`, `Checkbox`, `Slider`, `Label` | Radix (Select/Switch/Checkbox/Slider) |
| Cards | `Card + CardHeader/Title/Description/Content/Footer` | Layout primitive |
| Progress | `Progress` | Radix progress |
| Form | `Form + FormField + FormItem + FormLabel + FormControl + FormDescription + FormMessage` | react-hook-form wrappers |
| Toast | `Sonner` `<Toaster />` | Patched for our ThemeContext |

**Rules for adding primitives:**
- Always use the CLI: `OPENCOMPANY_INSTALLING=true npx shadcn@latest add <name>` from `client/` (the env var suppresses the project's recursive postinstall hook).
- New variants go inside the generated file (we own it).
- Don't wrap primitives in `<Stack>`/`<Inline>`/`<Text>`/`<Heading>`. Use raw Tailwind classes. The Tailwind utility API IS the design system for layout/typography.

## Forms

shadcn's canonical composition — react-hook-form + zod + shadcn `Form`:

```tsx
const schema = z.object({ apiKey: z.string().min(1, 'API key is required') });
const form = useForm({ resolver: zodResolver(schema), defaultValues: { apiKey: '' } });

<Form {...form}>
  <form onSubmit={form.handleSubmit(onSubmit)}>
    <FormField
      control={form.control}
      name="apiKey"
      render={({ field }) => (
        <FormItem>
          <FormLabel>API Key</FormLabel>
          <FormControl><Input {...field} /></FormControl>
          <FormDescription>Paste your provider API key</FormDescription>
          <FormMessage />
        </FormItem>
      )}
    />
    <Button type="submit">Save</Button>
  </form>
</Form>
```

**Conventions:**
- Drop the `useForm<T>()` generic — let TS infer from the resolver. Otherwise zod `optional().default()` mismatches the control type.
- Inline small schemas in the component. For schemas with `superRefine`/conditional validation (e.g. email's custom-provider rule) move to a colocated file (`schemas/email.ts`).
- No `Form.useWatch`; use `form.watch('field')` or `form.formState.isDirty` directly.
- For per-field-save workflows (credentials), skip RHF entirely — `useCredentialPanel` uses `useState` + a ref-based `getFieldValue/setFieldValue` shim so call-sites don't change.

## Credentials subsystem (exemplar)

The template for scalable feature design in this codebase. Everything else should follow the same shape.

**Data flow:**
```
server/config/credential_providers.json
            │
            │ (includes _ai_base abstracts + extends resolution)
            ▼
server/services/credential_registry.py
            │
            │ (enriches each provider with stored: bool via auth_service)
            ▼
WebSocket: handle_get_credential_catalogue (server/routers/websocket.py)
            │
            │ (content-sha256 version hash; 304-style conditional fetch)
            ▼
hooks/useCatalogueQuery.ts  (TanStack Query + idb-keyval warm-start)
            │
            ▼
components/credentials/catalogueAdapter.ts  (hydrate JSON -> ProviderConfig)
            │
            ▼
components/credentials/CredentialsModal.tsx
   ├─ CredentialsPalette.tsx   (cmdk + fuzzysort + GroupedVirtuoso)
   └─ PanelRenderer.tsx        (lazy: ApiKey/OAuth/QrPairing/Email)
```

**State rules:**
- **Zustand** (`useCredentialRegistry`) holds ONLY UI state: `selectedId`, `paletteOpen`, `query`. Never catalogue data. Prevents the closure-retention bug where selectors keep the whole 5000-entry catalogue in memory.
- **TanStack Query** owns the server state (catalogue, usage summaries, etc).
- **idb-keyval** cache seeds first paint — opened modal renders from IndexedDB in <50 ms before the WS roundtrip completes.
- **`requestIdleCallback`** writes back to IDB so saves don't block first paint.
- **DB is the single source of truth.** The retired `client/src/components/credentials/providers.tsx` static fallback is gone — `useCatalogueQuery` is the only source. Cold-boot with no IDB cache renders a `<Skeleton>` palette while the WS catalogue arrives; server-unreachable shows an explicit error state, never stale fallback data.
- **`provider.stored` is the canonical "do we have a credential for X?".** The retired `apiKeyStatuses[id].hasKey` mirror duplicated this answer with no synchronisation contract. Two new selector hooks (`useProviderStored(id)`, `useStoredProviderCount()`) read the catalogue. `apiKeyStatuses[id]` now narrowly carries the validation result (`valid`, `models`, `message`, `timestamp`).

**App-wide query persistence ([client/src/lib/queryPersist.ts](../client/src/lib/queryPersist.ts)):** the QueryClient is wrapped in `<PersistQueryClientProvider>` ([main.tsx](../client/src/main.tsx)) with a localStorage persister + `__APP_VERSION__` buster + 24h SWR window. Only queries with key prefixes `nodeSpec` / `nodeGroups` / `skillContent` are dehydrated -- high-frequency / per-session queries stay in-memory. Hard refresh paints from cached specs **before** the WebSocket connects, so canvas nodes never flash placeholder icons. The credentials catalogue uses its own dedicated `idb-keyval` warm-start (above) because its payload is large enough that localStorage's 5-10MB cap is a real constraint. **Decrypted credential values are NOT persisted** (was the retired `'credentialValues'` prefix) per OWASP HTML5 Security Cheat Sheet / ASVS V9.9 — plaintext API keys in `localStorage` are readable via DevTools on shared / compromised browsers; the in-memory TanStack Query cache (`gcTime: ∞`) keeps the form populated for the session lifetime, on reload the panel refetches via WS.

**`useNodeSpec` is a slice subscription, not a `useQuery`** ([client/src/lib/nodeSpec.ts](../client/src/lib/nodeSpec.ts)): reads via `useSyncExternalStore` filtered by `hashKey(['nodeSpec', type])`. Per-spec observer count is **0**; only the matching slot triggers a re-render. Lazy fetch is one-shot via `useEffect` gated on `isReady`. Do not re-introduce `useQuery(['nodeSpec', type])` -- N consumers create N observers, all woken on every cache write.

**Slice-subscribed cache entries MUST set `gcTime: GC_TIME.FOREVER`.** Slice subscribers don't register as TanStack observers, so without this override the cache entry is garbage-collected after the default `gcTime` (5 min) and every consumer reads `undefined`. The user-visible regression is "canvas nodes lose their icons / handles after idle." Applies to `fetchNodeSpec`, `fetchNodeGroups`, and the `useNodeGroups` `useQuery`; the persistor in `lib/queryPersist.ts` only handles cross-reload survival.

**Anchor cache contracts at the prefix root via `setQueryDefaults`, not per-call options.** `PersistQueryClientProvider` hydrates entries from localStorage with the QueryClient's *default* options, so per-call `staleTime: FOREVER` does not stop `gcTime: 5min` eviction on hydration. Every persisted prefix must have a matching `queryClient.setQueryDefaults(['<prefix>'], { staleTime: FOREVER, gcTime: FOREVER })` declaration in [client/src/lib/queryClient.ts](../client/src/lib/queryClient.ts). The current canonical set is `['nodeSpec']`, `['nodeGroups']`, `['skillContent']`. The persistor whitelist in [client/src/lib/queryPersist.ts](../client/src/lib/queryPersist.ts) must mirror it; a string in the whitelist that doesn't match a real query key (the prior `'pluginCatalogue'` typo) is silently dead. `credentialCatalogue` is intentionally NOT in either list — it has its own `idb-keyval` warm-start. `credentialValues` keeps `gcTime: FOREVER` for the in-memory cache so the credentials form survives idle, but it is intentionally NOT persisted (OWASP — see "Persistence layers" above).

**Component rules:**
- `PanelRenderer` lazy-loads each panel type so the initial JS payload doesn't grow linearly with provider count.
- Panels are config-driven: `StatusCard`, `ActionBar`, `FieldRenderer`, `OAuthConnect` consume `ProviderConfig` fields rather than hand-coding per-provider JSX.
- Exception: EmailPanel has conditional `custom` IMAP/SMTP fields that the simple schema can't express — it gets a dedicated zod schema and RHF form. That's the boundary where config-driven hands off to hand-written.

## ParameterRenderer (pending Phase 6)

Currently a 2152-line switch on `parameter.type` ([client/src/components/ParameterRenderer.tsx](../client/src/components/ParameterRenderer.tsx)). 15+ branches for `string | number | boolean | options | collection | fixedCollection | code | file | credential | ...`.

**Phase 6 plan:** replace with `@jsonforms/react` renderer registry. Requires backend to expose a `get_node_spec` WebSocket handler returning `NodeSpec { jsonSchema, uiSchema, _uiHints? }` per the RFC. Frontend will own the custom renderer set (one file per widget under `components/inspector/renderers/`) and route via JSON Forms' tester-based dispatch. Feature flag `VITE_USE_NODESPEC` gates the rollout; the old `ParameterRenderer` deletes once stable.

See [ui_migration_plan.md](./ui_migration_plan.md) Phase 6.

## Real-time

`contexts/WebSocketContext.tsx` is the single connection + event bus. ~125 handlers (see `server/routers/websocket.py`). Handlers follow a request/response pattern via `sendRequest(type, data)` with correlation IDs. Push-only events (node status, workflow progress, token usage, android/whatsapp status) set context state directly; components subscribe via selector hooks (`useAndroidStatus`, `useNodeStatus(nodeId)`).

**Rules:**
- `useEffect` fetch-on-mount is banned for anything the backend can push. Subscribe to the context slice instead.
- All modifying operations go through WebSocket — REST is reserved for auth + webhooks.
- No polling. If a component wants fresh data, call `sendRequest` once (or use TanStack Query with `staleTime: Infinity` + manual `invalidate`).
- **`sendRequest` queues during disconnect with backpressure.** When the socket is not open, the request enqueues with an `AbortController`-backed per-request timeout (default 30s) and replays on reconnect inside `ws.onopen` before `setIsReady(true)`. Queue caps at 200 with FIFO eviction (rejects oldest with `backpressure: too many queued requests`). Intentional close (`event.code === 1000`) drops the queue; transient closes preserve it. Eliminates indefinite spinners during the 3-second reconnect window. Implementation: `pendingSendQueueRef` + `drainPendingSends` in `WebSocketContext.tsx`.
- **Workflow-control state reconciles on every WS connect.** The toolbar derives
  Start/Pause/Resume/Reset availability from `get_workflow_control_status`, whose
  persisted generation record is reconciled with the Temporal controller rather
  than inferred from process-local tasks. `workflow_control_status` broadcasts
  update transitions in real time; reconnect performs an authoritative read.
  The older `deployment_snapshot` and binary deployment status remain migration
  adapters for legacy deployments and must not override a resolved controller
  generation. See [Temporal Execution Engine RFC](temporal-execution-engine-rfc.md).
- **Runtime Reset clears execution and conversation projections.** The
  `workflow_runtime_reset` broadcast drops the workflow's node-status slot,
  variables, and current console/chat arrays; `Dashboard` remounts the
  parameter panel so local output reducers cannot leak the previous run.
  Simple Memory parameters are refreshed from the backend's cleared-row
  broadcast, and `compactionStats` caches are evicted. The old transcript stays
  available only through the archived generation. See
  [Memory Lifecycle](memory_lifecycle.md#workflow-reset-archives-then-clears-memory).
- **`currentWorkflowId` lives in `useAppStore` only.** Non-React listeners (WS handlers) read it via `useAppStore.getState().currentWorkflow?.id` -- the documented Zustand escape hatch (https://github.com/pmndrs/zustand#read-state-without-subscription). The previous `currentWorkflowIdRef` mirror inside WebSocketContext was a one-render-late copy that misrouted broadcasts during workflow switches. The push to `nodeStatusStore.setCurrentWorkflowId` is driven from a single `useEffect` in `Dashboard.tsx`.

## Ownership boundary: TanStack Query vs Zustand vs WebSocketContext

This is the rule that keeps the data layer schema-driven instead of imperatively glued together.

| Owns | What goes here | Examples |
|---|---|---|
| **TanStack Query** | Anything the server has authoritative state for. List / single-record / settings reads. Mutations that change server state. | `useWorkflowsQuery`, `useNodeParamsQuery`, `useUserSettingsQuery`, `useCatalogueQuery`, `useSaveWorkflowMutation`, `useSaveNodeParamsMutation`, `useSaveUserSettingsMutation` |
| **Zustand** | UI-only state that survives navigation. The active edit buffer for the current workflow. Sidebar/panel visibility flags. | `useAppStore.currentWorkflow` (mutable buffer), `sidebarVisible`, `proMode`, `renamingNodeId`, `useCredentialRegistry.selectedId` |
| **`useState` / `useReducer`** | Per-component transient state. Form-field drafts. Hover/focus. | text-input drafts, dropdown-open, inline-edit toggles |
| **`WebSocketContext`** | Raw WS connection, `sendRequest`, push-only broadcast slices (workflow progress, android/whatsapp/twitter status, console/terminal logs). The provider value is `useMemo`'d so unrelated state changes do not re-render every consumer. Exposes `isOpen` (socket open) and `isReady` (post init-burst) -- gate catalogue/spec queries on `isReady`. The init burst now runs **in parallel** via `Promise.allSettled` over named helpers (`probeApiKey`, `loadTerminalLogs`, `loadChatHistory`, `loadConsoleLogs`), each backed by a small `sendBurstRequest` factory that owns its own request id, message handler, and 5 s timeout. `drainPendingSends(ws)` still runs synchronously after the await and before `setIsReady(true)` so the queue replay ordering is preserved. Time-to-`isReady` is one wide round-trip rather than 8 sequential ones. Catalogue invalidation routes through `invalidateCatalogue(queryClient)` ([`hooks/useCatalogueQuery.ts`](../client/src/hooks/useCatalogueQuery.ts)) which debounces the refetch on a 300 ms trailing edge, so an oauth burst or multi-service reconnect collapses to one refetch instead of N. | `androidStatus`, `consoleLogs`, broadcast streams |
| **`stores/nodeStatusStore.ts`** (Zustand) | Per-workflow node-execution statuses -- moved out of WebSocketContext so a status tick does not cascade through the React tree. `useNodeStatus(id)` is a slice selector; only the affected node's consumers re-render. Mirror this pattern for any new high-frequency push state. | `allStatuses[workflowId][nodeId]`, `currentWorkflowId` |

**Hard rules:**
- **Read Zustand stores via slice selectors, never whole-store destructure.** Always `const x = useAppStore((s) => s.x)`, never `const { x } = useAppStore()`. The whole-store form re-renders the consumer on ANY store mutation (sidebar toggle, unrelated workflow rename, parameter save on another node), which defeats `React.memo` + `nodePropsEqual` on the canvas. Setters are stable refs from Zustand — single-field selectors are the cheapest read. Audited and converted across the canvas + parameter-panel hot paths (every node component, `Dashboard.tsx`, `useDragVariable`, `useParameterPanel`, `useReactFlowNodes`, `useWorkflowManagement`, `InputSection`, `MiddleSection`, `OutputPanel`, `ParameterRenderer`, `ToolSchemaEditor`, `ParameterPanel`, `InputNodesPanel`).
- A list of server records (`workflows`, `nodeParameters`, `userSettings`, `credentialCatalogue`, `userSkills`, node output schemas) lives in TanStack Query. Never duplicate it in Zustand. Phase-1 follow-up commit `c3a7aa4` removed `savedWorkflows` from `useAppStore` for exactly this reason; Wave 3 commit `7706afb` did the same for `userSkills` in MasterSkillEditor.
- Imperative WebSocket request/response inside a component (`useEffect` + `sendRequest` + `setState`) is a code smell — wrap it in a `useQuery` hook. Inline the hook at the top of the consuming file when there's exactly one consumer (Wave 2/3 colocation rule); promote to `client/src/hooks/` when a second consumer appears. Phase-2 commit `b2b6fba` did this for `useParameterPanel` and `useOnboarding`; Wave 3 commits `2c5f227` / `7706afb` / `327f792` followed the same pattern inline inside MiddleSection / MasterSkillEditor / InputSection.
- After a mutation, **invalidate the corresponding query key**, don't manually patch a Zustand list or call a local refetch helper. Mutations that need it from non-React code use the `queryClient` singleton at [client/src/lib/queryClient.ts](../client/src/lib/queryClient.ts).
- Schema metadata for parameter behavior (selectors, validators, dynamic options) belongs in the node-definition `typeOptions`, NOT in `parameter.name === '...'` checks inside `ParameterRenderer`. Phase-5 commit `8353c48` introduced `typeOptions.loadOptionsMethod` for the WhatsApp selectors as the canonical pattern.
- **Runtime output shapes for the Input panel's variable list live on the backend** via Pydantic models in `server/services/node_output_schemas.py`. The frontend fetches them lazy via `get_node_output_schema`; real execution data takes precedence. See the "Node output shape" section below and [schema_source_of_truth_rfc.md](./schema_source_of_truth_rfc.md).
- **Never hand-roll a modal backdrop.** Destructive confirmations use `<AlertDialog>`; composite panels use the `Modal.tsx` primitive on top of shadcn `<Dialog>`. A raw `position: fixed; background: rgba(0,0,0,0.5)` in new code should not pass review.

## Schema-driven node + panel hints

Wave 2 introduced two typed fields on `INodeTypeDescription` so panels and the inspector can make rendering decisions from the schema instead of `nodeDefinition.name === '…'` string compares.

### `uiHints` — per-node panel visibility flags

Defined on `INodeTypeDescription.uiHints` ([client/src/types/INodeProperties.ts](../client/src/types/INodeProperties.ts)). Each flag is consumed by exactly one panel and defaults to off (the panel renders normally). The current set (live list = the `known` set in `test_node_spec.py`):

| Flag | Read by | Effect |
|---|---|---|
| `hideInputSection` | `ParameterPanel`, `InputSection` | Skip the connected-inputs panel (start, skill, monitor) |
| `hideOutputSection` | `ParameterPanel`, `OutputSection` | Skip the execution-results panel |
| `hideRunButton` | `ParameterPanel` | Hide the Run button (skill / memory / tool nodes) |
| `hasCodeEditor` | `MiddleSection` | Give the params block extra flex space for an embedded code editor |
| `isMasterSkillEditor` | `MiddleSection`, `Dashboard` (component dispatch), `useAutoSkillEdges` | Render the MasterSkillEditor split panel; route to `ToolkitNode` on the canvas; identify Master Skill aggregators in the auto-skill edge dispatcher |
| `isMemoryPanel` | `MiddleSection` | Render the memory markdown panel + token usage stats |
| `isToolPanel` | `MiddleSection` | Surface the ToolSchemaEditor for connected services |
| `isMonitorPanel` | `MiddleSection`, `ParameterPanel` | Render the team-monitor panel |
| `showLocationPanel` | `LocationParameterPanel` | Special-case panel for nodes with map preview |
| `isAndroidToolkit` | `ToolSchemaEditor` | Toolkit aggregator (Android service hub) |
| `isChatTrigger` | `ConsolePanel` | This node is a chat-message target |
| `isConsoleSink` | `ConsolePanel` | This node consumes console output (filter source) |
| `hasSkills` | Agent panels | Connect the connected-skills section |
| `isConfigNode` | `InputSection`, `OutputPanel` | This node is auxiliary configuration — its panel inherits the parent's main inputs instead of showing direct upstream connections. **Auto-derived on the backend** by `_derive_auto_ui_hints` in [`server/services/plugin/base.py`](../server/services/plugin/base.py): plugins whose `group` tuple contains `memory` or `tool` get this for free. Explicit `cls.ui_hints` always wins. |
| `outputMode: "terminal"` | `output/OutputPanel` | The node's textual output is CLI/terminal text: render it in a `<pre>` painted with the per-theme `--code-*` tokens instead of ReactMarkdown (which turns `#` into headings and collapses indentation). Strings that are wholly JSON route to the JSON tree via the shared `tryParseJson` helper. Declared by the CLI-wrapper plugins (`githubAction`, `vercelAction`, `shell`). |

Adding new panel behaviour: add a flag to `INodeUIHints`, annotate the relevant node definitions (or extend the auto-derivation rule on the backend), read the flag in the panel. Don't add another `nodeDefinition.name === '…'` branch — six such checks for `'masterSkill'` were retired in this round in favour of `uiHints.isMasterSkillEditor`. Pytest invariant `test_ui_hints_only_carry_known_flags` in `server/tests/test_node_spec.py` locks the flag set; new flags must be added there too.

### Node output shape — backend as single source of truth

Frontend does **not** declare output shapes anymore. The backend owns them exclusively via Pydantic models in [server/services/node_output_schemas.py](../server/services/node_output_schemas.py) — live size via `len(NODE_OUTPUT_SCHEMAS)`. JSON Schema is emitted via Pydantic's `model_json_schema()` and exposed two ways:

- `GET /api/schemas/nodes/{node_type}.json` — static, long-cache (`Cache-Control: public, max-age=86400`), no auth. n8n-style static-asset pattern.
- `get_node_output_schema` WebSocket handler — authenticated editor path.

[InputSection.tsx](../client/src/components/parameterPanel/InputSection.tsx) consumes schemas lazy via `fetchNodeOutputSchema(nodeType)` (inline helper wrapping `queryClient.fetchQuery` with `staleTime: Infinity`). The draggable variable list's shape precedence is:

1. Real execution data from the last run (primary).
2. Backend-declared schema fetched on demand (fallback).
3. `{ data: 'any' }` empty state (final fallback — the legacy `sampleSchemas` map was deleted in Wave 3).

**Adding a new node type's output shape:** define a Pydantic model in `node_output_schemas.py`, register it in `NODE_OUTPUT_SCHEMAS`. The frontend picks it up automatically — no client change, no rebuild. Research and rationale in [docs-internal/schema_source_of_truth_rfc.md](./schema_source_of_truth_rfc.md).

### Renderer registry shape (Phase 6 — pending)

When the backend `get_node_spec` handler lands, the inspector will own a 4-file colocated layout under `client/src/components/inspector/`:

```
inspector/
├── ParameterRenderer.tsx     # dispatcher + 11 inline widgets + drag-drop wrapper + WIDGETS registry
├── CollectionWidget.tsx      # recursive (>150 LOC, independently testable)
├── CodeWidget.tsx            # CodeEditor + theme/toolbar plumbing
└── types.ts                  # WidgetProps discriminated union, registry tester signature
```

The DIY widget registry (RHF + zod + a tester+rank dispatch) is modeled on n8n's monolithic `ParameterInput.vue`. Library-survey research preferred this over @jsonforms / @rjsf — bundle delta ≤ +50 KB gz vs +60–110 KB for any framework option, and shadcn theming would have to be hand-authored against any of them. `@rjsf/core` v6 + `@rjsf/shadcn` is the documented escape hatch if collection recursion bites.

## Reusable component primitives

| File | When to use |
|---|---|
| [client/src/components/ui/action-button.tsx](../client/src/components/ui/action-button.tsx) | Colored "soft" toolbar button (Run / Save / Cancel / Reset / Stop). One semantic `intent` prop (`run | stop | save | config | secret | tools`) drives bg / border / text / hover against the matching `--action-X` quartet (`-soft`, `-hover`, `-border`, base) via static Tailwind classes — no opacity arithmetic. Disabled state is the shadcn-idiomatic `disabled:opacity-50` on the base class (one rule, all intents). Replaces the `actionButtonStyle(color, isDisabled)` style helper that was copy-pasted across 4 files. The credential-modal panels (`OAuthConnect`, `EmailPanel`, `QrPairingPanel`, `ActionBar`) and the skill / tool-schema editors all consume `<ActionButton>` directly; their `ActionDef` records carry an `intent` key, never a free-form colour. |
| [client/src/styles/canvasAnimations.ts](../client/src/styles/canvasAnimations.ts) | Canvas-wide CSS injected once into Dashboard's `<style>` tag. Three named groups (`KEYFRAMES`, `edgeStatusStyles`, `nodeStatusStyles`) for the React Flow edge/node status visuals -- adding a new keyframe or status class is a single-file change. Light/dark distinction is encoded entirely in the `colors` arg coming from `theme.ts` -- `buildCanvasStyles(colors)` is single-arg with zero hardcoded hexes, and `CanvasStatusColors` carries the full set (`edgeDefault | edgeSelected | edgeExecuting | edgeCompleted | edgeError | edgePending | edgeMemoryActive | edgeToolActive`). The `nodeGlow` keyframe consumes scoped `--node-glow` / `--node-glow-soft` vars so one keyframe serves both themes. |
| [client/src/components/ui/alert-dialog.tsx](../client/src/components/ui/alert-dialog.tsx) | Confirmation / destructive-action modals. **Never hand-roll a `position: fixed; background: rgba(0,0,0,0.5)` backdrop** — use `<AlertDialog open onOpenChange>` with `AlertDialogHeader` / `AlertDialogDescription` / `AlertDialogFooter`. Focus trap, escape-to-close, and `role="alertdialog"` come from Radix. MiddleSection Clear Memory + Reset Skill dialogs are the canonical consumers (Wave 3 commit `61bf23c`). |
| [client/src/components/ui/sonner.tsx](../client/src/components/ui/sonner.tsx) | The `<Toaster />` mount — call `import { toast } from 'sonner'` directly at use sites; do not wrap. |
| [client/src/components/ui/Modal.tsx](../client/src/components/ui/Modal.tsx) | Composition primitive on top of shadcn `<Dialog>`. Owns the recurring "title bar with centered headerActions and a close button + size-constrained content panel" 8 panels share. Not an antd facade. For destructive confirmations prefer `AlertDialog` above. |
| `client/src/components/ui/{button,input,select,switch,checkbox,form,…}.tsx` | shadcn-generated primitives. Add new ones via `npx shadcn@latest add <name>`. Don't re-implement what the registry ships. |

## Theme + canvas chrome

[index.css](../client/src/index.css) also styles React Flow, scrollbars, and dot grid against the CSS-var palette. No inline hex codes in theme-sensitive surfaces — everything references `var(--...)` so themes flip cleanly.

## Build + dev

```bash
# from repo root
pnpm install            # client deps + server Python deps via postinstall
pnpm run dev            # concurrently: client (Vite :3000) + server (uvicorn :3010) + temporal + whatsapp
pnpm run build          # full prod build; bundle analyzer at dist/stats.html if ANALYZE=1

# client-only
cd client
pnpm dev                # Vite dev server
pnpm build              # Vite prod build (Tailwind v4 via @tailwindcss/vite plugin)
pnpm exec tsc --noEmit  # Typecheck
```

**Adding shadcn components:**
```bash
cd client
OPENCOMPANY_INSTALLING=true npx shadcn@latest add <name>
```
The `OPENCOMPANY_INSTALLING=true` env var suppresses the recursive project postinstall hook during shadcn's internal `pnpm install`. Without it the hook's `company build` run fails and shadcn aborts before writing the component file.

## Migration history (for context)

This architecture is the post-migration state. Pre-migration was antd + `styled-components` + a custom theme.ts-driven palette. See [ui_migration_plan.md](./ui_migration_plan.md) for the phase-by-phase transition and the 17 commits that executed it.

**`useAppTheme()` powers the canvas + maps surface across all 12 themes.** The hook returns a `theme` object with the legacy `Colors` shape (`theme.colors.X`, `theme.isDarkMode`) so existing call sites don't change. Under non-light/dark themes it merges a per-theme overlay (primary, focus, action palette, edge stroke / selection / executing / completed / error) on top of the chosen base pack (`lightColors` for utopian-bright themes, `darkColors` for dystopian / dark themes). Adding a new theme overlay is a single entry in the `THEME_OVERRIDES` map in [hooks/useAppTheme.ts](../client/src/hooks/useAppTheme.ts).

Read sites: every canvas node component (`AIAgentNode`, `SquareNode`, `TriggerNode`, `StartNode`, `ToolkitNode`, `TeamMonitorNode`, `GenericNode`), `EdgeConditionEditor`, and the Maps surface (`MapSelector`, `GoogleMapsPicker`, `MapsPreviewPanel`) — they interpolate per-definition `nodeColor` and JS-side hex values that Tailwind classes can't express. Every other surface uses Tailwind + the token tiers above and retints automatically through the per-theme `[data-theme="..."]` block.
