# Theme System — design-handoff token contract + 4-way themes

The MachinaOs frontend supports four visual themes — **light**, **dark**, **renaissance**, **cyber** — selected at runtime via `<html data-theme="...">`. The system is purely CSS-variable-driven: components render against semantic token names, and the active `[data-theme="..."]` block in `client/src/themes/` rebinds those tokens to the theme's surface, foreground, accent, typography, geometry, and motion values.

This document is the playbook for working in the design system: token taxonomy, migration recipe, anti-patterns, and where each piece lives.

## Architecture at a glance

```
client/src/themes/
├── base.css         neutral defaults (space, motion easings, sound pack hint)
├── light.css        :root + :root[data-theme="light"] — new contract values
├── dark.css         .dark + :root[data-theme="dark"]   — new contract values
├── renaissance.css  :root[data-theme="renaissance"]    — full palette + bridge
└── cyber.css        :root[data-theme="cyber"]          — full palette + bridge

client/src/index.css
├── @layer base :root  — shadcn HSL-triplet tokens (light defaults)
├── @layer base .dark  — shadcn HSL-triplet tokens (dark overrides)
└── @theme inline { … } — Tailwind v4 utility bindings for both contracts

client/src/contexts/ThemeContext.tsx
├── theme: 'light' | 'dark' | 'renaissance' | 'cyber'
├── persists to localStorage['machinaos-theme']
├── migrates legacy 'darkMode' boolean
└── sets <html data-theme="..."> + (.dark class for legacy `dark:` Tailwind variants)

client/src/components/ui/ThemeSwitcher.tsx — DropdownMenu in TopToolbar
client/src/components/ui/StatusBar.tsx     — fixed-bottom system console
client/src/components/ui/CommandPalette.tsx + CommandPaletteHost.tsx — ⌘K launcher
```

## Token tiers

There are five tiers, ordered from most semantic to most concrete. Always pick the most semantic that fits the call site.

### 1. New-contract surface tokens (preferred for new code)

Surface hierarchy across themes; every theme assigns these in its `:root[data-theme="..."]` block.

| Tailwind utility | CSS var | Purpose |
|---|---|---|
| `bg-bg-app` | `--bg-app` | Outer page / root background |
| `bg-bg-panel` | `--bg-panel` | Sidebars, palette, toolbar, footer chrome |
| `bg-bg-canvas` | `--bg-canvas` | The workflow canvas |
| `bg-bg-elevated` | `--bg-elevated` | Modals, dropdowns, popovers, cards inside panels |
| `bg-bg-input` | `--bg-input` | Form-field backgrounds |
| `bg-bg-hover` | `--bg-hover` | Hover state for rows / buttons |
| `bg-bg-active` | `--bg-active` | Selected / pressed state |
| `bg-bg-overlay` | `--bg-overlay` | Modal scrims |

| Tailwind utility | CSS var | Purpose |
|---|---|---|
| `text-fg-default` | `--fg-default` | Primary text |
| `text-fg-muted` | `--fg-muted` | Secondary / metadata |
| `text-fg-faint` | `--fg-faint` | Placeholders / divider text |
| `text-fg-on-accent` | `--fg-on-accent` | Text over `--accent` fills |

| Tailwind utility | CSS var | Purpose |
|---|---|---|
| `border-default` | `--border-default` | Standard borders + dividers |
| `border-strong` | `--border-strong` | Section separators, focused panels |
| `border-focus` | `--border-focus` | Focus rings (often shadowed) |

| Tailwind utility | CSS var | Purpose |
|---|---|---|
| `font-display` | `--font-display` | Headings, panel titles, action labels (Cinzel under Ren, Major Mono under Cyber) |
| `font-body` | `--font-body` | Paragraph + UI copy |
| `font-mono` | `--font-mono` | Code, console, JSON, status bar, kbd |

| Tailwind utility | CSS var | Purpose |
|---|---|---|
| `tracking-[var(--type-tracking-display)]` | `--type-tracking-display` | Letter-spacing for display headings |
| `[text-transform:var(--type-uppercase)]` | `--type-uppercase` | Theme-driven uppercase / capitalize / none |

