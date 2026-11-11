/**
 * A team task's status, marked the way the app marks any state: a dot in
 * the state's colour beside a neutral label (theme_system.md, "Colour marks
 * state or role"). Only work that is under way or wants someone takes a
 * colour:
 *
 * | Status                    | Mark                                      |
 * |---------------------------|-------------------------------------------|
 * | running                   | working (green), the one dot that pulses  |
 * | queued                    | ready (cyan): next in line                |
 * | submitted                 | waiting (orange): the lead must review it |
 * | failed                    | attention (pink)                          |
 * | accepted                  | a tick in ink: done                       |
 * | blocked, cancelled, other | paused (grey)                             |
 */

import { Check } from 'lucide-react';
import type { StatusTone } from '@/features/home/data/presentation';
import { StatusDot } from '@/features/home/ui/primitives';
import { cn } from '@/lib/utils';

const TONE: Partial<Record<string, StatusTone>> = {
  running: 'working',
  queued: 'ready',
  submitted: 'waiting',
  failed: 'attention',
};

/** The mark alone: a status's count chip. */
export function TaskStatusDot({ status, className }: { status: string; className?: string }) {
  if (status === 'accepted') {
    return <Check aria-hidden strokeWidth={2.5} className={cn('size-3 shrink-0 text-fg-default', className)} />;
  }
  return <StatusDot tone={TONE[status] ?? 'paused'} pulse={status === 'running'} className={cn('shrink-0', className)} />;
}

/** The mark and the status's name, as a neutral pill. */
export function TaskStatus({ status }: { status: string }) {
  return (
    <span className="inline-flex h-6 shrink-0 items-center gap-1.5 rounded-pill border border-border-default px-2.25 text-xs font-medium whitespace-nowrap text-fg-default capitalize">
      <TaskStatusDot status={status} />
      {status}
    </span>
  );
}
