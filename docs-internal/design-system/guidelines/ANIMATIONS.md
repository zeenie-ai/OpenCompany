# MachinaOS animation system

Motion is a **first-class part of each theme's identity**, not a global constant. Every theme declares the same six-token motion contract; the personality lives in the **easing function**, not just duration. Live demo: `guidelines/animations-all-themes.html`. Tokens + keyframes: `tokens/animations.css`.

## The contract (per theme)

| Token | Role |
|---|---|
| `--dur-fast / -default / -slow` | interaction / transition / panel timings |
| `--ease-default` | the curve every transition uses — carries the personality |
| `--ease-emphasis` | entrances, springy moments |
| `--motion-style` | a name (smooth, organic, mechanical, bouncy, glitch, jittery, drift, stiff, scanline) |
| `--pulse-keyframe / -duration / -timing` | which executing-node pulse this theme uses |
| `--node-pulse-color` | the glow color — **theme-chosen for contrast**, never the node's own role color |

## All 12 themes

| Theme | fast/def/slow | ease-default | motion-style | exec pulse | ambient |
|---|---|---|---|---|---|
| Light | 90/180/320 | cubic(.2,.7,.3,1) | smooth | node-pulse 1.4s ease-in-out | — |
| Dark | 90/180/320 | cubic(.2,.7,.3,1) | smooth | node-pulse 1.4s | — |
| Renaissance | 140/280/520 | cubic(.4,0,.2,1) | organic | ren-pulse-exec 2.0s (candle flame) | candle flicker 6s |
| Greek | 120/240/460 | cubic(.3,.7,.4,1) | smooth | node-pulse 1.4s | — |
| Edo | 90/220/520 | cubic(.4,0,.2,1) | organic | node-pulse 1.6s | — |
| Steampunk | 110/240/480 | cubic(.5,0,.4,1.1) | mechanical | node-pulse 1.4s | — |
| Atomic | 100/200/380 | **cubic(.34,1.6,.5,1)** | bouncy (1.6 overshoot) | node-pulse 1.4s | — |
| Cyber | 60/120/240 | **steps(4,end)** | glitch | cyber-pulse-exec 1.6s steps(2) | CRT flicker 5s + rolling scanline 8s + hover glitch |
| Wasteland | 60/180/320 | cubic(.6,0,.4,1) | jittery | node-pulse 1.3s | — |
| Rot | 140/320/**680** | cubic(.4,0,.6,1) | drift (slowest) | node-pulse 2.2s | — |
| Plague | 100/220/460 | cubic(.4,0,.6,1) | stiff | node-pulse 1.6s | — |
| Surveillance | 60/140/240 | **linear** | scanline | surv-pulse-exec 1.0s steps(2) | REC-LED blink + scanline |

## Signature keyframes (`tokens/animations.css`)

- **`node-pulse`** — default 3-layer glow (ring + mid halo + atmospheric halo) scaling out/in. Used by 8 themes.
- **`ren-pulse-exec`** — candle-flame: never fully dark, gentle ease-in-out swell (Renaissance).
- **`cyber-pulse-exec` / `cyber-flicker` / `cyber-roll` / `cyber-blink` / `cyber-glitch`** — hard digital strobe via `steps(2)`, whole-frame CRT flicker, rolling scanline band, LED blink, hover text-shear (Cyber).
- **`surv-pulse-exec` / `surv-blink`** — REC-red glow strobe + status-LED blink (Surveillance).
- **`machina-pip-blink`** — generic status-pip blink. **`machina-spin`** — spinner.

## Interaction motion (all themes)

- **Hover:** background tint shift; cards/nodes lift `-1` to `-2px` and gain shadow. Cyber replaces lift with a glitch shear.
- **Press:** `translateY(1px)`.
- **Disabled:** `opacity .5`, no transition.
- **Entrances:** slide/fade gated on `[data-deck-active]`-style activation and `--ease-emphasis`; base state is the visible end-state so reduced-motion / print show content, never the pre-animation frame.

## Usage

```html
<html data-theme="cyber">           <!-- picks the whole motion contract -->
<div class="machina-pulse node">…</div>  <!-- executing node: reads --pulse-keyframe + color -->
<div class="machina-crt machina-scanline">…</div>  <!-- app frame ambient (cyber/surveillance) -->
```

Set `--node-pulse-color` on (or above) the pulsing element. `.machina-pulse` automatically uses the active theme's keyframe, duration and timing. All loops stop under `prefers-reduced-motion: reduce`.

## Trigger nodes — the "listening / armed" state

Triggers (`whatsappReceive`, `cronScheduler`, `webhookTrigger`, `start`) animate differently from regular nodes:

- **No input handle**, and a **lightning ⚡ badge** (bottom-left, gold) marking them as workflow entry points.
- A **continuous "listening" pulse** (`trigger-listening`, ~2.4s, gentle ease-in-out) that runs the whole time the trigger is *armed and waiting for events* — not just during execution. This is the key distinction: a normal node is dark until it runs and pulses once; a trigger breathes continuously while listening. The armed pip blinks (`machina-pip-pulse`).
- A **one-shot `trigger-fire` flash** the moment an event arrives, before it hands off downstream.
- WhatsApp triggers fold connection state into the pip: connected → success, pairing → waiting (pulses), disconnected → error.

```jsx
<SquareNode icon="💬" label="WhatsApp Receive" color="var(--node-trigger)"
  trigger status="listening" pulseColor="var(--node-pulse-color)" />
```

## Glow color & visibility

The glow is **never the node's own faint role tint** — it uses `--node-pulse-color` (or the component's `pulseColor`), a saturated accent the theme picks **for contrast against its own canvas**: lapis on Renaissance vellum, neon cyan on Cyber's near-black void, REC-red on Surveillance grey, deep teal on Atomic cream. This keeps both the execution pulse and the trigger listening glow legible on light and dark themes alike. When adding a theme, choose its `--node-pulse-color` against the background, not to match the node.

## Rules

1. **Drive transitions from `var(--ease-default)` / `var(--dur-*)`** — never hardcode timing, so a theme swap restyles motion for free.
2. **Loops only for live processes** (executing pulse, connection LED). No decorative perpetual motion on idle content.
3. **The pulse color is the theme's, not the node's** — guarantees contrast on every canvas (lapis on vellum, cyan on void, REC-red on grey).
4. **Always provide the static end-state** so print, PDF export and reduced-motion render meaningfully.
