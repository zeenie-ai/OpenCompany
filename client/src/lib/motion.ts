/**
 * Motion — the one place Web Animations are started.
 *
 * Durations and easings come from CSS tokens (`--dur-*` / `--ease-*` in
 * themes/base.css), read from the computed style of <html> and cached per
 * theme. A theme that retunes its motion (Cyber's stepped easing, Atomic's
 * bounce) therefore retunes every animation that names the token. Two
 * policies are applied here instead of at each call site:
 *
 * - prefers-reduced-motion: durations become 1 ms and delays 0, so elements
 *   still land in their final state; looping animations do not start.
 * - page not visible (pageActivity): one-shot animations run at 1 ms because
 *   nobody sees them; loops pause and resume with the page.
 *
 * Everything returns null / no-ops where `Element.animate` is missing (jsdom,
 * very old engines), so callers never need a guard.
 *
 * Default fill is `backwards`: an entrance shows its first keyframe during
 * its delay, then hands the element back to its CSS. A persistent fill would
 * override hover and state styles forever after, so pass `fill: 'forwards'`
 * only for exits whose element is about to be removed.
 */

import { pageActivity } from './pageActivity';
import { prefersReducedMotion } from './useReducedMotion';

export type EaseName = 'default' | 'emphasis' | 'spring' | 'overshoot' | 'reveal';

export type DurName =
  | 'fast'
  | 'default'
  | 'slow'
  | 'intro'
  | 'card-in'
  | 'view-swap'
  | 'panel-in'
  | 'panel-out'
  | 'scrim-in'
  | 'sidebar-in'
  | 'sidebar-out'
  | 'toast-in'
  | 'toast-hold'
  | 'toast-out'
  | 'mode-out'
  | 'mode-in'
  | 'theme-reveal'
  | 'theme-icon'
  | 'theme-fade'
  | 'glow'
  | 'pip-loop'
  | 'switch';

/** Used when a token is unreadable (no DOM, or a theme that omits it).
 *  Mirrors themes/base.css; keep the two in step. */
const EASE_FALLBACK: Record<EaseName, string> = {
  default: 'cubic-bezier(0.2, 0.7, 0.3, 1)',
  emphasis: 'cubic-bezier(0.6, -0.05, 0.3, 1.4)',
  spring: 'cubic-bezier(0.16, 1, 0.3, 1)',
  overshoot: 'cubic-bezier(0.34, 1.56, 0.64, 1)',
  reveal: 'cubic-bezier(0.65, 0, 0.35, 1)',
};

const DUR_FALLBACK: Record<DurName, number> = {
  fast: 90,
  default: 180,
  slow: 320,
  intro: 720,
  'card-in': 700,
  'view-swap': 520,
  'panel-in': 520,
  'panel-out': 200,
  'scrim-in': 280,
  'sidebar-in': 380,
  'sidebar-out': 300,
  'toast-in': 420,
  'toast-hold': 2600,
  'toast-out': 240,
  'mode-out': 240,
  'mode-in': 420,
  'theme-reveal': 760,
  'theme-icon': 700,
  'theme-fade': 460,
  glow: 1400,
  'pip-loop': 1600,
  switch: 280,
};

let cacheTheme: string | null = null;
const tokenCache = new Map<string, string>();

function readToken(name: string): string {
  if (typeof document === 'undefined') return '';
  const theme = document.documentElement.dataset.theme ?? '';
  if (theme !== cacheTheme) {
    tokenCache.clear();
    cacheTheme = theme;
  }
  const hit = tokenCache.get(name);
  if (hit !== undefined) return hit;
  let value: string;
  try {
    value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  } catch {
    value = '';
  }
  tokenCache.set(name, value);
  return value;
}

/** Forget cached token values. The theme provider calls this on every change. */
export function invalidateMotionTokens(): void {
  tokenCache.clear();
  cacheTheme = null;
}

/** `760ms` / `0.76s` / `760` → milliseconds; null when unparseable. */
export function parseDuration(value: string): number | null {
  const match = /^(-?\d*\.?\d+)\s*(ms|s)?$/.exec(value.trim());
  if (!match) return null;
  const n = Number.parseFloat(match[1]);
  if (!Number.isFinite(n)) return null;
  return match[2] === 's' ? n * 1000 : n;
}

