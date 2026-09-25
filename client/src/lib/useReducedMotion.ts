/**
 * prefers-reduced-motion, as a plain read and as a React hook.
 *
 * `prefersReducedMotion()` is for code that decides at call time (the motion
 * helper, the theme reveal). `useReducedMotion()` re-renders when the user
 * changes the setting, for components that render differently (the orb's
 * static fallback).
 */

import { useSyncExternalStore } from 'react';

const QUERY = '(prefers-reduced-motion: reduce)';

function mediaQuery(): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null;
  try {
    return window.matchMedia(QUERY);
  } catch {
    return null;
  }
}

export function prefersReducedMotion(): boolean {
  return mediaQuery()?.matches ?? false;
}

function subscribe(onChange: () => void): () => void {
  const mql = mediaQuery();
  if (!mql || typeof mql.addEventListener !== 'function') return () => {};
  mql.addEventListener('change', onChange);
  return () => mql.removeEventListener('change', onChange);
}

export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, prefersReducedMotion, () => false);
}
