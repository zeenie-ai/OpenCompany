# Theme System — design-handoff token contract + 2 themes

The OpenCompany frontend ships **two visual themes**, `light` and `dark`. The design handoff's ten stylized skins are gone: nine (renaissance, greek, edo, steampunk, cyber, wasteland, rot, plague, surveillance) were removed on 2026-10-12, and the last, `atomic` (Atomic Modern), on 2026-11-09. Home, the editor and the sign-in screen all show the chosen theme, and one theme button toggles between the two.

Selected at runtime via `<html data-theme="...">`. The system is purely CSS-variable-driven: components render against semantic token names, and the active `[data-theme="..."]` block in `client/src/themes/` rebinds those tokens to the theme's surface, foreground, accent, typography, geometry, motion, and sound-pack values.

This document is the playbook for working in the design system: token taxonomy, per-component decoration hooks, per-theme sound + canvas packs, migration recipe, anti-patterns, and where each piece lives.

> **Source precedence + token format.** Live `client/src/themes/` CSS is
> authoritative for production values and theme behavior; `client/src/index.css`
> supplies the Tailwind bridge. This document and [Frontend architecture](frontend_architecture.md)
> explain that implementation. The vendored [Design System Bundle](./design-system/IMPLEMENTATION.md)
> contains prototype/reference tokens and components; do not copy its older
> token values over the live themes. **Token colors are `hex` + `color-mix()`** (e.g. `--primary: #2563eb`, `--action-run-soft: color-mix(in srgb, var(--dracula-green) 15%, transparent)`), NOT HSL-triplets. The Tailwind v4 `@theme inline` bridge maps `--color-X: var(--X)` directly (no `hsl()` wrapper); the `/opacity` modifier still works because v4 compiles it to `color-mix`. Action intents additionally expose an `--action-X-ink` readable-text variant (light: a darkened accent; dark: the raw accent) consumed by `ActionButton` via `text-action-X-ink`.

## Architecture at a glance

