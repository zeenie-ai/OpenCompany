/**
 * sound.ts — per-theme WebAudio sound packs.
 *
 * Synthesizes click / hover / type / success / error / run / save /
 * modalOpen / modalClose events via a single OscillatorNode + GainNode
 * per call. No external samples; no module-level audio context until
 * the first play(). Off by default — toggle with `Sounds.setEnabled`.
 *
 * The active pack is selected by name (matches the per-theme
 * `--sound-pack` CSS token). React glue lives in
 * client/src/hooks/useSound.ts which:
 *   1. Reads `--sound-pack` from `:root` whenever the active theme
 *      changes and calls `Sounds.setPack(...)`.
 *   2. Wires `Sounds.setEnabled(...)` to the `soundEnabled` Zustand
 *      slice (persisted to localStorage as `opencompany-sound`).
 *
 * Ported from design_handoff_machinaos_themes/app/sound.js. The DOM
 * autocapture (`document.addEventListener('click', ...)`) at the
 * bottom of the upstream module is intentionally dropped — in React
 * we fire `play()` from explicit handlers (ActionButton onClick,
 * Modal open/close effect, etc.) so the side-effect surface is
 * traceable.
 */

export type SoundEvent =
  | 'click'
  | 'hover'
  | 'type'
  | 'success'
  | 'error'
  | 'run'
  | 'save'
  | 'modalOpen'
  | 'modalClose';

export type SoundPackName =
  | 'none'
  | 'parchment'
  | 'terminal';

interface OscConfig {
  type: OscillatorType;
  freq: number;
  dur: number;
  vol: number;
  attack: number;
  decay: number;
  /** Optional low-pass filter cutoff (Hz). */
  lp?: number;
  /** Optional frequency sweep multiplier; final = freq * sweep. */
  sweep?: number;
}

type EventTable = Partial<Record<SoundEvent, OscConfig>>;

// Singleton AudioContext lazily constructed on first play.
let audioCtx: AudioContext | null = null;
function ensureCtx(): AudioContext | null {
  if (audioCtx) return audioCtx;
  try {
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    if (!Ctx) return null;
    audioCtx = new Ctx();
    return audioCtx;
  } catch {
    return null;
  }
}

let enabled = false;
let activePackName: SoundPackName = 'none';
let activePack: EventTable = {};

/**
 * Per-event last-fire timestamps for throttling. Currently only `type`
 * is throttled — rapid typing into a long input shouldn't queue dozens
 * of OscillatorNodes per second. Other events (click, hover, success,
 * etc.) are user-paced and don't need throttling.
 *
 * Uses `performance.now()` for monotonic timing. The 30 ms window
 * matches the upstream `app/sound.js` reference engine's documented
 * hover debounce; we apply the same to `type` since each oscillator
 * costs ~25 ms decay on the parchment pack.
 */
const lastFireMs: Partial<Record<SoundEvent, number>> = {};
const THROTTLE_MS: Partial<Record<SoundEvent, number>> = {
  type: 30,
  hover: 30,
};

function play(cfg: OscConfig | undefined): void {
  if (!cfg || !enabled) return;
  const ac = ensureCtx();
  if (!ac) return;
  // If suspended, defer oscillator scheduling until resume() settles.
  // Synchronously scheduled oscillators on a suspended context are
  // silently dropped per the WebAudio autoplay policy
  // (https://developer.chrome.com/blog/autoplay/#webaudio).
  const schedule = () => {
    try {
      const osc = ac.createOscillator();
      const gain = ac.createGain();
      osc.type = cfg.type;
      osc.frequency.value = cfg.freq;
      let target: AudioNode = osc;
      if (cfg.lp) {
        const lp = ac.createBiquadFilter();
        lp.type = 'lowpass';
        lp.frequency.value = cfg.lp;
        target.connect(lp);
        target = lp;
      }
      target.connect(gain);
      gain.connect(ac.destination);
      const now = ac.currentTime;
      gain.gain.setValueAtTime(0, now);
      gain.gain.linearRampToValueAtTime(cfg.vol, now + cfg.attack);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + cfg.attack + cfg.decay);
      if (cfg.sweep) {
        osc.frequency.exponentialRampToValueAtTime(cfg.freq * cfg.sweep, now + cfg.dur);
      }
      osc.start(now);
      osc.stop(now + cfg.dur + 0.02);
    } catch {
      // WebAudio errors are non-fatal — silently drop.
    }
  };
  if (ac.state === 'running') {
    schedule();
  } else {
    void Promise.resolve(ac.resume()).then(schedule).catch(() => {/* noop */});
  }
}

