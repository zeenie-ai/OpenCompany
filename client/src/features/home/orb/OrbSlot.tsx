/**
 * Where the orb sits: the hire and employee views, the Connecting screen
 * and sign-in. The slot reserves the orb's square and the orb, drawn behind
 * the content, glides into it; the newest slot mounted is the one it fills
 * (orb.ts). Where the orb cannot run, the slot shows the static mark.
 */

import { useLayoutEffect, useRef } from 'react';
import { OcMark } from '@/components/brand/Logo';
import { cn } from '@/lib/utils';
import { popOrbSlot, pushOrbSlot, useOrbFallback } from './orb';

const SLOT_SIZE = {
  hire: 'size-(--size-orb-hire)',
  employee: 'size-(--size-orb-employee)',
  connecting: 'size-(--size-orb-connecting)',
  login: 'size-(--size-orb-login)',
} as const;

export function OrbSlot({ size }: { size: keyof typeof SLOT_SIZE }) {
  const ref = useRef<HTMLDivElement>(null);
  const fallback = useOrbFallback();

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    pushOrbSlot(element);
    return () => popOrbSlot(element);
  }, []);

  return (
    <div ref={ref} aria-hidden data-orb-slot={size} className={cn('grid shrink-0 place-items-center', SLOT_SIZE[size])}>
      {fallback && <OcMark className="w-3/5" />}
    </div>
  );
}

export default OrbSlot;
