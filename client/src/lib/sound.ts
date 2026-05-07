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
 *      slice (persisted to localStorage as `machinaos-sound`).
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
  | 'marble'
  | 'ink'
  | 'clockwork'
  | 'vibraphone'
  | 'terminal'
  | 'scrap'
  | 'crypt'
  | 'bell'
  | 'telex';

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
 * costs ~25 ms decay on the parchment / marble packs.
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
  // WebAudio context starts suspended on most browsers until a user
  // gesture; resume() is a no-op once running.
  if (ac.state === 'suspended') void ac.resume();
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

const MARBLE: EventTable = {
  click:      { type: 'sine',     freq: 330, dur: 0.05,  vol: 0.07, attack: 0.001, decay: 0.05 },
  hover:      { type: 'sine',     freq: 520, dur: 0.02,  vol: 0.025, attack: 0.001, decay: 0.02 },
  type:       { type: 'triangle', freq: 260, dur: 0.03,  vol: 0.04, attack: 0.001, decay: 0.03, lp: 1200 },
  success:    { type: 'sine',     freq: 392, dur: 0.5,   vol: 0.09, attack: 0.005, decay: 0.5,  sweep: 1.6 },
  error:      { type: 'sawtooth', freq: 130, dur: 0.30,  vol: 0.10, attack: 0.005, decay: 0.30, lp: 500 },
  run:        { type: 'sine',     freq: 440, dur: 0.22,  vol: 0.10, attack: 0.005, decay: 0.22, sweep: 1.4 },
  save:       { type: 'sine',     freq: 330, dur: 0.35,  vol: 0.10, attack: 0.01,  decay: 0.35 },
  modalOpen:  { type: 'sine',     freq: 392, dur: 0.20,  vol: 0.07, attack: 0.02,  decay: 0.20, sweep: 1.3 },
  modalClose: { type: 'sine',     freq: 392, dur: 0.16,  vol: 0.06, attack: 0.005, decay: 0.16, sweep: 0.7 },
};

const INK: EventTable = {
  click:      { type: 'triangle', freq: 280, dur: 0.05,  vol: 0.05, attack: 0.005, decay: 0.05, lp: 1500 },
  hover:      { type: 'sine',     freq: 440, dur: 0.018, vol: 0.02, attack: 0.001, decay: 0.018 },
  type:       { type: 'sine',     freq: 220, dur: 0.04,  vol: 0.04, attack: 0.005, decay: 0.04, lp: 600 },
  success:    { type: 'sine',     freq: 660, dur: 1.2,   vol: 0.08, attack: 0.01,  decay: 1.2 },
  error:      { type: 'triangle', freq: 174, dur: 0.4,   vol: 0.08, attack: 0.005, decay: 0.4,  lp: 800 },
  run:        { type: 'sine',     freq: 523, dur: 0.6,   vol: 0.08, attack: 0.02,  decay: 0.6 },
  save:       { type: 'sine',     freq: 440, dur: 0.8,   vol: 0.10, attack: 0.01,  decay: 0.8 },
  modalOpen:  { type: 'sine',     freq: 523, dur: 0.4,   vol: 0.06, attack: 0.04,  decay: 0.4 },
  modalClose: { type: 'sine',     freq: 392, dur: 0.4,   vol: 0.05, attack: 0.005, decay: 0.4 },
};

const CLOCKWORK: EventTable = {
  click:      { type: 'square',   freq: 800,  dur: 0.04,  vol: 0.06, attack: 0.001, decay: 0.04,  lp: 2200 },
  hover:      { type: 'square',   freq: 1100, dur: 0.012, vol: 0.02, attack: 0.001, decay: 0.012 },
  type:       { type: 'square',   freq: 600,  dur: 0.025, vol: 0.05, attack: 0.001, decay: 0.025, lp: 1800 },
  success:    { type: 'sawtooth', freq: 523,  dur: 0.5,   vol: 0.08, attack: 0.005, decay: 0.5,   sweep: 1.5, lp: 2500 },
  error:      { type: 'sawtooth', freq: 196,  dur: 0.35,  vol: 0.10, attack: 0.005, decay: 0.35,  lp: 700 },
  run:        { type: 'square',   freq: 440,  dur: 0.30,  vol: 0.10, attack: 0.005, decay: 0.30,  sweep: 1.4, lp: 2000 },
  save:       { type: 'triangle', freq: 660,  dur: 0.45,  vol: 0.10, attack: 0.01,  decay: 0.45,  lp: 2400 },
  modalOpen:  { type: 'square',   freq: 392,  dur: 0.18,  vol: 0.07, attack: 0.005, decay: 0.18,  sweep: 1.3, lp: 1800 },
  modalClose: { type: 'square',   freq: 392,  dur: 0.14,  vol: 0.06, attack: 0.005, decay: 0.14,  sweep: 0.65, lp: 1400 },
};

