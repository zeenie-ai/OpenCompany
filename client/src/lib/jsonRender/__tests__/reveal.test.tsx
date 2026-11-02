/**
 * The patch stream for a spec (root, state, then each element depth first,
 * ids escaped as RFC 6901 says), held line for line against the chat
 * fixtures, and its paced replay: one element every 90 ms, forward only
 * as the spec grows, everything at once under reduced motion or for a UI
 * that is not live.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import type { Spec } from '@json-render/core';
import { setReducedMotion } from '@/test/waapi';
import { REVEAL_STEP_MS, specToPatches, useSpecReveal } from '../reveal';

const FIXTURES = join(__dirname, '..', '..', '..', 'features', 'chat', '__fixtures__');

function read(name: string): string {
  return readFileSync(join(FIXTURES, name), 'utf-8');
}

const SPEC: Spec = {
  root: 'r',
  state: { on: true },
  elements: {
    r: { type: 'Stack', props: {}, children: ['a', 'b/c', 'r'] },
    a: { type: 'Card', props: {}, children: ['a1', 'missing', 'a2'] },
    a1: { type: 'Text', props: { text: '1' } },
    a2: { type: 'Text', props: { text: '2' } },
    'b/c': { type: 'Text', props: { text: 'slash' }, children: ['a1'] },
    'x~y': { type: 'Text', props: { text: 'unreachable' } },
  },
};

describe('specToPatches', () => {
  it.each(['saturday-booking', 'reply-insights', 'reminders'])('matches %s.patches.jsonl line by line', (name) => {
    const spec = JSON.parse(read(`${name}.spec.json`)) as Spec;
    const lines = read(`${name}.patches.jsonl`)
      .split(/\r?\n/)
      .filter((line) => line.trim() !== '');
    const patches = specToPatches(spec).map((patch) => JSON.stringify(patch));
    expect(patches).toEqual(lines);
  });

  it('walks depth first from the root, each element once, escaping ids', () => {
    expect(specToPatches(SPEC).map((patch) => patch.path)).toEqual([
      '/root',
      '/state',
      '/elements/r',
      '/elements/a',
      '/elements/a1',
      '/elements/a2',
      '/elements/b~1c',
    ]);
    expect(specToPatches({ root: 'r', elements: {} })).toEqual([
      { op: 'add', path: '/root', value: 'r' },
      { op: 'add', path: '/state', value: {} },
    ]);
  });
});

describe('useSpecReveal', () => {
  let restoreMotion: () => void;

  beforeEach(() => {
    restoreMotion = setReducedMotion(false);
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'performance'] });
  });

  afterEach(() => {
    vi.useRealTimers();
    restoreMotion();
  });

  const shownIds = (result: { current: ReturnType<typeof useSpecReveal> }) => Object.keys(result.current.spec?.elements ?? {});

  it('shows one element every REVEAL_STEP_MS, depth first', () => {
    const { result } = renderHook(() => useSpecReveal(SPEC));
    expect(result.current.spec?.root).toBe('r');
    expect(shownIds(result)).toEqual(['r']);
    expect(result.current.revealing).toBe(true);
    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS);
    });
    expect(shownIds(result)).toEqual(['r', 'a']);
    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS * 2);
    });
    expect(shownIds(result)).toEqual(['r', 'a', 'a1', 'a2']);
    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS * 10);
    });
    // Once everything shows, the spec itself is drawn.
    expect(result.current.spec).toBe(SPEC);
    expect(result.current.revealing).toBe(false);
  });

  it('only moves forward while the spec grows', () => {
    const first: Spec = { root: 'r', elements: { r: { type: 'Stack', props: {}, children: ['a'] }, a: SPEC.elements.a1 } };
    const { result, rerender } = renderHook(({ spec }) => useSpecReveal(spec), { initialProps: { spec: first } });
    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS * 10);
    });
    expect(result.current.spec).toBe(first);
    rerender({ spec: SPEC });
    expect(shownIds(result)).toEqual(['r', 'a']);
    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS * 10);
    });
    expect(result.current.spec).toBe(SPEC);
  });

  it('shows a UI that is not live at once', () => {
    const { result } = renderHook(() => useSpecReveal(SPEC, false));
    expect(result.current).toEqual({ spec: SPEC, revealing: false });
  });

  it('shows everything at once under reduced motion', () => {
    restoreMotion();
    restoreMotion = setReducedMotion(true);
    const { result } = renderHook(() => useSpecReveal(SPEC));
    expect(result.current.spec).toBe(SPEC);
    expect(result.current.revealing).toBe(false);
  });

  it('has nothing to show without a spec', () => {
    const { result } = renderHook(() => useSpecReveal(null));
    expect(result.current).toEqual({ spec: null, revealing: false });
  });
});
