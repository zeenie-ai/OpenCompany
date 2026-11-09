import { useEffect } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, renderHook } from '@testing-library/react';
import { AVAILABLE_THEMES, ThemeProvider, nextTheme, useTheme } from '../ThemeContext';
import { installWaapiStub, setReducedMotion } from '../../test/waapi';

const html = document.documentElement;

function resetDom(): void {
  localStorage.clear();
  html.removeAttribute('data-theme');
  html.className = '';
  document.head.querySelector('meta[name="theme-color"]')?.remove();
}

/** What the browser fires in this window when another window or tab writes
 *  localStorage (`key` null: the other window cleared it). */
function otherWindowWrites(key: string | null, newValue: string | null): void {
  act(() => {
    window.dispatchEvent(new StorageEvent('storage', { key, newValue }));
  });
}

interface FakeTransition {
  ready: Promise<void>;
  finished: Promise<void>;
  finish: () => void;
  runUpdate: () => void;
}

/** A stand-in for document.startViewTransition. `defer` holds each update
 *  callback until runUpdate(), like a browser capturing the old frame. */
function installViewTransitionStub({ defer = false } = {}) {
  const transitions: FakeTransition[] = [];
  const start = vi.fn((update: () => void) => {
    let finish!: () => void;
    let markReady!: () => void;
    const ready = new Promise<void>((resolve) => {
      markReady = resolve;
    });
    const finished = new Promise<void>((resolve) => {
      finish = resolve;
    });
    let ran = false;
    const runUpdate = () => {
      if (ran) return;
      ran = true;
      update();
      markReady();
    };
    transitions.push({ ready, finished, finish, runUpdate });
    if (!defer) runUpdate();
    return { ready, finished };
  });
  Object.defineProperty(document, 'startViewTransition', { configurable: true, writable: true, value: start });
  return {
    start,
    transitions,
    restore(): void {
      delete (document as { startViewTransition?: unknown }).startViewTransition;
    },
  };
}

beforeEach(resetDom);
afterEach(resetDom);

describe('theme helpers', () => {
  it('steps through the themes in order and wraps round', () => {
    expect(AVAILABLE_THEMES.map(nextTheme)).toEqual(['dark', 'light']);
  });
});

describe('initial theme', () => {
  it('defaults to dark and writes it to <html>', () => {
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    expect(result.current.theme).toBe('dark');
    expect(html.dataset.theme).toBe('dark');
    expect(html.classList.contains('dark')).toBe(true);
    expect(localStorage.getItem('opencompany-theme')).toBe('dark');
  });

  it('restores a stored theme', () => {
    localStorage.setItem('opencompany-theme', 'light');
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    expect(result.current.theme).toBe('light');
    expect(html.classList.contains('dark')).toBe(false);
  });

  it('migrates the pre-rebrand key', () => {
    localStorage.setItem('machinaos-theme', 'light');
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    expect(result.current.theme).toBe('light');
    expect(localStorage.getItem('opencompany-theme')).toBe('light');
    expect(localStorage.getItem('machinaos-theme')).toBeNull();
  });

  it('migrates the legacy darkMode flag', () => {
    localStorage.setItem('darkMode', 'false');
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    expect(result.current.theme).toBe('light');
    expect(localStorage.getItem('darkMode')).toBeNull();
  });

  it('opens in dark when the saved theme no longer exists', () => {
    localStorage.setItem('opencompany-theme', 'atomic');
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    expect(result.current.theme).toBe('dark');
    expect(localStorage.getItem('opencompany-theme')).toBe('dark');
  });
});

describe('setTheme', () => {
  it('writes <html> before React state changes', () => {
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    act(() => {
      result.current.setTheme('light');
      expect(html.dataset.theme).toBe('light');
      expect(html.classList.contains('dark')).toBe(false);
      expect(result.current.theme).toBe('dark');
    });
    expect(result.current.theme).toBe('light');
    expect(result.current.isDarkMode).toBe(false);
    expect(localStorage.getItem('opencompany-theme')).toBe('light');
  });

  it('lets a child effect read the new theme tokens (the sound pack read)', () => {
    const seen: string[] = [];
    let setTheme: ((t: 'light') => void) | undefined;
    function Probe() {
      const ctx = useTheme();
      setTheme = ctx.setTheme;
      useEffect(() => {
        seen.push(`${ctx.theme}:${html.dataset.theme}`);
      }, [ctx.theme]);
      return null;
    }
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    act(() => setTheme!('light'));
    expect(seen).toEqual(['dark:dark', 'light:light']);
  });

  it('cycles through the themes from the stored one', () => {
    localStorage.setItem('opencompany-theme', 'light');
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    act(() => result.current.cycleTheme());
    expect(result.current.theme).toBe('dark');
    act(() => result.current.cycleTheme());
    expect(result.current.theme).toBe('light');
    expect(html.dataset.theme).toBe('light');
    expect(localStorage.getItem('opencompany-theme')).toBe('light');
  });
});

describe('a theme chosen in another window', () => {
  it('shows here at once without being saved again', () => {
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    try {
      otherWindowWrites('opencompany-theme', 'light');
      expect(result.current.theme).toBe('light');
      expect(html.dataset.theme).toBe('light');
      expect(html.classList.contains('dark')).toBe(false);
      // Writing it back could echo an older theme to the other window.
      expect(setItem).not.toHaveBeenCalled();

      // The next press steps on from the synced theme, and is saved.
      act(() => result.current.cycleTheme());
      expect(result.current.theme).toBe('dark');
      expect(setItem).toHaveBeenCalledWith('opencompany-theme', 'dark');
    } finally {
      setItem.mockRestore();
    }
  });

  it('ignores other keys, a cleared key and unknown names', () => {
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    otherWindowWrites('machinaos-theme', 'light');
    otherWindowWrites('opencompany-theme', null);
    otherWindowWrites(null, null);
    otherWindowWrites('opencompany-theme', 'neon');
    expect(result.current.theme).toBe('dark');
    expect(html.dataset.theme).toBe('dark');
  });
});