const VIBRAPHONE: EventTable = {
  click:      { type: 'triangle', freq: 880,  dur: 0.10, vol: 0.06, attack: 0.001, decay: 0.10 },
  hover:      { type: 'triangle', freq: 1320, dur: 0.05, vol: 0.025, attack: 0.001, decay: 0.05 },
  type:       { type: 'sine',     freq: 660,  dur: 0.06, vol: 0.04, attack: 0.001, decay: 0.06 },
  success:    { type: 'triangle', freq: 988,  dur: 0.5,  vol: 0.09, attack: 0.001, decay: 0.5,  sweep: 1.5 },
  error:      { type: 'sawtooth', freq: 233,  dur: 0.25, vol: 0.10, attack: 0.005, decay: 0.25, sweep: 0.6 },
  run:        { type: 'triangle', freq: 1175, dur: 0.30, vol: 0.10, attack: 0.001, decay: 0.30, sweep: 1.4 },
  save:       { type: 'triangle', freq: 880,  dur: 0.40, vol: 0.10, attack: 0.001, decay: 0.40 },
  modalOpen:  { type: 'triangle', freq: 988,  dur: 0.18, vol: 0.06, attack: 0.001, decay: 0.18, sweep: 1.4 },
  modalClose: { type: 'triangle', freq: 988,  dur: 0.18, vol: 0.06, attack: 0.001, decay: 0.18, sweep: 0.65 },
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

const SCRAP: EventTable = {
  click:      { type: 'sawtooth', freq: 180, dur: 0.05,  vol: 0.07, attack: 0.0005, decay: 0.05,  lp: 1400 },
  hover:      { type: 'sawtooth', freq: 320, dur: 0.012, vol: 0.025, attack: 0.0005, decay: 0.012, lp: 2000 },
  type:       { type: 'square',   freq: 140, dur: 0.03,  vol: 0.05, attack: 0.0005, decay: 0.03,  lp: 800 },
  success:    { type: 'sawtooth', freq: 440, dur: 0.18,  vol: 0.10, attack: 0.001,  decay: 0.18,  sweep: 1.3, lp: 1600 },
  error:      { type: 'sawtooth', freq: 90,  dur: 0.5,   vol: 0.12, attack: 0.001,  decay: 0.5,   lp: 400 },
  run:        { type: 'square',   freq: 220, dur: 0.20,  vol: 0.10, attack: 0.001,  decay: 0.20,  sweep: 1.2, lp: 1200 },
  save:       { type: 'sawtooth', freq: 330, dur: 0.18,  vol: 0.10, attack: 0.001,  decay: 0.18,  lp: 1400 },
  modalOpen:  { type: 'sawtooth', freq: 196, dur: 0.10,  vol: 0.07, attack: 0.001,  decay: 0.10,  lp: 1000 },
  modalClose: { type: 'sawtooth', freq: 196, dur: 0.08,  vol: 0.06, attack: 0.001,  decay: 0.08,  sweep: 0.5, lp: 700 },
};

const CRYPT: EventTable = {
  click:      { type: 'sine',     freq: 220, dur: 0.08,  vol: 0.06, attack: 0.005, decay: 0.08, lp: 600 },
  hover:      { type: 'sine',     freq: 330, dur: 0.025, vol: 0.02, attack: 0.005, decay: 0.025, lp: 800 },
  type:       { type: 'sine',     freq: 165, dur: 0.04,  vol: 0.04, attack: 0.005, decay: 0.04, lp: 400 },
  success:    { type: 'triangle', freq: 261, dur: 0.8,   vol: 0.08, attack: 0.02,  decay: 0.8,  sweep: 1.5, lp: 1200 },
  error:      { type: 'sawtooth', freq: 73,  dur: 0.6,   vol: 0.10, attack: 0.01,  decay: 0.6,  lp: 300 },
  run:        { type: 'sine',     freq: 196, dur: 0.45,  vol: 0.10, attack: 0.02,  decay: 0.45, sweep: 1.3, lp: 800 },
  save:       { type: 'sine',     freq: 174, dur: 0.7,   vol: 0.10, attack: 0.05,  decay: 0.7,  lp: 700 },
  modalOpen:  { type: 'sine',     freq: 220, dur: 0.5,   vol: 0.07, attack: 0.05,  decay: 0.5,  lp: 600 },
  modalClose: { type: 'sine',     freq: 220, dur: 0.4,   vol: 0.06, attack: 0.005, decay: 0.4,  sweep: 0.6, lp: 500 },
};

const BELL: EventTable = {
  click:      { type: 'triangle', freq: 392, dur: 0.10,  vol: 0.06, attack: 0.001, decay: 0.10, lp: 1500 },
  hover:      { type: 'sine',     freq: 660, dur: 0.025, vol: 0.025, attack: 0.001, decay: 0.025 },
  type:       { type: 'square',   freq: 220, dur: 0.025, vol: 0.04, attack: 0.001, decay: 0.025, lp: 800 },
  success:    { type: 'triangle', freq: 523, dur: 1.0,   vol: 0.09, attack: 0.001, decay: 1.0 },
  error:      { type: 'sawtooth', freq: 110, dur: 0.6,   vol: 0.10, attack: 0.005, decay: 0.6,  lp: 500 },
  run:        { type: 'triangle', freq: 440, dur: 0.6,   vol: 0.10, attack: 0.001, decay: 0.6 },
  save:       { type: 'triangle', freq: 392, dur: 0.9,   vol: 0.10, attack: 0.001, decay: 0.9 },
  modalOpen:  { type: 'triangle', freq: 523, dur: 0.4,   vol: 0.07, attack: 0.001, decay: 0.4 },
  modalClose: { type: 'triangle', freq: 392, dur: 0.4,   vol: 0.06, attack: 0.001, decay: 0.4 },
};

const TELEX: EventTable = {
  click:      { type: 'square',   freq: 1500, dur: 0.012, vol: 0.07, attack: 0.0005, decay: 0.012 },
  hover:      { type: 'square',   freq: 2200, dur: 0.006, vol: 0.02, attack: 0.0005, decay: 0.006 },
  type:       { type: 'square',   freq: 1100, dur: 0.010, vol: 0.05, attack: 0.0005, decay: 0.010 },
  success:    { type: 'square',   freq: 1318, dur: 0.10,  vol: 0.08, attack: 0.0005, decay: 0.10 },
  error:      { type: 'sawtooth', freq: 220,  dur: 0.4,   vol: 0.12, attack: 0.0005, decay: 0.4 },
  run:        { type: 'square',   freq: 1760, dur: 0.06,  vol: 0.10, attack: 0.0005, decay: 0.06 },
  save:       { type: 'square',   freq: 1175, dur: 0.08,  vol: 0.08, attack: 0.0005, decay: 0.08 },
  modalOpen:  { type: 'square',   freq: 988,  dur: 0.05,  vol: 0.07, attack: 0.0005, decay: 0.05 },
  modalClose: { type: 'square',   freq: 988,  dur: 0.04,  vol: 0.06, attack: 0.0005, decay: 0.04, sweep: 0.6 },
};

const PACKS: Record<SoundPackName, EventTable> = {
  none: {},
  parchment: PARCHMENT,
  marble: MARBLE,
  ink: INK,
  clockwork: CLOCKWORK,
  vibraphone: VIBRAPHONE,
  terminal: TERMINAL,
  scrap: SCRAP,
  crypt: CRYPT,
  bell: BELL,
  telex: TELEX,
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
};
