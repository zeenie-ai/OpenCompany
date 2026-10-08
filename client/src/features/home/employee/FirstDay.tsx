/**
 * A new hire's first day (onboarding handoff C): what their page shows in
 * place of the empty conversation, from the hire until the owner's first
 * message, for a hire made in this session (homeStore.firstDays).
 *
 * - Arrival: their avatar, with a ring turning while they start, then the
 *   hire glow and a ready pip; "{Name} joined your team".
 * - Getting them ready: four rows driven by what really happened, never by
 *   timers. The hire saved their setup and built their workflow, so those
 *   two are done; their start follows the control state, and their
 *   conversation is ready once they run with Talk on. A start that did not
 *   go ahead says why, with their main action (connect what is missing, or
 *   Start).
 * - What they do: their routine, from the hire's plan.
 *
 * The chat around it keeps the box waiting while they start and says hello
 * once they can read messages (EmployeeChat).
 */

import { Check, Plug } from 'lucide-react';
import { useLayoutEffect, useRef } from 'react';
import { RingSpinner } from '@/components/ui/ring-spinner';
import { RISE, animate, rise, stagger } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { useEmployeeDetailQuery } from '../data/employees';
import { firstDayNote, type FirstDayPhase } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import type { FirstDay as FirstDayEntry } from '../state/homeStore';
import { Avatar, MicroLabel, StatusPill } from '../ui/primitives';
import { RoutineRow, RoutineTimeline } from '../ui/routine';
import { PrimaryActionButton } from './PrimaryActionButton';
import type { EmployeeControl } from './useEmployeeControl';

type RowState = 'pending' | 'active' | 'done';

const POP: Keyframe[] = [
  { transform: 'scale(0.4)', opacity: 0 },
  { transform: 'none', opacity: 1 },
];

/** A row's done mark, popping in when it arrives. */
function DoneMark() {
  const ref = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    animate(ref.current, POP, { duration: 'chip-pop', easing: 'overshoot' });
  }, []);
  return (
    <span
      ref={ref}
      className="grid size-5.5 place-items-center rounded-full border border-action-run-border bg-action-run-soft text-action-run-ink"
    >
      <Check aria-hidden className="size-3" strokeWidth={2.5} />
    </span>
  );
}

function ReadyRow({ state, label, done }: { state: RowState; label: string; done: string }) {
  return (
    <li className={cn('grid grid-cols-[22px_minmax(0,1fr)] items-center gap-2.5 py-1.75', state === 'pending' && 'opacity-50')}>
      <span className="grid size-5.5 place-items-center">
        {state === 'done' ? (
          <DoneMark />
        ) : state === 'active' ? (
          <RingSpinner className="size-3 border-status-ready-border border-t-status-ready-dot" />
        ) : (
          <span aria-hidden className="size-1.25 rounded-full bg-fg-faint" />
        )}
      </span>
      <span className={cn('truncate text-row', state === 'done' ? 'text-fg-default' : 'text-fg-muted')}>
        {state === 'done' ? done : label}
      </span>
    </li>
  );
}

/** The ready pip on their avatar, popping in once they run. */
function ReadyPip() {
  const ref = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    animate(ref.current, [{ transform: 'scale(0)' }, { transform: 'none' }], { duration: 'chip-pop', easing: 'overshoot' });
  }, []);
  return (
    <span
      ref={ref}
      aria-hidden
      className="absolute right-0 bottom-0 box-content size-3.25 rounded-full border-3 border-bg-app bg-status-ready-dot"
    />
  );
}

