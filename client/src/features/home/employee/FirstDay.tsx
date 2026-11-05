/**
 * A new hire's first day (onboarding v3): one slim strip at the top of
 * their conversation for the rest of the session, from the hire until a
 * reload, New conversation or deleting them (homeStore.firstDays).
 *
 * - Who they are: the header's avatar, with a ring turning while they start
 *   and the hire glow once they run, then "{Name} · {Role}".
 * - How their start is going, on one line driven by what really happened,
 *   never by timers (firstDayLine). A start that did not go ahead says why,
 *   with their main action (connect what is missing, or Start).
 * - Their status as the header says it, a dot and a label; Starting…
 *   while the hire's own start is under way.
 * - What they do: their routine on one line, from the hire's plan, until
 *   the conversation has a message.
 *
 * The chat around it keeps the box waiting while they start (EmployeeChat).
 */

import { useLayoutEffect, useRef } from 'react';
import { RingSpinner } from '@/components/ui/ring-spinner';
import { animate, rise } from '@/lib/motion';
import { useWorkflowControlPending } from '@/stores/workflowControlStore';
import { useEmployeeDetailQuery } from '../data/employees';
import { useAnswering } from '../data/liveTask';
import { busyLabelFor, firstDayLine, presentEmployee, type FirstDayPhase } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import type { FirstDay as FirstDayEntry } from '../state/homeStore';
import { Avatar, StatusDot } from '../ui/primitives';
import { RoutineLine } from '../ui/routine';
import { PrimaryActionButton } from './PrimaryActionButton';
import type { EmployeeControl } from './useEmployeeControl';

function TheirRoutine({ workflowId, name }: { workflowId: string; name: string }) {
  const plan = useEmployeeDetailQuery(workflowId).data?.plan ?? [];
  if (plan.length === 0) return null;
  return <RoutineLine steps={plan} label={`What ${name} does`} className="border-t border-border-default pt-2.25" />;
}

export function FirstDay({
  employee,
  firstDay,
  phase,
  control,
  spoken,
}: {
  employee: EmployeeSummary;
  firstDay: FirstDayEntry;
  phase: FirstDayPhase;
  control: EmployeeControl;
  /** The conversation has a message: the routine line goes. */
  spoken: boolean;
}) {
  const { name } = employee;
  const rootRef = useRef<HTMLElement>(null);
  const avatarRef = useRef<HTMLSpanElement>(null);
  const pending = useWorkflowControlPending(employee.workflow_id);
  const answering = useAnswering(employee);
  const { pill, pulse } = presentEmployee(employee, pending, answering);
  // The hire's own start, before Start makes a control row, while the
  // summary still says Not started.
  const status = phase === 'starting' ? { label: busyLabelFor('start'), tone: 'ready' as const } : pill;

  useLayoutEffect(() => {
    rise(rootRef.current, { duration: 'chat-rise' });
  }, []);
  // They are ready: the hire glow, once.
  useLayoutEffect(() => {
    if (phase !== 'ready') return;
    animate(
      avatarRef.current,
      [
        { transform: 'scale(1.06)', boxShadow: 'var(--glow-hire)' },
        { transform: 'none', boxShadow: '0 0 0 0 transparent' },
      ],
      { duration: 'glow', easing: 'spring' },
    );
  }, [phase]);

  return (
    <section
      ref={rootRef}
      aria-label={`${name}’s first day`}
      className="flex flex-col gap-2.5 rounded-card border border-border-default bg-bg-panel px-3 py-2.5"
    >
      <div className="flex items-center gap-2.5">
        <div className="relative shrink-0">
          <span ref={avatarRef} className="block rounded-full">
            <Avatar name={name} colorRole={employee.color_role} photo={employee.photo_url} size="md" accent />
          </span>
          {phase === 'starting' && (
            <RingSpinner slow className="absolute -inset-1 border-status-ready-border border-t-status-ready-dot" />
          )}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-px">
          <p className="m-0 truncate text-row font-semibold text-fg-default">
            {name}
            {employee.role && <span className="font-normal text-fg-muted"> · {employee.role}</span>}
          </p>
          <p aria-live="polite" className="m-0 truncate text-meta text-fg-muted">
            {firstDayLine(employee, phase, firstDay.nodeCount)}
          </p>
        </div>
        {phase === 'stopped' && <PrimaryActionButton control={control} className="h-7 px-3 text-meta" />}
        <span className="inline-flex shrink-0 items-center gap-1.5 text-xs font-medium text-fg-muted">
          <StatusDot tone={status.tone} pulse={pulse} className="size-1.5" />
          {status.label}
        </span>
      </div>
      {!spoken && <TheirRoutine workflowId={employee.workflow_id} name={name} />}
    </section>
  );
}

export default FirstDay;