export function ease(name: EaseName): string {
  return readToken(`--ease-${name}`) || EASE_FALLBACK[name];
}

export function dur(name: DurName): number {
  return parseDuration(readToken(`--dur-${name}`)) ?? DUR_FALLBACK[name];
}

/** True when motion should be skipped right now: the user asked for reduced
 *  motion, or the page is not visible. */
export function motionSuppressed(): boolean {
  return prefersReducedMotion() || !pageActivity.isActive();
}

export interface MotionOptions {
  /** A duration token name, or milliseconds. Default: `default`. */
  duration?: DurName | number;
  /** An easing token name, or any CSS easing. Default: `default`. */
  easing?: EaseName | string;
  delay?: number;
  fill?: FillMode;
  iterations?: number;
  direction?: PlaybackDirection;
  pseudoElement?: string;
}

function resolveEasing(easing: MotionOptions['easing']): string {
  if (!easing) return ease('default');
  return easing in EASE_FALLBACK ? ease(easing as EaseName) : easing;
}

function resolveDuration(duration: MotionOptions['duration']): number {
  if (duration === undefined) return dur('default');
  return typeof duration === 'number' ? duration : dur(duration);
}

type AnimatableElement = Element & { animate?: Element['animate'] };

export function animate(
  el: Element | null | undefined,
  keyframes: Keyframe[] | PropertyIndexedKeyframes,
  options: MotionOptions = {},
): Animation | null {
  const target = el as AnimatableElement | null | undefined;
  if (!target || typeof target.animate !== 'function') return null;
  const looping = options.iterations === Infinity;
  const suppressed = motionSuppressed();
  if (looping && suppressed) return null;
  const timing: KeyframeAnimationOptions = {
    duration: suppressed ? 1 : resolveDuration(options.duration),
    easing: resolveEasing(options.easing),
    delay: suppressed ? 0 : options.delay ?? 0,
    fill: options.fill ?? 'backwards',
    iterations: options.iterations ?? 1,
  };
  if (options.direction) timing.direction = options.direction;
  if (options.pseudoElement) timing.pseudoElement = options.pseudoElement;
  try {
    return target.animate(keyframes, timing);
  } catch {
    return null;
  }
}

export interface StaggerOptions extends MotionOptions {
  /** Delay before the first element, ms. */
  base?: number;
  /** Delay added per element, ms. */
  step?: number;
  /** Stop growing the delay after this many elements. */
  cap?: number;
}

export function stagger(
  els: ArrayLike<Element> | Iterable<Element>,
  keyframes: Keyframe[] | PropertyIndexedKeyframes,
  { base = 0, step = 80, cap = Number.POSITIVE_INFINITY, ...options }: StaggerOptions = {},
): Animation[] {
  const started: Animation[] = [];
  Array.from(els).forEach((el, i) => {
    const anim = animate(el, keyframes, { ...options, delay: base + step * Math.min(i, cap) });
    if (anim) started.push(anim);
  });
  return started;
}

/** Resolves when the animation ends, is cancelled, or overruns its own
 *  timing by a small margin (so a throttled background tab never hangs a
 *  choreography waiting on it). Never rejects. */
export function finished(anim: Animation | null): Promise<void> {
  if (!anim) return Promise.resolve();
  return new Promise((resolve) => {
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    anim.finished.then(done, done);
    const end = anim.effect?.getComputedTiming?.().endTime;
    const total = typeof end === 'number' && Number.isFinite(end) ? end : 0;
    window.setTimeout(done, total + 120);
  });
}

/** Start a looping animation that pauses while the page is not visible.
 *  Returns a stop function. Under reduced motion nothing starts. */
export function loop(
  el: Element | null | undefined,
  keyframes: Keyframe[] | PropertyIndexedKeyframes,
  options: Omit<MotionOptions, 'iterations'> = {},
): () => void {
  const anim = animate(el, keyframes, { fill: 'none', ...options, iterations: Number.POSITIVE_INFINITY });
  if (!anim) return () => {};
  const unsubscribe = pageActivity.subscribe((active) => {
    if (active) anim.play();
    else anim.pause();
  });
  return () => {
    unsubscribe();
    anim.cancel();
  };
}