describe('theme-color meta', () => {
  it('keeps one theme-color meta at the theme background', () => {
    const style = document.createElement('style');
    style.textContent =
      ':root[data-theme="dark"] { --background: #0d0f13; } :root[data-theme="light"] { --background: #f5f7fa; }';
    document.head.append(style);
    try {
      const metas = () => document.head.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]');
      const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
      expect(metas()).toHaveLength(1);
      expect(metas()[0].content).toBe('#0d0f13');
      act(() => result.current.setTheme('light'));
      expect(metas()).toHaveLength(1);
      expect(metas()[0].content).toBe('#f5f7fa');
    } finally {
      style.remove();
    }
  });

  it('writes no colour where no theme CSS is loaded', () => {
    renderHook(() => useTheme(), { wrapper: ThemeProvider });
    const meta = document.head.querySelector('meta[name="theme-color"]');
    expect(meta).not.toBeNull();
    expect(meta?.hasAttribute('content')).toBe(false);
  });
});

describe('revealed switch', () => {
  let waapi: ReturnType<typeof installWaapiStub>;
  let restoreMotion: () => void;

  beforeEach(() => {
    waapi = installWaapiStub();
    restoreMotion = setReducedMotion(false);
  });

  afterEach(() => {
    waapi.restore();
    restoreMotion();
    vi.useRealTimers();
  });

  it('grows the new theme as a circle from the origin', async () => {
    const vt = installViewTransitionStub();
    try {
      const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
      await act(async () => {
        result.current.setTheme('light', { reveal: true, origin: { x: 10, y: 20 } });
      });
      expect(vt.start).toHaveBeenCalledTimes(1);
      expect(result.current.theme).toBe('light');
      expect(html.dataset.theme).toBe('light');
      // Transitions stay suspended until the transition finishes.
      expect(html.classList.contains('oc-theme-swapping')).toBe(true);

      const radius = Math.hypot(Math.max(10, window.innerWidth - 10), Math.max(20, window.innerHeight - 20));
      const reveal = waapi.calls.find((c) => c.options.pseudoElement === '::view-transition-new(root)');
      expect(reveal?.target).toBe(html);
      expect(reveal?.keyframes).toEqual({
        clipPath: ['circle(0px at 10px 20px)', `circle(${radius}px at 10px 20px)`],
      });
      expect(reveal?.options).toMatchObject({ duration: 760, easing: 'cubic-bezier(0.65, 0, 0.35, 1)' });
      expect(waapi.calls.some((c) => c.options.pseudoElement === '::view-transition-old(root)')).toBe(true);

      await act(async () => {
        vt.transitions[0].finish();
      });
      expect(html.classList.contains('oc-theme-swapping')).toBe(false);
    } finally {
      vt.restore();
    }
  });

  it('cycles from the pending theme while a transition has not captured yet', async () => {
    const vt = installViewTransitionStub({ defer: true });
    try {
      const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
      act(() => {
        result.current.cycleTheme({ reveal: true });
        result.current.cycleTheme({ reveal: true });
      });
      await act(async () => {
        for (const t of vt.transitions) t.runUpdate();
      });
      // Two presses from dark step on to light and back to dark; stepping
      // from the rendered theme would have landed on light twice.
      expect(result.current.theme).toBe('dark');
      expect(html.dataset.theme).toBe('dark');
    } finally {
      vt.restore();
    }
  });

  it('never lets a late transition callback override a newer switch', async () => {
    const vt = installViewTransitionStub({ defer: true });
    try {
      const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
      act(() => result.current.setTheme('light', { reveal: true }));
      act(() => result.current.setTheme('dark'));
      expect(result.current.theme).toBe('dark');
      await act(async () => {
        vt.transitions[0].runUpdate();
      });
      expect(result.current.theme).toBe('dark');
      expect(html.dataset.theme).toBe('dark');
    } finally {
      vt.restore();
    }
  });

  it('never lets a pending switch override a theme from another window', async () => {
    const vt = installViewTransitionStub({ defer: true });
    try {
      const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
      act(() => result.current.setTheme('light', { reveal: true }));
      otherWindowWrites('opencompany-theme', 'dark');
      expect(result.current.theme).toBe('dark');
      await act(async () => {
        vt.transitions[0].runUpdate();
      });
      expect(result.current.theme).toBe('dark');
      expect(html.dataset.theme).toBe('dark');
    } finally {
      vt.restore();
    }
  });

  it('fades colours where View Transitions are missing', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    act(() => result.current.setTheme('light', { reveal: true }));
    expect(result.current.theme).toBe('light');
    expect(html.classList.contains('oc-theme-fade')).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(460);
    });
    expect(html.classList.contains('oc-theme-fade')).toBe(false);
  });

  it('switches instantly under reduced motion', () => {
    restoreMotion();
    restoreMotion = setReducedMotion(true);
    const vt = installViewTransitionStub();
    try {
      const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
      act(() => result.current.setTheme('light', { reveal: true }));
      expect(vt.start).not.toHaveBeenCalled();
      expect(result.current.theme).toBe('light');
      expect(html.classList.contains('oc-theme-swapping')).toBe(false);
      expect(html.classList.contains('oc-theme-fade')).toBe(false);
      expect(waapi.calls).toHaveLength(0);
    } finally {
      vt.restore();
    }
  });
});
