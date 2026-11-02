# OpenCompany theming architecture — analysis

> **Status: Reference guide.** Live `client/src/themes/` and `client/src/lib/motion.ts` own production tokens and lifecycle behavior. Bundled specimens and token snapshots illustrate design intent; retain explicit product amendments when they differ.

Source: `client/src/themes/*.css` + `client/src/index.css` in https://github.com/zeenie-ai/OpenCompany (the live files are authoritative; the `reference/themes/` snapshots were retired — see its README). The app ships **3 themes**: 2 canonical (light, dark) + 1 "skin" (Atomic). The handoff's nine other skins (Renaissance, Greek, Edo, Steampunk, Cyber, Wasteland, Rot, Plague, Surveillance) were removed on 2026-10-12, and Home, the editor and the sign-in screen all show the chosen theme.

## 1. How it works — a four-layer contract

Every theme is **one CSS file, zero component changes**. The system is layered so a theme can do as little as retint colors or as much as redraw every surface:

1. **`base.css` — neutral defaults on `:root`.** Space scale, radii, motion durations/eases, `--type-uppercase: none`, `--canvas-grid: none`, `--sound-pack: none`, cursor defaults, plus the *default node visuals* (gradient fill, accent border, hover lift, the 3-layer `node-pulse` keyframe). Any theme that omits a variable inherits these.
2. **New-contract tokens — full-color CSS vars, scoped `:root[data-theme="X"]`.** The ~40-variable contract every theme must fill: surfaces (`--bg-app/panel/canvas/elevated/input/hover/active/overlay`), borders (`-default/strong/focus` + `--ornament-frame`), foreground (`--fg-default/muted/faint` + four `--fg-on-*` contrast colors), 3 font slots, type scale/tracking/case, 4 radii, 3 durations + 2 eases + `--motion-style`, `--sound-pack`, `--node-pulse-color`, 2 cursors, 2 scrollbar colors, `--canvas-grid` (+opacity).
3. **shadcn semantic bridge — the same palette re-declared under the shadcn token names as hex / `color-mix()`** (`--background: #0d0f13`) so every existing Tailwind utility (`bg-card`, `text-muted-foreground`, `bg-action-save-soft`) retints with zero call-site edits. `index.css`'s `@theme inline` maps `--color-X: var(--X)` with no `hsl()` wrapper. Includes the six action-intent roles (a skin may raise or lower their alphas) and `--tint-soft`/`--tint-border` alphas.
4. **Structural-class decorations.** Components carry stable hook classes — `.toolbar .sidebar .palette .canvas .statusbar .chat .modal .cmdk .node .sq-node-box .sq-node-gear .sq-node-pip .sq-node-label .wf-card .comp .cat .row .btn .action-btn .pip` — and a skin attaches its material identity to them: Atomic gives panels thick slate edges, cards and palette tiles boomerang corners, action buttons a hard offset shadow, and the canvas a starburst. Decorations are *additive*; un-themed components still work from layers 1–3.

**Key inversions worth copying:** color is bridged *twice* (full-color for new code, shadcn-named hex aliases for legacy utilities); status pips read `data-status` attributes so themes restyle state without touching React; the executing pulse uses a *theme-chosen* `--node-pulse-color` (not the node's own accent) so the glow always contrasts with that theme's canvas; `prefers-reduced-motion` and a `data-page-hidden` animation-pause are handled globally in base.css.

## 2. What a theme is allowed to change

Beyond color: **typeface trio, base font size (13–15px), letter case, tracking (0–0.18em), all four radii (0 → 999px), motion speed (60ms → 680ms), easing curve *shape* (smooth bezier / bouncy overshoot / `steps()` strobe / `linear`), a named `--motion-style`, a sound pack, the cursor itself (SVG data-URI), the canvas background pattern (SVG tile), scrollbar chrome, and selection color.** That breadth is the design insight: OpenCompany themes are *material systems*, not palettes.

## 3. The 3 themes at a glance

| Theme | World | Surfaces | Signature accents | Display / body / mono | Case·track | Radii | Motion (style) | Sound | Pulse |
|---|---|---|---|---|---|---|---|---|---|
| **Light** | quiet workspace | grey-blue paper | Dracula tints + blue | Geist ×2 / sys mono | none·0 | 4/6/8 | 90/180/320 smooth | parchment | info blue |
| **Dark** | signature look | neutral slate | Dracula neon | Geist ×2 / sys mono | none·0 | 4/6/8 | 90/180/320 smooth | terminal | info sky |
| **Atomic** | 1962 Eames | cream cardstock | atomic orange #e85a26 · turquoise #3a9aa0 · mustard | Bevan / Lato / Space Mono | UPPER·.06em | 0/2/4 (pill 999) | 100/200/380 bouncy (1.6 overshoot) | vibraphone | turquoise |

Atomic draws its starburst canvas through `--canvas-grid` and keeps the default `node-pulse`.

## 4. Patterns / observations

- **Motion personality is encoded in the *easing function*, not just speed**: Atomic's `cubic-bezier(.34,1.6,.5,1)` is a boomerang bounce against light and dark's smooth curve.
- **The shadcn alias layer is the cost of Tailwind compat** — every theme declares its palette twice (new-contract names + shadcn names), both in hex. New work should target the full-color contract only.

## 5. Status in THIS design system

`tokens/colors.css` encodes the two canonical themes (light `:root`, dark `.dark`). Atomic is **documented** here and sourced from the live `client/src/themes/atomic.css`, but not yet expressed as a token scope here. To port it: copy its layer-2 block into a `[data-theme="atomic"]` scope in `tokens/`, map its accents onto the action/node role triplets, and load its Google Fonts (Bevan, Lato, Space Mono — the same delivery as production). The table in §3 and `client/src/themes/` are authoritative; the panel-by-panel visual target is `reference-mockup/Panel Theme Matrix.dc.html`.