// ── Sound packs ──────────────────────────────────────────────────────────
//
// Each pack maps each `SoundEvent` to an oscillator config. Volume is
// kept ≤ 0.10 across the board so no pack clips. Configs are ported
// verbatim from app/sound.js (handoff bundle).

const PARCHMENT: EventTable = {
  click:      { type: 'sine',     freq: 220, dur: 0.04,  vol: 0.06, attack: 0.005, decay: 0.04 },
  hover:      { type: 'triangle', freq: 380, dur: 0.02,  vol: 0.025, attack: 0.002, decay: 0.02 },
  type:       { type: 'square',   freq: 180, dur: 0.025, vol: 0.04, attack: 0.001, decay: 0.025, lp: 800 },
  success:    { type: 'sine',     freq: 523, dur: 0.4,   vol: 0.08, attack: 0.01,  decay: 0.4,  sweep: 1.5 },
  error:      { type: 'sawtooth', freq: 110, dur: 0.25,  vol: 0.10, attack: 0.005, decay: 0.25, lp: 600 },
  run:        { type: 'sine',     freq: 392, dur: 0.18,  vol: 0.10, attack: 0.01,  decay: 0.18, sweep: 1.3 },
  save:       { type: 'triangle', freq: 294, dur: 0.3,   vol: 0.10, attack: 0.02,  decay: 0.3 },
  modalOpen:  { type: 'sine',     freq: 392, dur: 0.18,  vol: 0.07, attack: 0.02,  decay: 0.18, sweep: 1.2 },
  modalClose: { type: 'sine',     freq: 392, dur: 0.14,  vol: 0.06, attack: 0.005, decay: 0.14, sweep: 0.7 },
};

const TERMINAL: EventTable = {
  click:      { type: 'square',   freq: 1200, dur: 0.025, vol: 0.06, attack: 0.001, decay: 0.025 },
  hover:      { type: 'square',   freq: 1800, dur: 0.012, vol: 0.025, attack: 0.001, decay: 0.012 },
  type:       { type: 'square',   freq: 880,  dur: 0.018, vol: 0.05, attack: 0.0005, decay: 0.018 },
  success:    { type: 'square',   freq: 1318, dur: 0.18,  vol: 0.08, attack: 0.001, decay: 0.18,  sweep: 1.5 },
  error:      { type: 'sawtooth', freq: 220,  dur: 0.18,  vol: 0.10, attack: 0.001, decay: 0.18,  sweep: 0.5 },
  run:        { type: 'square',   freq: 1568, dur: 0.10,  vol: 0.10, attack: 0.001, decay: 0.10 },
  save:       { type: 'square',   freq: 988,  dur: 0.12,  vol: 0.08, attack: 0.001, decay: 0.12,  sweep: 1.2 },
  modalOpen:  { type: 'square',   freq: 1175, dur: 0.06,  vol: 0.06, attack: 0.001, decay: 0.06,  sweep: 1.4 },
  modalClose: { type: 'square',   freq: 1175, dur: 0.06,  vol: 0.06, attack: 0.001, decay: 0.06,  sweep: 0.6 },
};

const PACKS: Record<SoundPackName, EventTable> = {
  none: {},
  parchment: PARCHMENT,
  terminal: TERMINAL,
};

// ── Public API ───────────────────────────────────────────────────────────

