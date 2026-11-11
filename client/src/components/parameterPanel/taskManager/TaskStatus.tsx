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

import type { StatusTone } from '@/features/home/data/presentation';
import { StateMark, StatePill, type StateMarkProps } from '../StatePill';

const TONE: Partial<Record<string, StatusTone>> = {
  running: 'working',
  queued: 'ready',
  submitted: 'waiting',
  failed: 'attention',
};

const markOf = (status: string): StateMarkProps => ({
  tone: TONE[status] ?? 'paused',
  pulse: status === 'running',
  done: status === 'accepted',
});

/** The mark alone: a status's count chip. */
export function TaskStatusDot({ status, className }: { status: string; className?: string }) {
  return <StateMark {...markOf(status)} className={className} />;
}

/** The mark and the status's name, as a neutral pill. */
export function TaskStatus({ status }: { status: string }) {
  return <StatePill {...markOf(status)}>{status}</StatePill>;
}
