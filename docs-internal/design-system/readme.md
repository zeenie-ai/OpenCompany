# OpenCompany Design System (zeenie.ai)

Design system for **OpenCompany** — zeenie.ai's open-source, local-first AI workflow OS. "Your own AI assistant that does real work": users drag, drop, and connect AI agents to email, calendar, messages, phone, and 50+ services on a visual node canvas, then run or deploy workflows that keep running in the background. No code, no subscription; bring your own API keys or run models locally.

**Sources** (explore these to design even better against this product):
- GitHub: https://github.com/zeenie-ai/MachinaOS — primary source. Key design files: `client/src/index.css` (all color/role tokens), `client/src/themes/{base,light,dark}.css` (contract tokens: surfaces, type, motion), `client/tailwind.config.js`, `client/src/components/ui/` (TopToolbar, ComponentPalette, ActionButton, StatusBar, WorkflowSidebar…), `client/src/components/SquareNode.tsx` (canvas node anatomy), `client/src/assets/icons/index.ts` (icon resolver).
- Hosted docs: https://docs.zeenie.xyz/ · DeepWiki: https://deepwiki.com/zeenie-ai/MachinaOS
- Reference screenshot: `assets/product-canvas-screenshot.png` (real product — dark canvas, neon nodes, WhatsApp automation demo).

## Product surfaces
1. **OpenCompany app** (`localhost:3000`) — the core product. A three-zone desktop workspace: workflow sidebar (left), node canvas (center), component palette (right), top toolbar, bottom status bar, multi-tab console dock. Recreated in `ui_kits/opencompany/`.
2. **Marketing/docs** (zeenie.ai, docs.zeenie.xyz) — not in the repo; no UI kit was fabricated for them. Use the foundations here plus the README voice when designing those.

The app ships 12 visual themes (light, dark, Renaissance, Cyber, Edo, Steampunk…). This design system encodes the two canonical ones: **light** (default `:root`) and **dark** (`class="dark"` — the signature look used in every screenshot and demo).

---

## CONTENT FUNDAMENTALS

**Voice: confident, concrete, slightly conspiratorial about doing less work.** The README sells outcomes, not features: "doing the work you'd rather not", "Agent teams that delegate", "Code execution that's actually safe".

- **Second person, possessive.** "Your own AI assistant", "your data stays with you", "your machine". The product is *yours*; zeenie.ai rarely says "we".
- **Short declarative fragments for value props.** "No code required. No subscription. No usage limits."
- **Sentence case everywhere** — headings ("What You Can Build" is Title Case in README headers, but UI copy is sentence case: "No workflows yet", "Create your first workflow to get started"). Buttons are single words or short verb phrases: **Start**, **Stop**, **Save**, **New Workflow**, **Apply All**.
- **UI microcopy is terse and systemy.** Status bar reads like a shell prompt: `ONLINE | WF: Test | NODES: 12 | THEME: DARK | 14:02:33`. States: "Saved" / "Modified". Tooltips are full sentences: "Override all agent nodes in this workflow to use the selected model".
- **Numbers as proof.** "50+ other services", "11 LLM providers", "17 specialized agent types", "16 device services". Counts are surfaced in the UI too (badge with component count, "3 saved").
- **Emoji: not part of the brand voice.** Marketing copy uses none. In-product the only emoji are *functional glyphs* (⚙️ gear button, 📦 fallback icon) — never decorative, never in sentences.
- **Bold for product nouns** mid-sentence: "**AI Employee**", "**Stripe**", "**WhatsApp**".
- Example headline pairs: "Personal AI assistants that remember" / "Task automations that run themselves" — lowercase-after-first-word, noun phrase + verb clause.

## VISUAL FOUNDATIONS

**Core idea: a quiet professional workspace wearing neon jewelry.** Surfaces are muted (grey-blue paper in light; neutral slate in dark); all personality comes from the Dracula accent palette applied as *soft tints* — 8–15% alpha fills, 30–60% alpha borders, full-strength text/icons.

