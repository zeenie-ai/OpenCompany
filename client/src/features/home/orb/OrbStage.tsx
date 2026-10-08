/**
 * The orb's canvas host: behind the content, never taking pointer events.
 * `home` fills Home's main area below the header; `screen` fills the whole
 * window (the Connecting and sign-in screens). A stage mounted over another
 * borrows the orb until it unmounts (orb.ts).
 */

import { useLayoutEffect, useRef } from 'react';
import { cn } from '@/lib/utils';
import { mountOrb, unmountOrb } from './orb';

const FILL = {
  home: 'absolute inset-x-0 top-(--h-home-header) bottom-0',
  screen: 'fixed inset-0',
} as const;

export function OrbStage({ fill = 'home' }: { fill?: keyof typeof FILL }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    mountOrb(element);
    return () => unmountOrb(element);
  }, []);
  return <div ref={ref} aria-hidden className={cn('pointer-events-none z-0 overflow-hidden', FILL[fill])} />;
}

export default OrbStage;
