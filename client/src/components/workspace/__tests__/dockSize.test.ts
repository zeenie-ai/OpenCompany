/**
 * The Workspace dock's width rules, shared by Home and the editor: what a
 * drag is kept to, and the width the dock is given as CSS.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { clampDockWidth, dockWidth } from '../dockSize';

/** The window is this wide. */
const windowIs = (px: number) => vi.stubGlobal('innerWidth', px);

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('clampDockWidth', () => {
  it('keeps a width between the narrowest and what the window allows', () => {
    windowIs(1280);
    expect(clampDockWidth(360)).toBe(360);
    expect(clampDockWidth(500)).toBe(500);
    expect(clampDockWidth(860)).toBe(860);
  });

  it('stops at 360px for a width below the narrowest', () => {
    windowIs(1280);
    expect(clampDockWidth(359)).toBe(360);
    expect(clampDockWidth(10)).toBe(360);
    expect(clampDockWidth(-200)).toBe(360);
  });

  it('leaves the rest of the screen 420px for a width above what the window allows', () => {
    windowIs(1280);
    expect(clampDockWidth(861)).toBe(860);
    expect(clampDockWidth(99_999)).toBe(860);
    windowIs(1920);
    expect(clampDockWidth(99_999)).toBe(1500);
  });

  it('rounds to a whole pixel', () => {
    windowIs(1280);
    expect(clampDockWidth(500.4)).toBe(500);
    expect(clampDockWidth(500.5)).toBe(501);
    // At the limits too.
    expect(clampDockWidth(360.4)).toBe(360);
    expect(clampDockWidth(859.6)).toBe(860);
  });

  it('gives 360px in a window narrower than 780px, whatever is asked', () => {
    windowIs(779);
    expect(clampDockWidth(10)).toBe(360);
    expect(clampDockWidth(500)).toBe(360);
    expect(clampDockWidth(99_999)).toBe(360);
    windowIs(320);
    expect(clampDockWidth(500)).toBe(360);
    // At 780px the two limits meet; a pixel wider, a drag has a pixel of room.
    windowIs(780);
    expect(clampDockWidth(500)).toBe(360);
    windowIs(781);
    expect(clampDockWidth(500)).toBe(361);
  });
});

describe('dockWidth', () => {
  it('is the dragged width, never wider than the window less 40px', () => {
    expect(dockWidth(460, false)).toBe('min(460px, max(340px, 100vw - 40px))');
    expect(dockWidth(612, false)).toBe('min(612px, max(340px, 100vw - 40px))');
  });

  it('is Expand’s width while expanded, whatever was dragged', () => {
    expect(dockWidth(460, true)).toBe('max(420px, min(1100px, 100vw - 420px))');
    expect(dockWidth(612, true)).toBe(dockWidth(460, true));
  });
});
