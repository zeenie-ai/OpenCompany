# Theme System — design-handoff token contract + 10 themes

The MachinaOs frontend supports **ten visual themes**, organised as a utopian / dystopian taxonomy from the design handoff:

**Utopian:** `light` · `dark` · `renaissance` · `greek` · `edo` · `steampunk` · `atomic`
**Dystopian:** `cyber` · `wasteland` · `rot` · `plague` · `surveillance`

Selected at runtime via `<html data-theme="...">`. The system is purely CSS-variable-driven: components render against semantic token names, and the active `[data-theme="..."]` block in `client/src/themes/` rebinds those tokens to the theme's surface, foreground, accent, typography, geometry, motion, and sound-pack values.

This document is the playbook for working in the design system: token taxonomy, decorative-layer wrappers, per-theme sound + canvas packs, migration recipe, anti-patterns, and where each piece lives.

## Architecture at a glance

```
client/src/themes/
├── base.css         neutral defaults (space, radii, motion easings, sound pack hint)
├── light.css        :root + :root[data-theme="light"]    — new contract values
├── dark.css         .dark + :root[data-theme="dark"]     — new contract values
├── renaissance.css  :root[data-theme="renaissance"]      — full palette + shadcn bridge
├── greek.css        :root[data-theme="greek"]            — lapis + oxblood on marble
├── edo.css          :root[data-theme="edo"]              — washi + sumi + vermillion
├── steampunk.css    :root[data-theme="steampunk"]        — brass + copper on leather
├── atomic.css       :root[data-theme="atomic"]           — atomic orange on cardstock
├── cyber.css        :root[data-theme="cyber"]            — neon over void
├── wasteland.css    :root[data-theme="wasteland"]        — ochre + radioactive scrap
├── rot.css          :root[data-theme="rot"]              — moss bloom in crypt
├── plague.css       :root[data-theme="plague"]           — woodcut quarantine
└── surveillance.css :root[data-theme="surveillance"]     — REC red + phosphor

client/src/index.css
├── @layer base :root  — shadcn HSL-triplet tokens (light defaults) + dracula raw
├── @layer base .dark  — shadcn HSL-triplet tokens (dark overrides)
└── @theme inline { … } — Tailwind v4 utility bindings for both contracts
                          (--color-bg-app, --color-fg-default, --color-border-default,
                           --font-display, --font-body, etc.)

client/src/contexts/ThemeContext.tsx
├── ThemeName = 'light' | 'dark' | 'renaissance' | 'greek' | 'edo' | 'steampunk'
│             | 'atomic' | 'cyber' | 'wasteland' | 'rot' | 'plague' | 'surveillance'
├── DARK_FAMILY = {dark, cyber, wasteland, rot, surveillance, steampunk}
├── persists to localStorage['machinaos-theme']
├── migrates legacy 'darkMode' boolean on first load
└── sets <html data-theme="..."> + .dark class (only for DARK_FAMILY themes)

client/src/hooks/useAppTheme.ts        — 10-way Colors overlay (canvas + maps)
client/src/lib/sound.ts                — WebAudio engine, 10 packs × 9 events
client/src/hooks/useSound.ts           — useSoundSync() + useSound()
client/src/components/ui/ThemeSwitcher.tsx — grouped DropdownMenu (System/Utopian/Dystopian)
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
| `border-border-default` | `--border-default` | Standard borders + dividers |
| `border-border-strong` | `--border-strong` | Section separators, focused panels |
| `border-border-focus` | `--border-focus` | Focus rings (often shadowed) |

(Tailwind v4 names colour utilities `border-{token}` where `{token}` is the suffix after `--color-`; since we expose `--color-border-default`, the utility is `border-border-default`.)

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

Heading surfaces under all 10 themes use the same triplet. Keep them grouped in the same className for clarity:

```tsx
<span className="font-display tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
  {heading}