- **Color:** Two-layer system. (1) Calm structural tokens: `--bg-app/panel/elevated`, `--fg-default/muted/faint`, `--border-default`. (2) Vibrant Dracula accents (green `#50fa7b`, purple `#bd93f9`, pink `#ff79c6`, cyan `#8be9fd`, orange `#ffb86c`, yellow `#f1fa8c`) assigned to *semantic roles*, never used raw as backgrounds: action intents (run/stop/save/config/secret/tools) and node roles (agent/model/tool/trigger/workflow). Standard status colors (success `#22c55e` dark / `#059669` light, etc.).
- **The "soft tinted button" is THE signature component pattern:** `background: color-mix(accent 15%)`, `border: color-mix(accent 60%)`, text in the accent color, hover deepens fill to 25%. Never solid neon fills.
- **Type:** Geist everywhere (display = body), 14px base, 600 weight for headings/buttons, 500 for UI labels. Mono (JetBrains Mono / system) for metadata, counts, timestamps, status bar — mono is the "machine voice". Uppercase only in the status bar and micro-labels with 0.04em tracking.
- **Backgrounds:** flat colors, no gradients in light/dark chrome. The canvas is the darkest surface (dark) or white (light) with a fine dot grid at 30% opacity. Node cards get a *subtle* 135° gradient from accent-tint(14–18%) to card color — the only gradient in the system.
- **Borders:** 1px `--border-default` for chrome; 2px accent-tinted (60%) for nodes; 3px left edge accent for selected list cards. Dashed 2px borders mark empty/drop states.
- **Radii:** controls 8px (`--radius-lg`), chips/inner elements 4–6px, canvas nodes 10–12px, pills 999px. Cards: 8px.
- **Shadows:** near-silent on chrome (`0 2px 8px rgba(0,0,0,.06)`); dark mode goes deeper-not-glowier (`0 4px 12px rgba(0,0,0,.3)`). Glow is reserved for *meaning*: accent-colored `box-shadow` on nodes (18% of node color), three-layer pulse on executing nodes, 1px ring + 14px glow for success/error states.
- **Hover:** background tint shift (`--bg-hover` rgba 4–6%), cards translate -1–2px and gain shadow + 2px foreground ring at 15%. **Press:** translateY(1px). Disabled: opacity .5.
- **Motion:** fast and smooth — 90/180/320ms, ease `cubic-bezier(0.2,0.7,0.3,1)`. Loops only for live processes (executing pulse 1.4s, status pip blink). Respects `prefers-reduced-motion`.
- **Layout:** fixed chrome — 48px toolbar, 24px status bar, 280px sidebar, 320px palette; canvas fills the rest. Density is high; controls are 32px tall, text 13–14px.
- **Transparency/blur:** overlays use flat rgba scrims (45%/70%), no backdrop blur in light/dark.
- **Imagery:** real product screen-recordings/screenshots on dark theme — cool, saturated neon-on-slate. No stock photography, no illustration system; diagrams (`assets/diagrams/`) are flat SVG node-graphs in brand colors.

## ICONOGRAPHY

- **Lucide is the system icon set** (`lucide-react` in app; pinned CDN UMD in this kit). 1.5px–2px stroke, no fill, sized 12–20px, colored via `currentColor`. Common verbs: Play/Square (run/stop), Save, Settings, KeyRound (credentials), FolderOpen, FilePlus, Search, GripVertical (drag), PanelLeft/RightClose, ChevronDown, Zap (dev mode), Clock (normal mode).
- **Provider/brand logos** come from `@lobehub/icons` (OpenAI, Claude, Gemini, Groq, Ollama…) — colored brand marks, used at 16–28px on nodes and credential rows. In prototypes substitute colored dots or the service's official mark.
- **Node/service icons in production are backend-declared** (`asset:<key>` SVGs, `lucide:<Name>`, or plain emoji strings). Many shipped nodes use full-color emoji-style glyphs (🧠 memory, 🤖 agent robot, ⚙️ gear) — see `assets/product-canvas-screenshot.png`. The ⚙️ gear emoji is literally the node's settings button.
- **Fallback icon is 📦.** Status communicated by 6–10px colored dot "pips", not icons.
- **No icon font; no custom-drawn icon set.** Use Lucide via the `Icon` component (`components/icons/`), which renders from the lucide CDN bundle.

