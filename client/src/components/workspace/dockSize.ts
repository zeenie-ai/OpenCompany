/**
 * The Workspace dock's size, the same on Home and in the editor: both keep
 * their own open state and width (state/homeStore, stores/canvasDockStore),
 * and both take the limits from here.
 *
 * At 1100px and wider the dock pushes the page aside, and then it never
 * takes the room the page needs (`pageKeeps`: Home's header and its
 * buttons, the editor's canvas). Narrower, it lies over the page.
 */

import { useSyncExternalStore } from 'react';

/** Narrowest the dock drags to (design handoff "Workspace panel"): its row
 *  of tabs and buttons still fits. */
export const DOCK_MIN_WIDTH = 360;
/** Its width before anyone drags it. */
export const DOCK_DEFAULT_WIDTH = 460;
/** A bound for stored widths only; while dragging, the window sets the limit. */
export const DOCK_MAX_STORED_WIDTH = 4000;
/** Room a drag always leaves for the rest of the screen. */
const DOCK_KEEP_PX = 420;
/** From this window width up the dock pushes the page aside; under it, it
 *  lies over the page (the shell's `max-[1100px]:` classes say the same). */
const PUSHES = '(min-width: 1100px)';

/** A dragged width, kept between the narrowest and what the window allows. */
export function clampDockWidth(px: number): number {
  const widest = Math.max(DOCK_MIN_WIDTH, window.innerWidth - DOCK_KEEP_PX);
  return Math.round(Math.min(widest, Math.max(DOCK_MIN_WIDTH, px)));
}

/**
 * The dock's width, as CSS: the dragged width, or Expand's, always leaving
 * the window some room. `pageKeeps` is the room the page beside it keeps
 * while the dock pushes it aside, as a CSS length (the host's: what else is
 * on the row, and the page's own least width); the dock then stops there,
 * however far it was dragged, but never under its own narrowest. Left out
 * (the dock lies over the page), only the window limits it.
 */
export function dockWidth(widthPx: number, wide: boolean, pageKeeps?: string): string {
  if (pageKeeps) {
    return `min(${wide ? '1100px' : `${widthPx}px`}, max(${DOCK_MIN_WIDTH}px, 100vw - ${pageKeeps}))`;
  }
  return wide ? 'max(420px, min(1100px, 100vw - 420px))' : `min(${widthPx}px, max(340px, 100vw - 40px))`;
}

function pushesQuery(): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null;
  return window.matchMedia(PUSHES);
}

function subscribe(onChange: () => void): () => void {
  const query = pushesQuery();
  if (!query || typeof query.addEventListener !== 'function') return () => {};
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}

/** Whether the dock pushes the page aside (a window 1100px or wider) or
 *  lies over it. A host passes `pageKeeps` to `dockWidth` only while it
 *  pushes. */
export function useDockPushes(): boolean {
  return useSyncExternalStore(subscribe, () => pushesQuery()?.matches ?? false, () => false);
}
