/**
 * The Workspace dock's size, the same on Home and in the editor: both keep
 * their own open state and width (state/homeStore, stores/canvasDockStore),
 * and both take the limits from here.
 */

/** Narrowest the dock drags to (design handoff "Workspace panel"): its row
 *  of tabs and buttons still fits. */
export const DOCK_MIN_WIDTH = 360;
/** Its width before anyone drags it. */
export const DOCK_DEFAULT_WIDTH = 460;
/** A bound for stored widths only; while dragging, the window sets the limit. */
export const DOCK_MAX_STORED_WIDTH = 4000;
/** Room a drag always leaves for the rest of the screen. */
const DOCK_KEEP_PX = 420;

/** A dragged width, kept between the narrowest and what the window allows. */
export function clampDockWidth(px: number): number {
  const widest = Math.max(DOCK_MIN_WIDTH, window.innerWidth - DOCK_KEEP_PX);
  return Math.round(Math.min(widest, Math.max(DOCK_MIN_WIDTH, px)));
}

/** The dock's width, as CSS: the dragged width, or Expand's, always leaving
 *  the window some room. */
export function dockWidth(widthPx: number, wide: boolean): string {
  return wide ? 'max(420px, min(1100px, 100vw - 420px))' : `min(${widthPx}px, max(340px, 100vw - 40px))`;
}
