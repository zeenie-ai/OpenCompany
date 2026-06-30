# Theme System — design-handoff token contract + 10 themes

The MachinaOs frontend supports **ten visual themes**, organised as a utopian / dystopian taxonomy from the design handoff:

**Utopian:** `light` · `dark` · `renaissance` · `greek` · `edo` · `steampunk` · `atomic`
**Dystopian:** `cyber` · `wasteland` · `rot` · `plague` · `surveillance`

Selected at runtime via `<html data-theme="...">`. The system is purely CSS-variable-driven: components render against semantic token names, and the active `[data-theme="..."]` block in `client/src/themes/` rebinds those tokens to the theme's surface, foreground, accent, typography, geometry, motion, and sound-pack values.

This document is the playbook for working in the design system: token taxonomy, decorative-layer wrappers, per-theme sound + canvas packs, migration recipe, anti-patterns, and where each piece lives.

> **Canonical reference + token format.** The vendored [Design System Bundle](./design-system/IMPLEMENTATION.md) (`docs-internal/design-system/`) is the canonical spec; its `tokens/*.css` carry the authoritative values — copy them verbatim, never re-derive by eye. **Token colors are `hex` + `color-mix()`** (e.g. `--primary: #2563eb`, `--action-run-soft: color-mix(in srgb, var(--dracula-green) 15%, transparent)`), NOT HSL-triplets. The Tailwind v4 `@theme inline` bridge maps `--color-X: var(--X)` directly (no `hsl()` wrapper); the `/opacity` modifier still works because v4 compiles it to `color-mix`. Action intents additionally expose an `--action-X-ink` readable-text variant (light: a darkened accent; dark: the raw accent) consumed by `ActionButton` via `text-action-X-ink`.

## Architecture at a glance