**Note**: full-colour values, no HSL triplet, so alpha composition (`bg-bg-app/50`) does **not** work for these. Use the shadcn alias (`bg-background/50`) when you need alpha.

### 2. shadcn semantic tokens (preserved for primitives + alpha-composed call sites)

Standard shadcn keys (`background`, `foreground`, `card`, `popover`, `primary`, `destructive`, `success`, `warning`, `info`, `border`, `input`, `ring`, etc.). Each theme's CSS file regenerates them as HSL triplets matching the same colour as the new-contract token, so existing utilities keep working. **Use these when you need `/50` alpha** (e.g., `bg-primary/10`, `text-foreground/80`).

### 3. Action role tokens

Six semantic intents: `run | stop | save | config | secret | tools`. Each exposes `base / -soft / -hover / -border` (see [client/src/index.css](../client/src/index.css)). Read via `<ActionButton intent="run">` or directly: `bg-action-run-soft text-action-run border-action-run-border`. Themes redefine these in their own block.

### 4. Node-type role tokens

Six tokens for canvas identity: `agent / model / skill / tool / trigger / workflow`. Each exposes `base / -soft / -border`. Used on palette icons, parameter-panel section headers, draggable variable cards. **Never use `/N` opacity arithmetic** at call sites — themes own the alpha.

### 5. Dracula raw accents (palette, not consumed directly)

`--dracula-green/purple/pink/cyan/red/orange/yellow/selection/current-line/comment`. Same value across light + dark. Used as the underlying palette that `--action-X` and `--node-X` reference; do not use directly in components.

## Migration recipe

The single most important rule from the design handoff:

> Components render against `var(--token)` references only. There is no theme-conditional logic in component bodies. Every visual switch lives in the per-theme CSS file under `client/src/themes/`.

Concrete swaps when migrating an existing component:

| Before (legacy) | After (new contract) | When |
|---|---|---|
| `bg-card` (panel chrome) | `bg-bg-panel` | Sidebars, palette, toolbar, footer |
| `bg-card` (elevated card) | `bg-bg-elevated` | Modal heads, dropdowns, settings sections, chat bubbles |
| `bg-background` | `bg-bg-app` | Modal body, page-level backgrounds |
| `bg-muted` (subtle distinct surface) | `bg-bg-app` or `bg-bg-elevated` | Pick by elevation intent |
| `border-border` | `border-border-default` | Dividers, card outlines |
| `text-foreground` | `text-fg-default` | Body text |
| `text-muted-foreground` | `text-fg-muted` | Secondary text |
| `text-sm font-medium` (display surface) | `font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)] text-fg-default` | Panel titles, section headers, node labels |
| `text-xs` (metadata / counts) | `font-mono text-xs` | Status pills, timestamps, IDs |
| `<input>` field bg | `bg-bg-input` | Form fields |
| Hover row | `hover:bg-bg-hover` | Lists, menu items |
| Selected row | `bg-bg-active` | Selected workflow cards, active tabs |
| Modal scrim | `bg-bg-overlay` | Dialog overlays |
| `<ActionButton intent="...">` | unchanged | All toolbar / panel action buttons |

### Display-typography helper

Heading surfaces under all four themes use the same triplet. Keep them grouped in the same className for clarity:

```tsx
<span className="font-display tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
  {heading}
</span>
```

Result:
- Light/dark: clean Geist sans, no transform, no extra tracking
- Renaissance: Cinzel uppercase, +0.10em tracking
- Cyber: Major Mono Display uppercase, +0.18em tracking

## Adding a new theme

