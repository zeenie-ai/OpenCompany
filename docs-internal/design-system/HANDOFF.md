# Handoff: OpenCompany Theme System (3 themes × 17 panels)

> **Merged copy (2026-08)** — this is the handoff brief from the external
> `design_handoff_theme_system/` bundle, merged into
> `docs-internal/design-system/` as the canonical location. The external
> bundle was verified fully migrated (every directory diffed against its
> in-repo counterpart) and **removed from disk on 2026-08-14**; a
> `.gitignore` entry guards against a re-drop. Path mapping of what it
> contained: its `themes/` was byte-identical to the live
> [`client/src/themes/`](../../client/src/themes/) (which is authoritative
> and has since moved ahead via the fidelity pass); its `tokens/` /
> `components/` / `guidelines/` / `ui_kits/` are this directory's
> same-named folders; its `_source_docs/` are this directory's root files;
> `reference-mockup/` lives here at
> [`reference-mockup/`](./reference-mockup/); its `assets/diagrams/` were
> stripped exports of the public diagrams in
> [`docs/diagrams/`](../../docs/diagrams/).
>
> **Product amendments** (decisions recorded after the fidelity pass —
> where this brief and the product deliberately differ, the amendment
> wins):
>
> 1. **Themes CSS is authoritative over the mockup.** The shared 1.4s
>    `--pulse-duration` token drives both node families (the mockup's
>    synthesized 1.5s square-node duration and its `--accent-text-mix`
>    variable are mockup-only inventions, not product tokens).
> 2. **Fonts: legibility substitutions ADOPTED.** Cyber shipped Space Mono
>    and Plague shipped Cinzel as their display faces. No longer applies:
>    both skins were removed (amendment 7).
> 3. **Toolbar: the one shrinkable group is the workflow-name chip.**
>    The live toolbar has no wordmark; long workflow names are the real
>    overflow source, so the name chip carries `min-w-0 flex-1 truncate`
>    and every other cluster is pinned `shrink-0`.
> 4. **Edges:** resting edges follow this brief (pale neutral
>    `--edge-stroke`, dashed, orthogonal step); during execution the
>    status classes recolor with semantic tokens (executing / completed /
>    error / pending / memory- / tool- / skill-active) — runtime feedback
>    is functionality this product keeps. A condition-carrying
>    `ConditionalEdge` highlights in `--accent` — a sanctioned deviation.
> 5. **Console Dock is a hybrid**, not plain three tabs: Chat / Console /
>    Terminal tabs plus a split-view toggle that docks Chat beside the
>    Console/Terminal pane (simultaneous chat + logs during agent runs is
>    load-bearing functionality).
> 6. **Themed glyphs: ten themes ship their own set** (renaissance, cyber,
>    greek, edo, steampunk, atomic, wasteland, rot, plague, surveillance);
>    only Light and Dark fall through to lucide. The "eight themes" claim
>    later in this brief is stale.
> 7. **Nine skins removed (2026-10-12).** The product ships Light, Dark and
>    Atomic only; Renaissance, Greek, Edo, Steampunk, Cyber, Wasteland, Rot,
>    Plague and Surveillance were removed, with their glyph sets (only
>    Atomic's remain of amendment 6). Home, the editor and the sign-in screen
>    all show the chosen theme. The theme lists below are trimmed to the
>    three; the rest of the brief is unchanged.

## Overview

OpenCompany is a desktop workflow canvas for building agent workflows. It ships **3 visual themes** — two neutral base themes plus one "world" skin, Atomic (the brief was written for twelve; see amendment 7). Every theme reskins the *entire* application: surfaces, type (a different display/body/mono trio per theme), radii, iconography, canvas background texture, and motion.

This bundle contains the design system's real source files plus a working reference mockup that renders **every panel in every theme on one canvas**, so you can diff your implementation against the intended result panel-by-panel.

The theming architecture is the whole point: a component is written **once**, against tokens, and picks up every theme for free. If you find yourself writing `if (theme === 'atomic')` in a component, the token layer is missing something — add the token instead.

## About the Design Files

**Historical layout:** references below to the external bundle's `themes/`
directory describe the handoff at extraction time. That local directory is
not shipped here. Use live [`client/src/themes/`](../../client/src/themes/)
for production CSS and the recorded product amendments above for intended
deviations. The token/component snapshots are scoped prototype references.

The files in `reference-mockup/` are a **design reference created in HTML** — a prototype showing intended look and behavior, not production code to copy. Recreate the designs in the target codebase's existing environment (this app is React + TypeScript + Tailwind + React Flow) using its established patterns.

At the original handoff, `themes/`, `tokens/`, `components/`, and `guidelines/`
were copied from that source bundle. They do not define current production
precedence: theme snapshots were retired and live `client/src/themes/` has
advanced. Use bundled components and guidelines as explicitly scoped design
references.

## Fidelity

**High-fidelity.** Exact colors, type, spacing, radii, motion curves and glow values throughout, all traceable to the CSS in this bundle. Recreate pixel-perfectly.

---

## What's in the bundle

```
design_handoff_theme_system/
├─ themes/            ← historical external-bundle directory; not retained here.
│                       Production source: client/src/themes/ (3 themes)
├─ tokens/            ← 7 CSS files: the distilled token layer, well commented.
│                       Read these first — they explain the system.
├─ components/        ← 106 files. The component library, grouped by family
│                       (buttons/ canvas/ display/ feedback/ forms/ icons/ panels/).
│                       Each component ships .jsx + .d.ts + .prompt.md.
│                       The .prompt.md files are the per-component contracts —
│                       read them before implementing that component.
├─ guidelines/        ← THEMES.md + ANIMATIONS.md (prose contracts) and 21
│                       standalone HTML specimen pages (type scale, radii,
│                       node roles, shadows, all-theme comparisons).
├─ ui_kits/opencompany/ ← A runnable recreation of the app shell
│                       (App / CanvasView / Toolbar / Panels / ConsoleDock).
├─ reference/themes/  ← Pointer README only. The distilled snapshots went stale
│                       (pre-hex-migration) and were retired; `client/src/themes/`
│                       is authoritative.
├─ assets/            ← Product canvas screenshot + 6 architecture diagrams.
├─ reference-mockup/  ← The panel × theme matrix (open the .dc.html in a browser).
└─ _source_docs/      ← DS readme, IMPLEMENTATION.md, SKILL.md, manifest,
                        and _adherence.oxlintrc.json (lint rules that enforce
                        token usage — worth wiring into CI).
```

**For production:** read `client/src/themes/base.css`, the relevant live
`client/src/themes/{light,dark,atomic}.css`, and the current frontend/theme
docs. For prototype anatomy, then read `tokens/*.css`, `guidelines/THEMES.md`
and the relevant `components/*/**.prompt.md`. The historical tree above is
not a current copying checklist.

---

## Architecture: a three-layer cascade

1. **`tokens/`** — names the design decisions (`--bg-panel`, `--node-agent`, `--dur-fast`). Light values live in `:root`; dark overrides live under `.dark, [data-theme="dark"]`.
2. **`themes/base.css`** — the structural contract every theme must satisfy: which tokens exist, the node glow ladder, the canvas layer stack, motion defaults.
3. **`themes/<name>.css`** — one theme = one `[data-theme="x"]` block that re-declares the token set. Themes may add signature keyframes and ornament layers, but must not restyle components directly.

Theme switching is a single attribute on `<html>`: `data-theme="atomic"`. Nothing else changes.

---

## Design tokens

### Surfaces (light `:root` → dark)
| Token | Light | Dark |
|---|---|---|
| `--bg-app` | `#f5f7fa` | `#0d0f13` |
| `--bg-panel` | `#fafbfc` | `#15171c` |
| `--bg-canvas` | `#ffffff` | `#0d0f13` |
| `--bg-elevated` | `#ffffff` | `#1b1e25` |
| `--bg-input` | `#ffffff` | `#15171c` |
| `--surface-card` | `#ffffff` | `#1b1e25` |
| `--bg-hover` | `rgba(0,0,0,.04)` | `rgba(255,255,255,.05)` |
| `--bg-active` | `rgba(0,0,0,.06)` | `rgba(255,255,255,.08)` |
| `--bg-overlay` | `rgba(0,0,0,.45)` | `rgba(0,0,0,.7)` |

### Foreground & borders
| Token | Light | Dark |
|---|---|---|
| `--fg-default` | `#1a1d21` | `#e8eaed` |
| `--fg-muted` | `#4b5563` | `#9aa1ac` |
| `--fg-faint` | `#9ca3af` | `#6b7280` |
| `--border-default` | `#d1d5db` | `#2b2f37` |
| `--border-strong` | `#9ca3af` | `#3c424c` |
| `--border-focus` | `#3b82f6` | `#3b82f6` |

### Semantic status
`--success` `#059669`→`#22c55e` · `--warning` `#d97706`→`#f59e0b` · `--destructive` `#dc2626`→`#ef4444` · `--info` `#0891b2`→`#38bdf8`

### Action intents (toolbar soft-tinted buttons)
Six intents — `run` (deploy), `stop` (destructive), `save` (commit), `config` (settings), `secret` (credentials), `tools` (palette). Each has four tokens plus an ink alias:

```
--action-run          the raw hue (Dracula green #50fa7b)
--action-run-soft     color-mix(… 15%, transparent)   ← the fill
--action-run-hover    color-mix(… 25%, transparent)
--action-run-border   color-mix(… 60%, transparent)   ← the border
--action-run-ink      #15803d light / #50fa7b dark    ← the TEXT color
```

**Use `-ink` for text, never the raw hue.** The vivid Dracula palette is bright enough to be text on dark grounds but fails contrast on light ones; the ink aliases resolve per mode. This is the single easiest thing to get wrong.

### Node role colors (canvas)
`--node-agent` purple · `--node-model` cyan · `--node-tool` green · `--node-trigger` pink · `--node-workflow` orange. Each also has `-soft` (8%) and `-border` (30%) variants. Plus `--node-pulse-color` — see the glow ladder below.

### Typography
Base **14px** (dense desktop tool). Scale: `--text-2xs` 11 · `--text-xs` 12 · `--text-sm` 13 · `--text-base` 14 · `--text-md` 16 · `--text-lg` 18 · `--text-xl` 24 · `--text-2xl` 32 · `--text-3xl` 44.
Weights 400/500/600/700. Leading 1.2 / 1.5 / 1.65.
Base fonts: **Geist** (sans/display/body), **JetBrains Mono** (mono).

Three tokens carry the themed typography and are the reason components need no theme branching:
```
--font-display  --font-body  --font-mono
--type-uppercase          none | uppercase
--type-tracking-display   0 | 0.04em … 0.18em
```
Every theme re-declares all five. A heading is written once as `font-family: var(--font-display); text-transform: var(--type-uppercase); letter-spacing: var(--type-tracking-display)`.

### Spacing, radii, chrome
Space scale 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 (`--space-1`…`--space-8`).
Radii `--radius-sm` 4 · `--radius-md` 6 · `--radius-lg` 8 · `--radius-xl` 12 · `--radius-node` 10 · `--radius-pill` 999. In the live app each theme overrides `--radius-sm/md/lg/pill` (Atomic: 0 / 2 / 4px, pill 999), but no theme overrides `--radius`, `--radius-xl` or `--radius-node`. The Tailwind `rounded-sm/md/lg/xl` utilities are computed from `--radius` in `index.css`, so they stay 4/6/8/12px in every theme; a skin's radii reach `rounded-pill`, the Home radii (`--radius-row|card|panel|draft|composer`) and any CSS that reads `var(--radius-*)` directly.
Fixed chrome: `--h-toolbar` 48 · `--h-statusbar` 24 · `--h-control` 32 · `--w-sidebar` 280 · `--w-palette` 320.

### Shadows & motion
`--shadow-card`, `--shadow-card-hover`, `--shadow-modal` (all darker in dark mode).
`--dur-fast` 90ms · `--dur-default` 180ms · `--dur-slow` 320ms · `--ease-default` `cubic-bezier(.2,.7,.3,1)` · `--ease-emphasis` `cubic-bezier(.6,-.05,.3,1.4)`.
**Themes override durations and easing, not just colors** — Atomic runs 100/200/380 on a bouncy 1.6-overshoot curve. Personality lives in the curve. See `guidelines/ANIMATIONS.md`.

---

## The themes

| Theme | Family | Character | Display / Body / Mono |
|---|---|---|---|
| Light | base | grey-blue paper workspace | Geist / Geist / JetBrains Mono |
| Dark | base | neutral slate + Dracula neon | Geist / Geist / JetBrains Mono |
| Atomic | skin | 1962 Eames optimism | Bevan / Lato / Space Mono |

---

## The node glow ladder

Defined in `themes/base.css`. Getting this right is most of the canvas's quality. Five states:

| State | Treatment |
|---|---|
| **Resting** | `box-shadow: 0 2px 8px color-mix(in srgb, <node-color> 18%, transparent)` — a quiet drop shadow. **No halo.** |
| **Hover** | `translateY(-1px)` + `0 4px 14px color-mix(… 28%, transparent)`, transitioned on `--dur-fast` / `--ease-default` |
| **Selected** | 1px accent ring + `0 4px 14px color-mix(… 32%, transparent)`, border goes to full strength |
| **Executing** | the theme's own `--pulse-keyframe`, driven by `--node-pulse-color`. Timing: the shared `--pulse-duration` token (**1.4s**) for both `.node` and `.sq-node-box` |
| **Success / error** | `0 0 0 1px <semantic>, 0 0 14px color-mix(in srgb, <semantic> 60%, transparent)` |

Three traps, all of which I hit:

1. **The halo belongs to the pulse and to outcome states — not to the resting node.** A permanent accent halo on every node reads as "everything is running."
2. **A `box-shadow` keyframe overwrites any static `box-shadow` every frame.** So a `.node.selected` ring is invisible on an executing node. Don't combine the two states; pick one.
3. **An author `!important` hover shadow outranks a running animation and freezes the pulse.** Animated nodes must get **transform-only** hover; only static nodes get the hover shadow.

Also: `--node-pulse-color` — not the node's own accent — drives the pulse, so it stays legible on every theme's canvas, light or dark. Status pips follow `data-status`: executing/waiting take the node color + a 6px halo and blink; success/error take their semantic token.

---

## The canvas background

Three stacked layers, bottom to top:

1. **`--bg-canvas`** — the base fill.
2. **The `.canvas` gradient stack** — per-theme atmosphere (Atomic's five confetti dots).
3. **`--canvas-grid`** — the pattern. Atomic defines it as an SVG data-url, not a gradient: its starburst (160px tile).

---

## Panels to implement (17)

Each is in the reference mockup, rendered in all three themes.

| # | Panel | Size | Source |
|---|---|---|---|
| 1 | Workflow Sidebar | 280px | `ui/WorkflowSidebar.tsx` |
| 2 | Component Palette | 320px | `ui/ComponentPalette.tsx` |
| 3 | Node Configuration | 3-column modal | `ParameterPanel.tsx` |
| 4 | Settings | modal | `ui/SettingsPanel.tsx` |
| 5 | API Credentials | modal | `credentials/CredentialsModal.tsx` |
| 6 | Console Dock | tabbed | `ui/ConsolePanel.tsx` |
| 7 | Output Panel | — | `output/OutputPanel.tsx` |
| 8 | Input Data Panel | 350px | `ui/InputNodesPanel.tsx` |
| 9 | Context Panel | — | `parameterPanel/ContextPanel.tsx` |
| 10 | Memory Tool Panel | — | `parameterPanel/MemoryToolPanel.tsx` |
| 11 | Task Manager Panel | — | `parameterPanel/TaskManagerPanel.tsx` |
| 12 | Team Monitor Panel | — | `parameterPanel/TeamMonitorPanel.tsx` |
| 13 | Process Manager Panel | 9-col table | `parameterPanel/ProcessManagerPanel.tsx` |
| 14 | Gallery Panel | grid ⇄ list | `parameterPanel/GalleryPanel.tsx` |
| 15 | Toolbar + Status Bar | 48 + 24px | `ui/TopToolbar.tsx`, `StatusBar` |
| 16 | Node Canvas | — | `Dashboard.tsx`, `SquareNode.tsx` |
| 17 | Motion + Animations | — | `tokens/animations.css` |

**Canvas edges are React Flow `step` edges** — dashed orthogonal runs with right-angle corners in one pale neutral stroke. Not beziers.

**Status bar** field set: `● ONLINE | WF: <name> | NODES: n | THEME: <name> | <clock>`.

---

## Layout traps under themed typography

Atomic's `uppercase` + `.06em` tracking makes **every label wider** than in Light/Dark. Two consequences:

- **Pin your toolbar clusters.** The wordmark, workflow-name chip, mode toggle and action cluster all need `flex: none`; leave exactly one group (File/Edit/View) shrinkable. Without this the mode toggle collapses to a sliver in Atomic.
- **Test the widest theme, not the default.** Anything that fits in Light can overflow in Atomic, the stress case.

Also: canvas node icons are **36px** everywhere, and the size is a token, not a class — `theme.nodeSize.squareIcon` in `client/src/styles/theme.ts` (0.5625 x the 64px `nodeSize.square` box), passed to `NodeIcon` as `size=`; non-canvas surfaces use the `theme.iconSize.*` scale the same way. `NodeIcon` applies that one token as the box's width, height AND font size, so SVG, library and emoji icons all draw at the same edge. Never size an icon with Tailwind `h-*`/`text-*` classes: the theme type scale maps `text-3xl` to 44px, which is how emoji nodes once painted half again larger than every SVG-backed node beside them. Atomic ships its own glyph set; Light and Dark fall through to lucide.

---

## Interactions & state

- **Theme switch** — `data-theme` on `<html>`; persist the choice.
- **Node select** — click sets selected state (ring + lift). Only one at a time.
- **Node execute** — pulse runs for the duration of the step, then resolves to success or error glow.
- **Panel tabs** (Console: Chat / Console / Terminal) — plain tab state.
- **Palette search** — filters sections live; empty state when no match.
- **Settings** — switches and sliders write through immediately (auto-save interval 10–300s, compaction ratio 5–95%).
- **Gallery** — grid ⇄ list toggle.
- **Reduced motion** — honor `prefers-reduced-motion`; the pulse and all ornament animations should stop.

---

## Suggested implementation order

1. Wire the token layer + `themes/base.css`, get Light and Dark correct. **Do not move on until a component has zero hardcoded colors.**
2. Add the skin (Atomic) — it will expose missing tokens, especially in type and radii.
3. Build the panels against tokens, checking each against the reference mockup.
4. Add the canvas: node glow ladder first, then step edges, then the background stack.
5. Wire `_source_docs/_adherence.oxlintrc.json` into CI to keep hardcoded values from creeping back.

## Assets

`assets/product-canvas-screenshot.png` — the real running canvas; the fidelity target for node/edge rendering.
[`docs/diagrams/*.svg`](../../docs/diagrams/) — the 20 diagrams: six overview diagrams (node anatomy, execution flow, AI agent routing, system overview, default workflows, how-it-works) plus 14 source-backed architecture and product-panel diagrams indexed in `CONTRIBUTING.md`. They live at the repo root only; `assets/diagrams/` holds a pointer, not copies.

## Files

- `reference-mockup/Panel Theme Matrix.dc.html` — open in a browser. Left rail picks the panel; header zooms and toggles motion. Interactions are shared across themes on purpose: type in one palette search or toggle one switch and all three respond, so you can compare the same state across skins.
- `reference-mockup/support.js` — runtime for the above; keep it beside the HTML.