function TheirRoutine({ workflowId, name }: { workflowId: string; name: string }) {
  const plan = useEmployeeDetailQuery(workflowId).data?.plan ?? [];
  const rowsRef = useRef<HTMLDivElement>(null);
  const count = plan.length;
  useLayoutEffect(() => {
    const rows = rowsRef.current?.querySelectorAll('[data-routine-row]');
    if (count > 0 && rows) stagger(rows, RISE, { base: 300, step: 140, duration: 'follow-in', easing: 'spring' });
  }, [count]);
  if (count === 0) return null;
  return (
    <section className="flex w-full flex-col gap-2 rounded-card border border-border-default bg-bg-panel px-4 py-3.5">
      <div className="flex items-baseline gap-2">
        <h3 className="m-0 flex-1 text-row font-semibold text-fg-default">What {name} does</h3>
        <span className="font-mono text-2xs text-fg-faint">
          {count} {count === 1 ? 'step' : 'steps'}
        </span>
      </div>
      <RoutineTimeline ref={rowsRef}>
        {plan.map((step, index) => (
          <RoutineRow key={index} role={step.role} title={step.title} detail={step.detail} size="sm" />
        ))}
      </RoutineTimeline>
    </section>
  );
}

export function FirstDay({
  employee,
  firstDay,
  phase,
  control,
}: {
  employee: EmployeeSummary;
  firstDay: FirstDayEntry;
  phase: FirstDayPhase;
  control: EmployeeControl;
}) {
  const { name } = employee;
  const rootRef = useRef<HTMLDivElement>(null);
  const avatarRef = useRef<HTMLSpanElement>(null);

  useLayoutEffect(() => {
    rise(rootRef.current);
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

  const start: RowState = phase === 'ready' ? 'done' : phase === 'starting' ? 'active' : 'pending';
  const talk: RowState = phase === 'ready' && employee.talk.state === 'on' ? 'done' : 'pending';
  const note = phase === 'stopped' ? firstDayNote(employee) : null;

  return (
    <div ref={rootRef} className="mx-auto flex w-full max-w-135 flex-col items-center gap-5.5">
      <div className="flex flex-col items-center gap-3">
        <div className="relative">
          <span ref={avatarRef} className="block rounded-full">
            <Avatar name={name} colorRole={employee.color_role} photo={employee.photo_url} size="xl" />
          </span>
          {phase === 'starting' && (
            <RingSpinner slow className="absolute -inset-1.5 border-status-ready-border border-t-status-ready-dot" />
          )}
          {phase === 'ready' && <ReadyPip />}
        </div>
        <div className="flex flex-col items-center gap-1.5 text-center">
          <MicroLabel className="text-node-agent-ink">New hire</MicroLabel>
          <h2 className="m-0 text-headline leading-hero font-semibold tracking-hero text-balance text-fg-default">
            {name} joined your team
          </h2>
        </div>
      </div>

      <section className="w-full overflow-hidden rounded-card border border-border-default bg-bg-elevated shadow-float">
        <div className="flex flex-col gap-1 px-4 pt-3.5 pb-2.5">
          <div className="flex items-center gap-2.5">
            <h3 aria-live="polite" className="m-0 flex-1 truncate text-row font-semibold text-fg-default">
              {phase === 'ready' ? `${name} is ready` : `Getting ${name} ready`}
            </h3>
            {phase === 'ready' ? (
              <StatusPill tone="ready" label="Ready" size="compact" />
            ) : phase === 'starting' ? (
              <StatusPill tone="ready" label="Starting…" size="compact" />
            ) : (
              <StatusPill tone="paused" label="Not started" size="compact" />
            )}
          </div>
          <ul className="m-0 list-none p-0">
            <ReadyRow state="done" label="Saving their setup" done="Setup saved" />
            <ReadyRow
              state="done"
              label="Building their workflow"
              done={firstDay.nodeCount === null ? 'Workflow built' : `Workflow built · ${firstDay.nodeCount} blocks`}
            />
            <ReadyRow state={start} label={`Starting ${name}`} done={`${name} is running`} />
            <ReadyRow state={talk} label="Opening their conversation" done="Ready to talk" />
          </ul>
        </div>
        {note && (
          <div className="flex flex-wrap items-center gap-3 border-t border-border-default bg-bg-panel px-4 py-3">
            <Plug aria-hidden className="size-3.75 shrink-0 text-node-trigger-ink" />
            <p className="m-0 min-w-40 flex-1 text-sm text-fg-muted">{note}</p>
            <PrimaryActionButton control={control} className="h-7.5 px-3.5" />
          </div>
        )}
      </section>

      <TheirRoutine workflowId={employee.workflow_id} name={name} />
    </div>
  );
}

export default FirstDay;
