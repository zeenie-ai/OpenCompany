/**
 * Home's 3D orb (design handoff orb/ORB.md): what the engine reads each
 * frame (the slot to fill, the energy target, a spike), and its lifecycle.
 * three.js loads only when the orb first runs (orbEngine, its own chunk).
 * Leaving Home keeps the engine for the next visit; the app shell disposes
 * it. Without WebGL, under reduced motion, or after a lost WebGL context,
 * slots show the static mark instead.
 */

import { useSyncExternalStore } from 'react';
import { prefersReducedMotion } from '@/lib/useReducedMotion';

/** The prototype's energy levels and event bursts. */
export const ENERGY = { idle: 0.15, focus: 0.35, typing: 0.55, generating: 1 } as const;
export const SPIKE = {
  hire: 1,
  connect: 0.7,
  theme: 1,
  mode: 0.9,
  task: 0.35,
  settings: 0.5,
  workspace: 0.5,
  draftReady: 0.8,
  draftFailed: 0.4,
  profileSaved: 0.7,
} as const;

/** Read by the engine every frame; the engine decays `spike` itself. */
export const orbState = {
  slot: null as HTMLElement | null,
  target: ENERGY.idle as number,
  spike: 0,
};

export function setEnergyTarget(target: number): void {
  orbState.target = target;
}

export function spikeOrb(amount: number): void {
  orbState.spike = Math.max(orbState.spike, amount);
}

interface OrbEngine {
  attach(host: HTMLElement): void;
  detach(): void;
  dispose(): void;
}

let engine: OrbEngine | null = null;
let loading = false;
let host: HTMLElement | null = null;
let fallback = false;
const listeners = new Set<() => void>();

function canRun(): boolean {
  if (prefersReducedMotion()) return false;
  try {
    const canvas = document.createElement('canvas');
    return Boolean(canvas.getContext('webgl2') || canvas.getContext('webgl'));
  } catch {
    return false;
  }
}

function showFallback(): void {
  fallback = true;
  listeners.forEach((listener) => listener());
}

/** The stage mounted: draw the orb into it (loading it the first time). */
export function mountOrb(element: HTMLElement): void {
  host = element;
  if (engine) {
    engine.attach(element);
    return;
  }
  if (loading || fallback) return;
  if (!canRun()) {
    showFallback();
    return;
  }
  loading = true;
  import('./orbEngine')
    .then(({ createOrbEngine }) => {
      loading = false;
      engine = createOrbEngine(() => {
        engine?.dispose();
        engine = null;
        showFallback();
      });
      if (host) engine.attach(host);
    })
    .catch(() => {
      loading = false;
      showFallback();
    });
}

export function unmountOrb(element: HTMLElement): void {
  if (host !== element) return;
  host = null;
  engine?.detach();
}

export function disposeOrb(): void {
  engine?.dispose();
  engine = null;
  host = null;
  orbState.slot = null;
}

/** True where slots show the static mark instead of the orb. */
export function useOrbFallback(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => fallback,
    () => false,
  );
}

/** Test-only. */
export function resetOrbForTests(): void {
  disposeOrb();
  loading = false;
  fallback = false;
  orbState.target = ENERGY.idle;
  orbState.spike = 0;
}