1. Drop a CSS file at `client/src/themes/<name>.css`. Required block:
   ```css
   :root[data-theme="<name>"] {
     /* New contract — full colour values */
     --bg-app: …; --bg-panel: …; --bg-canvas: …; --bg-elevated: …;
     --bg-input: …; --bg-hover: …; --bg-active: …; --bg-overlay: …;
     --border-default: …; --border-strong: …; --border-focus: …;
     --fg-default: …; --fg-muted: …; --fg-faint: …;
     --fg-on-accent: …; --fg-on-success: …; --fg-on-danger: …; --fg-on-warning: …;
     --font-display: …; --font-body: …; --font-mono: …;
     --type-uppercase: none|uppercase|capitalize;
     --type-tracking-display: 0|0.10em|0.18em;
     --radius-sm: …; --radius-md: …; --radius-lg: …; --radius-pill: …;

     /* Shadcn HSL-triplet bridge — same colours, HSL form */
     --background: H S% L%; --foreground: H S% L%;
     --card: H S% L%; --popover: H S% L%; --primary: H S% L%;
     --secondary: H S% L%; --muted: H S% L%; --accent: H S% L%;
     --destructive: H S% L%; --success: H S% L%; --warning: H S% L%; --info: H S% L%;
     --border: H S% L%; --input: H S% L%; --ring: H S% L%;

     /* Action role tokens — derive from theme semantic palette */
     --action-run: H S% L%;     --action-run-soft: H S% L% / 0.18; …
     --action-stop: H S% L%;    …
     --action-save: H S% L%;    …
     --action-config: H S% L%;  …
     --action-secret: H S% L%;  …
     --action-tools: H S% L%;   …
   }
   ```
2. Import it in [client/src/main.tsx](../client/src/main.tsx) **before** `index.css`.
3. Add the name to `AVAILABLE_THEMES` in [client/src/contexts/ThemeContext.tsx](../client/src/contexts/ThemeContext.tsx).
4. (Optional) Add a `THEME_META` entry in [client/src/components/ui/ThemeSwitcher.tsx](../client/src/components/ui/ThemeSwitcher.tsx) for the dropdown label + blurb.
5. (Optional) If the theme uses non-system fonts, add a `<link rel="stylesheet">` in [client/index.html](../client/index.html) — currently Renaissance + Cyber load Cinzel / Cormorant Garamond / IM Fell English / Major Mono Display / VT323 / JetBrains Mono via the deferred Google Fonts link there.

**No component code changes.** That is the contract.

## Anti-patterns (forbidden)

1. **Theme-conditional logic in components.** No `if (theme === 'cyber')` branches. Visual differences live in the per-theme CSS file.
2. **Whole-store Zustand destructure.** Always `useAppStore((s) => s.x)`, never `{ x } = useAppStore()`.
3. **Opacity arithmetic on new-contract tokens.** `bg-bg-app/10` does not work — they're full-colour. Add a new variant under `client/src/themes/<name>.css` if you need a different alpha, or fall back to the shadcn alias (`bg-background/10`).
4. **Hardcoded colours.** No `bg-white`, `bg-black`, `text-gray-500`, `style={{ backgroundColor: '#fff' }}`. Use tokens.
5. **`useAppTheme()` in new files.** Grandfathered for canvas node components (`AIAgentNode`, `SquareNode`, `TriggerNode`, `StartNode`, `ToolkitNode`, `TeamMonitorNode`, `GenericNode`) and `EdgeConditionEditor` — they interpolate per-definition `nodeColor` into gradients. Plus `MapSelector` / `GoogleMapsPicker` which need hex for the Maps SDK. Every other surface uses Tailwind + tokens.
6. **Hand-rolled modal backdrops.** Use `<Modal>` from `client/src/components/ui/Modal.tsx`.
7. **Hand-rolled colored buttons.** Use `<ActionButton intent="...">`.
8. **Static fallback theme palettes in TS.** Themes live in CSS, not TS exports. The `client/src/styles/theme.ts` file is grandfathered for the canvas + Maps SDK only.

## What's done vs deferred

### Done (this migration)
- Token contract: 4 themes (light, dark, renaissance, cyber)
- ThemeProvider extended; legacy `darkMode` localStorage migration
- ThemeSwitcher in TopToolbar
- Tailwind v4 `@theme inline` exposing new utilities
- Migrated: Modal, TopToolbar, WorkflowSidebar, ComponentPalette, ComponentItem, CollapsibleSection, ConsolePanel chrome, SettingsPanel
- New: StatusBar (fixed-bottom system console), CommandPalette (⌘K), CommandPaletteHost
- Google Fonts deferred-load
- Build + typecheck clean

