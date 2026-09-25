/**
 * Page activity — whether anyone can currently see the app.
 *
 * `html[data-page-hidden] * { animation-play-state: paused }` (themes/base.css)
 * pauses CSS keyframe animations while the tab is hidden or the window is
 * blurred, but it cannot reach Web Animations started from script or
 * requestAnimationFrame loops (the Home orb). Those subscribe here instead.
 * The app shell keeps this in sync with the same visibility / focus events
 * that toggle `data-page-hidden`.
 *
 * A plain module store: read it without re-rendering (`isActive()`), or
 * subscribe for changes. Deliberately not React state.
 */

type Listener = (active: boolean) => void;

let active = typeof document === 'undefined' ? true : !document.hidden;
const listeners = new Set<Listener>();

export const pageActivity = {
  isActive(): boolean {
    return active;
  },
  set(next: boolean): void {
    if (next === active) return;
    active = next;
    for (const listener of listeners) listener(active);
  },
  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};
