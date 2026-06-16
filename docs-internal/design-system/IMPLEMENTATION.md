# MachinaOS Design System — Claude Code Implementation Guide

This bundle is the **MachinaOS (zeenie.ai) design system**: real design tokens, reference React components, full-screen UI-kit recreations, and foundation/theme documentation. Hand it to Claude Code to implement MachinaOS-branded UI in a real codebase.

> **What these files are.** `styles.css` + everything under `tokens/` is **production-ready CSS** — ship it as-is. Everything under `components/`, `ui_kits/`, and `guidelines/` is a **design reference**: faithful prototypes (React with inline styles that read the CSS variables) showing the intended look, anatomy, and behavior. Recreate them in the target codebase's own environment and patterns (your component library, your styling system) rather than copying verbatim. If there's no environment yet, React + plain CSS variables is the path of least resistance because the components are already written that way.

This guide is self-sufficient — a developer who wasn't in the original conversation can implement from it alone.

---

## 1. Quickstart

```
your-app/
  design-system/        ← copy tokens/ + styles.css here unchanged
    styles.css
    tokens/{colors,typography,spacing,motion,animations,fonts,base}.css
```

```html
<!-- load the global stylesheet once -->
<link rel="stylesheet" href="/design-system/styles.css">
<!-- icons: Lucide (the only icon system) -->
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
```

```html
<html class="dark"> <!-- signature look. Omit `dark` for the light theme -->
```

That's the whole foundation. Every component reads CSS custom properties, so theming is automatic — no JS theme context required for light/dark.

**Fidelity: high.** Colors, type, spacing, radii, motion and component anatomy are final. Recreate pixel-for-pixel using the tokens; do not re-derive values by eye.

---

## 2. Design tokens (the contract)

All tokens are CSS custom properties under `:root` (light) and `.dark` / `[data-theme="dark"]` (dark). Full source: `tokens/`. Consume them as `var(--token)` — never hardcode the resolved hex.

### Surfaces
| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg-app` | `#f5f7fa` | `#0d0f13` | app/window background |
| `--bg-panel` | `#fafbfc` | `#15171c` | toolbar, sidebar, palette, status bar |
| `--bg-canvas` | `#ffffff` | `#0d0f13` | node canvas |
| `--bg-elevated` | `#ffffff` | `#1b1e25` | cards, section heads, inputs-on-panel |
| `--bg-input` | `#ffffff` | `#15171c` | form fields |
| `--surface-card` | `#ffffff` | `#1b1e25` | node/data card fill |
| `--bg-hover` | `rgba(0,0,0,.04)` | `rgba(255,255,255,.05)` | hover wash |
| `--bg-active` | `rgba(0,0,0,.06)` | `rgba(255,255,255,.08)` | selected/pressed wash |
| `--bg-overlay` | `rgba(0,0,0,.45)` | `rgba(0,0,0,.7)` | modal scrim |