## Use of this system (quick rules)

1. Link `styles.css`; add `class="dark"` on `<html>` for the signature dark look (default for product UI; light for docs/print).
2. Color = role, not decoration. Pick the action intent or node role first, then use its `-soft`/`-border`/ink triplet.
3. Geist for words, mono for numbers/state. 14px base, sentence case, terse copy.
4. Compose from `components/` (namespace `window.OpenCompanyDS` — see `check_design_system`/cards): ActionButton, Button, Badge, Input, Select, RadioGroup, Textarea, Switch, Checkbox, Card, Tabs, Avatar, Kbd, LogLine, ChatBubble, Modal, Toast, Tooltip, Spinner, Progress, EmptyState, SquareNode, ComponentItem, WorkflowCard, StatusBar, ModeToggle, Icon.

## Index

- `styles.css` — global entry; imports everything in `tokens/`.
- `tokens/` — `colors.css`, `typography.css`, `spacing.css`, `motion.css`, `animations.css`, `fonts.css`, `base.css`.
- `guidelines/` — foundation specimen cards (Design System tab), plus `THEMES.md` (deep analysis of the app's 12-theme architecture) and `theme-matrix.html` (visual comparison of all 12 themes).
- `reference/themes/` — verbatim copies of all 13 theme CSS files from the repo (`base, light, dark + 10 skins`) for porting.
- `assets/` — `product-canvas-screenshot.png`, `diagrams/*.svg` (official README diagrams).
- `components/buttons/` — ActionButton (6 intents), Button (shadcn variants).
- `components/forms/` — Input, Select, RadioGroup, Textarea, Switch, Checkbox, Slider, ApiKeyInput.
- `components/display/` — Badge, Card, Tabs, Avatar, Kbd, LogLine, ChatBubble.
- `components/feedback/` — Modal, Toast, Tooltip, Spinner, Progress, EmptyState.
- `components/panels/` — PanelModal, SettingsSection (+SettingsRow), CollapsibleSection, DataCard — the workspace panel anatomy (Node Configuration, Settings, Credentials).
- `components/canvas/` — SquareNode, ComponentItem, WorkflowCard, StatusBar, ModeToggle.
- `components/icons/` — Icon (Lucide bridge).
- `ui_kits/opencompany/` — interactive recreation of the workflow canvas app, including the Settings panel (toolbar ⚙), API Credentials panel (🔑), and the three-column Node Configuration panel (gear on any node).
- `SKILL.md` — agent-skill entry point.

## Caveats
- **Fonts substituted:** Geist served from Google Fonts instead of the app's bundled `@fontsource-variable/geist` (same typeface, different delivery). Drop real `.woff2` files into `tokens/` + add `@font-face` if pixel-exact metrics matter.
- **No official logo found in the repo** (favicon is the Vite placeholder; README hero is a GitHub user-attachment). The wordmark card sets "OpenCompany" in Geist 600 as a stand-in — replace with the real mark when available.
- zeenie.ai marketing site isn't in the repo; no marketing UI kit was invented.
- The app's 10 themed skins are analyzed in `guidelines/THEMES.md` (token contract, per-theme matrix, porting recipe) with sources in `reference/themes/` — but only light/dark are encoded as live token scopes here. Ask to port a skin (e.g. Cyber) into `tokens/` if you want it usable.
- **Motion** is documented in `guidelines/ANIMATIONS.md` and encoded in `tokens/animations.css` (per-theme `--dur`/`--ease`/`--motion-style` scopes + signature keyframes for all 12 themes). Live demo: `guidelines/animations-all-themes.html`.