</span>
```

Result by theme (display font / tracking / case):
- **Light + Dark** — Geist Variable / 0 / `none`
- **Renaissance** — Cinzel / 0.10em / `uppercase`
- **Greek** — Cinzel / 0.18em / `uppercase`
- **Edo** — Shippori Mincho / 0.06em / `none`
- **Steampunk** — IM Fell English SC / 0.10em / `uppercase`
- **Atomic** — Bevan / 0.06em / `uppercase`
- **Cyber** — Major Mono Display / 0.18em / `uppercase`
- **Wasteland** — Special Elite / 0.10em / `uppercase`
- **Rot** — Pirata One / 0.04em / `none`
- **Plague** — UnifrakturCook / 0.06em / `uppercase`
- **Surveillance** — Anonymous Pro / 0.10em / `uppercase`

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
3. Add the name to `ThemeName` + `AVAILABLE_THEMES` in [client/src/contexts/ThemeContext.tsx](../client/src/contexts/ThemeContext.tsx). Add to `DARK_FAMILY` if the theme's background is dark.
4. Add a `THEME_META` entry in [client/src/components/ui/ThemeSwitcher.tsx](../client/src/components/ui/ThemeSwitcher.tsx) (label + blurb) and append to the matching `THEME_GROUPS` row (System / Utopian / Dystopian).
5. Add the theme name to `THEME_LABEL` in [client/src/components/ui/StatusBar.tsx](../client/src/components/ui/StatusBar.tsx) and [client/src/components/ui/CommandPaletteHost.tsx](../client/src/components/ui/CommandPaletteHost.tsx).
6. **(Optional, canvas)** Add a `THEME_OVERRIDES` entry in [client/src/hooks/useAppTheme.ts](../client/src/hooks/useAppTheme.ts) so canvas selection rings, action buttons, and edge strokes pick up the theme's accents. Whichever keys you omit fall through to `lightColors` / `darkColors` (chosen by `DARK_BASE_THEMES`).
7. **(Optional, fonts)** If the theme uses Google Fonts not already loaded, append the family to the deferred-load `<link>` in [client/index.html](../client/index.html).
8. **(Optional, sound)** Pick one of the 10 existing `SoundPackName` values in `--sound-pack` (or add a new pack to [client/src/lib/sound.ts](../client/src/lib/sound.ts) and the `VALID_PACKS` set in [client/src/hooks/useSound.ts](../client/src/hooks/useSound.ts)).

**No component code changes.** That is the contract.

## Anti-patterns (forbidden)

1. **Theme-conditional logic in components.** No `if (theme === 'cyber')` branches. Visual differences live in the per-theme CSS file.
2. **Whole-store Zustand destructure.** Always `useAppStore((s) => s.x)`, never `{ x } = useAppStore()`.
3. **Opacity arithmetic on new-contract tokens.** `bg-bg-app/10` does not work — they're full-colour. Add a new variant under `client/src/themes/<name>.css` if you need a different alpha, or fall back to the shadcn alias (`bg-background/10`).
4. **Hardcoded colours.** No `bg-white`, `bg-black`, `text-gray-500`, `style={{ backgroundColor: '#fff' }}`. Use tokens.
5. **`useAppTheme()` in new files.** Allowed only for surfaces that need JS-side hex values Tailwind can't express: canvas node components (`AIAgentNode`, `SquareNode`, `TriggerNode`, `StartNode`, `ToolkitNode`, `TeamMonitorNode`, `GenericNode`), `EdgeConditionEditor`, and the Google Maps SDK consumers (`MapSelector`, `GoogleMapsPicker`, `MapsPreviewPanel`). Every other surface uses Tailwind + tokens. New themes contribute to `useAppTheme` via a `THEME_OVERRIDES` entry, never by importing `lightColors` / `darkColors` directly.
6. **Hand-rolled modal backdrops.** Use `<Modal>` from `client/src/components/ui/Modal.tsx`.
7. **Hand-rolled colored buttons.** Use `<ActionButton intent="...">`.
8. **Static fallback theme palettes in TS.** Themes live in CSS (`client/src/themes/<name>.css`), not TS exports. `client/src/styles/theme.ts` ships only the two `lightColors` / `darkColors` base packs that `useAppTheme` overlays on top of — it is not a place to add a third / fourth / Nth palette.

## What's done vs deferred

### Done
- **Token contract**: all 10 themes ported in [client/src/themes/](../client/src/themes) (5 utopian + 5 dystopian). 13 CSS files, 3,749 LOC total.
- **ThemeProvider** ([client/src/contexts/ThemeContext.tsx](../client/src/contexts/ThemeContext.tsx)) — 10-way `ThemeName`, `DARK_FAMILY` ⊃ `{dark, cyber, wasteland, rot, surveillance, steampunk}`, legacy `darkMode` localStorage migration on first load
- **ThemeSwitcher** — DropdownMenu grouped into System / Utopian / Dystopian sections, all 10 themes
- **Tailwind v4 `@theme inline`** exposing new-contract utilities (`bg-bg-app`, `text-fg-default`, `border-border-default`, `font-display`, `font-body`, `rounded-pill`)
- **Decorative wrappers** — Dashboard root carries `app-frame`, the React Flow host carries `canvas-host` (aliased as `canvas` so handoff selectors resolve), every Modal carries `modal-frame`. Per-theme CSS targets these classes for outer ornaments (gilded corners, scanlines, riveted borders, REC dot, etc.)
- **Per-component structural classNames** (W15) — every chrome / interactive / canvas component carries the handoff selector co-class so per-theme decorative rules resolve without component-level branching. See [Per-component decorations](#per-component-decorations) for the full list.
- **Per-theme decorative CSS** (W16) — every per-component rule from `design_handoff_machinaos_themes/themes/*.css` is now ported into `client/src/themes/<theme>.css`. Each theme owns its panel textures, canvas decorations, node pseudo-element overlays, and keyframes. CSS bundle: 145 KB → 244 KB (+99 KB).
- **Selection state** (W17, shipped as part of W15) — every canvas node component carries the `selected` co-class when React Flow's `selected` prop is true, activating per-theme selection effects (cyber-blink on Cyber sq-nodes, hanko seal on Edo sq-nodes, etc.).
- **Sound event wiring** (W18) — 9 events (`click`, `hover`, `type`, `success`, `error`, `run`, `save`, `modalOpen`, `modalClose`) fire automatically across the React tree. See [Sound system → Event wiring](#event-wiring-w18).
- **Reduced-motion accessibility** (W19) — `@media (prefers-reduced-motion: reduce)` block in [client/src/themes/base.css](../client/src/themes/base.css) disables every keyframe across all themes. See [Reduced motion](#reduced-motion).
- **Sound throttling** (W19) — `type` and `hover` events throttled to a 30 ms last-fire window inside the engine to prevent OscillatorNode flooding. See [Throttling](#throttling).
- **Migrated chrome**: TopToolbar, WorkflowSidebar, ComponentPalette + ComponentItem + CollapsibleSection, ConsolePanel chrome, SettingsPanel, Modal, ParameterPanel modal title, AIResultModal title, OutputDisplayPanel title, InputSection title
- **New shell components**: StatusBar (fixed-bottom system line with WS connection / workflow / theme / clock), CommandPalette (`⌘K`), CommandPaletteHost (canonical command list with Workflow / Run / Open / View / Theme groups)
- **Google Fonts** — deferred-load `<link>` covers all 10 themes' typefaces (Cinzel, Cormorant Garamond, IM Fell English / SC, JetBrains Mono, Major Mono Display, VT323, Shippori Mincho, Sawarabi Mincho, Special Elite, Bevan, Lato, Pirata One, EB Garamond, UnifrakturCook, Anonymous Pro, IBM Plex Mono, Courier Prime, Space Mono)
- **Sound contract** — [client/src/lib/sound.ts](../client/src/lib/sound.ts) ports the upstream WebAudio engine with all 10 packs (`parchment`, `marble`, `ink`, `clockwork`, `vibraphone`, `terminal`, `scrap`, `crypt`, `bell`, `telex`). [client/src/hooks/useSound.ts](../client/src/hooks/useSound.ts) reads `--sound-pack` from `:root` on theme change and mirrors the `soundEnabled` Zustand slice into `Sounds.setEnabled()`. Settings panel ships the toggle (Audio section). Persists to `localStorage['machinaos-sound']`; default off (opt-in).
- **Canvas overlay packs** — [client/src/hooks/useAppTheme.ts](../client/src/hooks/useAppTheme.ts) extended from 2-way (`{light, dark}`) to 10-way: a `THEME_OVERRIDES` map applies a small overlay (primary, focus, focusRing, action colours, edge palette) on top of `lightColors` / `darkColors`. Existing call sites continue to read `theme.colors.X` and `theme.isDarkMode` unchanged; canvas selection rings, action buttons, and edge strokes pick up the active theme's accents under any of the 10 themes.

### Deferred (future PRs)
- **Per-theme icon sets** — the upstream [`app/icons.js`](../design_handoff_machinaos_themes/app/icons.js) ships 28-key glyph sets per theme (heraldic shields under Renaissance, wireframe + glow under Cyber, woodcut hatching under Plague, etc.). The current shell uses `lucide-react` icons under all themes via `currentColor`, which retints correctly via the bridge but doesn't carry per-theme glyph language. Migration recipe: build `client/src/icons/` with an `<Icon name>` component that reads the active theme via `useTheme()` and resolves to one of 10 SVG-string sets. Lucide stays as a fallback for missing keys.
- **Drop-cap `::first-letter`** — Renaissance `.v-display.drop-cap` selector ports verbatim but needs an HTML wrapper component before the rule resolves. Add a `<DisplayHeading dropCap>` primitive when next touching the chrome.
- **Edge SVG filters** — Renaissance `.edge path { filter: url(#ink-blot) }` needs an inline `<svg><defs>` injected into the React tree.
- **Cursor SVG reticles** — Cyber crosshair, Renaissance quill, Surveillance reticle: `cursor: url(...)` rules in per-theme CSS, low ROI.
- **Parameter panel internals** — `MiddleSection`, `MasterSkillEditor`, `ParameterRenderer` consume shadcn tokens through the bridge so they retint correctly, but their interior section headers don't yet carry the display-typography triplet. Apply the recipe (`font-display tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]`) when next touched.
- **Credentials modal sub-panels** — `OAuthPanel`, `EmailPanel`, `ApiKeyPanel`, `QrPairingPanel` headers same pattern.

## Per-component decorations

Wave 15 added structural classNames across the React tree so the handoff CSS selectors resolve without component-level branching. Wave 16 ported the matching decorative rules from `design_handoff_machinaos_themes/themes/*.css` into `client/src/themes/<theme>.css`.

### Class registry

| Surface | className(s) | Component |
|---|---|---|
| Top toolbar | `.toolbar` | TopToolbar |
| Sidebar | `.sidebar` | WorkflowSidebar |
| Component palette | `.palette` | ComponentPalette |
| Console / chat panel | `.chat`, `.chat-msg`, `.chat-msg-user`, `.chat-msg-bot` | ConsolePanel |
| Status bar | `.statusbar`, `.pip` (connection dot) | StatusBar |
| Modal | `.modal`, `.modal-frame`, `.modal-head` | Modal |
| Collapsible section | `.cat`, `.cat-head`, `.cat-body` | CollapsibleSection |
| Workflow card | `.wf-card` | WorkflowCard |
| Palette tile | `.comp` | ComponentItem |
| Interactive list rows | `.row` | WorkflowSidebar / ComponentItem |
| shadcn Button | `.btn` (CVA base) | button.tsx |
| ActionButton | `.action-btn .btn` (CVA base) | action-button.tsx |
| Form field | `.input` | input.tsx, textarea.tsx |
| Dropdown menu | `.menu-pop` (Content), `.menu-pop-item` (Item) | dropdown-menu.tsx |
| Canvas host | `.canvas-host`, `.canvas` (alias) | Dashboard |
| Generic canvas node | `.node`, `.selected` | AIAgentNode, GenericNode, TriggerNode, StartNode, ToolkitNode, TeamMonitorNode |
| Agent node variant | `.node-agent` | AIAgentNode |
| Trigger node variant | `.node-trigger` | TriggerNode |
| Square canvas node | `.sq-node`, `.sq-node-box`, `.sq-node-pip`, `.sq-node-gear`, `.sq-node-handle.in`, `.sq-node-handle.out`, `.selected` | SquareNode |

The `.selected` co-class is bound to React Flow's `selected` prop on every canvas node component, activating per-theme selection effects (Cyber `cyber-blink`, Edo hanko seal, Renaissance wax-seal stamp, etc.).

### Decorative content per theme

| Theme | Panel textures | Canvas decoration | Node pseudo overlay | Keyframes |
|---|---|---|---|---|
| renaissance | parchment + vellum noise | fleur-de-lis vignette + marginalia | wax seal (`.node::after`) | `ren-flicker` |
| greek | marble veins | meander pattern | stelae shape | — |
| edo | washi texture | ink-wash mountain (radial gradient) | hanko seal (`.sq-node.selected::before`) | — |
| steampunk | leather grain | brass bolt grid | brass rivets (`.sq-node-box::before/::after`) | — |
| atomic | flat solid | starburst + grid lines | boomerang corners | — |
| cyber | neon glow grain | perspective grid + scanlines + status text | corner LED blink | `cyber-flicker`, `cyber-roll`, `cyber-blink`, `cyber-glitch` |
| wasteland | dust + grain | cracked earth (SVG) | scrap noise (`.sq-node-box::before`) | — |
| rot | gradient | flagstone + candlelight pools | bone gradient | — |
| plague | parchment | red X pattern (SVG) | nailed nail (`.sq-node-box::after`) | — |
| surveillance | solid | CCTV crosshair + REC label + phosphor scanlines | REC LED blink (`.sq-node-box::before`) | `surv-blink` |

`var(--accent)` etc. handoff reads were wrapped to `hsl(var(--accent))` for shadcn-bridged tokens; theme-specific spot colours (`--gold`, `--crimson`, `--neon-magenta`, `--rec-red`) port verbatim as hex.

## Sound system

Each theme declares `--sound-pack: "<pack>"` in its `:root[data-theme="..."]` block. Pack names map to event tables in [client/src/lib/sound.ts](../client/src/lib/sound.ts):

| Theme | Pack | Voice |
|---|---|---|
| renaissance | `parchment` | Soft sine plucks + low-pass |
| greek | `marble` | Warm chisel taps |
| edo | `ink` | Soft brush + temple bell |
| steampunk | `clockwork` | Mechanical ratchet + brass |
| atomic | `vibraphone` | Mid-century jazz mallet |
| cyber | `terminal` | Square-wave terminal beeps |
| wasteland | `scrap` | Distorted clanging metal |
| rot | `crypt` | Dripping water + low organ |
| plague | `bell` | Struck church bell + stone echo |
| surveillance | `telex` | Typewriter clack + Geiger tick |
| light / dark | (system fallback `parchment`/`terminal` if requested) | — |

### Event wiring (W18)

9 events fire automatically across the React tree — call sites do not need to wrap:

| Event | Surface | File |
|---|---|---|
| `click` | shadcn Button onClick | [button.tsx](../client/src/components/ui/button.tsx) |
| `click` | ActionButton (CVA primitive) | [action-button.tsx](../client/src/components/ui/action-button.tsx) |
| `click` | DropdownMenuItem onSelect | [dropdown-menu.tsx](../client/src/components/ui/dropdown-menu.tsx) |
| `click` | SelectItem onClick | [select.tsx](../client/src/components/ui/select.tsx) |
| `hover` | Global capture-phase `mouseover` delegate matching `.btn, .action-btn, .row, .menu-pop-item, .wf-card, .comp, .cmdk-item, [data-sound-hover]` | [hooks/useSound.ts](../client/src/hooks/useSound.ts) |
| `type` | Input onChange | [input.tsx](../client/src/components/ui/input.tsx) |
| `type` | Textarea onChange | [textarea.tsx](../client/src/components/ui/textarea.tsx) |
| `success` | Monkey-patched `toast.success` (sonner) — idempotent under React Strict Mode | [hooks/useSound.ts](../client/src/hooks/useSound.ts) (`patchToast()`) |
| `error` | Monkey-patched `toast.error` (sonner) | [hooks/useSound.ts](../client/src/hooks/useSound.ts) |
| `run` | `withSound('run', handleDeploy)` / `withSound('run', handleRun)` at TopToolbar `onDeploy` / `onRun` + CommandPaletteHost handlers | [Dashboard.tsx](../client/src/Dashboard.tsx) |
| `save` | `withSound('save', handleSave)` at TopToolbar `onSave` + CommandPaletteHost + Ctrl/Cmd+S keyboard shortcut | [Dashboard.tsx](../client/src/Dashboard.tsx) |
| `modalOpen` / `modalClose` | useEffect on `isOpen` edges in Modal | [Modal.tsx](../client/src/components/ui/Modal.tsx) |

The hover delegate uses `mouseover` on capture phase + a `relatedTarget` filter so a hover only fires once per crossing-into-element (`mouseenter` doesn't bubble). `touchstart` is registered alongside on the same selector list for hybrid devices.

`patchToast()` wraps sonner's `toast.success` / `toast.error` once at module load; the guard flag prevents Strict-Mode double-wrap. `withSound(event, handler)` is the convenience HOC exported from [useSound.ts](../client/src/hooks/useSound.ts) for `run` / `save` semantics — fires the sound, then defers to the original handler.

Wiring summary:
1. Mount `useSoundSync()` once at the Dashboard root — done in [Dashboard.tsx](../client/src/Dashboard.tsx).
2. Settings panel ships an Audio section with a Switch bound to `useAppStore.soundEnabled`.

Adding a new sound event: extend `SoundEvent` in `lib/sound.ts`, add an entry per pack, fire `play('<event>')` from the handler.

## Reduced motion

[client/src/themes/base.css](../client/src/themes/base.css) ships a `@media (prefers-reduced-motion: reduce)` block that disables every keyframe across all themes when the user's OS-level "Reduce Motion" preference is on. Disabled animations:

- `cyber-flicker`, `cyber-roll`, `cyber-blink`, `cyber-glitch` (Cyber)
- `surv-blink` (Surveillance)
- `ren-flicker` (Renaissance)
- generic `.animate-pulse` (used by node execution pip)

Plus any `transition` on canvas-host pseudo-elements that synthesise motion. Sounds are not motion — they remain enabled regardless. The user-facing audio toggle lives in Settings → Audio → Sound effects.

Verify in Chrome DevTools → Rendering → "Emulate CSS media feature prefers-reduced-motion: reduce", or in OS settings (macOS: System Settings → Accessibility → Display → Reduce motion; Windows: Settings → Accessibility → Visual effects → Animation effects).

## Throttling

Two events flood under rapid input and are throttled inside the engine to prevent OscillatorNode flooding:

- `type` — 30 ms last-fire window (protects against keystroke bursts)
- `hover` — 30 ms last-fire window (protects against `mouseover` firing dozens of times during a fast cursor sweep)

Implementation lives in [client/src/lib/sound.ts](../client/src/lib/sound.ts): `Sounds.play()` consults a `THROTTLE_MS` map per event name and a `lastFireMs` map keyed by event before each `OscillatorNode` schedule. Other events (`click`, `success`, `error`, `run`, `save`, `modalOpen`, `modalClose`) fire on every call.

## Canvas overlay packs (`useAppTheme`)

The hook returns a `theme` object with the same shape every call site already destructures — `theme.colors.X` and `theme.isDarkMode` continue to work. Under non-light/dark themes a small overlay merges into the base pack:

```typescript
const THEME_OVERRIDES: Partial<Record<ThemeName, ColorOverride>> = {
  cyber: {
    primary: '#f51eb6',          // neon magenta
    focus: '#1dd9e5',
    actionRun: '#26d97a',
    edgeDefault: '#f51eb6',
    edgeSelected: '#1dd9e5',
    /* ... */
  },
  /* ... 9 more themes */
};
```

The overlay only re-binds the keys most visible on the canvas (primary, focus, action buttons, edge palette). Everything else (background, text, border) stays on the chosen base pack (`darkColors` for `DARK_BASE_THEMES`, `lightColors` for the rest). Adding a new theme means adding one `THEME_OVERRIDES` entry; missing entries are a no-op (theme falls back to pure light/dark).

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
| [client/src/themes/greek.css](../client/src/themes/greek.css) | Greek theme + bridge |
| [client/src/themes/edo.css](../client/src/themes/edo.css) | Edo theme + bridge |
| [client/src/themes/steampunk.css](../client/src/themes/steampunk.css) | Steampunk theme + bridge |
| [client/src/themes/atomic.css](../client/src/themes/atomic.css) | Atomic Modern theme + bridge |
| [client/src/themes/cyber.css](../client/src/themes/cyber.css) | Cyber-Tyranny theme + bridge |
| [client/src/themes/wasteland.css](../client/src/themes/wasteland.css) | Wasteland theme + bridge |
| [client/src/themes/rot.css](../client/src/themes/rot.css) | Necromantic Rot theme + bridge |
| [client/src/themes/plague.css](../client/src/themes/plague.css) | Plague City theme + bridge |
| [client/src/themes/surveillance.css](../client/src/themes/surveillance.css) | Surveillance theme + bridge |
| [client/src/index.css](../client/src/index.css) | shadcn HSL triplets + `@theme inline` Tailwind utilities |
| [client/src/contexts/ThemeContext.tsx](../client/src/contexts/ThemeContext.tsx) | `<ThemeProvider>` + `useTheme()` (10-way) |
| [client/src/hooks/useAppTheme.ts](../client/src/hooks/useAppTheme.ts) | Canvas overlay packs (10 themes × Colors override) |
| [client/src/lib/sound.ts](../client/src/lib/sound.ts) | WebAudio engine — 10 sound packs, 9 events |
| [client/src/hooks/useSound.ts](../client/src/hooks/useSound.ts) | `useSoundSync()` + `useSound()` React glue |
| [client/src/components/ui/ThemeSwitcher.tsx](../client/src/components/ui/ThemeSwitcher.tsx) | Grouped dropdown picker (System / Utopian / Dystopian) |
| [client/src/components/ui/StatusBar.tsx](../client/src/components/ui/StatusBar.tsx) | Fixed-bottom system line |
| [client/src/components/ui/CommandPalette.tsx](../client/src/components/ui/CommandPalette.tsx) | ⌘K palette primitive |
| [client/src/components/ui/CommandPaletteHost.tsx](../client/src/components/ui/CommandPaletteHost.tsx) | Registered command set |
| [client/src/components/ui/action-button.tsx](../client/src/components/ui/action-button.tsx) | CVA action role primitive (fires `play('click')`) |
| [client/src/components/ui/Modal.tsx](../client/src/components/ui/Modal.tsx) | Shared modal layout (fires `play('modalOpen'/'modalClose')`) |
| [client/index.html](../client/index.html) | Google Fonts loading (all 10 themes' typefaces) |
| [design_handoff_machinaos_themes/](../design_handoff_machinaos_themes/) | Reference HTML mocks + token spec |
| [design_handoff_machinaos_themes/MIGRATION_PLAYBOOK.md](../design_handoff_machinaos_themes/MIGRATION_PLAYBOOK.md) | Upstream recipe for the 4 deferred items (canvas, headers, decorative wrapper, sound) — all now landed |
