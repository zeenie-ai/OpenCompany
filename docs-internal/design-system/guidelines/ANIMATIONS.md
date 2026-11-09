# OpenCompany animation system

> **Status: Reference guide.** Live `client/src/themes/` and `client/src/lib/motion.ts` own production tokens and lifecycle behavior. Bundled specimens and token snapshots illustrate design intent; retain explicit product amendments when they differ.

Motion is a **first-class part of each theme's identity**, not a global constant. Every theme declares the same six-token motion contract; the personality lives in the **easing function**, not just duration. Tokens + keyframes: `tokens/animations.css`.

## The contract (per theme)

| Token | Role |
|---|---|
| `--dur-fast / -default / -slow` | interaction / transition / panel timings |
| `--ease-default` | the curve every transition uses — carries the personality |
| `--ease-emphasis` | entrances, springy moments |
| `--motion-style` | a name (smooth for light and dark) |
| `--pulse-keyframe / -duration / -timing` | which executing-node pulse this theme uses |
| `--node-pulse-color` | the glow color — **theme-chosen for contrast**, never the node's own role color |

## Both themes

| Theme | fast/def/slow | ease-default | motion-style | exec pulse | ambient |
|---|---|---|---|---|---|
| Light | 90/180/320 | cubic(.2,.7,.3,1) | smooth | node-pulse 1.4s ease-in-out | — |
| Dark | 90/180/320 | cubic(.2,.7,.3,1) | smooth | node-pulse 1.4s | — |

## Signature keyframes (`tokens/animations.css`)

- **`node-pulse`** — default 3-layer glow (ring + mid halo + atmospheric halo) scaling out/in. Used by every theme.
- **`opencompany-pip-blink`** — generic status-pip blink. **`opencompany-spin`** — spinner.

## Interaction motion (all themes)

- **Hover:** background tint shift; cards/nodes lift `-1` to `-2px` and gain shadow.
- **Press:** `translateY(1px)`.
- **Disabled:** `opacity .5`, no transition.
- **Entrances:** slide/fade gated on `[data-deck-active]`-style activation and `--ease-emphasis`; base state is the visible end-state so reduced-motion / print show content, never the pre-animation frame.

## Usage

```html
<html data-theme="dark">            <!-- picks the whole motion contract -->
<div class="opencompany-pulse node">…</div>  <!-- executing node: reads --pulse-keyframe + color -->
```

Set `--node-pulse-color` on (or above) the pulsing element. `.opencompany-pulse` automatically uses the active theme's keyframe, duration and timing. All loops stop under `prefers-reduced-motion: reduce`.

## Trigger nodes — the "listening / armed" state

Triggers (`whatsappReceive`, `cronScheduler`, `webhookTrigger`, `start`) animate differently from regular nodes:

- **No input handle**, and a **lightning ⚡ badge** (bottom-left, gold) marking them as workflow entry points.
- A **continuous "listening" pulse** (`trigger-listening`, ~2.4s, gentle ease-in-out) that runs the whole time the trigger is *armed and waiting for events* — not just during execution. This is the key distinction: a normal node is dark until it runs and pulses once; a trigger breathes continuously while listening. The armed pip blinks (`opencompany-pip-pulse`).
- A **one-shot `trigger-fire` flash** the moment an event arrives, before it hands off downstream.
- WhatsApp triggers fold connection state into the pip: connected → success, pairing → waiting (pulses), disconnected → error.

```jsx
<SquareNode icon="💬" label="WhatsApp Receive" color="var(--node-trigger)"
  trigger status="listening" pulseColor="var(--node-pulse-color)" />
```

## Glow color & visibility

The glow is **never the node's own faint role tint** — it uses `--node-pulse-color` (or the component's `pulseColor`), a saturated accent the theme picks **for contrast against its own canvas**: the info blue on light and dark. This keeps both the execution pulse and the trigger listening glow legible on light and dark themes alike. When adding a theme, choose its `--node-pulse-color` against the background, not to match the node.

## Rules

1. **Drive transitions from `var(--ease-default)` / `var(--dur-*)`** — never hardcode timing, so a theme swap restyles motion for free.
2. **Loops only for live processes** (executing pulse, connection LED). No decorative perpetual motion on idle content.
3. **The pulse color is the theme's, not the node's** — guarantees contrast on every canvas.
4. **Always provide the static end-state** so print, PDF export and reduced-motion render meaningfully.
