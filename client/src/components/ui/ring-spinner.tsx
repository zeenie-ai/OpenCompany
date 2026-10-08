/**
 * A thin ring that spins: a chat step at work, a new employee starting up,
 * a server being dialled. Colour the track with a `border-*` class and the
 * moving head with `border-t-*`; size it with `size-*`. `slow` turns at the
 * live-ring pace (1200 ms) instead of the spinner's 800 ms. Reduced motion
 * stops it (themes/animations.css).
 */

import { cn } from '@/lib/utils';

export function RingSpinner({ slow = false, className }: { slow?: boolean; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        slow ? 'opencompany-spinner-slow' : 'opencompany-spinner',
        'block shrink-0 rounded-full border-2 border-action-tools-soft border-t-action-tools-ink',
        className,
      )}
    />
  );
}
