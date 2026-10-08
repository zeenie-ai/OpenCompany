/**
 * The orb's lifecycle (orb.ts): stages and slots are stacks, so a screen
 * mounted over another borrows the orb and hands it back; the engine loads
 * once; a failed engine load shows the mark until the next stage tries
 * again, while no WebGL, reduced motion and a lost context hold for good.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const engine = { attach: vi.fn(), detach: vi.fn(), dispose: vi.fn() };
const createOrbEngine = vi.fn((onLost: () => void) => {
  lose = onLost;
  return engine;
});
let lose: () => void = () => {};

vi.mock('../orbEngine', () => ({ createOrbEngine: (onLost: () => void) => createOrbEngine(onLost) }));

import { setReducedMotion } from '@/test/waapi';
import { mountOrb, orbState, popOrbSlot, pushOrbSlot, resetOrbForTests, unmountOrb, useOrbFallback } from '../orb';

const element = () => document.createElement('div');

/** Let the engine's chunk "load". */
async function loaded() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

let restoreMotion: () => void = () => {};
let getContext: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  resetOrbForTests();
  engine.attach.mockClear();
  engine.detach.mockClear();
  engine.dispose.mockClear();
  createOrbEngine.mockClear();
  getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as RenderingContext);
});

afterEach(() => {
  restoreMotion();
  restoreMotion = () => {};
  getContext.mockRestore();
  resetOrbForTests();
});

describe('slots', () => {
  it('fill the newest slot, and the one under it again when it goes', () => {
    const home = element();
    const connecting = element();
    pushOrbSlot(home);
    pushOrbSlot(connecting);
    expect(orbState.slot).toBe(connecting);
    popOrbSlot(connecting);
    expect(orbState.slot).toBe(home);
    popOrbSlot(home);
    expect(orbState.slot).toBeNull();
  });

  it('keep the newest slot when an older one goes first', () => {
    const home = element();
    const connecting = element();
    pushOrbSlot(home);
    pushOrbSlot(connecting);
    popOrbSlot(home);
    expect(orbState.slot).toBe(connecting);
  });
});

describe('stages', () => {
  it('load the engine once, and hand the orb back to the stage underneath', async () => {
    const home = element();
    const overlay = element();
    mountOrb(home);
    await loaded();
    expect(createOrbEngine).toHaveBeenCalledTimes(1);
    expect(engine.attach).toHaveBeenLastCalledWith(home);

    mountOrb(overlay);
    expect(engine.attach).toHaveBeenLastCalledWith(overlay);
    unmountOrb(overlay);
    expect(engine.detach).toHaveBeenCalledTimes(1);
    expect(engine.attach).toHaveBeenLastCalledWith(home);
    expect(createOrbEngine).toHaveBeenCalledTimes(1);
  });

  it('leave the orb where it is when a stage underneath goes', async () => {
    const home = element();
    const overlay = element();
    mountOrb(home);
    await loaded();
    mountOrb(overlay);
    unmountOrb(home);
    expect(engine.detach).not.toHaveBeenCalled();
    expect(engine.attach).toHaveBeenLastCalledWith(overlay);
  });

  it('attach to the newest stage once a slow engine loads', async () => {
    const home = element();
    const overlay = element();
    mountOrb(home);
    mountOrb(overlay);
    await loaded();
    expect(createOrbEngine).toHaveBeenCalledTimes(1);
    expect(engine.attach).toHaveBeenCalledTimes(1);
    expect(engine.attach).toHaveBeenLastCalledWith(overlay);
  });
});

describe('the static mark', () => {
  it('shows while the engine failed to load, and the next stage tries again', async () => {
    const { result } = renderHook(() => useOrbFallback());
    createOrbEngine.mockImplementationOnce(() => {
      throw new Error('the chunk did not load');
    });
    const first = element();
    mountOrb(first);
    await loaded();
    expect(result.current).toBe(true);

    unmountOrb(first);
    mountOrb(element());
    await loaded();
    expect(createOrbEngine).toHaveBeenCalledTimes(2);
    expect(result.current).toBe(false);
  });

  it('holds without WebGL, and nothing tries again', async () => {
    getContext.mockReturnValue(null);
    const { result } = renderHook(() => useOrbFallback());
    mountOrb(element());
    await loaded();
    expect(result.current).toBe(true);
    getContext.mockReturnValue({} as RenderingContext);
    mountOrb(element());
    await loaded();
    expect(createOrbEngine).not.toHaveBeenCalled();
    expect(result.current).toBe(true);
  });

  it('holds under reduced motion', async () => {
    restoreMotion = setReducedMotion(true);
    const { result } = renderHook(() => useOrbFallback());
    mountOrb(element());
    await loaded();
    expect(createOrbEngine).not.toHaveBeenCalled();
    expect(result.current).toBe(true);
  });

  it('holds after the WebGL context is lost', async () => {
    const { result } = renderHook(() => useOrbFallback());
    mountOrb(element());
    await loaded();
    act(() => lose());
    expect(engine.dispose).toHaveBeenCalledTimes(1);
    expect(result.current).toBe(true);
    mountOrb(element());
    await loaded();
    expect(createOrbEngine).toHaveBeenCalledTimes(1);
  });
});