export const Sounds = {
  setEnabled(value: boolean): void {
    enabled = value;
  },
  isEnabled(): boolean {
    return enabled;
  },
  setPack(name: SoundPackName): void {
    activePackName = name;
    activePack = PACKS[name] ?? PACKS.none;
  },
  pack(): SoundPackName {
    return activePackName;
  },
  /**
   * Idempotent gesture-time unlock. Constructs the AudioContext (if not
   * already constructed) and resumes it if the browser autoplay policy
   * left it `suspended`. Safe to call repeatedly — `resume()` on a
   * running context is a no-op.
   *
   * Modern Chrome / Safari require the resume() call to occur inside
   * the same task as a user gesture (pointerdown / keydown / touchstart).
   * `Sounds.play()` defensively calls resume() too, but state-change-
   * driven plays (e.g. modal open from an effect) can land microseconds
   * after the gesture frame and lose the very first sound. Calling
   * `unlock()` from a once-listener on the gesture path closes that gap.
   */
  unlock(): void {
    const ac = ensureCtx();
    if (!ac) return;
    if (ac.state === 'suspended') void ac.resume();
  },
  play(event: SoundEvent): void {
    const throttle = THROTTLE_MS[event];
    if (throttle !== undefined) {
      const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
      const last = lastFireMs[event] ?? 0;
      if (now - last < throttle) return;
      lastFireMs[event] = now;
    }
    play(activePack[event]);
  },
  /**
   * Snapshot the engine state for DevTools self-diagnosis. Returns an
   * object the user can inspect from the browser console:
   *   `> Sounds.diagnose()` →
   *     { enabled, pack, eventsForPack, audioContextState, browserSupport }
   * Common silent-audio modes:
   *   - `enabled: false` → user hasn't toggled Settings → Audio.
   *   - `pack: 'none'` → :root[data-theme="..."] declares chime/typo
   *     and the validator falls through to none.
   *   - `audioContextState: 'suspended'` after a click → a browser
   *     extension or autoplay-block policy is denying resume().
   */
  diagnose(): {
    enabled: boolean;
    pack: SoundPackName;
    eventsForPack: string[];
    cssSoundPack: string;
    cssDataTheme: string | null;
    audioContextState: string | 'no-ac' | 'no-audio-api';
    browserSupport: boolean;
  } {
    const Ctx = typeof window !== 'undefined'
      ? window.AudioContext || (window as any).webkitAudioContext
      : undefined;
    const cssSoundPack = typeof document !== 'undefined'
      ? getComputedStyle(document.documentElement).getPropertyValue('--sound-pack').trim().replace(/['"]/g, '')
      : '';
    const cssDataTheme = typeof document !== 'undefined'
      ? document.documentElement.getAttribute('data-theme')
      : null;
    return {
      enabled,
      pack: activePackName,
      eventsForPack: Object.keys(activePack),
      cssSoundPack,
      cssDataTheme,
      audioContextState: !Ctx ? 'no-audio-api' : audioCtx ? audioCtx.state : 'no-ac',
      browserSupport: !!Ctx,
    };
  },
  /**
   * Force a sound to play for testing — bypasses the `enabled` gate.
   * Call from DevTools: `Sounds.test('click')` after the AC has unlocked.
   * Returns whether the play scheduled successfully.
   */
  test(event: SoundEvent = 'click'): boolean {
    const cfg = activePack[event];
    if (!cfg) return false;
    const ac = ensureCtx();
    if (!ac) return false;
    const wasEnabled = enabled;
    enabled = true;
    try {
      play(cfg);
      return true;
    } finally {
      enabled = wasEnabled;
    }
  },
};

// Expose on `window` in dev mode so users can self-diagnose silent audio
// from DevTools (`> Sounds.diagnose()`, `> Sounds.test()`). No-op in
// production builds — Vite tree-shakes the `import.meta.env.DEV` branch.
if (typeof window !== 'undefined' && import.meta.env.DEV) {
  (window as any).Sounds = Sounds;
}
