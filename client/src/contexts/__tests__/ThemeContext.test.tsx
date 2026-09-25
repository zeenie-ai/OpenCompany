import { useEffect } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, renderHook } from '@testing-library/react';
import {
  AVAILABLE_THEMES,
  DARK_FAMILY,
  ThemeProvider,
  isDarkTheme,
  oppositeBaseTheme,
  useTheme,
} from '../ThemeContext';
import { installWaapiStub, setReducedMotion } from '../../test/waapi';

const html = document.documentElement;

function resetDom(): void {
  localStorage.clear();
  html.removeAttribute('data-theme');
  html.className = '';
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
  it('maps every theme to the base theme of the other family', () => {
    for (const theme of AVAILABLE_THEMES) {
      expect(oppositeBaseTheme(theme)).toBe(DARK_FAMILY.has(theme) ? 'light' : 'dark');
      expect(isDarkTheme(theme)).toBe(DARK_FAMILY.has(theme));
    }
    expect(oppositeBaseTheme('light')).toBe('dark');
    expect(oppositeBaseTheme('dark')).toBe('light');
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
    localStorage.setItem('opencompany-theme', 'renaissance');
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    expect(result.current.theme).toBe('renaissance');
    expect(html.classList.contains('dark')).toBe(false);
  });

  it('migrates the pre-rebrand key', () => {
    localStorage.setItem('machinaos-theme', 'cyber');
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    expect(result.current.theme).toBe('cyber');
    expect(localStorage.getItem('opencompany-theme')).toBe('cyber');
    expect(localStorage.getItem('machinaos-theme')).toBeNull();
  });

  it('migrates the legacy darkMode flag', () => {
    localStorage.setItem('darkMode', 'false');
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    expect(result.current.theme).toBe('light');
    expect(localStorage.getItem('darkMode')).toBeNull();
  });
});

describe('setTheme', () => {
  it('writes <html> before React state changes', () => {
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    act(() => {
      result.current.setTheme('cyber');
      expect(html.dataset.theme).toBe('cyber');
      expect(html.classList.contains('dark')).toBe(true);
      expect(result.current.theme).toBe('dark');
    });
    expect(result.current.theme).toBe('cyber');
    expect(result.current.isDarkMode).toBe(true);
    expect(localStorage.getItem('opencompany-theme')).toBe('cyber');
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

  it('toggles to the base theme of the other family', () => {
    localStorage.setItem('opencompany-theme', 'steampunk');
    const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe('light');
    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe('dark');
  });
});

describe('baseOnly (Home)', () => {
  function renderWith(baseOnly: boolean) {
    let ctx: ReturnType<typeof useTheme> | undefined;
    function Probe() {
      ctx = useTheme();
      return null;
    }
    const view = render(
      <ThemeProvider baseOnly={baseOnly}>
        <Probe />
      </ThemeProvider>,
    );
    return {
      ctx: () => ctx!,
      setBaseOnly: (next: boolean) =>
        view.rerender(
          <ThemeProvider baseOnly={next}>
            <Probe />
          </ThemeProvider>,
        ),
    };
  }

  it('shows a stylized theme as its family base and keeps the choice', () => {
    localStorage.setItem('opencompany-theme', 'atomic');
    const view = renderWith(true);
    expect(view.ctx().theme).toBe('light');
    expect(view.ctx().chosenTheme).toBe('atomic');
    expect(html.dataset.theme).toBe('light');
    expect(localStorage.getItem('opencompany-theme')).toBe('atomic');
  });

  it('shows a dark-family theme as dark', () => {
    localStorage.setItem('opencompany-theme', 'cyber');
    const view = renderWith(true);
    expect(html.dataset.theme).toBe('dark');
    expect(html.classList.contains('dark')).toBe(true);
    expect(view.ctx().isDarkMode).toBe(true);
  });

  it('brings the chosen theme back when it clears', () => {
    localStorage.setItem('opencompany-theme', 'atomic');
    const view = renderWith(true);
    view.setBaseOnly(false);
    expect(html.dataset.theme).toBe('atomic');
    expect(view.ctx().theme).toBe('atomic');
    view.setBaseOnly(true);
    expect(html.dataset.theme).toBe('light');
  });

  it('keeps a stylized choice while showing the base', () => {
    const view = renderWith(true);
    act(() => view.ctx().setTheme('renaissance'));
    expect(html.dataset.theme).toBe('light');
    expect(view.ctx().chosenTheme).toBe('renaissance');
    expect(localStorage.getItem('opencompany-theme')).toBe('renaissance');
  });

  it('toggles between the base themes', () => {
    localStorage.setItem('opencompany-theme', 'atomic');
    const view = renderWith(true);
    act(() => view.ctx().toggleTheme());
    expect(html.dataset.theme).toBe('dark');
    expect(view.ctx().chosenTheme).toBe('dark');
    act(() => view.ctx().toggleTheme());
    expect(html.dataset.theme).toBe('light');
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

  it('toggles from the pending theme while a transition has not captured yet', async () => {
    const vt = installViewTransitionStub({ defer: true });
    try {
      const { result } = renderHook(() => useTheme(), { wrapper: ThemeProvider });
      act(() => {
        result.current.toggleTheme({ reveal: true });
        result.current.toggleTheme({ reveal: true });
      });
      await act(async () => {
        for (const t of vt.transitions) t.runUpdate();
      });
      // Two toggles from dark land back on dark, not on light.
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
      act(() => result.current.setTheme('renaissance'));
      expect(result.current.theme).toBe('renaissance');
      await act(async () => {
        vt.transitions[0].runUpdate();
      });
      expect(result.current.theme).toBe('renaissance');
      expect(html.dataset.theme).toBe('renaissance');
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