```
client/src/themes/        (colour VALUES are hex + color-mix(); never HSL triplets)
├── base.css         shared scalars: space / radii / chrome / shadow / type scale, motion
│                    easings, the --tint-* alpha scale (single home for color-mix %), node visuals
├── light.css        :root + :root[data-theme="light"]  — light colour hex: shadcn + dracula
│                    + node + action (+ -ink); defined on bare :root so they apply globally
├── dark.css         .dark + :root[data-theme="dark"]   — dark colour-hex overrides (+ -ink, shadows)
├── renaissance.css  :root[data-theme="renaissance"]    — full palette (hex) + per-theme decorations
├── greek / edo / steampunk / atomic / cyber / wasteland / rot / plague / surveillance .css
└── animations.css   per-theme --pulse-keyframe/-duration/-timing + executing/trigger keyframes
                     + .machina-* helpers (.machina-pulse / -trigger-armed / -bolt / -crt / -scanline)

client/src/index.css       PLUMBING ONLY — holds NO literal colour values:
├── @layer base            global resets only (*, html/body, #root) — token blocks moved to themes/
└── @theme inline { … }    Tailwind v4 bridge mapping --color-X: var(--X) (NO hsl() wrapper);
                           /opacity (bg-primary/50) composes via color-mix for any colour format

client/tailwind.config.js  second colour bridge — theme.extend.colors map var(--X) (no hsl() wrapper)

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

There are six tiers, ordered from most semantic to most concrete. Always pick the most semantic that fits the call site.

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

**Note**: values are hex + `color-mix()`. Tailwind v4 composes `/opacity` via `color-mix` for any colour format, so `bg-bg-app/50` *does* work — but prefer a named token (`--bg-overlay`, a `-soft`/`-tint` variant) over ad-hoc surface alpha.

### 2. shadcn semantic tokens (preserved for primitives)

Standard shadcn keys (`background`, `foreground`, `card`, `popover`, `primary`, `destructive`, `success`, `warning`, `info`, `border`, `input`, `ring`, etc.). Each theme defines them as **hex** matching the new-contract colour, so shadcn primitives + generic chrome resolve against the active theme. `/opacity` works on these too (`bg-primary/10`, `text-foreground/80`) via v4 color-mix.

### 3. Action role tokens

Six semantic intents: `run | stop | save | config | secret | tools`. Each exposes `base / -soft / -hover / -border / -ink` — the colour-hex bases live in light.css/dark.css and the `-soft/-hover/-border` tints are `color-mix` over the base referencing the shared `var(--tint-action-*)` scale. `-ink` is the readable label colour (light: darkened accent; dark: raw accent). Read via `<ActionButton intent="run">` or directly: `bg-action-run-soft text-action-run-ink border-action-run-border`. Themes redefine these (or just override the `--tint-action-*` scalars) in their own block.

### 4. Node-type role tokens

Six tokens for canvas identity: `agent / model / skill / tool / trigger / workflow`. Each exposes `base / -soft / -border`. Used on palette icons, parameter-panel section headers, draggable variable cards. **Never use `/N` opacity arithmetic** at call sites — themes own the alpha.

### 5. Dracula raw accents (palette, not consumed directly)

`--dracula-green/purple/pink/cyan/red/orange/yellow/selection/current-line/comment`. Same value across light + dark. Used as the underlying palette that `--action-X` and `--node-X` reference; do not use directly in components.

### 6. Code & syntax tokens

`--code-*` — the per-theme syntax palette for the code editor, console/output JSON viewers, and chat code blocks. Each theme defines its own `--code-*` block in its own CSS file (`client/src/themes/<theme>.css`): editor chrome (`--code-bg`, `--code-gutter-bg`, `--code-gutter-fg`, `--code-caret`, `--code-border`, `--code-line-active`, `--code-selection`) + syntax roles (`--code-text`, `--code-comment`, `--code-keyword`, `--code-string`, `--code-number`, `--code-boolean`, `--code-function`, `--code-property`, `--code-operator`, `--code-punctuation`, `--code-tag`). Light / Dark / Cyber are copied verbatim from the design system's `tokens/code.css`; the other skins derive syntax from their own role hues — **keyword→trigger, string→success, number→agent, function→model, tag→tool, comment→faint, punctuation→muted** — on an adaptive dark/light code surface (`color-mix(#000 45%, --bg-panel)` dark, `color-mix(#000 5%, --surface-card)` light).

Consumed as `var(--code-*)` in [index.css](../client/src/index.css) (`.code-editor-container`, `.console-json-output`, the `.chat-markdown` dark overrides) and exposed as Tailwind utilities (`text-code-tag`, `bg-code-bg`, …) via the `@theme inline` bridge; the `OutputPanel` `@uiw/react-json-view` viewer reads the same vars. This **replaced the old global dracula-hardcoded `--prism-*` block and the dead `getPrismTokenCSS()` helper** — code/JSON now paints in each theme's palette instead of one dracula scheme everywhere. (`prismjs` is still the tokenizer; only the colours moved to `--code-*`.)

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

     /* Shadcn bridge — same colours, HEX (not HSL triplets) */
     --background: #rrggbb; --foreground: #rrggbb;
     --card: #rrggbb; --popover: #rrggbb; --primary: #rrggbb;
     --secondary: #rrggbb; --muted: #rrggbb; --accent: #rrggbb;
     --destructive: #rrggbb; --success: #rrggbb; --warning: #rrggbb; --info: #rrggbb;
     --border: #rrggbb; --input: #rrggbb; --ring: #rrggbb;

     /* Action role tokens — base hex + color-mix tints over the shared
        var(--tint-action-*) scale (override --tint-action-* here if the
        theme wants stronger/weaker tints). -ink = readable label:
        a darkened accent on light themes, var(--action-X) on dark. */
     --action-run: #rrggbb;
     --action-run-soft:   color-mix(in srgb, var(--action-run) var(--tint-action-soft), transparent);
     --action-run-hover:  color-mix(in srgb, var(--action-run) var(--tint-action-hover), transparent);
     --action-run-border: color-mix(in srgb, var(--action-run) var(--tint-action-border), transparent);
     --action-run-ink:    #rrggbb;   /* or var(--action-run) on dark themes */
     /* …repeat for stop / save / config / secret / tools */
   }
   ```
2. Import it in [client/src/main.tsx](../client/src/main.tsx) **before** `index.css`.
3. Add the name to `ThemeName` + `AVAILABLE_THEMES` in [client/src/contexts/ThemeContext.tsx](../client/src/contexts/ThemeContext.tsx). Add to `DARK_FAMILY` if the theme's background is dark.
4. Add a `THEME_META` entry in [client/src/components/ui/ThemeSwitcher.tsx](../client/src/components/ui/ThemeSwitcher.tsx) (label + blurb) and append to the matching `THEME_GROUPS` row (System / Utopian / Dystopian).
5. Add the theme name to `THEME_LABEL` in [client/src/components/ui/StatusBar.tsx](../client/src/components/ui/StatusBar.tsx) and [client/src/components/ui/CommandPaletteHost.tsx](../client/src/components/ui/CommandPaletteHost.tsx).
6. **(Optional, canvas)** Add a `THEME_OVERRIDES` entry in [client/src/hooks/useAppTheme.ts](../client/src/hooks/useAppTheme.ts) so canvas selection rings, action buttons, and edge strokes pick up the theme's accents. Whichever keys you omit fall through to `lightColors` / `darkColors` (chosen by `DARK_BASE_THEMES`).
7. **(Optional, fonts)** If the theme uses Google Fonts not already loaded, append the family to the deferred-load `<link>` in [client/index.html](../client/index.html).
8. **(Optional, sound)** Pick one of the 10 existing `SoundPackName` values in `--sound-pack` (or add a new pack to [client/src/lib/sound.ts](../client/src/lib/sound.ts) and the `VALID_PACKS` set in [client/src/hooks/useSound.ts](../client/src/hooks/useSound.ts)).
9. **(Optional, motion)** The theme inherits the default `node-pulse` executing glow and the `.machina-trigger-armed` trigger heartbeat automatically. For a bespoke executing pulse, add a `[data-theme="X"]` block in [client/src/themes/animations.css](../client/src/themes/animations.css) overriding `--pulse-keyframe` / `--pulse-duration` / `--pulse-timing` and define that keyframe there. Set the glow accent via `--node-pulse-color` in the theme's own block.

**No component code changes.** That is the contract.

## Anti-patterns (forbidden)

1. **Theme-conditional logic in components.** No `if (theme === 'cyber')` branches. Visual differences live in the per-theme CSS file.
2. **Whole-store Zustand destructure.** Always `useAppStore((s) => s.x)`, never `{ x } = useAppStore()`.
3. **Ad-hoc opacity arithmetic on role/surface tokens.** Don't inline alpha (`bg-bg-app/10`, `bg-node-agent/30`, `bg-action-run/25`). Tailwind v4 *can* now color-mix `/opacity` on hex tokens, but the discipline is to reference a NAMED token: add/define a `-soft` / `-hover` / `-border` variant, or a `--tint-*` step in base.css, and use it by name.
4. **Hardcoded colours.** No `bg-white`, `bg-black`, `text-gray-500`, `style={{ backgroundColor: '#fff' }}`. Use tokens.
5. **`useAppTheme()` in new files.** Allowed only for surfaces that need JS-side hex values Tailwind can't express: canvas node components (`AIAgentNode`, `SquareNode`, `TriggerNode`, `StartNode`, `ToolkitNode`, `TeamMonitorNode`, `GenericNode`), `EdgeConditionEditor`, and the Google Maps SDK consumers (`MapSelector`, `GoogleMapsPicker`, `MapsPreviewPanel`). Every other surface uses Tailwind + tokens. New themes contribute to `useAppTheme` via a `THEME_OVERRIDES` entry, never by importing `lightColors` / `darkColors` directly.
6. **Hand-rolled modal backdrops.** Use `<Modal>` from `client/src/components/ui/Modal.tsx`.
7. **Hand-rolled colored buttons.** Use `<ActionButton intent="...">`.
8. **Static fallback theme palettes in TS.** Themes live in CSS (`client/src/themes/<name>.css`), not TS exports. `client/src/styles/theme.ts` ships only the two `lightColors` / `darkColors` base packs that `useAppTheme` overlays on top of — it is not a place to add a third / fourth / Nth palette.

## What's done vs deferred

### Done
- **`--node-pulse-color` contract** — executing-node glow color is independent of `--node-color` (the plugin accent). Each theme overrides `--node-pulse-color` to its highest-contrast accent (Cyber neon cyan, Surveillance REC red, Renaissance ultramarine `--lapis-bright`, Atomic turquoise, etc.) so the glow stays visible against any canvas background. Defined in [base.css](../client/src/themes/base.css) at `:root`, overridden in each per-theme file. The executing keyframes (`node-pulse` + per-theme `cyber-pulse-exec` / `surv-pulse-exec` / `ren-pulse-exec`) and the per-theme `--pulse-keyframe` / `--pulse-duration` / `--pulse-timing` tokens live in [animations.css](../client/src/themes/animations.css); base.css's `.sq-node[data-executing] .sq-node-box` / `.react-flow__node.executing .{sq-node-box,node}` rule reads `var(--pulse-keyframe, node-pulse)`, so a theme picks its pulse by overriding ONE token (Cyber → `cyber-pulse-exec`, Surveillance → `surv-pulse-exec`, Renaissance → `ren-pulse-exec`) — no high-specificity per-theme animation rule. `node-pulse` is a triple-layer expanding box-shadow (12px ring + 32px mid halo + 56px outer halo) consuming `var(--node-pulse-color)` + the shared `--tint-pulse-*` alphas. Trigger nodes use a separate continuous `.machina-trigger-armed` "listening" heartbeat (`trigger-listening` keyframe) while waiting, plus `.machina-bolt` on the ⚡ badge — distinct from the one-shot execution pulse. **Never animate `opacity` on whole-node selectors** — it fades the node icon + content, not just the glow.
- **`data-page-hidden` animation pause** — [Dashboard.tsx](../client/src/Dashboard.tsx) mounts a `visibilitychange` listener that toggles `data-page-hidden` on `<html>`; [base.css](../client/src/themes/base.css) declares `html[data-page-hidden] *, *::before, *::after { animation-play-state: paused !important; }`. Without this, paused CSS keyframes accumulate frames in the compositor queue while the tab is hidden; on tab return all 50+ executing nodes' triple-layer pulses + Cyber's full-viewport `cyber-flicker` / `cyber-roll` resume simultaneously, stalling the GPU compositor 100-200ms and blocking input dispatch (first-click-feels-frozen pattern). The unpause is deferred via `requestAnimationFrame` so input dispatch wins the frame before composite resumes.
- **Parameter panel theme contract** — MasterSkillEditor, OutputPanel, MiddleSection, ToolSchemaEditor, ParameterPanel removed `useAppTheme()`. Every surface renders against Tailwind tokens + new-contract CSS custom props. Section headers carry the display-typography triplet (`font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)] text-fg-default`); action buttons use `<ActionButton intent="run|stop|save|config|tools">`; backgrounds use `bg-bg-elevated` / `bg-bg-panel` / `bg-bg-input` from the surface tier table above. EditableNodeLabel emits both `sq-node-label` and `node-label` classNames so per-theme typography rules fire under either topology. Per-theme scrollbar webkit rules (`::-webkit-scrollbar-thumb` etc.) declared in all 10 themes — gold (Renaissance), square-cornered (Atomic / Edo / Greek / Wasteland / Plague), metallic (Steampunk), phosphor (Rot), neon (Cyber), REC-red (Surveillance), shadcn neutral (Light / Dark).
- **Canvas-node class topology alignment** (W26) — TriggerNode, StartNode, ToolkitNode migrated from `.node` (rectangular spec-card) topology to `.sq-node` / `.sq-node-box` (square-icon node) topology so per-theme decorations (Steampunk brass rivets, Edo hanko seal, Surveillance REC LED, Renaissance gold emblem) reach them. Status pips, gear buttons, and React Flow handles on every node component now carry `.sq-node-pip` / `.sq-node-gear` / `.sq-node-handle.in/.out` (square nodes) or `.node-pip` / `.node-gear` / `.node-handle.in/.out` (rectangular nodes — AIAgentNode, GenericNode, TeamMonitorNode). Pip background is data-driven via `data-status="idle | executing | waiting | success | error"` — base.css picks the colour from shadcn semantic tokens; per-theme files override. Inline JS-computed `backgroundColor` / `border` / `animation` on these elements stripped — CSS owns visuals. Cyber's `.node-trigger` rule extended to dual-target both rectangular and square topologies (`.sq-node.node-trigger .sq-node-box`). Orphan `.node-output` rule removed. EditableNodeLabel now emits both `sq-node-label` and `node-label` classNames so per-theme typography rules fire. StartNode reads `nodeColor` from `definition?.defaults?.color` instead of hardcoded `theme.dracula.cyan`.
- **Audio fix** (W20) — `--sound-pack: chime` typo in `light.css` + `dark.css` was silently falling through to `'none'` (`chime` not in the engine's pack registry), making sound silent for the most-used themes. Renamed `light` → `parchment`, `dark` → `terminal`. Added `Sounds.unlock()` exported from [lib/sound.ts](../client/src/lib/sound.ts) plus a one-shot `pointerdown / keydown / touchstart` capture-phase listener in `useSoundSync()` ([hooks/useSound.ts](../client/src/hooks/useSound.ts)) so the AudioContext resumes on the user's first gesture (Chrome / Safari autoplay policy compliance).
- **Canvas-node visual contract** (W21) — every node component (`AIAgentNode`, `SquareNode`, `GenericNode`, `TriggerNode`, `StartNode`, `ToolkitNode`, `TeamMonitorNode`) stripped its inline `background` / `border` / `borderRadius` / `boxShadow` / `animation` props. Each now exposes the per-definition accent hex via `style={{ '--node-color': accentColor }}` on its outer wrapper. Visual styling lives in [base.css](../client/src/themes/base.css) defaults + per-theme overrides; `var(--node-color)` carries the plugin's `visuals.json` accent through CSS without fighting specificity. New `NodeStyle` helper type in [client/src/types/NodeTypes.ts](../client/src/types/NodeTypes.ts) (`React.CSSProperties & Record<\`--${string}\`, string | number>`) makes the inline custom-prop typecheck-clean.
- **Per-theme icons** (W23) — [client/src/assets/icons/themedGlyphs.ts](../client/src/assets/icons/themedGlyphs.ts) ports all 290 SVG glyphs from `design_handoff_machinaos_themes/app/icons.js` (29 keys × 10 themes). [NodeIcon.tsx](../client/src/assets/icons/NodeIcon.tsx) consults `THEMED_GLYPHS[activeTheme][key]` first; falls through to `lucide-react` / `lobehub:` / `asset:` dispatch on miss. Renaissance gets heraldic shields, Cyber wireframe + glow, Plague woodcut hatching, etc. Light + dark themes have no entries — they fall through to existing dispatch.
- **Canvas grid + cursors** (W24) — `--canvas-grid` and `--cursor-default` slots declared in [base.css](../client/src/themes/base.css) and bound to `.canvas-host`. Per-theme grids: Cyber 24px magenta+cyan grid, Surveillance CCTV crosshair + REC overlay, Renaissance fleur-de-lis cartography, Greek key meander, Steampunk brass bolt grid, Atomic mid-century starburst, Wasteland cracked-earth fissures, Rot flagstone, Plague broadsheet red-X. Custom cursors: Cyber crosshair reticle, Surveillance snooper reticle, Renaissance gold-leaf quill.
- **Decorative HTML primitives** (W25) — [client/src/components/SvgFilterDefs.tsx](../client/src/components/SvgFilterDefs.tsx) mounts a hidden inline `<svg><defs>` at app root exposing `#ink-blot` (Renaissance edge warble), `#noise` (Wasteland turbulence), `#crt` (Cyber chromatic aberration) so per-theme `filter: url(#...)` rules resolve. [client/src/components/ui/DropCap.tsx](../client/src/components/ui/DropCap.tsx) wraps content with `v-display drop-cap` className so the Renaissance `::first-letter` ornament rule fires.
- **Token contract**: all 10 themes ported in [client/src/themes/](../client/src/themes) (5 utopian + 5 dystopian). 13 CSS files, ~3,750 LOC total.
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
- **Sound contract** — [client/src/lib/sound.ts](../client/src/lib/sound.ts) ports the upstream WebAudio engine with all 10 packs (`parchment`, `marble`, `ink`, `clockwork`, `vibraphone`, `terminal`, `scrap`, `crypt`, `bell`, `telex`). [client/src/hooks/useSound.ts](../client/src/hooks/useSound.ts) reads `--sound-pack` from `:root` on theme change and mirrors the `soundEnabled` Zustand slice into `Sounds.setEnabled()`. Settings panel ships the toggle (Audio section). Persists to `localStorage['machinaos-sound']`; **default ON** (browsers gesture-gate WebAudio without a separate permission API — the AudioContext starts suspended and resumes on the user's first pointerdown / keydown / touchstart via `Sounds.unlock()`, registered as a one-shot capture-phase listener in `useSoundSync()`).
- **Canvas overlay packs** — [client/src/hooks/useAppTheme.ts](../client/src/hooks/useAppTheme.ts) extended from 2-way (`{light, dark}`) to 10-way: a `THEME_OVERRIDES` map applies a small overlay (primary, focus, focusRing, action colours, edge palette) on top of `lightColors` / `darkColors`. Existing call sites continue to read `theme.colors.X` and `theme.isDarkMode` unchanged; canvas selection rings, action buttons, and edge strokes pick up the active theme's accents under any of the 10 themes.

### Deferred (future PRs)
- **`--*-soft` token family** — `--info-soft`, `--warning-soft`, `--danger-soft`, `--success-soft` are referenced across multiple theme rules (greek, atomic, wasteland, plague, action-deploy, chat-msg-user, wf-card.selected, cmdk-item.active, action-run) but never declared. the hex migration converted the inline alpha compositions to `color-mix(in srgb, var(--info) 25%, transparent)`; the broader undeclared-family pattern is still tech debt. Centralise the family as `color-mix` tokens alongside their base (e.g. `--info-soft: color-mix(in srgb, var(--info) 18%, transparent)`) in a theme/base file.
- **Edo / Steampunk / Atomic canvas-host pseudo-element decorations** — these themes' richer canvas overlays (Edo radial-gradient ink-wash mountain, Steampunk corner radial accents, Atomic dot constellation) are pseudo-element decorations rather than tileable `--canvas-grid` patterns; W24 ported the tileable layer only. Wire via `.canvas::before/::after` rules in a future pass if needed.
- **Parameter panel internals** — RESOLVED: `ParameterRenderer`, `MiddleSection`, `MasterSkillEditor`, `ToolSchemaEditor` are fully on shadcn primitives + tokens (zero `useAppTheme` / `theme.colors`); section headers carry the display-typography triplet. The panel modal also picks up per-theme `.modal` / `.modal-head` / `.modal-title` decoration via `Modal.tsx`.
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

Token reads use `var(--token)` directly (no `hsl()` wrapper) — every colour value is hex / `color-mix()`, including the shadcn-bridged tokens and theme spot colours (`--gold`, `--crimson`, `--neon-magenta`, `--rec-red`).

## Canvas-node visual contract (W21)

Every canvas-node React component renders against pure CSS for visual styling. The component is responsible for:

1. **Setting `--node-color` inline** on its outer wrapper:
   ```tsx
   <div
     className={`node node-agent ${selected ? 'selected' : ''}`}
     style={{ '--node-color': accentColor, /* layout only */ } as NodeStyle}
   >
   ```
   `accentColor` comes from the plugin's `visuals.json` accent hex via `useNodeSpec(type)`.

2. **Keeping ONLY layout inline** — `position`, `padding`, `display`, `flex*`, `gap`, `width`, `height`, `transition`, `cursor`, `fontFamily`, `fontSize`. React Flow `<Handle>` positioning (`position: absolute; left/top/right/bottom`) MUST stay inline.

3. **Letting CSS own everything visual** — `background`, `border`, `borderRadius`, `boxShadow`, `animation` live in [base.css](../client/src/themes/base.css) defaults + per-theme overrides. Both consume `var(--node-color)` for accent surfaces.

`NodeStyle` ([types/NodeTypes.ts](../client/src/types/NodeTypes.ts)) is the helper type for inline custom-prop typecheck-cleanliness:
```ts
export type NodeStyle = React.CSSProperties & Record<`--${string}`, string | number>;
```

The `selected` co-class (driven by React Flow's `selected` prop) and the `data-executing` attribute (driven by SquareNode's `isGlowing` minimum-glow timer) toggle execution / selection visuals via CSS. The wider `.react-flow__node.executing .node` selector is bound by `client/src/styles/canvasAnimations.ts` for the standard pulse animation.

**Anti-pattern:** never set `background` / `border` / `borderRadius` / `boxShadow` inline on canvas-node components. Inline styles win specificity and block per-theme decorations from rendering.

### Class topology (W26)

Two visually distinct node families, each with its own class hooks. Pick the right one when adding a new canvas-node component:

| Family | Outer class | Inner box | Pip | Gear | Handle | Label | Used by |
|---|---|---|---|---|---|---|---|
| Square-icon | `.sq-node` | `.sq-node-box` | `.sq-node-pip` | `.sq-node-gear` | `.sq-node-handle.in` / `.sq-node-handle.out` | `.sq-node-label` | SquareNode, TriggerNode, StartNode, ToolkitNode |
| Rectangular | `.node` (+ optional `.node-agent` / `.node-trigger` co-class for type-specific theming) | (none — single-div card) | `.node-pip` | `.node-gear` | `.node-handle.in` / `.node-handle.out` | `.node-label` | AIAgentNode, GenericNode, TeamMonitorNode |

Both families share the same status-pip data contract:

```tsx
<div className="sq-node-pip" data-status={pipStatus} />  // or className="node-pip"
```

`pipStatus` is a string from the bucket `'idle' | 'executing' | 'waiting' | 'success' | 'error'`. base.css colors the pip per-status using `var(--success)` / `var(--destructive)` / `var(--node-color)` etc. Per-theme files override for material-specific identity.

Execution-state animation: `data-executing={isExecuting ? '' : undefined}` on the outer wrapper. base.css binds `.sq-node[data-executing] .sq-node-box` and `.react-flow__node.executing .node` to `var(--pulse-keyframe, node-pulse)` (the theme-selected pulse; keyframes + per-theme `--pulse-keyframe` live in `animations.css`). Trigger nodes apply `.machina-trigger-armed` (continuous listening heartbeat) on the `waiting` state instead — distinct from the execution pulse — plus `.machina-bolt` on the ⚡ badge.

Selection state: `selected` co-class on the outer wrapper, driven by React Flow's `selected` prop.

Type-specific co-classes (Cyber-only today): `.node-agent` colors AIAgentNode neon magenta; `.sq-node.node-trigger .sq-node-box` colors TriggerNode neon green. Other themes ignore these co-classes — single colour by node type is fine when not differentiating.

## Canvas-wide edge + node status animations (`canvasAnimations.ts`)

[client/src/styles/canvasAnimations.ts](../client/src/styles/canvasAnimations.ts) owns the `@keyframes` + `.react-flow__edge.{status}` + `.react-flow__node.{status}` rules injected once into Dashboard's `<style>` tag. It is the single home for canvas-wide rules that need to match React Flow's wrapper classes (per-node inline animations live in their own components and read theme tokens directly — see the visual contract above).

**Three named exports** — adding a new keyframe or status visual is a single-file change:

| Symbol | Role |
|---|---|
| `KEYFRAMES` | `@keyframes` definitions for edges (`dashFlow` — the marching-ants stroke-dashoffset cycle on executing edges) |
| `edgeStatusStyles(colors)` | `.react-flow__edge.{selected,executing,completed,error,pending,memory-active,tool-active}` stroke rules |
| `nodeStatusStyles(_colors)` | `.react-flow__node.{...}` status-class colours (currently a no-op stub; the arg is kept on the signature so the `buildCanvasStyles` / `CanvasStatusColors` contract stays stable for downstream consumers) |
| `buildCanvasStyles(colors)` | Composes the three into the final string for Dashboard |

**Light/dark distinction lives entirely in `theme.ts`.** `buildCanvasStyles(colors)` is single-arg and the file ships **zero hardcoded hex colours** — the theme object provides different values per mode, so this module knows nothing about which theme is active.

`CanvasStatusColors` is the contract interface, carrying exactly eight edge-stroke keys:

```ts
export interface CanvasStatusColors {
  edgeDefault: string;
  edgeSelected: string;
  edgeExecuting: string;
  edgeCompleted: string;
  edgeError: string;
  edgePending: string;
  edgeMemoryActive: string;
  edgeToolActive: string;
}
```

**No `nodeGlow` keyframe here.** This module used to inject a `nodeGlow` keyframe targeting the React Flow wrapper, but it was dead code (only the inner `.node` child animated) and was removed. Node execution glow is owned solely by [base.css](../client/src/themes/base.css) — the `node-pulse` keyframe + `.react-flow__node.executing .node` / `.sq-node[data-executing] .sq-node-box` rules (see [`--node-pulse-color` contract](#done) above). Do not re-introduce a competing `nodeGlow` keyframe here.

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
2. `<html data-theme="dark">` — neutral slate + Dracula
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
| [client/src/themes/base.css](../client/src/themes/base.css) | Shared scalars (space / radii / chrome / shadow / type) + `--tint-*` alpha scale + node-visual contract |
| [client/src/themes/light.css](../client/src/themes/light.css) | Light colour hex (shadcn + dracula + node + action + `-ink`); on bare `:root` so global |
| [client/src/themes/dark.css](../client/src/themes/dark.css) | Dark colour-hex overrides (+ `-ink`, deeper shadows) |
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
| [client/src/themes/animations.css](../client/src/themes/animations.css) | Per-theme `--pulse-keyframe` tokens + executing / trigger keyframes + `.machina-*` helpers |
| [client/tailwind.config.js](../client/tailwind.config.js) | Colour bridge (`theme.extend.colors` → `var(--X)`, no `hsl()`) |
| [client/src/index.css](../client/src/index.css) | Plumbing: global resets + `@theme inline` `var()` bridge (no literal colours) |
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
| [client/src/assets/icons/themedGlyphs.ts](../client/src/assets/icons/themedGlyphs.ts) | Per-theme SVG glyph map (29 keys × 10 themes) — Wave 23 |
| [client/src/assets/icons/NodeIcon.tsx](../client/src/assets/icons/NodeIcon.tsx) | Theme-aware icon resolver (consults THEMED_GLYPHS first) |
| [client/src/components/SvgFilterDefs.tsx](../client/src/components/SvgFilterDefs.tsx) | Inline `<svg><defs>` carrying `#ink-blot` / `#noise` / `#crt` filter IDs — Wave 25 |
| [client/src/components/ui/DropCap.tsx](../client/src/components/ui/DropCap.tsx) | `v-display drop-cap` wrapper for Renaissance ornament rule — Wave 25 |
| [client/src/types/NodeTypes.ts](../client/src/types/NodeTypes.ts) | `NodeStyle` helper type for inline `--*` custom props — Wave 21 |
| [client/index.html](../client/index.html) | Google Fonts loading (all 10 themes' typefaces) |
| [design_handoff_machinaos_themes/](../design_handoff_machinaos_themes/) | Reference HTML mocks + token spec |
| [design_handoff_machinaos_themes/app/icons.js](../design_handoff_machinaos_themes/app/icons.js) | Upstream icon source (ported by Wave 23) |
| [design_handoff_machinaos_themes/MIGRATION_PLAYBOOK.md](../design_handoff_machinaos_themes/MIGRATION_PLAYBOOK.md) | Upstream recipe — all originally-deferred items now landed (W14–W25) |
