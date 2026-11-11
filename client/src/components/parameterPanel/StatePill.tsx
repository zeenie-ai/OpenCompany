/**
 * A state, marked the way the app marks one: a dot in the state's colour
 * beside neutral words (theme_system.md, "Colour marks state or role").
 * `done` draws an ink tick in place of the dot, since finished work takes no
 * colour. The node panels share it: a team task, a teammate, a process.
 */

import type { ReactNode } from 'react';
import { Check } from 'lucide-react';
import type { StatusTone } from '@/features/home/data/presentation';
import { StatusDot } from '@/features/home/ui/primitives';
import { cn } from '@/lib/utils';

export interface StateMarkProps {
  tone: StatusTone;
  /** Work is under way: the dot pulses. */
  pulse?: boolean;
  /** Finished: an ink tick, no dot. */
  done?: boolean;
  className?: string;
}

/** The mark alone, for a count or a legend. */
export function StateMark({ tone, pulse = false, done = false, className }: StateMarkProps) {
  if (done) {
    return <Check aria-hidden strokeWidth={2.5} className={cn('size-3 shrink-0 text-fg-default', className)} />;
  }
  return <StatusDot tone={tone} pulse={pulse} className={cn('shrink-0', className)} />;
}

/** The mark and the state's name, as a neutral pill. */
export function StatePill({ children, ...mark }: StateMarkProps & { children: ReactNode }) {
  return (
    <span className="inline-flex h-6 shrink-0 items-center gap-1.5 rounded-pill border border-border-default px-2.25 text-xs font-medium whitespace-nowrap text-fg-default capitalize">
      <StateMark {...mark} />
      {children}
    </span>
  );
}
