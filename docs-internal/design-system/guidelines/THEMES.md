# OpenCompany theming architecture — analysis

Source: `client/src/themes/*.css` + `client/src/index.css` in https://github.com/zeenie-ai/MachinaOS (full copies in `reference/themes/`). The app ships **12 themes**: 2 canonical (light, dark) + 10 "skins" (Renaissance, Greek, Edo, Steampunk, Atomic, Cyber, Wasteland, Rot, Plague, Surveillance).

## 1. How it works — a four-layer contract

Every theme is **one CSS file, zero component changes**. The system is layered so a theme can do as little as retint colors or as much as redraw every surface:

1. **`base.css` — neutral defaults on `:root`.** Space scale, radii, motion durations/eases, `--type-uppercase: none`, `--canvas-grid: none`, `--sound-pack: none`, cursor defaults, plus the *default node visuals* (gradient fill, accent border, hover lift, the 3-layer `node-pulse` keyframe). Any theme that omits a variable inherits these.
2. **New-contract tokens — full-color CSS vars, scoped `:root[data-theme="X"]`.** The ~40-variable contract every theme must fill: surfaces (`--bg-app/panel/canvas/elevated/input/hover/active/overlay`), borders (`-default/strong/focus` + `--ornament-frame`), foreground (`--fg-default/muted/faint` + four `--fg-on-*` contrast colors), 3 font slots, type scale/tracking/case, 4 radii, 3 durations + 2 eases + `--motion-style`, `--sound-pack`, `--node-pulse-color`, 2 cursors, 2 scrollbar colors, `--canvas-grid` (+opacity).
3. **shadcn HSL bridge — the same palette re-declared as HSL triplets** (`--background: 28 41% 12%`) so every existing Tailwind utility (`bg-card`, `text-muted-foreground`, `bg-action-save-soft`) retints with zero call-site edits. Includes the six action-intent triplets (with per-theme alphas — e.g. Steampunk raises soft fill to 20–22% because brass needs more presence than Dracula neon) and `--tint-soft`/`--tint-border` alphas.
4. **Structural-class decorations.** Components carry stable hook classes — `.toolbar .sidebar .palette .canvas .statusbar .chat .modal .cmdk .node .sq-node-box .sq-node-gear .sq-node-pip .sq-node-label .wf-card .comp .cat .row .btn .action-btn .pip .ornament` — and themes attach material identity to them: vellum noise + gilded `ornament-frame` (Renaissance), riveted brass plates with `::before/::after` rivets (Steampunk), CRT flicker + rolling scanline + glitch-on-hover keyframes (Cyber), REC-LED blink (Surveillance). Decorations are *additive*; un-themed components still work from layers 1–3.

