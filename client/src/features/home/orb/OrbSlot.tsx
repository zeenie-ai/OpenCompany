/**
 * Where the orb sits on the hire and employee views (design handoff "Orb").
 * The slot reserves the orb's square and the orb, drawn behind the content,
 * glides into it. Where the orb cannot run, the slot shows the static mark.
 */

import { useLayoutEffect, useRef } from 'react';
import { OcMark } from '@/components/brand/Logo';
import { cn } from '@/lib/utils';
import { orbState, useOrbFallback } from './orb';

const SLOT_SIZE = {
  hire: 'size-(--size-orb-hire)',
  employee: 'size-(--size-orb-employee)',
} as const;

export function OrbSlot({ size }: { size: keyof typeof SLOT_SIZE }) {
  const ref = useRef<HTMLDivElement>(null);
  const fallback = useOrbFallback();

  useLayoutEffect(() => {
    const element = ref.current;
    orbState.slot = element;
    return () => {
      if (orbState.slot === element) orbState.slot = null;
    };
  }, []);

  return (
    <div ref={ref} aria-hidden data-orb-slot={size} className={cn('grid shrink-0 place-items-center', SLOT_SIZE[size])}>
      {fallback && <OcMark className="w-3/5" />}
    </div>
  );
}

export default OrbSlot;
