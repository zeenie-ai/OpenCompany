/**
 * Where the orb sits on the hire and employee views (design handoff "Orb").
 *
 * The slot reserves the orb's square at the view's size token. It shows
 * the static mark: the WebGL orb is loaded lazily into these slots later
 * (features/home/orb), and the static mark stays for reduced motion, no
 * WebGL, or a lost WebGL context.
 */

import { OcMark } from '@/components/brand/Logo';
import { cn } from '@/lib/utils';

const SLOT_SIZE = {
  hire: 'size-(--size-orb-hire)',
  employee: 'size-(--size-orb-employee)',
} as const;

export function OrbSlot({ size }: { size: keyof typeof SLOT_SIZE }) {
  return (
    <div aria-hidden data-orb-slot={size} className={cn('grid shrink-0 place-items-center', SLOT_SIZE[size])}>
      <OcMark className="w-3/5" />
    </div>
  );
}

export default OrbSlot;