### Deferred (future PRs)
- **Canvas nodes**: `AIAgentNode`, `SquareNode`, `TriggerNode`, `StartNode`, `ToolkitNode`, `TeamMonitorNode`, `GenericNode` still call `useAppTheme()` for inline gradients tied to per-node colours. Under Renaissance/Cyber they'll use the light or dark palette (depending on `isDarkMode`). To fully migrate, extend `useAppTheme` to return one of four colour packs, or move per-node gradient styles into per-theme CSS.
- **Parameter panel internals** (`ParameterRenderer`, `MiddleSection`, `MasterSkillEditor`): consume shadcn semantic tokens through the bridge — function correctly under all themes but headers don't yet pick up `font-display`. Apply the migration recipe above when touching them.
- **Credentials modal sub-panels** (`OAuthPanel`, `EmailPanel`, `ApiKeyPanel`, `QrPairingPanel`): same — inherit via the bridge, can be promoted when touched.
- **Decorative layers** from the design handoff (vellum noise, fleur-de-lis, scanlines, CRT flicker on `.app-frame`): per-theme CSS files include the body-level background gradients, but per-component decorations (corner ornaments, marginalia, scanlines on modals) target classes like `.app-frame`, `.canvas`, `.sq-node` that don't always exist in the React tree. Add wrapper classes (e.g., add `app-frame` to the Dashboard root) and import the decorative blocks from `design_handoff_machinaos_themes/themes/` as the surfaces stabilise.
- **Sound pack** (`--sound-pack` token): each theme declares its sound name (`parchment`, `terminal`, `chime`); JS hook + WebAudio implementation are tracked on the handoff but not yet wired.

## Verification checklist

When changing a component, verify under all four themes:

```bash
cd client && pnpm dev
```

1. `<html data-theme="light">` — clean default, near-white surfaces
2. `<html data-theme="dark">` — Solarized + Dracula
3. `<html data-theme="renaissance">` — parchment, gold accents, Cinzel headings
4. `<html data-theme="cyber">` — neon over void, JetBrains Mono everywhere

Build + typecheck must stay clean:

```bash
cd client && pnpm typecheck && pnpm build
```

Touched components should pass lint:

```bash
cd client && npx eslint <files>
```

## File index

| File | Role |
|---|---|
| [client/src/themes/base.css](../client/src/themes/base.css) | Neutral defaults |
| [client/src/themes/light.css](../client/src/themes/light.css) | Light theme tokens |
| [client/src/themes/dark.css](../client/src/themes/dark.css) | Dark theme tokens |
| [client/src/themes/renaissance.css](../client/src/themes/renaissance.css) | Renaissance theme + bridge |
| [client/src/themes/cyber.css](../client/src/themes/cyber.css) | Cyber theme + bridge |
| [client/src/index.css](../client/src/index.css) | shadcn HSL triplets + `@theme inline` Tailwind utilities |
| [client/src/contexts/ThemeContext.tsx](../client/src/contexts/ThemeContext.tsx) | `<ThemeProvider>` + `useTheme()` |
| [client/src/components/ui/ThemeSwitcher.tsx](../client/src/components/ui/ThemeSwitcher.tsx) | Dropdown picker in TopToolbar |
| [client/src/components/ui/StatusBar.tsx](../client/src/components/ui/StatusBar.tsx) | Fixed-bottom system line |
| [client/src/components/ui/CommandPalette.tsx](../client/src/components/ui/CommandPalette.tsx) | ⌘K palette primitive |
| [client/src/components/ui/CommandPaletteHost.tsx](../client/src/components/ui/CommandPaletteHost.tsx) | Registered command set |
| [client/src/components/ui/action-button.tsx](../client/src/components/ui/action-button.tsx) | CVA action role primitive |
| [client/src/components/ui/Modal.tsx](../client/src/components/ui/Modal.tsx) | Shared modal layout |
| [client/index.html](../client/index.html) | Google Fonts loading |
| [design_handoff_machinaos_themes/](../design_handoff_machinaos_themes/) | Reference HTML mocks + token spec |
