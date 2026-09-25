import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { animate, dur, ease, finished, invalidateMotionTokens, loop, motionSuppressed, parseDuration, stagger } from '../motion';
import { pageActivity } from '../pageActivity';
import { installWaapiStub, setReducedMotion } from '../../test/waapi';

const root = document.documentElement;

function setToken(name: string, value: string): void {
  root.style.setProperty(name, value);
  invalidateMotionTokens();
}

describe('parseDuration', () => {
  it.each([
    ['760ms', 760],
    ['0.76s', 760],
    ['760', 760],
    [' 90ms ', 90],
  ])('%j -> %d', (input, expected) => {
    expect(parseDuration(input)).toBe(expected);
  });

  it.each(['', 'fast', 'calc(1s)', 'ms'])('%j is unparseable', (input) => {
    expect(parseDuration(input)).toBeNull();
  });
});

describe('token reads', () => {
  afterEach(() => {
    root.removeAttribute('style');
    root.removeAttribute('data-theme');
    invalidateMotionTokens();
  });

  it('reads a duration token from <html>', () => {
    setToken('--dur-intro', '999ms');
    expect(dur('intro')).toBe(999);
  });

  it('falls back to the base.css value when the token is missing or unparseable', () => {
    expect(dur('theme-reveal')).toBe(760);
    setToken('--dur-theme-reveal', 'soon');
    expect(dur('theme-reveal')).toBe(760);
  });

  it('reads an easing token, falling back to the base.css curve', () => {
    expect(ease('spring')).toBe('cubic-bezier(0.16, 1, 0.3, 1)');
    setToken('--ease-spring', 'steps(4)');
    expect(ease('spring')).toBe('steps(4)');
  });

  it('re-reads tokens when the theme changes', () => {
    setToken('--dur-glow', '100ms');
    expect(dur('glow')).toBe(100);
    root.style.setProperty('--dur-glow', '200ms');
    // Same theme: the cached value stands until invalidated...
    expect(dur('glow')).toBe(100);
    // ...a theme change drops the cache on its own.
    root.dataset.theme = 'cyber';
    expect(dur('glow')).toBe(200);
  });
});

describe('animate', () => {
  let waapi: ReturnType<typeof installWaapiStub>;
  let restoreMotion: () => void;

  beforeEach(() => {
    waapi = installWaapiStub();
    restoreMotion = setReducedMotion(false);
    pageActivity.set(true);
  });

  afterEach(() => {
    waapi.restore();
    restoreMotion();
    pageActivity.set(true);
    invalidateMotionTokens();
  });

  it('returns null without Web Animations', () => {
    waapi.restore();
    expect(animate(document.createElement('div'), [{ opacity: 0 }, { opacity: 1 }])).toBeNull();
  });

  it('returns null for a missing element', () => {
    expect(animate(null, [{ opacity: 0 }])).toBeNull();
    expect(waapi.calls).toHaveLength(0);
  });

  it('resolves named durations and easings, defaulting fill to backwards', () => {
    const el = document.createElement('div');
    animate(el, [{ opacity: 0 }, { opacity: 1 }], { duration: 'panel-in', easing: 'spring', delay: 40 });
    expect(waapi.calls).toHaveLength(1);
    expect(waapi.calls[0].target).toBe(el);
    expect(waapi.calls[0].options).toMatchObject({
      duration: 520,
      easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
      delay: 40,
      fill: 'backwards',
      iterations: 1,
    });
  });

  it('passes numeric durations and raw CSS easings through', () => {
    animate(document.createElement('div'), [{ opacity: 0 }], { duration: 333, easing: 'linear', fill: 'forwards' });
    expect(waapi.calls[0].options).toMatchObject({ duration: 333, easing: 'linear', fill: 'forwards' });
  });

  it('collapses motion to 1ms with no delay under reduced motion', () => {
    restoreMotion();
    restoreMotion = setReducedMotion(true);
    expect(motionSuppressed()).toBe(true);
    animate(document.createElement('div'), [{ opacity: 0 }], { duration: 'intro', delay: 300 });
    expect(waapi.calls[0].options).toMatchObject({ duration: 1, delay: 0 });
  });

  it('collapses motion while the page is not visible', () => {
    pageActivity.set(false);
    animate(document.createElement('div'), [{ opacity: 0 }], { duration: 'intro', delay: 300 });
    expect(waapi.calls[0].options).toMatchObject({ duration: 1, delay: 0 });
  });

  it('never starts a loop when motion is suppressed', () => {
    pageActivity.set(false);
    expect(animate(document.createElement('div'), [{ opacity: 0 }], { iterations: Infinity })).toBeNull();
    expect(waapi.calls).toHaveLength(0);
  });

  it('staggers delays from a base, capped', () => {
    const els = Array.from({ length: 5 }, () => document.createElement('div'));
    const started = stagger(els, [{ opacity: 0 }], { base: 100, step: 30, cap: 3 });
    expect(started).toHaveLength(5);
    expect(waapi.calls.map((c) => c.options.delay)).toEqual([100, 130, 160, 190, 190]);
  });
});

describe('finished', () => {
  let waapi: ReturnType<typeof installWaapiStub>;

  beforeEach(() => {
    waapi = installWaapiStub();
  });

  afterEach(() => {
    waapi.restore();
    vi.useRealTimers();
  });

  it('resolves immediately for no animation', async () => {
    await expect(finished(null)).resolves.toBeUndefined();
  });

  it('resolves when the animation finishes or is cancelled', async () => {
    const a = animate(document.createElement('div'), [{ opacity: 0 }], { duration: 10_000 });
    const b = animate(document.createElement('div'), [{ opacity: 0 }], { duration: 10_000 });
    const doneA = finished(a);
    const doneB = finished(b);
    waapi.calls[0].animation.finish();
    waapi.calls[1].animation.cancel();
    await expect(doneA).resolves.toBeUndefined();
    await expect(doneB).resolves.toBeUndefined();
  });

  it('gives up shortly after the animation should have ended', async () => {
    vi.useFakeTimers();
    const a = animate(document.createElement('div'), [{ opacity: 0 }], { duration: 200, delay: 100 });
    let settled = false;
    void finished(a).then(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(300);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(130);
    expect(settled).toBe(true);
  });
});

describe('loop', () => {
  let waapi: ReturnType<typeof installWaapiStub>;
  let restoreMotion: () => void;

  beforeEach(() => {
    waapi = installWaapiStub();
    restoreMotion = setReducedMotion(false);
    pageActivity.set(true);
  });

  afterEach(() => {
    waapi.restore();
    restoreMotion();
    pageActivity.set(true);
  });

  it('pauses with the page and cancels on stop', () => {
    const stop = loop(document.createElement('div'), [{ opacity: 0.4 }, { opacity: 1 }], { duration: 'pip-loop' });
    const anim = waapi.calls[0].animation;
    expect(waapi.calls[0].options).toMatchObject({ iterations: Infinity, fill: 'none', duration: 1600 });
    pageActivity.set(false);
    expect(anim.playState).toBe('paused');
    pageActivity.set(true);
    expect(anim.playState).toBe('running');
    stop();
    expect(anim.playState).toBe('idle');
    // Stopped loops stop listening.
    pageActivity.set(false);
    expect(anim.playState).toBe('idle');
  });

  it('starts nothing under reduced motion', () => {
    restoreMotion();
    restoreMotion = setReducedMotion(true);
    const stop = loop(document.createElement('div'), [{ opacity: 0 }]);
    expect(waapi.calls).toHaveLength(0);
    expect(() => stop()).not.toThrow();
  });
});
