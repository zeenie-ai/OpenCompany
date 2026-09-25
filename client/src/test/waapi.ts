/**
 * Web Animations stub for tests.
 *
 * jsdom ships no `Element.animate`, so lib/motion.ts returns null there and
 * nothing animates. Tests that assert on animations install this stub: every
 * call is recorded with its target, keyframes and resolved timing, and
 * returns a FakeAnimation whose `finished` promise the test settles with
 * `finish()`.
 *
 *   const waapi = installWaapiStub();
 *   afterEach(() => waapi.restore());
 *   ...
 *   expect(waapi.calls[0].options).toMatchObject({ duration: 760 });
 */

import { vi } from 'vitest';

type Timing = KeyframeAnimationOptions | number | undefined;

export class FakeAnimation {
  playState: AnimationPlayState = 'running';
  readonly finished: Promise<FakeAnimation>;
  readonly effect: { getComputedTiming: () => { endTime: number } };
  private settle!: { resolve: (a: FakeAnimation) => void; reject: (e: unknown) => void };

  constructor(timing: Timing) {
    this.finished = new Promise<FakeAnimation>((resolve, reject) => {
      this.settle = { resolve, reject };
    });
    // A real `finished` promise is marked handled; keep cancelled
    // animations from surfacing as unhandled rejections.
    this.finished.catch(() => {});
    const t = typeof timing === 'number' ? { duration: timing } : (timing ?? {});
    const iterations = t.iterations ?? 1;
    const duration = typeof t.duration === 'number' ? t.duration : 0;
    const endTime = iterations === Infinity ? Infinity : (t.delay ?? 0) + duration * iterations;
    this.effect = { getComputedTiming: () => ({ endTime }) };
  }

  play(): void {
    this.playState = 'running';
  }

  pause(): void {
    this.playState = 'paused';
  }

  finish(): void {
    this.playState = 'finished';
    this.settle.resolve(this);
  }

  cancel(): void {
    this.playState = 'idle';
    this.settle.reject(new DOMException('The animation was cancelled', 'AbortError'));
  }
}

export interface RecordedAnimation {
  target: Element;
  keyframes: Keyframe[] | PropertyIndexedKeyframes | null;
  options: KeyframeAnimationOptions;
  animation: FakeAnimation;
}

export function installWaapiStub() {
  const calls: RecordedAnimation[] = [];
  const original = Object.getOwnPropertyDescriptor(Element.prototype, 'animate');
  const animate = vi.fn(function (
    this: Element,
    keyframes: Keyframe[] | PropertyIndexedKeyframes | null,
    options?: Timing,
  ) {
    const animation = new FakeAnimation(options);
    const resolved = typeof options === 'number' ? { duration: options } : (options ?? {});
    calls.push({ target: this, keyframes, options: resolved, animation });
    return animation as unknown as Animation;
  });
  Object.defineProperty(Element.prototype, 'animate', { configurable: true, writable: true, value: animate });

  return {
    calls,
    animate,
    /** Calls whose target is `el` (or, with a pseudo-element, the host). */
    callsFor(el: Element | null | undefined): RecordedAnimation[] {
      return calls.filter((c) => c.target === el);
    },
    restore(): void {
      if (original) Object.defineProperty(Element.prototype, 'animate', original);
      else delete (Element.prototype as { animate?: unknown }).animate;
    },
  };
}

/** Make `matchMedia('(prefers-reduced-motion: reduce)')` match (or not). */
export function setReducedMotion(reduce: boolean): () => void {
  const original = window.matchMedia;
  window.matchMedia = ((query: string) => ({
    matches: reduce && query.includes('prefers-reduced-motion'),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
  return () => {
    window.matchMedia = original;
  };
}
