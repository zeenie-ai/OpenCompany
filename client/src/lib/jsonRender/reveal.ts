/**
 * A generated UI arrives as JSON patches (docs-internal/chat_protocol.md,
 * "Generated UI"): `add /root`, `add /state`, then one `add
 * /elements/<id>` per element, depth first from the root, ids escaped as
 * RFC 6901 says. `specToPatches` writes that stream for a whole spec; the
 * chat fixtures (features/chat/__fixtures__) hold it line for line.
 *
 * `useSpecReveal` shows a spec the way that stream builds it: the root and
 * state at once, then one element every REVEAL_STEP_MS (design handoff), so
 * a screen reads as being written rather than dropped in. The count only
 * moves forward, so a spec that keeps growing (a UI still streaming in)
 * never restarts it; a host that wants a fresh reveal remounts. Everything
 * shows at once for a UI that is not `live` (read back later), under
 * reduced motion, or while the page is hidden (motionSuppressed).
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import type { JsonPatch, Spec, UIElement } from '@json-render/core';
import { motionSuppressed } from '@/lib/motion';
import { escapePointer } from './paths';

export const REVEAL_STEP_MS = 90;

function ownElement(spec: Spec, id: string): UIElement | undefined {
  return Object.prototype.hasOwnProperty.call(spec.elements, id) ? spec.elements[id] : undefined;
}

/** The ids of the elements the root reaches, depth first, each once. */
export function revealOrder(spec: Spec): string[] {
  const order: string[] = [];
  const walk = (id: string) => {
    const element = ownElement(spec, id);
    if (!element || order.includes(id)) return;
    order.push(id);
    element.children?.forEach(walk);
  };
  walk(spec.root);
  return order;
}

/** The patch stream that builds `spec`: root, state, then every element
 *  the root reaches, depth first, each once. */
export function specToPatches(spec: Spec): JsonPatch[] {
  return [
    { op: 'add', path: '/root', value: spec.root },
    { op: 'add', path: '/state', value: spec.state ?? {} },
    ...revealOrder(spec).map((id): JsonPatch => ({ op: 'add', path: `/elements/${escapePointer(id)}`, value: spec.elements[id] })),
  ];
}

export interface SpecReveal {
  /** The part of the spec shown so far (null while there is no spec). */
  spec: Spec | null;
  /** Elements are still arriving: render with `loading` so json-render does
   *  not warn about children that are on their way. */
  revealing: boolean;
}

export function useSpecReveal(spec: Spec | null, live = true): SpecReveal {
  const order = useMemo(() => (spec ? revealOrder(spec) : []), [spec]);
  const total = order.length;
  const [count, setCount] = useState(() => (!live || motionSuppressed() ? Number.POSITIVE_INFINITY : 1));
  const started = useRef<number | null>(null);

  useEffect(() => {
    if (count >= total) return;
    const since = started.current ?? performance.now();
    started.current = since;
    const timer = window.setInterval(() => {
      const due = motionSuppressed() ? total : Math.floor((performance.now() - since) / REVEAL_STEP_MS) + 1;
      setCount((current) => Math.max(current, Math.min(total, due)));
    }, REVEAL_STEP_MS);
    return () => window.clearInterval(timer);
  }, [count, total]);

  const shown = Math.min(total, count);
  const part = useMemo(() => {
    if (!spec || shown >= total) return spec;
    // No prototype: ids are looked up here as keys.
    const elements: Record<string, UIElement> = Object.create(null);
    for (const id of order.slice(0, shown)) elements[id] = spec.elements[id];
    return { root: spec.root, state: spec.state, elements };
  }, [spec, order, shown, total]);
  return { spec: part, revealing: shown < total };
}
