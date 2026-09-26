# Frontend Architecture

Post-migration (2026-04-14). Single source of truth for the current frontend.

> The pre-migration audit / research / RFC docs (`frontend_architecture_analysis.md`, `frontend_component_functionality_and_design.md`, `frontend_system_design_rfc.md`, `frontend_ui_framework_research.md`, `frontend_ui_stack_recommendation.md`) were deleted on 2026-04-14 — they're preserved in git history under commit `4cb3dd9` if you ever need to reference them. The migration log lives at [ui_migration_plan.md](./ARCHIVE/ui_migration_plan.md).

## TL;DR

- **React 19 + Vite 7**, type-checked by **TypeScript 7** (the native Go compiler, exact-pinned `7.0.2` in the **root** `devDependencies`). The client keeps `typescript@^5.9.3` only because typescript-eslint's peer range excludes 6/7 — it is not the gate. With the **React Compiler** (`babel-plugin-react-compiler@1.0.0`, `target: '19'`, scoped to all of `src/` except `components/ui/`).
- **Tailwind v4** via `@tailwindcss/vite` + `@import "tailwindcss"` in [src/index.css](../client/src/index.css). Tokens defined in the same CSS file via `@theme inline` (no `tailwind.config.js` colors block).
- **shadcn/ui** via the canonical CLI (`bun x shadcn@latest add` — bun's `npx` equivalent; npm is not part of the toolchain). All primitives live under [client/src/components/ui/](../client/src/components/ui/) as first-class repo files we can edit.
- **Radix UI** is the primitive engine shadcn uses (Dialog, Accordion, Select, Switch, Tabs, Tooltip, Popover, Dropdown, AlertDialog, Collapsible, Progress, Slider, Label, Checkbox).
- **Forms**: react-hook-form + zod via shadcn's `Form` composition. Per-form schemas live colocated with the form (e.g. `credentials/panels/schemas/email.ts`); tiny forms use inline zod.
- **Toasts**: `sonner` imported directly at call-sites. The shadcn `<Toaster />` wrapper (at [components/ui/sonner.tsx](../client/src/components/ui/sonner.tsx)) is patched to read our `ThemeContext` instead of `next-themes`. Normal mode has a second toaster, one bottom-centre pill at a time (`pillToast()` in [features/home/ui/pillToast.tsx](../client/src/features/home/ui/pillToast.tsx)).
- **Two screens**: Normal mode (Home, [features/home/](../client/src/features/home/)) and Dev mode (the workflow editor, `Dashboard.tsx`), both lazy chunks switched by the app shell in [app/](../client/src/app/). See [Normal Mode](./normal_mode.md).
- **State**: TanStack Query for server state, Zustand for UI-only state, plain `useState`/`useReducer` for local. No global redux store.
- **WebSocket realtime** via `WebSocketContext`; chat/node/workflow events are push-based, never polled.
- **antd is gone.** `styled-components` is gone. `@ant-design/icons` is gone. Icons come from `lucide-react`.

## Tech stack (current)

| Concern | Library | Where |
|---|---|---|
| Bundler | Vite 7 | [client/vite.config.js](../client/vite.config.js) |
| Framework | React 19 | [client/src/main.tsx](../client/src/main.tsx) |
| Type checker | `typescript@7.0.2` — native Go compiler, **exact-pinned in the root `package.json`** | root `bun run typecheck` → `tsc --noEmit -p client/tsconfig.json`; client's `typecheck` delegates up |
| Type checker (second opinion) | `typescript@^5.9.3` in **client** `dependencies` | Kept for typescript-eslint's peer range (`>=4.8.4 <6.1.0`). Two `typescript` entries cannot live in one manifest, and both 5.x and 7.x expose a `tsc` bin — hence the root/client split. `bun run typecheck:tsc` runs it for triage only. |
| Compiler | `babel-plugin-react-compiler@1.0.0` (exact-pinned; `target: '19'` = the React version, not the plugin version) | [vite.config.js](../client/vite.config.js) (scoped: all of `/src/` except `components/ui/`). Pin is exact because semver sorts the old `19.1.0-rc.3` **above** `1.0.0`, so a range would silently reinstall the release candidate — which lacks the incompatible-library skip list. |
| Styling | Tailwind v4 + `@tailwindcss/vite` | [index.css](../client/src/index.css) + [tailwind.config.js](../client/tailwind.config.js) |
| Component library | shadcn/ui (CLI `bun x shadcn@latest add`) | [components/ui/](../client/src/components/ui/) |
| Primitives | Radix UI | Pulled as transitive deps by shadcn |
| Icons | `lucide-react` | Everywhere. No more `@ant-design/icons`. Backend-declared node / provider icons go through `<NodeIcon size={token}>` — `theme.nodeSize.squareIcon` on canvas nodes, `theme.iconSize.*` elsewhere; the token is applied as width, height and emoji font size, so never size an icon with `h-*` / `text-*` classes. |
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
├── App.tsx                  # Root: SVG filter defs, ProtectedRoute -> AppShell, both Toasters
├── main.tsx                 # Providers (QueryClient, ShellThemeProvider, Auth, WebSocket) + renders <App/>
├── Dashboard.tsx            # Dev mode: canvas workspace (React Flow + top-level panels), a lazy chunk
├── ParameterPanel.tsx       # Per-node inspector (Phase 6 will schema-drive this)
│
├── app/                     # The shell around both screens
│   ├── AppShell.tsx         # .app-frame, boot effects, Settings / Credentials dialogs
│   ├── ShellModeSwitch.tsx  # Lazy Home / editor screens + preloadHome / preloadEditor
│   ├── ShellThemeProvider.tsx # ThemeProvider with baseOnly while Home shows
│   ├── useShellActions.ts   # enterNormal / enterDev (the only way to switch)
│   ├── shellTransition.ts   # The fade-out / swap choreography
│   └── useModeShortcut.ts / useCurrentWorkflowSync.ts / usePageActivitySync.ts / useUIDefaultsOnce.ts
│
├── features/home/           # Normal mode (see docs-internal/normal_mode.md)
│   ├── HomeShell.tsx        # Sidebar + header + current view + Workspace dock + Settings + orb stage
│   ├── sidebar/ header/ hire/ employee/ settings/ approvals/ data/ state/ ui/
│   ├── workspace/           # Home Workspace dock; shared live Browser tab, lazy Canvas tab
│   ├── genui/               # Setup-screen pipeline; only its index.ts is importable (ESLint)
│   └── orb/                 # orb.ts (state + lifecycle), orbEngine.ts (three.js, own chunk)
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
│   │   ├── tooltip.tsx / dropdown-menu.tsx
│   │   ├── select.tsx       # Radix select (no search; grouped items via SelectGroup/SelectLabel)
│   │   ├── input.tsx / textarea.tsx / switch.tsx / checkbox.tsx / label.tsx / slider.tsx
│   │   ├── collapsible.tsx / tabs.tsx / alert-dialog.tsx / card.tsx / progress.tsx
│   │   ├── form.tsx         # react-hook-form + FormField/FormItem/FormControl/FormMessage
│   │   ├── sonner.tsx       # Patched to read ThemeContext (not next-themes)
│   │   ├── ApiKeyInput.tsx  # Composite: input + eye toggle + save/delete buttons
│   │   ├── SettingsPanel.tsx # Shadcn Switch + Slider + Input
│   │   ├── ConsolePanel.tsx # Chat + console + terminal + output
│   │   └── TopToolbar.tsx   # File menu + model picker + action buttons
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
│   │   │   ├── ApiKeyPanel.tsx           # Generic api-key providers (+ the add form for a provider that holds several endpoints)
│   │   │   ├── EndpointList.tsx          # Saved named OpenAI-compatible endpoints: rows + Refresh / Remove (stateless)
│   │   │   ├── OAuthPanel.tsx            # Twitter / Google / Telegram
│   │   │   ├── QrPairingPanel.tsx        # WhatsApp / Android
│   │   │   ├── EmailPanel.tsx            # IMAP/SMTP (RHF + zod)
│   │   │   ├── BrowserProfilesPanel.tsx  # Web browser login profiles: list / add / delete, session-file import
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
│   │   ├── GetStartedChecklist.tsx # Post-wizard checklist
│   │   └── steps/                  # WelcomeStep / HowItWorksStep / ConnectAIStep / TryItStep
│   │
│   ├── icons/                      # AI provider icons (SVG data URIs)
│   ├── brand/Logo.tsx              # OcMark / OcWordmark / OcLogo (inline SVG, --lg-* palette, intro + pulse)
│   ├── shell/ModeToggle.tsx        # The Normal / Dev switch (editor toolbar + Home header)
│   ├── auth/                       # Login page + protected route
│   ├── SquareNode.tsx, StartNode.tsx, TriggerNode.tsx, AIAgentNode.tsx, ToolkitNode.tsx, TeamMonitorNode.tsx
│   │                               # React Flow nodes with lucide icons
│   └── APIKeyValidator.tsx         # Shadcn Input + Button + Tooltip composition
│
├── contexts/
│   ├── ThemeContext.tsx            # theme (on the page) / chosenTheme, setTheme with the circular reveal, baseOnly
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
│   ├── useComponentPalette.ts / useDragAndDrop.ts / useDragWorkspaceFile.ts
│   ├── useOnboarding.ts            # Reads via useUserSettingsQuery; writes via mutation
│   ├── useParameterPanel.ts        # Thin orchestrator over useNodeParamsQuery + save mutation
│   ├── useWhatsApp.ts             # WS-based WhatsApp ops (Android ops go via useWebSocket directly)
│   ├── useUserSkills.ts / useFolderSkills.ts # Skill library + skill-folder queries (Home Skills, Master Skill editor)
│   ├── useCanvasBoard.ts           # One Canvas board's query + remove / clear (every Canvas host)
│   └── useCopyPaste.ts / useRename.ts
│
├── store/
│   ├── useAppStore.ts              # UI state (sidebar, palette, shellMode, pro mode, persisted)
│   └── useCredentialRegistry.ts    # UI-only: selectedId + paletteOpen + query
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
│   ├── dynamicParameterService.ts  # Remote options loaders for ParameterRenderer
│   └── workflowApi.ts              # Workflow REST helpers
│
├── adapters/
│   └── nodeSpecToDescription.ts    # Backend NodeSpec -> legacy INodeTypeDescription shape
├── assets/icons/                   # NodeIcon.tsx + themedGlyphs.ts + icon-ref resolver (index.ts)
├── config/api.ts                   # Backend base-URL config (env-driven)
├── lib/
│   ├── nodeSpec.ts                 # TanStack-Query spec fetch, resolveNodeDescription, listCachedNodeSpecs
│   ├── aiModelProviders.ts         # Frontend-only AI provider icon/credential map
│   ├── queryClient.ts              # Module-singleton QueryClient (imperative invalidation without React context)
│   ├── queryConfig.ts / featureFlags.ts
│   ├── queryPersist.ts             # localStorage persister + PERSISTED_KEY_PREFIXES whitelist
│   ├── brandStorage.ts             # Canonical browser-storage keys + pre-rebrand aliases
│   ├── connectionConfig.ts         # WS reconnect + auth-bootstrap backoff constants
│   ├── workflowOps.ts              # applyOperations for backend workflow-ops batches
│   ├── canvasLock.ts               # Server-owned can_edit capability -> canvas lock
│   ├── credentialProviderId.ts     # Which catalogue provider a canvas node's status dot reads (plugin credential ids pass through; legacy names mapped)
│   ├── dynamicOptions.ts           # nextDynamicOptionValue: move a loadOptionsDependsOn field when its parent changes
│   ├── sound.ts                    # WebAudio sound engine (10 packs)
│   ├── motion.ts                   # The one place Web Animations start: --dur-* / --ease-* tokens,
│   │                               # 1 ms under reduced motion or a hidden page, no loops then
│   ├── pageActivity.ts / useReducedMotion.ts # Whether anyone can see the page; the motion preference
│   ├── debouncedInvalidate.ts      # Trailing-edge query invalidation (broadcast bursts)
│   └── utils.ts                    # cn() = clsx + tailwind-merge (shadcn convention)
├── schemas/workflowSchema.ts       # Structural pre-flight for workflow export (backend is the schema authority)
├── stores/
│   ├── nodeStatusStore.ts          # Per-workflow node statuses (slice-subscribed Zustand)
│   ├── canvasDockStore.ts          # Docked Canvas sidebar state
│   ├── workflowControlStore.ts     # Mirror of workflow control statuses for Home (written from WebSocketContext)
│   ├── shellDialogsStore.ts        # Settings / Credentials dialog flags (lifted out of Dashboard)
│   └── workflowSettingsStore.ts    # Editor settings (auto-save etc.) read by the shell
├── test/                           # Vitest setup.ts, builders.ts, providers.tsx, README.md
├── themes/                         # base.css + animations.css + 12 per-theme CSS files
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
- Always use the CLI: `OPENCOMPANY_INSTALLING=true bun x shadcn@latest add <name>` from `client/` (the env var suppresses the project's recursive postinstall hook).
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
   └─ PanelRenderer.tsx        (lazy: ApiKey/OAuth/QrPairing/Email/BrowserProfiles)
```

**State rules:**
- **Zustand** (`useCredentialRegistry`) holds ONLY UI state: `selectedId`, `paletteOpen`, `query`. Never catalogue data. Prevents the closure-retention bug where selectors keep the whole 5000-entry catalogue in memory.
- **TanStack Query** owns the server state (catalogue, usage summaries, etc).
- **idb-keyval** cache seeds first paint — opened modal renders from IndexedDB in <50 ms before the WS roundtrip completes.
- **`requestIdleCallback`** writes back to IDB so saves don't block first paint.
- **DB is the single source of truth.** The retired `providers.tsx` static fallback is gone — `useCatalogueQuery` is the only source. Cold-boot with no IDB cache renders a `<Skeleton>` palette while the WS catalogue arrives; server-unreachable shows an explicit error state, never stale fallback data.
- **`provider.stored` is the canonical "do we have a credential for X?".** The retired `apiKeyStatuses[id].hasKey` mirror duplicated this answer with no synchronisation contract. Two new selector hooks (`useProviderStored(id)`, `useStoredProviderCount()`) read the catalogue; `useStoredCredentialSignature()` adds each named endpoint and its model count, for re-fetches a count would miss (the toolbar's global model list). `apiKeyStatuses[id]` now narrowly carries the validation result (`valid`, `models`, `message`, `timestamp`).

**App-wide query persistence ([client/src/lib/queryPersist.ts](../client/src/lib/queryPersist.ts)):** the QueryClient is wrapped in `<PersistQueryClientProvider>` ([main.tsx](../client/src/main.tsx)) with a localStorage persister + `__APP_VERSION__` buster + 24h SWR window. Only queries with key prefixes `nodeSpec` / `nodeGroups` are dehydrated -- high-frequency / per-session queries stay in-memory. Hard refresh paints from cached specs **before** the WebSocket connects, so canvas nodes never flash placeholder icons. The credentials catalogue uses its own dedicated `idb-keyval` warm-start (above) because its payload is large enough that localStorage's 5-10MB cap is a real constraint. **Decrypted credential values are NOT persisted** (was the retired `'credentialValues'` prefix) per OWASP HTML5 Security Cheat Sheet / ASVS V9.9 — plaintext API keys in `localStorage` are readable via DevTools on shared / compromised browsers; the in-memory TanStack Query cache (`gcTime: ∞`) keeps the form populated for the session lifetime, on reload the panel refetches via WS.

**`useNodeSpec` is a slice subscription, not a `useQuery`** ([client/src/lib/nodeSpec.ts](../client/src/lib/nodeSpec.ts)): reads via `useSyncExternalStore` filtered by `hashKey(['nodeSpec', type])`. Per-spec observer count is **0**; only the matching slot triggers a re-render. Lazy fetch is one-shot via `useEffect` gated on `isReady`. Do not re-introduce `useQuery(['nodeSpec', type])` -- N consumers create N observers, all woken on every cache write.

**Slice-subscribed cache entries MUST set `gcTime: GC_TIME.FOREVER`.** Slice subscribers don't register as TanStack observers, so without this override the cache entry is garbage-collected after the default `gcTime` (5 min) and every consumer reads `undefined`. The user-visible regression is "canvas nodes lose their icons / handles after idle." Applies to `fetchNodeSpec`, `fetchNodeGroups`, and the `useNodeGroups` `useQuery`; the persistor in `lib/queryPersist.ts` only handles cross-reload survival.

**Anchor cache contracts at the prefix root via `setQueryDefaults`, not per-call options.** `PersistQueryClientProvider` hydrates entries from localStorage with the QueryClient's *default* options, so per-call `staleTime: FOREVER` does not stop `gcTime: 5min` eviction on hydration. Every persisted prefix must have a matching `queryClient.setQueryDefaults(['<prefix>'], { staleTime: FOREVER, gcTime: FOREVER })` declaration in [client/src/lib/queryClient.ts](../client/src/lib/queryClient.ts). The persisted set is `['nodeSpec']`, `['nodeGroups']`; both carry `setQueryDefaults`. `['skillContent']` and `['credentialValues']` carry `setQueryDefaults` for in-memory longevity only and are intentionally absent from the persistor whitelist. The persistor whitelist in [client/src/lib/queryPersist.ts](../client/src/lib/queryPersist.ts) must mirror it; a string in the whitelist that doesn't match a real query key (the prior `'pluginCatalogue'` typo) is silently dead. `credentialCatalogue` is intentionally NOT in either list — it has its own `idb-keyval` warm-start. `credentialValues` keeps `gcTime: FOREVER` for the in-memory cache so the credentials form survives idle, but it is intentionally NOT persisted (OWASP — see "Persistence layers" above).

**Component rules:**
- `PanelRenderer` lazy-loads each panel type so the initial JS payload doesn't grow linearly with provider count.
- Panels are config-driven: `StatusCard`, `ActionBar`, `FieldRenderer`, `OAuthConnect` consume `ProviderConfig` fields rather than hand-coding per-provider JSX.
- Exception: EmailPanel has conditional `custom` IMAP/SMTP fields that the simple schema can't express — it gets a dedicated zod schema and RHF form. That's the boundary where config-driven hands off to hand-written.

## ParameterRenderer (pending Phase 6)

Currently a 2152-line switch on `parameter.type` ([client/src/components/ParameterRenderer.tsx](../client/src/components/ParameterRenderer.tsx)). 15+ branches for `string | number | boolean | options | collection | fixedCollection | code | file | credential | ...`.

**Phase 6 plan:** replace with `@jsonforms/react` renderer registry. Requires backend to expose a `get_node_spec` WebSocket handler returning `NodeSpec { jsonSchema, uiSchema, _uiHints? }` per the RFC. Frontend will own the custom renderer set (one file per widget under `components/inspector/renderers/`) and route via JSON Forms' tester-based dispatch. Feature flag `VITE_USE_NODESPEC` gates the rollout; the old `ParameterRenderer` deletes once stable.

See [ui_migration_plan.md](./ARCHIVE/ui_migration_plan.md) Phase 6.

### Name-based magic in this file (read before naming a Params field)

Several branches key on **literal parameter names**, not on types or hints. A
backend field that happens to use one of these names inherits behaviour nobody
declared:

| Name | What happens |
|---|---|
| `model`, `api_key` | With a sibling `provider` field present, an effect (~line 866) overwrites `model` with the **chat-model** list and clears `api_key`. It does **not** validate that the provider is an LLM provider, so `provider: "elevenlabs"` still triggers it. Line 930 writes options onto the literal string `'model'`, not `parameter.name`. |
| `parameters` | Android branch: fetches `/api/android/services/.../parameters` and overwrites the whole value. |
| `message_type` | Drives the `accept=` of the `file` input and remounts it. |
| `group_id`, `group_name`, `channel_jid`, `channel_display_name`, `sender_number`, `sender_name` | WhatsApp loaders; several read sibling names for their display label. |
| `session_id`, `service_id`, `action` | Agent-session and Android loaders. |

Prefix instead — `tts_model`, `stt_model`. The speech nodes do, and
`tests/nodes/test_speech.py` asserts neither declares `model` or `api_key`.
This is a known wart of the pre-Phase-6 renderer; Phase 6's tester-based
dispatch is what removes the whole class of it.

### File parameters upload; they no longer base64

`case 'file'` POSTs to `/api/workspace/{workflow_id}/uploads` via
[`lib/workspaceUpload.ts`](../client/src/lib/workspaceUpload.ts) and stores the
returned `AudioRef` (~400 bytes). Do **not** set `Content-Type` on that fetch —
the browser must set it so the multipart boundary is generated.

The legacy base64 envelope (`{type: 'upload', data: '<base64>'}`) survives only
as a fallback for a workflow that has never been saved and therefore has no
workspace yet. It is a genuine hazard at media sizes: it is JSON-stringified
onto the WebSocket, persisted in the `node_parameters` row, re-broadcast to
every connected client, and re-`JSON.stringify`d on every render by
`hasUnsavedChanges` — then dies at Temporal's 2 MiB payload limit. A ~1.5 MB
clip is already ~2 MB of base64. `coerce_file_param` still accepts it
server-side so existing saved rows keep working.

See [media_transport.md](./media_transport.md).

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
  Reads, broadcasts, and mutation responses merge monotonically by generation
  and database revision, while per-workflow pending mutation state disables
  duplicate clicks. A `{success: false}` response carrying a recognizable
  control snapshot merges that snapshot before surfacing the error; every
  failure is followed by an authoritative resync, and a generic error is never
  normalized into `never_started`.
  Lifecycle mutations use a five-minute acknowledgement timeout because Start
  and Reset may wait on durable setup/cleanup. If a timeout races a successful
  operation, the resync-confirmed stable state is returned as success.
  Authoritative `pausing`, `resuming`, and `resetting` states remain retryable
  after the local request finishes. The UI treats stable `paused`/`running` as
  confirmation that strict cron-Schedule and running-execution fan-out
  completed; a fan-out failure must remain transitional after resync.
  Likewise, `ready` means Reset first quiesced controller, cron, and local
  producers and then completed its final execution sweep. A duplicate Reset in
  `ready` is a read-only idempotent response, so an old request cannot clean up
  resources belonging to a later Start. Standalone cron and detached child
  executions carry `EventWorkflowId`, allowing the server to uphold that
  boundary through Temporal Visibility.
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
  [Memory Lifecycle](ARCHIVE/memory_lifecycle.md#workflow-reset-archives-then-clears-memory)
  (archived; it describes the retired pre-RFC-0002 markdown memory model).
- **`currentWorkflowId` lives in `useAppStore` only.** Non-React listeners (WS handlers) read it via `useAppStore.getState().currentWorkflow?.id` -- the documented Zustand escape hatch (https://github.com/pmndrs/zustand#read-state-without-subscription). The previous `currentWorkflowIdRef` mirror inside WebSocketContext was a one-render-late copy that misrouted broadcasts during workflow switches. The push to `nodeStatusStore.setCurrentWorkflowId` is driven from a single `useEffect` in `Dashboard.tsx`.

## Browser workspace live view

Home and Dev share [WorkspaceTabs](../client/src/components/workspace/WorkspaceTabs.tsx) and [BrowserWorkspace](../client/src/components/browser/BrowserWorkspace.tsx). Browser nodes are discovered through the backend `isBrowserPanel` capability; viewing attaches to a saved workflow/node without launching Chrome. The runtime defaults to installed Chrome/Edge/Chromium with a dedicated profile rendered headless inside the workspace; a separate desktop window and testing-browser mode are explicit server settings. The Browser tab supports server-authorized Take control / Hand back for navigation, mouse, keyboard, paste and login. The Browser node's parameter panel shows the same viewer above its settings. When an agent in the open workflow calls `request_user`, the `browser_updated` case in `WebSocketContext` opens the Dev dock on its Browser tab (`canvasDockStore.showBrowser`); Home shows Needs you from the employee summary's `browser_request`. Canvas retains its renderer; Android remains an explanatory placeholder.

The viewer uses a separate authenticated same-origin `/ws/browser` socket, not the general `WebSocketContext` frame stream. Binary JPEG envelopes are decoded sequentially with a two-frame local bound; consumed/skipped frames are acknowledged. Canvas dimensions change only when image dimensions change, and input coordinates use the metadata of the frame actually painted. Hidden, disposed or obsolete frames cannot restore a stale picture. Blur, pointer cancellation, hiding and unmount release user control; the server owns the release barrier and lease checks.

`?browserStreamDebug=1` enables numeric first-frame/decode/draw summaries without recording page or input payloads. The [Browser workspace contract](./browser_workspace.md) describes identity, visibility and interaction; [Browser](./browser.md) owns runtime installation, profiles, security and capture/control internals. Tests live under `components/browser/__tests__`, with Home and Dev dock integration tests beside their hosts.

## Ownership boundary: TanStack Query vs Zustand vs WebSocketContext

This is the rule that keeps the data layer schema-driven instead of imperatively glued together.

| Owns | What goes here | Examples |
|---|---|---|
| **TanStack Query** | Anything the server has authoritative state for. List / single-record / settings reads. Mutations that change server state. | `useWorkflowsQuery`, `useNodeParamsQuery`, `useUserSettingsQuery`, `useCatalogueQuery`, `useSaveWorkflowMutation`, `useSaveNodeParamsMutation`, `useSaveUserSettingsMutation` |
| **Zustand** | UI-only state that survives navigation. The active edit buffer for the current workflow. Sidebar/panel visibility flags. | `useAppStore.currentWorkflow` (mutable buffer), `sidebarVisible`, `shellMode`, `proMode`, `renamingNodeId`, `useCredentialRegistry.selectedId`, Home's `useHomeStore` |
| **`useState` / `useReducer`** | Per-component transient state. Form-field drafts. Hover/focus. | text-input drafts, dropdown-open, inline-edit toggles |
| **`WebSocketContext`** | Raw WS connection, `sendRequest`, push-only broadcast slices (workflow progress, android/whatsapp/twitter status, console/terminal logs). The provider value is `useMemo`'d so unrelated state changes do not re-render every consumer. Exposes `isConnected` (socket open) and `isReady` (open + pending-send queue drained); gate catalogue/spec queries on `isReady`. `drainPendingSends(ws)` runs synchronously, then `setIsReady(true)` fires immediately; terminal/chat/console history restore is fire-and-forget in the background (Wave 32 removed the init-burst gate and the hardcoded `probeApiKey` loop). Catalogue invalidation routes through `invalidateCatalogue(queryClient)` ([`hooks/useCatalogueQuery.ts`](../client/src/hooks/useCatalogueQuery.ts)) which debounces the refetch on a 300 ms trailing edge, so an oauth burst or multi-service reconnect collapses to one refetch instead of N. | `androidStatus`, `consoleLogs`, broadcast streams |
| **`stores/nodeStatusStore.ts`** (Zustand) | Per-workflow node-execution statuses -- moved out of WebSocketContext so a status tick does not cascade through the React tree. `useNodeStatus(id)` is a slice selector; only the affected node's consumers re-render. Mirror this pattern for any new high-frequency push state. | `allStatuses[workflowId][nodeId]`, `currentWorkflowId` |

**Hard rules:**
- **Read Zustand stores via slice selectors, never whole-store destructure.** Always `const x = useAppStore((s) => s.x)`, never `const { x } = useAppStore()`. The whole-store form re-renders the consumer on ANY store mutation (sidebar toggle, unrelated workflow rename, parameter save on another node), which defeats `React.memo` + `nodePropsEqual` on the canvas. Setters are stable refs from Zustand — single-field selectors are the cheapest read. Audited and converted across the canvas + parameter-panel hot paths (every node component, `Dashboard.tsx`, `useDragVariable`, `useParameterPanel`, `useReactFlowNodes`, `useWorkflowManagement`, `InputSection`, `MiddleSection`, `OutputPanel`, `ParameterRenderer`, `ParameterPanel`).
- A list of server records (`workflows`, `nodeParameters`, `userSettings`, `credentialCatalogue`, `userSkills`, node output schemas) lives in TanStack Query. Never duplicate it in Zustand. Phase-1 follow-up commit `c3a7aa4` removed `savedWorkflows` from `useAppStore` for exactly this reason; Wave 3 commit `7706afb` did the same for `userSkills` in MasterSkillEditor.
- Imperative WebSocket request/response inside a component (`useEffect` + `sendRequest` + `setState`) is a code smell — wrap it in a `useQuery` hook. Inline the hook at the top of the consuming file when there's exactly one consumer (Wave 2/3 colocation rule); promote to `client/src/hooks/` when a second consumer appears. Phase-2 commit `b2b6fba` did this for `useParameterPanel` and `useOnboarding`; Wave 3 commits `2c5f227` / `7706afb` / `327f792` followed the same pattern inline inside MiddleSection / MasterSkillEditor / InputSection.
- After a mutation, **invalidate the corresponding query key**, don't manually patch a Zustand list or call a local refetch helper. Mutations that need it from non-React code use the `queryClient` singleton at [client/src/lib/queryClient.ts](../client/src/lib/queryClient.ts).
- Schema metadata for parameter behavior (selectors, validators, dynamic options) belongs in the node-definition `typeOptions`, NOT in `parameter.name === '...'` checks inside `ParameterRenderer`. Phase-5 commit `8353c48` introduced `typeOptions.loadOptionsMethod` for the WhatsApp selectors as the canonical pattern.
- **Runtime output shapes for the Input panel's variable list live on the backend** via Pydantic models in `server/services/node_output_schemas.py`. The frontend fetches them lazy via `get_node_output_schema`; real execution data takes precedence. See the "Node output shape" section below and [schema_source_of_truth_rfc.md](./ARCHIVE/schema_source_of_truth_rfc.md).
- **Never hand-roll a modal backdrop.** Destructive confirmations use `<AlertDialog>`; composite panels use the `Modal.tsx` primitive on top of shadcn `<Dialog>`. A raw `position: fixed; background: rgba(0,0,0,0.5)` in new code should not pass review.

## Schema-driven node + panel hints

Wave 2 introduced two typed fields on `INodeTypeDescription` so panels and the inspector can make rendering decisions from the schema instead of `nodeDefinition.name === '…'` string compares.

### `uiHints` — per-node panel visibility flags

Defined on `INodeTypeDescription.uiHints` ([client/src/types/INodeProperties.ts](../client/src/types/INodeProperties.ts)). Each flag is consumed by exactly one panel and defaults to off (the panel renders normally). The current set (the backend `known` set in `server/tests/test_node_spec.py` must match `INodeUIHints`; a flag with no frontend consumer is dead weight and gets removed from both):

| Flag | Read by | Effect |
|---|---|---|
| `hideInputSection` | `ParameterPanel`, `InputSection` | Skip the connected-inputs panel (start, skill, monitor) |
| `hideOutputSection` | `ParameterPanel`, `OutputSection` | Skip the execution-results panel |
| `hideRunButton` | `ParameterPanel` | Hide the Run button (skill / memory / tool nodes) |
| `hasCodeEditor` | `MiddleSection` | Give the params block extra flex space for an embedded code editor |
| `isMasterSkillEditor` | `MiddleSection`, `Dashboard` (component dispatch), `useAutoSkillEdges` | Render the MasterSkillEditor split panel; route to `ToolkitNode` on the canvas; identify Master Skill aggregators in the auto-skill edge dispatcher |
| `isMemoryPanel` | `MiddleSection` | **Legacy.** The pre-RFC-0002 combined markdown/transcript panel. `simpleMemory` no longer declares it; kept while `normalize_workflow_graph` upgrades `input-memory` graphs |
| `isMemoryToolPanel` | `MiddleSection` | Render the durable Memory item browser (search, edit, forget, clear). Declared by `simpleMemory`, and selected *before* `isMemoryPanel` |
| `isContextPanel` | `MiddleSection` | Render the Context inspector (journal, active replay, fork/export/clear). Read-only: it observes the agent's journal and must never alter execution |
| `isDataPanel` | `MiddleSection` | Render the Data node's mounts + read-only file browser (`DataPanel`). Declared by `dataSource`. |
| `requiresContext` | backend graph validation | Declared in `STD_AGENT_HINTS`. Not a rendering flag — `workflow_validator` accepts a Context edge only into a node carrying it. The Context itself is optional: nothing pairs one with the agent, and an agent without one is valid |
| `systemManaged` | `ComponentPalette` | Declared by the Context node; keeps it out of the component palette. It does not protect the node — the user can delete it, and neither normalization nor save re-creates or restores it |
| `isMonitorPanel` | `MiddleSection`, `ParameterPanel` | Render the team-monitor panel |
| `isTodoEditor` | `MiddleSection` | Render the editable Current Todos manager (`writeTodos`) instead of the plain params list |
| `isTaskManagerPanel` | `MiddleSection` | Render the execution-scoped team task control panel |
| `isProcessManagerPanel` | `MiddleSection` | Render live managed-process inspection and controls |
| `isGalleryPanel` | `MiddleSection` | Render the workspace file browser (breadcrumbs, grid/list, search, preview, upload, drag-to-parameter) instead of the plain params list. Declared by `gallery`, which pairs it with `hideInputSection` but **keeps** the Output section — unlike `processManager` it produces output worth seeing and dragging. The panel writes back to the node's own `path` / `selection` params, so what you browse is what the node emits. |
| `isCanvasPanel` | `MiddleSection`, `CanvasDock` | Render the pushed-content Canvas board instead of the plain params list. Declared by `canvas`. Double duty: the docked canvas sidebar also uses this flag to FIND Canvas nodes in the graph (`resolveNodeDescription(type)?.uiHints?.isCanvasPanel`) — never the type string. Pairs with an explicit `isConfigNode: False` because the `tool` group would auto-derive `True` while the node's `input-main` is real dataflow. See [canvas_node.md](./canvas_node.md). |
| `isBrowserPanel` | `MiddleSection`, `CanvasDock` (also the server's `graph_index`) | Show the node's live browser (`BrowserWorkspace`) above its parameters. Declared by `browser`. The Dev dock and Home's employee summary also use it to FIND Browser nodes in a graph, never the type string. See [browser_workspace.md](./browser_workspace.md). |
| `showLocationPanel` | `LocationParameterPanel` | Special-case panel for nodes with map preview |
| `isChatTrigger` | `ConsolePanel` | This node is a chat-message target |
| `isConsoleSink` | `ConsolePanel` | This node consumes console output (filter source) |
| `hasSkills` | Agent panels | Connect the connected-skills section |
| `isConfigNode` | `InputSection`, `OutputPanel` | This node is auxiliary configuration — its panel inherits the parent's main inputs instead of showing direct upstream connections. **Auto-derived on the backend** by `_derive_auto_ui_hints` in [`server/services/plugin/base.py`](../server/services/plugin/base.py): plugins whose `group` tuple contains `memory` or `tool` get this for free. Explicit `cls.ui_hints` always wins. |
| `outputMode: "terminal"` | `output/OutputPanel` | The node's textual output is CLI/terminal text: render it in a `<pre>` painted with the per-theme `--code-*` tokens instead of ReactMarkdown (which turns `#` into headings and collapses indentation). Strings that are wholly JSON route to the JSON tree via the shared `tryParseJson` helper. Declared by the CLI-wrapper plugins (`githubAction`, `vercelAction`, `shell`). |
| `outputMode: "audio"` | `output/OutputPanel` | The node produces audio: render an `<audio>` player for each `AudioRef` in the result instead of letting it fall into the JSON tree. Declared by `textToSpeech`. Note the panel's detection is **structural** (`kind === "audio"`), so a ref arriving from anywhere still renders — the hint is the declarative signal, not the gate. Widening this union needed no backend change: `test_ui_hints_only_carry_known_flags` checks flag *names*, not values. |

Adding new panel behaviour: add a flag to `INodeUIHints`, annotate the relevant node definitions (or extend the auto-derivation rule on the backend), read the flag in the panel. Don't add another `nodeDefinition.name === '…'` branch — six such checks for `'masterSkill'` were retired in this round in favour of `uiHints.isMasterSkillEditor`. Pytest invariant `test_ui_hints_only_carry_known_flags` in `server/tests/test_node_spec.py` locks the flag set; new flags must be added there too.

### Observable skill loading

The `agent_capability` wire route carries the canonical CloudEvents occurrence;
`node_status.data.active_skills` is its reconnect/latest-state projection. The
consumer validates `specversion`, reverse-DNS type, allowed kind/state, and
exact `subject === data.agent_node_id === data.author_node_id`, then
deduplicates on `(source, id)`. Root-execution checks reject late events from a
reset generation. It never falls back to an outer message node id or a label.
Agent cards show Loading/Loaded/Failed badges for the current turn;
the exact Master Skill card glows only during retrieval, then returns to a
non-executing success/error state while retaining the same safe names.
Agent live text replaces the card's center subtitle with `skill <name>` for
instruction retrieval or `tool <name>` for ordinary tool execution; it is never
rendered as an extra line near the bottom handles. Capability ownership is the
exact invoking agent node: descendant tool metadata is never mirrored to its
parent, completed agents restore their configured subtitle, and deleting a node
clears its status slot before that ID can be reused. The Master Skill editor projects runtime state onto
the matching enabled skill row so operators can see which listed skill is active.
Every agent's Connected Skills section also exposes an expandable, read-only
`Skill prompt` sourced from the connected Master Skill's authoritative
`skills_config.instructions`; this is configuration inspection, not runtime
status broadcasting or system-prompt injection.
Capability summaries are producer-owned. The status broadcaster preserves the
allowlisted `last_capability`, `last_tool_name`, and `last_skills` fields across
generic phase and outer terminal snapshots, but only within the same workflow;
the next agent initialization explicitly clears them. Temporal and legacy tool
paths emit the same typed capability lifecycle and also publish a raw executing
node status containing only safe tool-name fields for reconnect snapshots. The
parallel `agent_progress` CloudEvent intentionally carries only phase and
iteration counters. Do not deep-merge arbitrary status data in the browser:
omitted fields are not universally equivalent to unchanged fields. Capability
payloads never carry prompts, arguments, results, contents, secrets, or raw
exception text.
Ref-counted backend state prevents concurrent calls from clearing one another.
`agent.skill.cleared`, reconnect reconciliation, cancellation, pause, and reset
remove transient badges. This is independent from ordinary tool-node glow.

### Node output shape — backend as single source of truth

Frontend does **not** declare output shapes anymore. The backend owns them exclusively via Pydantic models in [server/services/node_output_schemas.py](../server/services/node_output_schemas.py) — live size via `len(NODE_OUTPUT_SCHEMAS)`. JSON Schema is emitted via Pydantic's `model_json_schema()` and exposed two ways:

- `GET /api/schemas/nodes/{node_type}.json` — static, long-cache (`Cache-Control: public, max-age=86400`), no auth. n8n-style static-asset pattern.
- `get_node_output_schema` WebSocket handler — authenticated editor path.

[InputSection.tsx](../client/src/components/parameterPanel/InputSection.tsx) consumes schemas lazy via `fetchNodeOutputSchema(nodeType)` (inline helper wrapping `queryClient.fetchQuery` with `staleTime: Infinity`). The draggable variable list's shape precedence is:

1. Real execution data from the last run (primary).
2. Backend-declared schema fetched on demand (fallback).
3. `{ data: 'any' }` empty state (final fallback — the legacy `sampleSchemas` map was deleted in Wave 3).

**Adding a new node type's output shape:** define a Pydantic model in `node_output_schemas.py`, register it in `NODE_OUTPUT_SCHEMAS`. The frontend picks it up automatically — no client change, no rebuild. Research and rationale in [docs-internal/schema_source_of_truth_rfc.md](./ARCHIVE/schema_source_of_truth_rfc.md).

**`jsonSchemaToShape` must resolve Pydantic's indirection.** `model_json_schema()` does not emit a flat `{field: {type}}` map: `Optional[X]` becomes `{anyOf: [<X>, {type: 'null'}]}` with **no top-level `type`**, and a nested `BaseModel` becomes `{$ref: '#/$defs/Name'}` with the body in `$defs`. A reader that only inspects `prop.type` types both as `'any'`. Since every field on the trigger output models is Optional, that mistyped *every* field and left nested blocks (`telegramReceive.media`, `whatsappReceive.group_info`) with no drillable leaves — the useful drag target is `media.file_id`, not `media`. `resolveSchemaNode` unwraps unions (first non-null branch) and follows local `#/$defs/` pointers, threading the expanded-ref chain through the mutual recursion so a self-referencing model cannot recurse until the stack blows and takes the panel with it. Locked by [jsonSchemaToShape.test.ts](../client/src/components/parameterPanel/__tests__/jsonSchemaToShape.test.ts).

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
| [client/src/styles/canvasAnimations.ts](../client/src/styles/canvasAnimations.ts) | Canvas-wide CSS injected once into Dashboard's `<style>` tag — adding a new keyframe or status class is a single-file change. Fully static since the design-handoff edge migration: `buildCanvasStyles()` takes no arguments; every colour/width/dash is a theme token (`--edge-stroke`, `--edge-stroke-width{,-active,-done}`, `--edge-dash{,-active}` from `themes/base.css`, plus semantic tokens for the status classes `selected/executing/completed/error/pending/memory-active/tool-active/skill-active`). Resting edges are pale-neutral dashed orthogonal step edges; the in-progress `.react-flow__connection-path` shares the resting rule. The old `CanvasStatusColors` per-theme hex interface was deleted — add a token, never a colour parameter. |
| [client/src/components/ui/alert-dialog.tsx](../client/src/components/ui/alert-dialog.tsx) | Confirmation / destructive-action modals. **Never hand-roll a `position: fixed; background: rgba(0,0,0,0.5)` backdrop** — use `<AlertDialog open onOpenChange>` with `AlertDialogHeader` / `AlertDialogDescription` / `AlertDialogFooter`. Focus trap, escape-to-close, and `role="alertdialog"` come from Radix. MiddleSection Clear Memory + Reset Skill dialogs are the canonical consumers (Wave 3 commit `61bf23c`). |
| [client/src/components/ui/sonner.tsx](../client/src/components/ui/sonner.tsx) | The `<Toaster />` mount — call `import { toast } from 'sonner'` directly at use sites; do not wrap. |
| [client/src/components/ui/Modal.tsx](../client/src/components/ui/Modal.tsx) | Composition primitive on top of shadcn `<Dialog>`. Owns the recurring "title bar with centered headerActions and a close button + size-constrained content panel" 8 panels share. Not an antd facade. For destructive confirmations prefer `AlertDialog` above. |
| `client/src/components/ui/{button,input,select,switch,checkbox,form,…}.tsx` | shadcn-generated primitives. Add new ones via `bun x shadcn@latest add <name>`. Don't re-implement what the registry ships. |

## Theme + canvas chrome

[index.css](../client/src/index.css) also styles React Flow, scrollbars, and dot grid against the CSS-var palette. No inline hex codes in theme-sensitive surfaces — everything references `var(--...)` so themes flip cleanly.

## Build + dev

```bash
# from repo root
bun install             # client deps + server Python deps via postinstall
bun run dev             # supervisor: Vite client (app port, proxying) + uvicorn backend; Temporal/WhatsApp are backend-owned on-demand daemons
bun run build           # full prod build; bundle analyzer at dist/stats.html if ANALYZE=1

# client-only
cd client
bun run dev             # Vite dev server
bun run build           # Vite prod build (Tailwind v4 via @tailwindcss/vite plugin)
bun run typecheck       # THE gate — delegates up to the root TypeScript 7 compiler
bun run typecheck:tsc   # second opinion under client's tsc 5.9.3 (triage only, never the gate)
```

**Adding shadcn components:**
```bash
cd client
OPENCOMPANY_INSTALLING=true bun x shadcn@latest add <name>
```
The `OPENCOMPANY_INSTALLING=true` env var suppresses the recursive project postinstall hook during shadcn's internal package-manager install. Without it the hook's `company build` run fails and shadcn aborts before writing the component file.

## Migration history (for context)

This architecture is the post-migration state. Pre-migration was antd + `styled-components` + a custom theme.ts-driven palette. See [ui_migration_plan.md](./ARCHIVE/ui_migration_plan.md) for the phase-by-phase transition and the 17 commits that executed it.

**`useAppTheme()` powers the canvas + maps surface across all 12 themes.** The hook returns a `theme` object with the legacy `Colors` shape (`theme.colors.X`, `theme.isDarkMode`) so existing call sites don't change. Under non-light/dark themes it merges a per-theme overlay (primary, focus, action palette, edge stroke / selection / executing / completed / error) on top of the chosen base pack (`lightColors` for utopian-bright themes, `darkColors` for dystopian / dark themes). Adding a new theme overlay is a single entry in the `THEME_OVERRIDES` map in [hooks/useAppTheme.ts](../client/src/hooks/useAppTheme.ts).

Read sites: every canvas node component (`AIAgentNode`, `SquareNode`, `TriggerNode`, `StartNode`, `ToolkitNode`, `TeamMonitorNode`), `EdgeConditionEditor`, and the Maps surface (`GoogleMapsPicker`, `MapsPreviewPanel`) — they interpolate per-definition `nodeColor` and JS-side hex values that Tailwind classes can't express. Every other surface uses Tailwind + the token tiers above and retints automatically through the per-theme `[data-theme="..."]` block.
