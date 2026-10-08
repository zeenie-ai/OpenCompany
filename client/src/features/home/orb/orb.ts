/**
 * The 3D orb (design handoff orb, onboarding handoff R3): what the engine
 * reads each frame (the slot to fill, the energy target, a spike, whether it
 * waits for the server), and its lifecycle. three.js loads only when the orb
 * first runs (orbEngine, its own chunk).
 *
 * Stages (OrbStage, the canvas host) and slots (OrbSlot, where the orb
 * glides) are stacks: a screen mounted over another, such as Connecting
 * over Home, borrows the orb while it shows and hands it back when it goes.
 * Leaving every stage keeps the engine for the next one; the app disposes
 * it (App.tsx).
 *
 * Slots show the static mark instead when the orb cannot run. No WebGL,
 * reduced motion and a lost WebGL context hold for the session; an engine
 * chunk that failed to load shows the mark until the next stage mounts,
 * which tries again.
 */

import { useSyncExternalStore } from 'react';
import { prefersReducedMotion } from '@/lib/useReducedMotion';

/** The prototype's energy levels and event bursts. */
export const ENERGY = { idle: 0.15, focus: 0.35, typing: 0.55, generating: 1, waiting: 0.22 } as const;
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
  /** The server answered again (Connecting). */
  connected: 1,
  signedIn: 1,
} as const;

/** Read by the engine every frame; the engine decays `spike` itself. */
export const orbState = {
  slot: null as HTMLElement | null,
  target: ENERGY.idle as number,
  spike: 0,
  /** The server can't be reached: the orb slows, breathes and searches. */
  waiting: false,
};

export function setEnergyTarget(target: number): void {
  orbState.target = target;
}

export function spikeOrb(amount: number): void {
  orbState.spike = Math.max(orbState.spike, amount);
}

export function setOrbWaiting(waiting: boolean): void {
  orbState.waiting = waiting;
}

interface OrbEngine {
  attach(host: HTMLElement): void;
  detach(): void;
  dispose(): void;
}

type Fallback = 'none' | 'missing' | 'latched';

let engine: OrbEngine | null = null;
let loading = false;
let fallback: Fallback = 'none';
const hosts: HTMLElement[] = [];
const slots: HTMLElement[] = [];
const listeners = new Set<() => void>();

function top<T>(stack: readonly T[]): T | null {
  return stack.length > 0 ? stack[stack.length - 1] : null;
}

function remove<T>(stack: T[], item: T): void {
  const at = stack.lastIndexOf(item);
  if (at !== -1) stack.splice(at, 1);
}

function canRun(): boolean {
  if (prefersReducedMotion()) return false;
  try {
    const canvas = document.createElement('canvas');
    return Boolean(canvas.getContext('webgl2') || canvas.getContext('webgl'));
  } catch {
    return false;
  }
}

function setFallback(next: Fallback): void {
  if (fallback === next) return;
  fallback = next;
  listeners.forEach((listener) => listener());
}

/** Draw the orb into the top stage, loading the engine the first time. */
function show(): void {
  const host = top(hosts);
  if (!host) return;
  if (engine) {
    engine.attach(host);
    return;
  }
  if (loading || fallback === 'latched') return;
  if (!canRun()) {
    setFallback('latched');
    return;
  }
  loading = true;
  import('./orbEngine')
    .then(({ createOrbEngine }) => {
      loading = false;
      engine = createOrbEngine(() => {
        engine?.dispose();
        engine = null;
        setFallback('latched');
      });
      setFallback('none');
      show();
    })
    .catch(() => {
      loading = false;
      setFallback('missing');
    });
}

/** A stage mounted: it hosts the orb until it unmounts. */
export function mountOrb(element: HTMLElement): void {
  hosts.push(element);
  show();
}

/** A stage unmounted: the one under it, if any, gets the orb back. */
export function unmountOrb(element: HTMLElement): void {
  const wasTop = top(hosts) === element;
  remove(hosts, element);
  if (!wasTop) return;
  engine?.detach();
  show();
}

/** A slot mounted: the orb glides to it until it unmounts. */
export function pushOrbSlot(element: HTMLElement): void {
  slots.push(element);
  orbState.slot = element;
}

export function popOrbSlot(element: HTMLElement): void {
  remove(slots, element);
  orbState.slot = top(slots);
}

export function disposeOrb(): void {
  engine?.dispose();
  engine = null;
  hosts.length = 0;
  slots.length = 0;
  orbState.slot = null;
}

/** True where slots show the static mark instead of the orb. */
export function useOrbFallback(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => fallback !== 'none',
    () => false,
  );
}

/** Test-only. */
export function resetOrbForTests(): void {
  disposeOrb();
  loading = false;
  fallback = 'none';
  orbState.target = ENERGY.idle;
  orbState.spike = 0;
  orbState.waiting = false;
}
