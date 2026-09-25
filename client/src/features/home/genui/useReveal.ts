/**
 * The setup screen appears one element at a time (design handoff: one
 * every 90 ms, depth first), so a long screen reads as being written
 * rather than dropped in. Everything shows at once under reduced motion or
 * while the page is hidden. A new version starts again from nothing.
 */

import { useEffect, useMemo, useState } from 'react';
import { motionSuppressed } from '@/lib/motion';

export const REVEAL_STEP_MS = 90;

export function useReveal(order: readonly string[], version: number): ReadonlySet<string> {
  const [progress, setProgress] = useState({ version, count: 0 });
  const count = progress.version === version ? progress.count : 0;
  const total = order.length;

  useEffect(() => {
    if (total === 0) return;
    if (motionSuppressed()) {
      setProgress({ version, count: total });
      return;
    }
    const started = performance.now();
    setProgress({ version, count: 1 });
    const timer = window.setInterval(() => {
      const next = motionSuppressed()
        ? total
        : Math.min(total, Math.floor((performance.now() - started) / REVEAL_STEP_MS) + 1);
      setProgress({ version, count: next });
      if (next >= total) window.clearInterval(timer);
    }, REVEAL_STEP_MS);
    return () => window.clearInterval(timer);
  }, [version, total]);

  return useMemo(() => new Set(order.slice(0, count)), [order, count]);
}
