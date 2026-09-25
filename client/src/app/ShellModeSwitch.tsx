/* eslint-disable react-refresh/only-export-components -- the editor chunk's preloader lives beside the lazy() call it must match. */
/**
 * Renders the shell's current screen: Normal mode (Home) or Dev mode (the
 * workflow editor). The editor is a lazy chunk, so ReactFlow and the canvas
 * code stay out of the Normal-mode bundle; `preloadEditor()` warms it before
 * a switch so the swap never waits on the network.
 *
 * With `featureFlags.normalMode` off the editor is the only screen, exactly
 * as before the shell existed.
 */

import React, { Suspense, lazy, useLayoutEffect, useRef } from 'react';
import ErrorBoundary from '../components/ui/ErrorBoundary';
import { OcLogo } from '../components/brand/Logo';
import { featureFlags } from '../lib/featureFlags';
import { animate } from '../lib/motion';
import { useAppStore, type ShellMode } from '../store/useAppStore';
import { SHELL_SCREEN_ATTR, consumeShellEntry } from './shellTransition';

const loadEditor = () => import('../Dashboard');
const LazyEditor = lazy(loadEditor);

/** Start fetching the editor chunk. Safe to call repeatedly. */
export function preloadEditor(): Promise<unknown> {
  return loadEditor();
}

// Home is its own chunk too, so a Dev-mode session never loads it.
const LazyHome = lazy(() => import('../features/home/HomeShell'));

/** The screen the shell shows. Always `dev` while the flag is off. */
export function useShellMode(): ShellMode {
  const mode = useAppStore((s) => s.shellMode);
  return featureFlags.normalMode ? mode : 'dev';
}

function ShellScreen({ mode, children }: { mode: ShellMode; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (!consumeShellEntry()) return;
    animate(
      ref.current,
      [
        { opacity: 0, transform: 'scale(0.985)' },
        { opacity: 1, transform: 'none' },
      ],
      { duration: 'mode-in', easing: 'spring' },
    );
  }, []);
  return (
    <div ref={ref} {...{ [SHELL_SCREEN_ATTR]: mode }} className="flex min-h-0 w-full flex-1 flex-col">
      {children}
    </div>
  );
}

function ScreenLoading() {
  return (
    <div className="flex flex-1 items-center justify-center" role="status" aria-label="Loading">
      <OcLogo size="header" wordmark={false} className="opacity-60" />
    </div>
  );
}

export function ShellModeSwitch() {
  const mode = useShellMode();
  return (
    <ShellScreen key={mode} mode={mode}>
      <ErrorBoundary>
        <Suspense fallback={<ScreenLoading />}>{mode === 'normal' ? <LazyHome /> : <LazyEditor />}</Suspense>
      </ErrorBoundary>
    </ShellScreen>
  );
}