### Foreground & borders
`--fg-default` (#1a1d21 / #e8eaed) · `--fg-muted` (#4b5563 / #9aa1ac) · `--fg-faint` (#9ca3af / #6b7280) · `--fg-on-accent`.
`--border-default` (#d1d5db / #2b2f37) · `--border-strong` · `--border-focus` (#3b82f6 / #3b82f6).

### Brand & status
`--primary` #2563eb / #3b82f6 · `--accent` #7c3aed / #8b5cf6.
`--success` #059669 / #22c55e · `--warning` #d97706 / #f59e0b · `--destructive` #dc2626 / #ef4444 · `--info` #0891b2 / #38bdf8.

### Dracula accents (identical in both themes — the personality layer)
`--dracula-green #50fa7b` · `--dracula-cyan #8be9fd` · `--dracula-purple #bd93f9` · `--dracula-pink #ff79c6` · `--dracula-orange #ffb86c` · `--dracula-yellow #f1fa8c` · `--dracula-red #ff5555`.

**These are never used as solid fills.** They are applied as *soft tints* via the role tokens below.

### Semantic roles — THE core idea
Color is assigned to **meaning**, not chosen decoratively. Two role families, each a triplet (`-soft` ~8–15% fill, `-border` ~30–60% border, full-strength text via `-ink`):

**Action intents** (buttons): `--action-run` (green) · `--action-stop` (pink) · `--action-save` (cyan) · `--action-config` (orange) · `--action-secret` (yellow) · `--action-tools` (purple). Each has `-soft`, `-hover`, `-border`, `-ink` variants.

**Node roles** (canvas): `--node-agent` (purple) · `--node-model` (cyan) · `--node-tool` (green) · `--node-trigger` (pink) · `--node-workflow` (orange). Each has `-soft` and `-border`.

The signature button recipe:
```css
background: var(--action-run-soft);   /* ~15% tint  */
border: 1px solid var(--action-run-border); /* ~60% */
color: var(--action-run-ink);          /* readable accent */
/* hover → background: var(--action-run-hover) (~25%) */
```

### Type
`--font-sans: 'Geist'` (display = body) · `--font-mono: 'JetBrains Mono'` (counts, state, timestamps — the "machine voice").
Scale (14px base, dense desktop): `--text-2xs 11` `--text-xs 12` `--text-sm 13` `--text-base 14` `--text-md 16` `--text-lg 18` `--text-xl 24` `--text-2xl 32` `--text-3xl 44`.
Weights: 400 body / 500 UI labels / 600 headings & buttons. Sentence case; uppercase + `--tracking-label .04em` only in the status bar and micro-labels.

### Spacing / radii / chrome
Space: `--space-1..8` = 4/8/12/16/24/32/48/64.
Radii: `--radius-sm 4` `--radius-md 6` `--radius-lg 8` (controls) `--radius-node 10` `--radius-xl 12` `--radius-pill 999`.
Fixed chrome: `--h-toolbar 48` `--h-statusbar 24` `--h-control 32` `--w-sidebar 280` `--w-palette 320`.

### Shadows / motion
`--shadow-card` (quiet) `--shadow-card-hover` `--shadow-modal`. Glow is reserved for meaning (accent-colored shadow on nodes; 3-layer executing pulse).
`--dur-fast 90ms` `--dur-default 180ms` `--dur-slow 320ms` · `--ease-default cubic-bezier(.2,.7,.3,1)` · `--ease-emphasis cubic-bezier(.6,-.05,.3,1.4)`. Hover lifts -1px; press translates +1px; disabled opacity .5. Honor `prefers-reduced-motion`.

### Motion / animation (`tokens/animations.css` — see §6)
Motion is theme-scoped: each `[data-theme]` overrides `--dur-*`, `--ease-default`, a named `--motion-style`, and which executing-pulse keyframe it uses. The glow color is **`--node-pulse-color`** — a theme-chosen contrast accent, never the node's own fill. Signature keyframes: `node-pulse` (default 3-layer glow), `trigger-listening` (armed heartbeat), `cyber-*` (steps strobe + CRT + scanline), `surv-*`, `ren-pulse-exec`. Helper classes: `.machina-pulse`, `.machina-trigger-armed`, `.machina-bolt`, `.machina-crt`, `.machina-scanline`.

---

## 3. Components (reference implementations)

34 components in `components/<group>/`. Each ships three files: `Name.jsx` (reference React, inline styles reading tokens), `Name.d.ts` (props contract), `Name.prompt.md` (one-line purpose + usage example + variants). **Read the `.prompt.md` first** for intent, then the `.jsx` for exact styling.

| Group | Components |
|---|---|
| `buttons/` | **ActionButton** (6 soft-tint intents — the signature button), Button (shadcn variants) |
| `forms/` | Input, Textarea, Select, Checkbox, RadioGroup, Switch, **Slider**, **ApiKeyInput** (masked + validate flow) |
| `display/` | Badge, Card, Tabs, Avatar, Kbd, **LogLine**, **ChatBubble** |
| `feedback/` | Modal, Toast, Tooltip, Spinner, Progress, EmptyState |
| `panels/` | **PanelModal** (title-left / centered actions / X-right), **SettingsSection**+SettingsRow, **CollapsibleSection**, **DataCard** (execution JSON card) |
| `canvas/` | **SquareNode** (the workflow node — pip, gear, handles, pulse; `trigger` variant adds the lightning ⚡ badge + listening pulse and drops the input handle), ComponentItem, WorkflowCard, StatusBar, ModeToggle |
| `icons/` | Icon (Lucide bridge — stroke 2, `currentColor`) |

Implementation notes:
- Components are self-contained: they import React only and reference styling exclusively through CSS variables. No CSS-in-JS lib, no other npm deps.
- To adopt into an existing system, map each to your primitive (e.g. your `<Button>`) and apply the token recipe from the `.jsx`. To adopt wholesale, the `.jsx` files run as-is in React 18.
- Product node icons are colorful emoji-style glyphs (🤖 🧠 💬 📱 🔍); provider logos come from `@lobehub/icons` in production — substitute colored marks if unavailable. Everything else is Lucide.

---

## 4. UI kits (full screens to match)

`ui_kits/machinaos/` is an interactive recreation of the product — the source of truth for how components compose into real views. Open `ui_kits/machinaos/index.html`.

- `Toolbar.jsx` — 48px top toolbar (file menu, workflow name, Normal/Dev toggle, action cluster, save state).
- `Panels.jsx` — 280px workflow sidebar + 320px component palette (search, categorized node catalogue, count badges).
- `CanvasView.jsx` — dot-grid canvas, dashed SVG edges, SquareNodes + the rectangular AI Agent node, executing-pulse run sequence.
- `ConsoleDock.jsx` — Chat / Console / Terminal tabbed dock.
- `PanelsModals.jsx` — **Settings**, **API Credentials**, and the three-column **Node Configuration / Parameter** panel (Input data | Parameters | Output).
- `App.jsx` — state wiring (panel toggles, theme, run/stop, add-node, open modals).

Layout contract: fixed 48px toolbar + 24px status bar top/bottom; 280px sidebar left; 320px palette right; canvas fills the remainder. Density is high (32px controls, 13–14px text).

---

## 5. Theming beyond light/dark

The real app ships **12 themes** (light, dark + 10 "skins": Renaissance, Greek, Edo, Steampunk, Atomic, Cyber, Wasteland, Rot, Plague, Surveillance). Only light/dark are encoded as live token scopes here. The architecture, per-theme token tables, and porting recipe are in `guidelines/THEMES.md`; verbatim source for all 13 theme CSS files is in `reference/themes/`. To add a skin: copy its token block into a `[data-theme="X"]` scope, map its accents onto the action/node role triplets, and load its fonts. **Readability rule:** decorative display faces (blackletter, Major Mono, etc.) are documentation specimens only — UI copy always uses a readable body font.

Visual verification pages (open in a browser): `guidelines/theme-comparison-full.html` (full screen ×12), `guidelines/panels-all-themes.html` (every panel — chrome + overlays — ×12), `guidelines/theme-matrix.html` (token matrix), `guidelines/animations-all-themes.html` (live motion ×12).

## 5b. Motion & animation (`tokens/animations.css` · `guidelines/ANIMATIONS.md`)

Motion is part of each theme's identity — the personality lives in the **easing function**, not just duration (smooth · organic · mechanical · bouncy 1.6-overshoot · steps glitch · jittery · 680ms drift · stiff · linear scanline). Per-`[data-theme]` scopes set `--dur-*`, `--ease-*`, `--motion-style`, and `--pulse-keyframe/-duration/-timing`.

- **Executing pulse:** add `.machina-pulse` to a running node; it reads the theme's keyframe + `--node-pulse-color` (the theme's contrast glow color — set it on/above the node).
- **Trigger nodes:** `.machina-trigger-armed` is the continuous "listening" heartbeat while a trigger waits for events (distinct from the one-shot execution pulse); pair with the lightning ⚡ badge (`.machina-bolt`) and no input handle. `trigger-fire` flashes when an event lands.
- **Ambient:** `.machina-crt` (CRT flicker) + `.machina-scanline` (rolling band) for Cyber/Surveillance frames.
- All loops stop under `prefers-reduced-motion`; always ship the static end-state so print/PDF render meaningfully.

---

## 6. Voice & content (for any new copy)

Second person, possessive ("your own AI assistant"). Terse declarative fragments ("No code. No subscription. No usage limits."). Sentence case. Buttons are one word / short verb phrase (Start, Stop, Save, New Workflow). Mono for numbers and state. **No decorative emoji** — in-product emoji are functional node glyphs only. Status bar reads like a shell prompt: `ONLINE | WF: Test | NODES: 12 | THEME: DARK | 14:02:33`.

---

## 7. File map

- `styles.css` — global entry (import this). `tokens/` — the seven token files including `animations.css` (ship as-is).
- `components/<group>/` — 38 components (`.jsx` + `.d.ts` + `.prompt.md`) + one `*.card.html` specimen per group.
- `ui_kits/machinaos/` — interactive full-app recreation (`index.html` + JSX) incl. Settings / Credentials / Node-Config panels.
- `guidelines/` — foundation specimen cards, `THEMES.md`, `ANIMATIONS.md`, and the theme/panel/animation comparison pages.
- `reference/themes/` — verbatim repo theme CSS (porting reference; not shipped to consumers).
- `assets/` — product screenshot + official architecture diagrams (SVG).
- `readme.md` — design guide (voice, visual foundations, iconography). `SKILL.md` — Agent-Skill entry point.

**Upstream source:** https://github.com/zeenie-ai/MachinaOS (`client/src/index.css`, `client/src/themes/*.css`, `client/src/components/ui/*`). Explore it for any detail this bundle doesn't cover.