**Key inversions worth copying:** color is bridged *twice* (full-color for new code, HSL triplets for legacy utilities); status pips read `data-status` attributes so themes restyle state without touching React; the executing pulse uses a *theme-chosen* `--node-pulse-color` (not the node's own accent) so the glow always contrasts with that theme's canvas; `prefers-reduced-motion` and a `data-page-hidden` animation-pause are handled globally in base.css.

## 2. What a theme is allowed to change

Beyond color: **typeface trio, base font size (13–15px), letter case, tracking (0–0.18em), all four radii (0 → 999px), motion speed (60ms → 680ms), easing curve *shape* (smooth bezier / bouncy overshoot / `steps()` strobe / `linear`), a named `--motion-style`, a sound pack, the cursor itself (SVG data-URI), the canvas background pattern (SVG tile), scrollbar chrome, and selection color.** That breadth is the design insight: OpenCompany themes are *material systems*, not palettes.

## 3. The 12 themes at a glance

| Theme | World | Surfaces | Signature accents | Display / body / mono | Case·track | Radii | Motion (style) | Sound | Pulse |
|---|---|---|---|---|---|---|---|---|---|
| **Light** | quiet workspace | grey-blue paper | Dracula tints + blue | Geist ×2 / sys mono | none·0 | 4/6/8 | 90/180/320 smooth | parchment | info blue |
| **Dark** | signature look | neutral slate | Dracula neon | Geist ×2 / sys mono | none·0 | 4/6/8 | 90/180/320 smooth | terminal | info sky |
| **Renaissance** | illuminated codex | vellum cream | gold #d4a030 · crimson #8a1410 · lapis #2548c8 | Cinzel / Cormorant Garamond / IM Fell English | UPPER·.10em | 2/3/4 (pill 12) | 140/280/520 organic | parchment | lapis (Marian halo) |
| **Greek** | marble agora | sun-bleached marble | lapis #284b82 · oxblood #7a1a18 · gold #c8a040 · olive | Cinzel / Cormorant / Courier Prime | UPPER·.18em | 0/0/2 | 120/240/460 smooth | marble | lapis |
| **Edo** | washi + sumi-e | rice paper | vermillion #b41e1e · sumi ink · bamboo · sakura | Shippori Mincho ×2 / JetBrains | none·.06em | all 0 | 90/220/520 organic | ink | vermillion |
| **Steampunk** | Verne submarine | oiled leather | brass #d8a848 · copper #b8602a · rust | IM Fell English SC / IM Fell / Special Elite | UPPER·.10em | 2/4/8 | 110/240/480 mechanical | clockwork | copper |
| **Atomic** | 1962 Eames | cream cardstock | atomic orange #e85a26 · turquoise #3a9aa0 · mustard | Bevan / Lato / Space Mono | UPPER·.06em | 0/2/4 (pill 999) | 100/200/380 bouncy (1.6 overshoot) | vibraphone | turquoise |
| **Cyber** | Neuromancer market | void #050010 | neon magenta #f51eb6 · cyan #1dd9e5 · green · yellow | Major Mono Display / JetBrains ×2 (13px) | UPPER·.18em | all 0 | 60/120/240 `steps()` glitch | terminal | neon cyan |
| **Wasteland** | Mad Max scrap | irradiated dust | ochre #e88a28 · rust #8a3a18 · rad-yellow #c8d038 · bone | Special Elite ×2 / VT323 | UPPER·.10em | all 0 | 60/180/320 jittery | scrap | rad yellow |
| **Rot** | mossy crypt | charcoal green | moss #78c878 · candle #e8a838 · bone · crypt brown | Pirata One / EB Garamond / JetBrains | none·.04em | 1/2/4 | 140/320/680 drift (slowest) | crypt | moss phosphor |
| **Plague** | 1349 quarantine notice | bleached linen | dried blood #783c28 · bile #98a838 · crow black | UnifrakturCook / EB Garamond / Special Elite | UPPER·.06em | all 0 | 100/220/460 stiff | bell | bile red |
| **Surveillance** | 1970s panopticon | institutional grey | REC red #e82626 · phosphor #6acc6a · amber | Anonymous Pro / IBM Plex Mono ×2 (13px) | UPPER·.10em | all 0 | 60/140/240 `linear` scanline | telex | REC red |

Special hardware: **Cyber** ships custom crosshair + bracket cursors and full-frame CRT flicker/roll keyframes; **Surveillance** ships a REC-red reticle cursor and a 1920×1080 CCTV crosshair canvas overlay; **Renaissance** ships a quill cursor, fleur-de-lis canvas tile and the only non-`none` `--ornament-frame` (triple gilded inset); **Steampunk** draws bolt-pattern canvas tiles and rivets nodes with pseudo-elements.

## 4. Patterns / observations

- **Time-period clustering**: 6 of 10 skins are historical (Renaissance, Greek, Edo, Steampunk, Plague, + Rot's gothic), 4 are speculative-tech (Cyber, Surveillance, Wasteland, Atomic). All keep the exact same information architecture — only material changes.
- **Light skins keep dark text contrast ratios** (vellum/marble/washi/linen ≈ #f0e8d0 family with near-black ink); dark skins pick one luminous accent family and use the soft-tint discipline from light/dark.
- **Radius is the loudest single lever**: 8 of 10 skins flatten to ≤4px or 0; only light/dark/atomic keep pills. Sharp corners + uppercase + serif/mono display = instant "other era".
- **Motion personality is encoded in the *easing function*, not just speed**: `steps(4)` = digital strobe, `cubic-bezier(.34,1.6,.5,1)` = boomerang bounce, `linear` = machine scan, 680ms drift = crypt.
- **The HSL bridge is the cost of Tailwind compat** — every theme declares its palette twice. New work should target the full-color contract only.

## 5. Status in THIS design system

`tokens/colors.css` encodes the two canonical themes (light `:root`, dark `.dark`). The 10 skins are **documented + sourced** (`reference/themes/`) but not yet expressed as token scopes here. To port one: copy its layer-2 block into a `[data-theme="X"]` scope in `tokens/`, map its accents onto the action/node role triplets, and load its Google Fonts (all skin fonts are on Google Fonts — same delivery as production). Visual comparison: `guidelines/theme-matrix.html`.
