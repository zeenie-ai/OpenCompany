/**
 * `on`, held for `ms` after it turns off. The sign-in gate keeps the
 * Connecting screen up a moment after the server answers, so "Connected"
 * is seen before sign-in or the app.
 */

import { useEffect, useState } from 'react';

export function useHold(on: boolean, ms: number): boolean {
  const [held, setHeld] = useState(false);
  const [wasOn, setWasOn] = useState(on);
  if (wasOn !== on) {
    setWasOn(on);
    setHeld(!on);
  }
  useEffect(() => {
    if (!held) return;
    const timer = window.setTimeout(() => setHeld(false), ms);
    return () => window.clearTimeout(timer);
  }, [held, ms]);
  return on || held;
}