```
client/src/themes/        (colour VALUES are hex + color-mix(); never HSL triplets)
├── base.css         shared scalars: space / radii / chrome / shadow / type scale, motion
│                    easings, the --tint-* alpha scale (single home for color-mix %), node visuals
├── light.css        :root + :root[data-theme="light"]  — light colour hex: shadcn + dracula
│                    + node + action (+ -ink); defined on bare :root so they apply globally
├── dark.css         .dark + :root[data-theme="dark"]   — dark colour-hex overrides (+ -ink, shadows)
└── animations.css   --pulse-keyframe/-duration/-timing + executing/trigger keyframes
                     + .opencompany-* helpers (.opencompany-pulse / -trigger-armed / -bolt / …)

client/src/index.css       PLUMBING ONLY — holds NO literal colour values:
├── @layer base            global resets only (*, html/body, #root) — token blocks moved to themes/
└── @theme inline { … }    Tailwind v4 bridge mapping --color-X: var(--X) (NO hsl() wrapper);
                           /opacity (bg-primary/50) composes via color-mix for any colour format

client/tailwind.config.js  second colour bridge — theme.extend.colors map var(--X) (no hsl() wrapper)

client/src/contexts/ThemeContext.tsx
├── ThemeName = 'light' | 'dark'; AVAILABLE_THEMES in that order
├── THEME_LABEL — each theme's name wherever one is shown
├── DARK_FAMILY = {dark}
├── persists the chosen theme to localStorage['opencompany-theme']; a saved
│   name that is no longer a theme falls back to the legacy 'darkMode' key,
│   else dark. A saved 'atomic' takes that path (there is no migration) and
│   opens in dark, the first-launch default
├── migrates legacy 'darkMode' boolean on first load
├── sets <html data-theme="..."> + .dark class (only for DARK_FAMILY themes)
├── shows a theme chosen in another window or tab at once (the theme key's
│   storage event), without saving it again, so two windows never echo
├── keeps <meta name="theme-color"> at the theme's --background, the colour
│   the desktop shell paints its window with (desktop_app.md)
└── cycleTheme / nextTheme — step to the next theme, wrapping round

client/src/components/ui/ThemeButton.tsx — the one theme control: the Home header,
                                           the editor's toolbar and the sign-in
                                           screen; each press toggles Light and
                                           Dark, and its icon and label name
                                           the other theme

client/src/hooks/useAppTheme.ts        — the lightColors / darkColors pack (canvas + maps)
client/src/lib/sound.ts                — WebAudio engine, 2 packs × 9 events
client/src/hooks/useSound.ts           — useSoundSync() + useSound()
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
| `font-display` | `--font-display` | Headings, panel titles, action labels |
| `font-body` | `--font-body` | Paragraph + UI copy |
| `font-mono` | `--font-mono` | Code, console, JSON, status bar, kbd |

| Tailwind utility | CSS var | Purpose |
|---|---|---|
| `tracking-[var(--type-tracking-display)]` | `--type-tracking-display` | Letter-spacing for display headings |
| `[text-transform:var(--type-uppercase)]` | `--type-uppercase` | Theme-driven uppercase / capitalize / none |

**Note**: values are hex + `color-mix()`. Tailwind v4 composes `/opacity` via `color-mix` for any colour format, so `bg-bg-app/50` *does* compile and work (to `color-mix(in oklab, var(--bg-app) 50%, transparent)` behind an `@supports` guard) — but opacity arithmetic at call sites is forbidden by the frontend rules; use a named token (`--bg-overlay`, a `-soft`/`-tint` variant) instead.

### 2. shadcn semantic tokens (preserved for primitives)

Standard shadcn keys (`background`, `foreground`, `card`, `popover`, `primary`, `destructive`, `success`, `warning`, `info`, `border`, `input`, `ring`, etc.). Each theme defines them as **hex** matching the new-contract colour, so shadcn primitives + generic chrome resolve against the active theme. `/opacity` works on these too (`bg-primary/10`, `text-foreground/80`) via v4 color-mix.

### 3. Action role tokens

Six semantic intents: `run | stop | save | config | secret | tools`. Each exposes `base / -soft / -hover / -border / -ink` — the colour-hex bases live in light.css/dark.css and the `-soft/-hover/-border` tints are `color-mix` over the base referencing the shared `var(--tint-action-*)` scale. `-ink` is the readable label colour (light: darkened accent; dark: raw accent). Read via `<ActionButton intent="run">` or directly: `bg-action-run-soft text-action-run-ink border-action-run-border`. Themes redefine these (or just override the `--tint-action-*` scalars) in their own block. On Home, in the shared chat, in the editor's top toolbar and in the node parameter panel they mark a state and colour no button ([Colour marks state or role](#colour-marks-state-or-role)).

### 4. Node-type role tokens

Six tokens for canvas identity: `agent / model / skill / tool / trigger / workflow`. Each exposes `base / -soft / -border`. Used on palette icons, parameter-panel section headers, draggable variable cards. **Never use `/N` opacity arithmetic** at call sites — themes own the alpha.

### 5. Dracula raw accents (palette, not consumed directly)

`--dracula-green/purple/pink/cyan/red/orange/yellow/selection/current-line/comment`. Same value across light + dark. Used as the underlying palette that `--action-X` and `--node-X` reference; do not use directly in components.

### 6. Code & syntax tokens

`--code-*` — the per-theme syntax palette for the code editor, console/output JSON viewers, and chat code blocks. Each theme defines its own `--code-*` block in its own CSS file (`client/src/themes/<theme>.css`): editor chrome (`--code-bg`, `--code-gutter-bg`, `--code-gutter-fg`, `--code-caret`, `--code-border`, `--code-line-active`, `--code-selection`) + syntax roles (`--code-text`, `--code-comment`, `--code-keyword`, `--code-string`, `--code-number`, `--code-boolean`, `--code-function`, `--code-property`, `--code-operator`, `--code-punctuation`, `--code-tag`). The per-theme `--code-*` blocks in `client/src/themes/*.css` are the only home of this tier (the vendored bundle ships no `tokens/code.css` — the tier postdates it).

Consumed as `var(--code-*)` in [index.css](../client/src/index.css) (`.code-editor-container`, `.console-json-output`, and in chat markdown `.chat-markdown pre`, whose surface, text and border are `--code-bg` / `--code-text` / `--code-border` on every theme; a reply's code block is framed in `.chat-markdown .chat-code` instead, with the same three on the frame, the fence's language and Copy on `--bg-panel` above and the line numbers in `--fg-faint`, at `max(0.8125em, var(--text-2xs))` (0.8125em of the reply text, never below `--text-2xs`) like the table frame `.chat-table`; plus the `.chat-markdown .token.*` colours shared with the editor) and exposed as Tailwind utilities (`text-code-tag`, `bg-code-bg`, …) via the `@theme inline` bridge; the `OutputPanel` `@uiw/react-json-view` viewer reads the same vars. This **replaced the old global dracula-hardcoded `--prism-*` block and the dead `getPrismTokenCSS()` helper** — code/JSON now paints in each theme's palette instead of one dracula scheme everywhere. (`prismjs` is still the tokenizer; only the colours moved to `--code-*`.)

### Home (Normal mode) tokens

Normal mode ([normal_mode.md](./normal_mode.md)) adds its own steps, all in
[base.css](../client/src/themes/base.css) unless noted, and bridged in
`index.css` where a Tailwind utility needs them (the layout and duration
tokens are used directly, as `w-(--w-home-sidebar)` or `duration-(--dur-slow)`):

- **Motion**: `--ease-spring | overshoot | reveal | shake` and the choreography
  durations (`--dur-intro`, `--dur-view-swap`, `--dur-panel-in/-out`,
  `--dur-toast-in/-hold/-out`, `--dur-mode-in/-out`, `--dur-theme-reveal`,
  `--dur-glow`, `--dur-pip-loop`, `--dur-dock-in` for the Workspace dock,
  `--dur-shake`, …). Web Animations read them through
  [lib/motion.ts](../client/src/lib/motion.ts), whose fallbacks mirror
  base.css. It also holds the onboarding presets: `RISE` / `rise(el)` (up from
  16px, scale .98, a 4px blur, `card-in` on the spring curve) and `shake(el)`
  (-6, 5, -3, 2px, each swing on `--ease-shake`). The CSS loops beside them in
  `animations.css` are `.opencompany-caret-blink` (a hard on/off caret) and
  `.opencompany-spinner-slow` (the `--dur-live-ring` pace), drawn as a ring by
  `components/ui/ring-spinner.tsx`; both stop under reduced motion.
- **Radii**: `--radius-row | card | panel | draft | composer`, multiples of each
  theme's `--radius-lg`, so square themes stay square. The Tailwind
  `rounded-sm/md/lg/xl` utilities do not follow themes: `index.css` computes
  them from `--radius`, which no theme overrides, so they are 4/6/8/12px
  everywhere. `rounded-pill`, these Home radii and CSS reading
  `var(--radius-*)` directly do follow the theme.
- **Type and layout**: `--text-meta | row | lead | title | headline | hero` (with
  `--tracking-hero`, `--leading-hero`; `headline` is 28px, the onboarding
  headings), and the Home layout constants (`--w-home-sidebar`,
  `--h-home-header`, `--w-composer`, the orb slot sizes `--size-orb-hire |
  employee | connecting | login`, the Settings, Welcome guide and AI models
  dialog sizes `--w-settings`/`--h-settings`, `--w-guide`/`--h-guide` and
  `--w-ai-models`/`--h-ai-models`, the last 780 by 600). The guide's demo
  stage is the `.home-welcome-stage` class in `index.css` (two washes of
  `--node-agent-fill` and `--node-model-soft` over `--bg-app`).
- **The message box**: `--h-chat-composer-max` (240px) is how tall the chat's
  message box (on Home and in Dev's console chat pane) and the box that edits
  one of the owner's messages grow before they scroll. `--leading-message`
  (1.55), bridged in `index.css` as `leading-message`, is the line height of
  Home's message box, which is at least 64px tall, of the owner's bubble and
  of its edit box; Dev's compact chat keeps `leading-normal`.
- **Status**: `--status-{working,ready,paused,attention,waiting}-{dot,fill,border,ink}`
  for the employee pills and dots. The dot is the raw role colour; light.css
  keeps the paused dot at the design's mid grey. `--shadow-pip`
  (`shadow-pip`) is a dot's glow in its own colour: give the dot a
  `text-status-*-dot` colour and `bg-current`. Live, the Workspace's
  status while an employee works, has no status token of its own: it is
  red, `--destructive`, for its blinking dot (the dock's status dot and
  the header Workspace button's dot) and for the dock's tab activity
  dots, on Home and in the editor, which show the same dock.
- **The employee's face**: `--accent-edge`, the rim (`--accent` mixed toward
  `--fg-default` at `--tint-accent-edge`, 70%), and `--shadow-avatar`, a soft
  highlight along the top edge, bridged in `index.css` as `border-accent-edge`
  and `shadow-avatar` (`lib/utils.ts` tells tailwind-merge that
  `shadow-avatar` is a shadow). `ACCENT_AVATAR_CLASS`
  ([components/catalog/presentation.ts](../client/src/components/catalog/presentation.ts))
  puts them on a solid `--accent` disc with the `--accent-foreground` initial:
  the employee beside their turns (`ChatAvatar`, on Home and in Dev), on their
  page's header and on a new hire's first-day strip (`Avatar` with `accent`).
  The team's lists keep the role tints (`AVATAR_CLASS`). A draft on the
  setup card and the owner's own avatar are plain (`Avatar` with `plain`:
  `bg-bg-hover`, `border-border-default`, `text-fg-default`), and nothing
  else on the setup card takes a role's colour either.
- **Errors**: `--danger-soft` (15%) and `--danger-border` (45%) tint
  `--destructive` for an error on a Home surface (`bg-danger-soft`,
  `border-danger-border`).
- **Per family** (light.css / dark.css): `--shadow-float | popover | dialog`
  (the Workspace dock has no shadow of its own: lying over the page below
  1100px, it casts `--shadow-modal`),
  the node-role `-fill / -edge / -hover / -ink` variants, and Home's glows:
  `--glow-connect | hire | hire-settle | refine | refine-out | task` plus the
  working pip's `--tint-pip-ring`. Dark keeps the neon; light swaps each neon
  for its ink at .75 of the alpha and .6 of the blur, the design's rule for
  white surfaces. The Web Animations that play them name the token
  (`boxShadow: 'var(--glow-hire)'`), so the colour follows the theme with no
  script. dark.css restates each one, because light.css's bare `:root` also
  matches the dark family.

Home shows the chosen theme, as the editor and the sign-in screen do (owner
decision, 2026-10-12).
`setTheme(next, { reveal: true, origin })`
grows the new theme as a circle from `origin` (View Transitions), falls back
to a short colour fade, and is instant under reduced motion.

### Colour marks state or role

On Home, in the shared chat (`features/chat`, so Dev's console chat pane
too) and in the Workspace dock, which is the same in Dev mode, colour marks
a state (a status dot or pill, a trend) or a role (an
avatar or node-role tint), and a control never borrows an action intent's
colour for an action it does not mean. This is the owner's rule, from the chat
colour pass of 4 Nov (`design_handoff_opencompany_home/chat/V2.md`, section 3)
and the onboarding v3 handoff:

- **No green buttons** (since 2026-11-10; until then green `run` started an
  employee): Start, Start again and Resume are the `invert` primary
  (`features/home/employee/PrimaryActionButton.tsx`, and the Workspace's
  round Resume). Their other main actions (Stop, Retry reset, connecting
  what is missing) are neutral.
- **One primary action per screen or card** may be `Button variant="invert"`
  (the foreground as the fill): Create on the hire view, Hire on the setup
  card, Send in the message box (Stop or Resume while a run holds it) and Use
  dictation, Send or Allow on an approval card, a generated interface's
  primary button, Connect on the AI models dialog's plain page, Turn on Talk,
  Save changes in Settings > Profile, Add in the skill form and in the
  custom connector form, Hand back in the Workspace's "You’re in control"
  banner (it resumes the employee when Take over stopped them), and Set up
  phone on the phone's setup card.
- **Every other button is neutral**: `variant="quiet"`, with
  `border-border-default` (or `border-border-strong`) and `text-fg-default`
  where it is drawn as an outline, written per call site, as a module constant
  where a file repeats it. Not `variant="outline"`: its `dark:` rules beat the
  caller's classes in dark mode.
- **Switches use `tone="ink"`** (`components/ui/switch.tsx`), and a picked chip
  is neutral: the Toggle `chips` variant's picked state is `bg-bg-active` with
  `border-border-strong`, and a picked starter chip is a raised surface with a
  foreground border.
- **Status colours stay**: the Ask first chip, an approval card's waiting
  frame, header and pill, and the Workspace's takeover frame and banner
  (config); the Web chip and the source and citation numbers (save); an
  approval card's Sending and Sent pill and its dot (run) and a failure's
  frame, header and footer (stop); live dots (red) and the
  streaming caret; in the chat a done step's tick and the copied tick; the hire view's
  pip, green only while someone is working; a stat's delta and a chart's
  bars.
- **The setup card and the Get started checklist have no colour** (since
  2026-11-10): on a new employee's setup card the routine's dots and labels
  are neutral (`features/home/ui/routine.tsx`), a `tone` in the generated
  spec is not drawn (`features/home/genui/views.tsx`), the draft's face is a
  team portrait in a plain ring, and the composer's "Editing the draft" is
  muted text. The checklist's bar and a done step's disc are ink, and so is
  a finished step's icon in the Welcome guide's list.
- **The Welcome guide follows the rule too** (since 2026-11-10): Next and
  Connect an AI model are `invert`, and its labels and notice are neutral.
  Only the demo illustration (`WelcomeDemo`) keeps its own colours, as a
  picture of the product. The Normal/Dev switch is a neutral segmented
  control for the same reason.
- **So does the editor's top toolbar** (since 2026-11-10,
  `components/ui/TopToolbar.tsx`), and it has no green: Start and Resume are
  its `invert` primary, and only the save dot carries a colour, for its state
  (the "Saved" / "Modified" label beside it is neutral). Every other control
  (the File menu and its items, Apply All, Settings, Credentials, the theme
  button, Logout, Stop, Reset, Save, the labels) is a neutral outline, and
  the sidebar, Components and Workspace toggles fill with `bg-bg-active`
  while their panel shows.
- **So does the node parameter panel** (since 2026-11-11: `ParameterPanel.tsx`,
  `LocationParameterPanel.tsx` and the views under
  `components/parameterPanel/`). In its header Run and Cancel are neutral
  outlines in the toolbar's shape and Save is the one `invert` primary,
  greyed until there is something to save. Inside, Load, Upload, Add,
  Reset, Clear and Delete are neutral, and each view has at most one
  `invert` button (Save in the skill editor and the Memory form, Add mount,
  Use this file, Finish team and Accept in the Task Manager). Hovers are
  `bg-bg-hover` and a picked row is `bg-bg-active`, never the violet
  `--accent` or the blue `--primary`. What still carries a colour marks
  something: a `{{node.field}}` variable (in the Input section and in the
  field it is dropped on), an error and a todo's status. The Canvas
  board's kind tiles (`canvas/CanvasLibrary.tsx`, shared with the
  Workspace dock) are neutral like a reply's document card, and the item
  on show has a foreground border.
- **A state in the node panels is a dot beside neutral words**
  (`components/parameterPanel/StatePill.tsx`, on the status tokens), and
  only work under way pulses. A team task (`taskManager/TaskStatus.tsx`,
  in the Task Manager and the Team Monitor): running is working, queued is
  ready, submitted is waiting (the lead must review it), failed is
  attention, accepted is a tick in ink, and blocked or cancelled is
  paused. A teammate: working, idle is ready, connected is paused. A
  process: running is working, error is attention, stopped is paused.
  Counts are ink; the Task Manager's are the neutral `chips`.
- **The Gallery takes no colour** (since 2026-11-11,
  `components/parameterPanel/gallery/`): a file's kind is its glyph's
  shape, in `text-fg-muted` (`fileIcons.tsx`); the grid or list switch is
  the `segmented` ToggleGroup; a pin is a `Toggle`, filled while pinned;
  and the file picked in Choose from workspace has a foreground border.
- **`<ActionButton intent="...">` is left only in the credentials panels
  and the console's chat pane.**

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
| `<ActionButton intent="...">` | `Button variant="quiet"` with a neutral border, or `invert` for the one primary | Everywhere but the credentials panels and the console's chat pane, which still use it ([Colour marks state or role](#colour-marks-state-or-role)) |

### Display-typography helper

Heading surfaces under every theme use the same triplet. Keep them grouped in the same className for clarity:

```tsx
<span className="font-display tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
  {heading}
</span>
```

Result by theme (display font / tracking / case):
- **Light + Dark** — Geist Variable / 0 / `none`

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
3. Add the name to `ThemeName`, `AVAILABLE_THEMES` (the theme button steps through it in order) and `THEME_LABEL` in [client/src/contexts/ThemeContext.tsx](../client/src/contexts/ThemeContext.tsx). Add to `DARK_FAMILY` if the theme's background is dark, and add the name to the pre-paint lists in [client/index.html](../client/index.html) (`themePrePaint.test.ts` checks them).
4. Give the theme an icon in `ICON` in [client/src/components/ui/ThemeButton.tsx](../client/src/components/ui/ThemeButton.tsx); the button shows the next theme's icon.
5. Nothing else lists themes: Settings → Appearance, the command palette and the status bar read `AVAILABLE_THEMES` and `THEME_LABEL`.
6. **(Canvas)** [client/src/hooks/useAppTheme.ts](../client/src/hooks/useAppTheme.ts) gives canvas components `lightColors` or `darkColors`, chosen by `DARK_FAMILY` (through `useTheme().isDarkMode`); there is no per-theme overlay.
7. **(Optional, fonts)** Light and dark use the bundled Geist and the system mono, and nothing loads from Google Fonts, so a theme that needs a font loads it in [client/index.html](../client/index.html).
8. **(Optional, sound)** Pick one of the existing `SoundPackName` values (`parchment`, `terminal`) in `--sound-pack` (or add a new pack to [client/src/lib/sound.ts](../client/src/lib/sound.ts) and the `VALID_PACKS` set in [client/src/hooks/useSound.ts](../client/src/hooks/useSound.ts)).
9. **(Optional, motion)** The theme inherits the default `node-pulse` executing glow and the `.opencompany-trigger-armed` trigger heartbeat automatically. For a bespoke executing pulse, add a `[data-theme="X"]` block in [client/src/themes/animations.css](../client/src/themes/animations.css) overriding `--pulse-keyframe` / `--pulse-duration` / `--pulse-timing` and define that keyframe there. Set the glow accent via `--node-pulse-color` in the theme's own block.

**No component code changes.** That is the contract.

## Anti-patterns (forbidden)

1. **Theme-conditional logic in components.** No `if (theme === 'dark')` branches. Visual differences live in the per-theme CSS file.
2. **Whole-store Zustand destructure.** Always `useAppStore((s) => s.x)`, never `{ x } = useAppStore()`.
3. **Ad-hoc opacity arithmetic on role/surface tokens.** Don't inline alpha (`bg-bg-app/10`, `bg-node-agent/30`, `bg-action-run/25`). Tailwind v4 *can* now color-mix `/opacity` on hex tokens, but the discipline is to reference a NAMED token: add/define a `-soft` / `-hover` / `-border` variant, or a `--tint-*` step in base.css, and use it by name.
4. **Hardcoded colours.** No `bg-white`, `bg-black`, `text-gray-500`, `style={{ backgroundColor: '#fff' }}`. Use tokens.
5. **`useAppTheme()` in new files.** Allowed only for surfaces that need JS-side hex values Tailwind can't express: canvas node components (`AIAgentNode`, `SquareNode`, `TriggerNode`, `StartNode`), `EdgeConditionEditor`, and the Google Maps SDK consumers (`GoogleMapsPicker`, `MapsPreviewPanel`) — note the map
tiles themselves are deliberately left to Google's own styling; `useAppTheme` here covers only
the surrounding chrome. Every other surface uses Tailwind + tokens. Call sites read the colour pack through `useAppTheme`, never by importing `lightColors` / `darkColors` directly.
6. **Hand-rolled modal backdrops.** Use `<Modal>` from `client/src/components/ui/Modal.tsx`.
7. **Hand-rolled colored buttons.** `<ActionButton intent="...">` is left only in the credentials panels and the console's chat pane. Everywhere else (Home, the shared chat, the editor's top toolbar, the node parameter panel), follow [Colour marks state or role](#colour-marks-state-or-role): an `invert` primary (Start and Resume included), neutral `quiet` buttons (with a border where they are drawn as an outline), and no button in an intent's colour.
8. **Static fallback theme palettes in TS.** Themes live in CSS (`client/src/themes/<name>.css`), not TS exports. `client/src/styles/theme.ts` ships only the two `lightColors` / `darkColors` base packs that `useAppTheme` returns — it is not a place to add a third / fourth / Nth palette.

## What's done vs deferred

### Done
- **`--node-pulse-color` contract** — executing-node glow color is independent of `--node-color` (the plugin accent). Light and dark deliberately keep the base.css `var(--info)` fallback; a theme may set its own accent so the glow stays visible against its canvas. Defined in [base.css](../client/src/themes/base.css) at `:root`, overridden in the theme's own file. The executing keyframe (`node-pulse`) and the `--pulse-keyframe` / `--pulse-duration` / `--pulse-timing` tokens live in [animations.css](../client/src/themes/animations.css); base.css's `.sq-node[data-executing] .sq-node-box` / `.react-flow__node.executing .{sq-node-box,node}` rule reads `var(--pulse-keyframe, node-pulse)`, so a theme can pick its own pulse by overriding ONE token (the removed Cyber, Surveillance and Renaissance skins did; no remaining theme does). `node-pulse` is a triple-layer expanding box-shadow (12px ring + 32px mid halo + 56px outer halo) consuming `var(--node-pulse-color)` + the shared `--tint-pulse-*` alphas. Trigger nodes use a separate continuous `.opencompany-trigger-armed` "listening" heartbeat (`trigger-listening` keyframe) while waiting, plus `.opencompany-bolt` on the ⚡ badge — distinct from the one-shot execution pulse. **Never animate `opacity` on whole-node selectors** — it fades the node icon + content, not just the glow.
- **`data-page-hidden` animation pause** — `usePageActivitySync()` ([app/usePageActivitySync.ts](../client/src/app/usePageActivitySync.ts)), mounted once in [App.tsx](../client/src/App.tsx), above the sign-in gate, so it covers every screen (sign-in and Connecting included), listens to `visibilitychange` alone and toggles `data-page-hidden` on `<html>` (Chrome reports a minimised or fully covered window as hidden too; a visible window that is only unfocused, such as the app beside an editor, keeps animating); [base.css](../client/src/themes/base.css) declares `html[data-page-hidden] *, *::before, *::after { animation-play-state: paused !important; }`. Without this, paused CSS keyframes accumulate frames in the compositor queue while the tab is hidden; on tab return all 50+ executing nodes' triple-layer pulses resume simultaneously, stalling the GPU compositor 100-200ms and blocking input dispatch (first-click-feels-frozen pattern). The unpause is deferred two `requestAnimationFrame` ticks so input dispatch wins the frame before composite resumes.
- **Parameter panel theme contract** — MasterSkillEditor, OutputPanel, MiddleSection, ParameterPanel removed `useAppTheme()`. Every surface renders against Tailwind tokens + new-contract CSS custom props. Section headers carry the display-typography triplet (`font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)] text-fg-default`); its buttons are neutral since 2026-11-11, with Save as the one `invert` primary ([Colour marks state or role](#colour-marks-state-or-role)); backgrounds use `bg-bg-elevated` / `bg-bg-panel` / `bg-bg-input` from the surface tier table above. EditableNodeLabel emits both `sq-node-label` and `node-label` classNames so per-theme typography rules fire under either topology. Scrollbar webkit rules (`::-webkit-scrollbar-thumb` etc.): the global token rules in index.css for every theme.
- **Canvas-node class topology alignment** (W26) — TriggerNode, StartNode, ToolkitNode migrated from `.node` (rectangular spec-card) topology to `.sq-node` / `.sq-node-box` (square-icon node) topology so per-theme decorations reach them. Status pips, gear buttons, and React Flow handles on every node component now carry `.sq-node-pip` / `.sq-node-gear` / `.sq-node-handle.in/.out` (square nodes) or `.node-pip` / `.node-gear` / `.node-handle.in/.out` (rectangular nodes — AIAgentNode, TeamMonitorNode; the former `GenericNode` was retired with the spec-driven canvas). Pip background is data-driven via `data-status="idle | executing | waiting | success | error"` — base.css picks the colour from shadcn semantic tokens; per-theme files override. Inline JS-computed `backgroundColor` / `border` / `animation` on these elements stripped — CSS owns visuals. Orphan `.node-output` rule removed. EditableNodeLabel now emits both `sq-node-label` and `node-label` classNames so per-theme typography rules fire. StartNode reads `nodeColor` from `definition?.defaults?.color` instead of hardcoded `theme.dracula.cyan`; the NodeSpec adapter never fills that field, so it resolves to `var(--node-workflow)`.
- **Audio fix** (W20) — `--sound-pack: chime` typo in `light.css` + `dark.css` was silently falling through to `'none'` (`chime` not in the engine's pack registry), making sound silent for the most-used themes. Renamed `light` → `parchment`, `dark` → `terminal`. Added `Sounds.unlock()` exported from [lib/sound.ts](../client/src/lib/sound.ts) plus a one-shot `pointerdown / keydown / touchstart` capture-phase listener in `useSoundSync()` ([hooks/useSound.ts](../client/src/hooks/useSound.ts)) so the AudioContext resumes on the user's first gesture (Chrome / Safari autoplay policy compliance).
- **Canvas-node visual contract** (W21) — every node component (`AIAgentNode`, `SquareNode`, `TriggerNode`, `StartNode`, `ToolkitNode`, `TeamMonitorNode`; `GenericNode` at the time, since retired) stripped its inline `background` / `border` / `borderRadius` / `boxShadow` / `animation` props. Each now exposes its accent via `style={{ '--node-color': accentColor }}` on its outer wrapper. Visual styling lives in [base.css](../client/src/themes/base.css) defaults + per-theme overrides; `var(--node-color)` carries that accent through CSS without fighting specificity. Only `AIAgentNode` passes the plugin's own colour (NodeSpec `color`); the other node components pass their role token (see "Canvas-node visual contract" below). New `NodeStyle` helper type in [client/src/types/NodeTypes.ts](../client/src/types/NodeTypes.ts) (`React.CSSProperties & Record<\`--${string}\`, string | number>`) makes the inline custom-prop typecheck-clean.
- **Icon size is a token** — `NodeIcon` takes `size=` (`theme.nodeSize.squareIcon`, 36px, on every canvas node; a `theme.iconSize.*` step elsewhere) and applies it as width, height and emoji font size together. Sizing classes are never passed: `--text-3xl` is 44px in `base.css`, so a `text-3xl` emoji once dwarfed every SVG-backed node next to it.
- **Per-theme icons** (W23) — removed. `themedGlyphs.ts` (Atomic's 29 SVG glyphs, ported from `design_handoff_machinaos_themes/app/icons.js`) and the themed-glyph branch of [NodeIcon.tsx](../client/src/assets/icons/NodeIcon.tsx) went on 2026-11-09 with Atomic, the last theme with a set (the other nine skins' sets went on 2026-10-12). `NodeIcon` resolves every icon through its `lucide-react` / `lobehub:` / `asset:` dispatch in both themes.
- **Editor dot grid** — the Dev canvas renders React Flow's dots `<Background>` (Dashboard.tsx), filled with `--grid-dot` (index.css). base.css declares it `transparent`; light.css and dark.css set it, in blocks that match `[data-theme="light"]` / `[data-theme="dark"]` alone (`--fg-muted` at 20% / 22%). The same light-only block sets `color-scheme: light` (dark.css already sets `dark`). Locked by `themes/__tests__/themeTokens.test.ts`.
- **Scrollbars** — the global rules in index.css read `--scrollbar-track` / `--scrollbar-thumb` (hover `--fg-faint`), which every theme declares.
- **Canvas grid + cursors** (W24) — `--canvas-grid` and `--cursor-default` slots declared in [base.css](../client/src/themes/base.css) and bound to `.canvas-host`. Light and dark draw no `--canvas-grid` and keep the `auto` cursor. Atomic's mid-century starburst, the last `--canvas-grid` decoration, went with it on 2026-11-09; the removed Cyber, Surveillance and Renaissance skins set the custom cursors.
- **Decorative HTML primitives** (W25) — removed. `SvgFilterDefs` (the `#ink-blot` / `#noise` / `#crt` SVG filters) went on 2026-10-12 with the skins it was meant for; no CSS read its filters. The `DropCap` wrapper planned alongside it was never retained. The bare `.v-display` display-typography co-class remains as a hook; no theme styles it (Atomic did until 2026-11-09).
- **Token contract**: the two themes in [client/src/themes/](../client/src/themes) (light and dark), in four CSS files (base + animations + the two themes). Of the ten skins ported from the handoff, nine went on 2026-10-12 and the last, atomic, on 2026-11-09.
- **ThemeProvider** ([client/src/contexts/ThemeContext.tsx](../client/src/contexts/ThemeContext.tsx)) — two-way `ThemeName`, `DARK_FAMILY` = `{dark}`, `cycleTheme`, legacy `darkMode` localStorage migration on first load
- **ThemeButton** — one button in both modes and on the sign-in screen, toggling Light and Dark. On 2026-10-12 it replaced the editor's grouped theme menu and Home's light/dark toggle.
- **Tailwind v4 `@theme inline`** exposing new-contract utilities (`bg-bg-app`, `text-fg-default`, `border-border-default`, `font-display`, `font-body`, `rounded-pill`)
- **Decorative wrappers** — the React Flow host carries `canvas-host` (aliased as `canvas` so handoff selectors resolve), where base.css paints `--canvas-grid`. The `app-frame` (app shell) and `modal-frame` (Modal) hooks went with the removed skins, the only CSS that styled them.
- **Per-component structural classNames** (W15) — every chrome / interactive / canvas component carries the handoff selector co-class so per-theme decorative rules resolve without component-level branching. See [Per-component decorations](#per-component-decorations) for the full list.
- **Per-theme decorative CSS** (W16) — every per-component rule from `design_handoff_machinaos_themes/themes/*.css` was ported into `client/src/themes/<theme>.css`. Each skin owned its panel textures, canvas decorations, node pseudo-element overlays, and keyframes. CSS bundle: 145 KB → 244 KB (+99 KB); removing nine of the ten skins on 2026-10-12 took the main CSS bundle from 339 kB to 217 kB, and the last, Atomic, went on 2026-11-09. light.css and dark.css add no decorative rules; the hook classes stay as the theme contract (see [Per-component decorations](#per-component-decorations)).
- **Selection state** (W17, shipped as part of W15) — every canvas node component carries the `selected` co-class when React Flow's `selected` prop is true, activating per-theme selection effects.
- **Sound event wiring** (W18) — 9 events (`click`, `hover`, `type`, `success`, `error`, `run`, `save`, `modalOpen`, `modalClose`) fire automatically across the React tree. See [Sound system → Event wiring](#event-wiring-w18).
- **Success is a moment, not a state** — terminal node statuses persist in `nodeStatusStore` until the next run, so the static `--success` ring/stroke on `.react-flow__node.completed` (base.css) and `.react-flow__edge.completed` (styles/canvasAnimations.ts) previously left every completed agent/tool node and traversed edge glowing green indefinitely. Both are now one-shot fades (`node-completed-fade` in animations.css / `edgeCompletedFade`): hold the success token briefly, then settle back to the resting border/stroke. `prefers-reduced-motion` keeps the static ring (legible outcome, no motion). Error states deliberately stay painted until acted on.
- **Reduced-motion accessibility** (W19) — `@media (prefers-reduced-motion: reduce)` block in [client/src/themes/base.css](../client/src/themes/base.css) disables every keyframe across all themes. See [Reduced motion](#reduced-motion).
- **Sound throttling** (W19) — `type` and `hover` events throttled to a 30 ms last-fire window inside the engine to prevent OscillatorNode flooding. See [Throttling](#throttling).
- **Migrated chrome**: TopToolbar, WorkflowSidebar, ComponentPalette + ComponentItem + CollapsibleSection, ConsolePanel chrome, SettingsPanel, Modal, ParameterPanel modal title, AIResultModal title, InputSection title
- **New shell components**: StatusBar (fixed-bottom system line with WS connection / workflow / theme / clock), CommandPalette (`⌘K`), CommandPaletteHost (canonical command list with Workflow / Run / Open / View / Theme groups)
- **Google Fonts** — none load: light and dark use the bundled Geist and the system mono. The deferred-load `<link>` in [client/index.html](../client/index.html) loaded seventeen families for the twelve themes until 2026-10-12, then Atomic's three (Bevan, Lato, Space Mono), and went with Atomic on 2026-11-09.
- **Sound contract** — [client/src/lib/sound.ts](../client/src/lib/sound.ts) ports the upstream WebAudio engine with the packs the themes use (`parchment` for light, `terminal` for dark; `vibraphone` went with Atomic on 2026-11-09, and the other seven with the skins removed on 2026-10-12). [client/src/hooks/useSound.ts](../client/src/hooks/useSound.ts) reads `--sound-pack` from `:root` on theme change and mirrors the `soundEnabled` Zustand slice into `Sounds.setEnabled()`. Settings panel ships the toggle (Audio section). Persists to `localStorage['opencompany-sound']`; **default ON** (browsers gesture-gate WebAudio without a separate permission API — the AudioContext starts suspended and resumes on the user's first pointerdown / keydown / touchstart via `Sounds.unlock()`, registered as a one-shot capture-phase listener in `useSoundSync()`).
- **Canvas colour packs** — [client/src/hooks/useAppTheme.ts](../client/src/hooks/useAppTheme.ts) returns `lightColors` or `darkColors` as `theme.colors`, chosen by the theme's family, with no per-theme overlay (see [Canvas colour packs](#canvas-colour-packs-useapptheme)). Existing call sites continue to read `theme.colors.X` and `theme.isDarkMode` unchanged.

### Deferred (future PRs)
- **`--*-soft` token family** — RESOLVED (theme fidelity pass): `--info-soft` / `--success-soft` / `--warning-soft` / `--danger-soft` (plus the `--danger` alias for `--destructive`) are now declared in `base.css` `:root` as `color-mix(in srgb, var(--X) var(--tint-semantic-soft), transparent)` with `--tint-semantic-soft: 15%` on the shared tint scale. Rules that read them render their intended tinted fills instead of computing to invalid-and-transparent. Themes may override any of them.
- **Parameter panel internals** — RESOLVED: `ParameterRenderer`, `MiddleSection`, `MasterSkillEditor` are fully on shadcn primitives + tokens (zero `useAppTheme` / `theme.colors`); section headers carry the display-typography triplet. The panel modal also picks up per-theme `.modal` / `.modal-head` / `.modal-title` decoration via `Modal.tsx`.
- **Credentials modal sub-panels** — `OAuthPanel`, `EmailPanel`, `ApiKeyPanel`, `QrPairingPanel` headers same pattern.

## Per-component decorations

Wave 15 added structural classNames across the React tree so the handoff CSS selectors resolve without component-level branching. Wave 16 ported the matching decorative rules from `design_handoff_machinaos_themes/themes/*.css` into `client/src/themes/<theme>.css`; nine skins' rules went on 2026-10-12 and Atomic's, the last, on 2026-11-09. light.css and dark.css add no rules of their own to these hooks; the classes stay as the theme contract.

### Class registry

| Surface | className(s) | Component |
|---|---|---|
| Top toolbar | `.toolbar` | TopToolbar |
| Sidebar | `.sidebar` | WorkflowSidebar |
| Component palette | `.palette` | ComponentPalette |
| Console / chat panel | `.chat` | ConsolePanel (root) |
| Chat turns (Home and Dev) | `.chat-msg .chat-msg-user` on the owner's bubble; `.chat-msg .chat-msg-bot` on a reply's text only; `.chat-turn-user`, `.chat-turn-bot` on each turn; `.chat-composer` on the message box; `.chat-approval` on a draft's card; `.chat-genui` on a generated interface | features/chat (`UserTurn`, `AssistantTurn`, `Composer`, `approval/ApprovalCard`, `turns/GeneratedUiBlock`) |
| Reply code block and table | `.chat-code` and `.chat-table`, the frames inside `.chat-markdown` (styled in `index.css`) | features/chat (`markdown/ReplyMarkdown`) |
| Status bar | `.statusbar`, `.pip` (connection dot) | StatusBar |
| Modal | `.modal`, `.modal-head` | Modal |
| Collapsible section | `.cat`, `.cat-head`, `.cat-body` | CollapsibleSection |
| Workflow card | `.wf-card` | WorkflowCard |
| Palette tile | `.comp` | ComponentItem |
| Interactive list rows | `.row` | WorkflowSidebar / ComponentItem |
| shadcn Button | `.btn` (CVA base) | button.tsx |
| ActionButton | `.action-btn .btn` (CVA base) | action-button.tsx |
| Form field | `.input` | input.tsx, textarea.tsx |
| Dropdown menu | `.menu-pop` (Content), `.menu-pop-item` (Item) | dropdown-menu.tsx |
| Canvas host | `.canvas-host`, `.canvas` (alias) | Dashboard |
| Generic canvas node | `.node`, `.selected` | AIAgentNode, GenericNode, TriggerNode, StartNode |
| Agent node variant | `.node-agent` | AIAgentNode |
| Trigger node variant | `.node-trigger` | TriggerNode |
| Square canvas node | `.sq-node`, `.sq-node-box`, `.sq-node-pip`, `.sq-node-gear`, `.sq-node-handle.in`, `.sq-node-handle.out`, `.selected` | SquareNode |

The `.selected` co-class is bound to React Flow's `selected` prop on every canvas node component, activating per-theme selection effects.

A reply has no bubble (design handoff chat): only the owner's message is one. `.chat-msg-bot` stays on the reply's text as a hook.

### Decorative content per theme

None: light and dark add no panel textures, canvas decoration, node pseudo-element overlays or keyframes.

Token reads use `var(--token)` directly (no `hsl()` wrapper) — every colour value is hex / `color-mix()`, including the shadcn-bridged tokens.

## Canvas-node visual contract (W21)

Every canvas-node React component renders against pure CSS for visual styling. The component is responsible for:

1. **Setting `--node-color` inline** on its outer wrapper:
   ```tsx
   <div
     className={`node node-agent ${selected ? 'selected' : ''}`}
     style={{ '--node-color': accentColor, /* layout only */ } as NodeStyle}
   >
   ```
   In `AIAgentNode`, `accentColor` is the plugin's colour from `useNodeSpec(type)` (NodeSpec `color`, from the plugin's `meta.json` or `visuals.json`). `SquareNode`, `TriggerNode` and `StartNode` use `definition?.defaults?.color`, which `nodeSpecToDescription` never fills, so they always fall back to their role token: `var(--node-model)`, `var(--node-trigger)` and `var(--node-workflow)` respectively. Plugin colours therefore do not reach those nodes today.

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
| Square-icon | `.sq-node` | `.sq-node-box` | `.sq-node-pip` | `.sq-node-gear` | `.sq-node-handle.in` / `.sq-node-handle.out` | `.sq-node-label` | SquareNode, TriggerNode, StartNode |
| Rectangular | `.node` (+ optional `.node-agent` / `.node-trigger` co-class for type-specific theming) | (none — single-div card) | `.node-pip` | `.node-gear` | `.node-handle.in` / `.node-handle.out` | `.node-label` | AIAgentNode (GenericNode was retired) |

Both families share the same status-pip data contract:

```tsx
<div className="sq-node-pip" data-status={pipStatus} />  // or className="node-pip"
```

`pipStatus` is a string from the bucket `'idle' | 'executing' | 'waiting' | 'success' | 'error'`. base.css colors the pip per-status using `var(--success)` / `var(--destructive)` / `var(--node-color)` etc. Per-theme files override for material-specific identity.

Execution-state animation: `data-executing={isExecuting ? '' : undefined}` on the outer wrapper. base.css binds `.sq-node[data-executing] .sq-node-box` and `.react-flow__node.executing .node` to `var(--pulse-keyframe, node-pulse)` (the theme-selected pulse; keyframes + per-theme `--pulse-keyframe` live in `animations.css`). Trigger nodes apply `.opencompany-trigger-armed` (continuous listening heartbeat) on the `waiting` state instead — distinct from the execution pulse — plus `.opencompany-bolt` on the ⚡ badge.

Selection state: `selected` co-class on the outer wrapper, driven by React Flow's `selected` prop.

Type-specific co-classes (`.node-agent`, `.node-trigger`) are hooks no theme styles today (Cyber, which coloured them, was removed); a theme that wants per-type colours can target them.

## Canvas-wide edge + node status animations (`canvasAnimations.ts`)

[client/src/styles/canvasAnimations.ts](../client/src/styles/canvasAnimations.ts) owns the `@keyframes` + `.react-flow__edge.{status}` rules injected once into Dashboard's `<style>` tag. It is the single home for canvas-wide rules that need to match React Flow's wrapper classes (per-node inline animations live in their own components and read theme tokens directly — see the visual contract above).

**Fully static since the design-handoff edge migration.** `buildCanvasStyles()` takes no arguments and the stylesheet is built once at module scope in Dashboard — every colour, width, and dash rhythm is a CSS token, so theme switches restyle it via variable resolution, never a rebuild. The old `CanvasStatusColors` interface (per-theme hex packs fed from `useAppTheme`) was deleted; **do not reintroduce it — add a token instead**.

**Edge visual contract (design-handoff):**

- **Resting edges** are one pale neutral: `stroke: var(--edge-stroke)` (a `--fg-default` mix at `--tint-edge`, default 62%), `stroke-width: var(--edge-stroke-width)` (1.5px), `stroke-dasharray: var(--edge-dash)` (5 5), orthogonal `step` geometry (`defaultEdgeOptions.type: 'step'`; `ConditionalEdge` uses `getSmoothStepPath({borderRadius: 0})`). The built-in `smoothstep` name is remapped to React Flow's `StepEdge` renderer in Dashboard's `edgeTypes`, so persisted pre-step workflows render orthogonal without data migration. The in-progress connection line (`.react-flow__connection-path`) shares the resting rule; Dashboard passes no `connectionLineStyle`.
- **Status classes recolor with semantic tokens** so runtime feedback survives in every theme: `selected` → `--accent`, `executing` → `--node-pulse-color` + `dashFlow` marching ants, `completed` → `--success`, `error` → `--destructive`, `pending` → `--fg-muted` + `dashFlow`, `memory-active` → `--node-agent`, `tool-active` → `--node-tool`, `skill-active` → `--node-skill`. Widths come from `--edge-stroke-width-active` (2.5px) / `--edge-stroke-width-done` (2px); the active dash rhythm is `--edge-dash-active` (8 4). All edge tokens live in [base.css](../client/src/themes/base.css); themes may override any of them.
- **No `!important` on the resting rule** (nothing competes — Dashboard inlines no stroke); status rules keep `!important` so they beat the resting rule and ConditionalEdge's inline conditional-accent styling during runs. `dashFlow` stops under `prefers-reduced-motion`.

**No `nodeGlow` keyframe here.** This module used to inject a `nodeGlow` keyframe targeting the React Flow wrapper, but it was dead code (only the inner `.node` child animated) and was removed. Node execution glow is owned solely by [base.css](../client/src/themes/base.css) — the `node-pulse` keyframe + `.react-flow__node.executing .node` / `.sq-node[data-executing] .sq-node-box` rules (see [`--node-pulse-color` contract](#done) above). Do not re-introduce a competing `nodeGlow` keyframe here. Success/error **outcome glow** also lives in base.css: the Wave-29 `data-status` selectors plus `.react-flow__node.completed/.error` class twins that fire from Dashboard's execution-status classNames (the classNames are the trigger; `data-status` on the box remains the per-theme override contract).

## Sound system

Each theme declares `--sound-pack: "<pack>"` in its `:root[data-theme="..."]` block. Pack names map to event tables in [client/src/lib/sound.ts](../client/src/lib/sound.ts):

| Theme | Pack | Voice |
|---|---|---|
| light | `parchment` (light.css) | Soft sine plucks + low-pass |
| dark | `terminal` (dark.css) | Square-wave terminal beeps |

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
1. Mount `useSoundSync()` once at the app shell — done in [AppShell.tsx](../client/src/app/AppShell.tsx)'s `ShellEffects`, so it runs on both Home and the editor.
2. Settings panel ships an Audio section with a Switch bound to `useAppStore.soundEnabled`.

Adding a new sound event: extend `SoundEvent` in `lib/sound.ts`, add an entry per pack, fire `play('<event>')` from the handler.

## Reduced motion

[client/src/themes/base.css](../client/src/themes/base.css) ships a `@media (prefers-reduced-motion: reduce)` block that disables every keyframe across all themes when the user's OS-level "Reduce Motion" preference is on. Disabled animations:

- generic `.animate-pulse` (used by node execution pip)
- the executing node pulse on BOTH node families — `.react-flow__node.executing .node` (rect) and `.react-flow__node.executing .sq-node-box` / `.sq-node[data-executing] .sq-node-box` (square; Square/Trigger/Toolkit/Start nodes are the dominant canvas topology, so the square selectors are load-bearing)
- the edge `dashFlow` marching-ants animation on `.executing` / `.pending` edges (guarded inside [canvasAnimations.ts](../client/src/styles/canvasAnimations.ts)'s injected stylesheet)

Sounds are not motion — they remain enabled regardless. The user-facing audio toggle lives in Settings → Audio → Sound effects.

Verify in Chrome DevTools → Rendering → "Emulate CSS media feature prefers-reduced-motion: reduce", or in OS settings (macOS: System Settings → Accessibility → Display → Reduce motion; Windows: Settings → Accessibility → Visual effects → Animation effects).

## Throttling

Two events flood under rapid input and are throttled inside the engine to prevent OscillatorNode flooding:

- `type` — 30 ms last-fire window (protects against keystroke bursts)
- `hover` — 30 ms last-fire window (protects against `mouseover` firing dozens of times during a fast cursor sweep)

Implementation lives in [client/src/lib/sound.ts](../client/src/lib/sound.ts): `Sounds.play()` consults a `THROTTLE_MS` map per event name and a `lastFireMs` map keyed by event before each `OscillatorNode` schedule. Other events (`click`, `success`, `error`, `run`, `save`, `modalOpen`, `modalClose`) fire on every call.

## Canvas colour packs (`useAppTheme`)

The hook returns a `theme` object with the same shape every call site already destructures — `theme.colors.X` and `theme.isDarkMode` continue to work. `theme.colors` is the base pack for the theme's family: `darkColors` when `useTheme().isDarkMode`, `lightColors` otherwise. There is no per-theme overlay: Atomic's `THEME_OVERRIDES` entry, which re-bound primary, focus and the action colours on top of the light pack, went with it on 2026-11-09.

**Canvas edge strokes are not part of the packs** — the design-handoff edge migration moved them to CSS tokens (`--edge-*` in base.css + semantic status recolors in `canvasAnimations.ts`); do not reintroduce `edge*` keys here.

## Verification checklist

When changing a component, verify under both themes, on Home and in the editor (both show the chosen theme):

```bash
cd client && bun run dev
```

1. `<html data-theme="light">` — clean default, near-white surfaces
2. `<html data-theme="dark">` — neutral slate + Dracula

Build + typecheck must stay clean:

```bash
cd client && bun run typecheck && bun run build
```

Touched components should pass lint:

```bash
cd client && bun x eslint <files>
```

## File index

| File | Role |
|---|---|
| [client/src/themes/base.css](../client/src/themes/base.css) | Shared scalars (space / radii / chrome / shadow / type) + `--tint-*` alpha scale + node-visual contract |
| [client/src/themes/light.css](../client/src/themes/light.css) | Light colour hex (shadcn + dracula + node + action + `-ink`); on bare `:root` so global |
| [client/src/themes/dark.css](../client/src/themes/dark.css) | Dark colour-hex overrides (+ `-ink`, deeper shadows) |
| [client/src/themes/animations.css](../client/src/themes/animations.css) | `--pulse-keyframe` tokens + executing / trigger keyframes + `.opencompany-*` helpers |
| [client/tailwind.config.js](../client/tailwind.config.js) | Colour bridge (`theme.extend.colors` → `var(--X)`, no `hsl()`) |
| [client/src/index.css](../client/src/index.css) | Plumbing: global resets + `@theme inline` `var()` bridge (no literal colours) |
| [client/src/contexts/ThemeContext.tsx](../client/src/contexts/ThemeContext.tsx) | `<ThemeProvider>` + `useTheme()` (two themes, `cycleTheme`, `THEME_LABEL`) |
| [client/src/hooks/useAppTheme.ts](../client/src/hooks/useAppTheme.ts) | Canvas colour pack (`lightColors` / `darkColors`) |
| [client/src/lib/sound.ts](../client/src/lib/sound.ts) | WebAudio engine — 2 sound packs, 9 events |
| [client/src/hooks/useSound.ts](../client/src/hooks/useSound.ts) | `useSoundSync()` + `useSound()` React glue |
| [client/src/components/ui/ThemeButton.tsx](../client/src/components/ui/ThemeButton.tsx) | The theme button (both modes + sign-in): toggles Light and Dark |
| [client/src/components/ui/StatusBar.tsx](../client/src/components/ui/StatusBar.tsx) | Fixed-bottom system line |
| [client/src/components/ui/CommandPalette.tsx](../client/src/components/ui/CommandPalette.tsx) | ⌘K palette primitive |
| [client/src/components/ui/CommandPaletteHost.tsx](../client/src/components/ui/CommandPaletteHost.tsx) | Registered command set |
| [client/src/components/ui/action-button.tsx](../client/src/components/ui/action-button.tsx) | CVA action role primitive (fires `play('click')`) |
| [client/src/components/ui/Modal.tsx](../client/src/components/ui/Modal.tsx) | Shared modal layout (fires `play('modalOpen'/'modalClose')`) |
| [client/src/assets/icons/NodeIcon.tsx](../client/src/assets/icons/NodeIcon.tsx) | Icon renderer: resolves a backend-declared icon string at the `size` token |
| [client/src/types/NodeTypes.ts](../client/src/types/NodeTypes.ts) | `NodeStyle` helper type for inline `--*` custom props — Wave 21 |
| [client/index.html](../client/index.html) | Pre-paint theme script |
| `design_handoff_machinaos_themes/` (external design handoff, not in repo) | Reference HTML mocks + token spec |
| `design_handoff_machinaos_themes/app/icons.js` (external) | Upstream icon source (ported by Wave 23; the port went with Atomic on 2026-11-09) |
| `design_handoff_machinaos_themes/MIGRATION_PLAYBOOK.md` (external) | Upstream recipe — all originally-deferred items now landed (W14–W25) |
| `design_handoff_theme_system/` (external, 2026-08 — **fully migrated, removed from disk**) | Second handoff drop: its `themes/` CSS was byte-identical to live, `tokens/`/`components/`/`guidelines/` matched the vendored bundle; the new content (handoff brief + 17-panel × 12-theme mockup) was merged as [design-system/HANDOFF.md](./design-system/HANDOFF.md) + [design-system/reference-mockup/](./design-system/reference-mockup/), and the fidelity-pass contracts it specified (step edges, outcome glow, toolbar pinning, font substitutions, reduced-motion coverage) are implemented |
