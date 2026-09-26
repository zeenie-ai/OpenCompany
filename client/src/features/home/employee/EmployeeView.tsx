/**
 * One employee (design handoff "Employee view"): who they are, what they
 * are doing now, the apps they use, how much they did today, and what to
 * do next (Pause, Resume, Start, or connect what they are missing).
 *
 * The card shows the server's summary, which the server re-sends the
 * moment the control plane moves. Start / Pause / Resume send the
 * summary's control revision; after one returns, the button keeps its
 * "Pausing…" label until the summary has caught up with the new state (or
 * a few seconds pass and the list is refetched), so it never flashes the
 * old label in between. "Open workflow" opens this employee's graph in Dev
 * mode, and "Watch live" opens the Workspace on this employee ("Help in
 * browser", on its Browser tab, while the agent is waiting for the owner).
 */

import { useQueryClient } from '@tanstack/react-query';
import { Code, Monitor } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  mergeWorkflowControlStatus,
  useWebSocketActions,
  type WorkflowControlStatus,
} from '@/contexts/WebSocketContext';
import { animate } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { useWorkflowControlPending } from '@/stores/workflowControlStore';
import { enterDev } from '../../../app/useShellActions';
import { invalidateEmployees, useEmployeeDetailQuery, useEmployeesQuery } from '../data/employees';
import { useLiveTask } from '../data/liveTask';
import { busyLabelFor, presentEmployee, primaryActionLabel, type PrimaryAction } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import { OrbSlot } from '../orb/OrbSlot';
import { SPIKE, spikeOrb } from '../orb/orb';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';
import { AppMark, Avatar, MicroLabel, StatusPill } from '../ui/primitives';
import { DraftsSection } from './DraftsSection';

/** How long a finished Start / Pause / Resume waits for the summary to catch up. */
const SYNC_WAIT_MS = 4000;

const CONTROL_ERRORS: Record<string, string> = {
  control_revision_conflict: 'That changed a moment ago. Try again.',
  missing_apps: 'Connect the apps they use first.',
};

function controlErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : '';
  return CONTROL_ERRORS[message] ?? 'That did not work. Try again.';
}

function TaskBox({ employee }: { employee: EmployeeSummary }) {
  const live = useLiveTask(employee);
  const task = live ?? employee.task;
  const textRef = useRef<HTMLSpanElement>(null);
  const shownText = useRef(task.text);
  // A new task blurs in and the card's border flashes green (design handoff "Live work").
  useLayoutEffect(() => {
    if (shownText.current === task.text) return;
    shownText.current = task.text;
    spikeOrb(SPIKE.task);
    const text = textRef.current;
    animate(
      text,
      [
        { opacity: 0, transform: 'translateY(8px)', filter: 'blur(3px)' },
        { opacity: 1, transform: 'none', filter: 'blur(0)' },
      ],
      { duration: 520, easing: 'spring', fill: 'backwards' },
    );
    animate(text?.closest('[data-employee]'), [{ borderColor: 'var(--glow-task)' }, { borderColor: 'var(--border-default)' }], {
      duration: 'glow',
      fill: 'none',
    });
  }, [task.text]);
  return (
    <div className="flex flex-col gap-1.5 rounded-card border border-border-default bg-bg-app px-4 py-3.5">
      <MicroLabel>{task.label}</MicroLabel>
      <span ref={textRef} data-task={employee.workflow_id} className="text-md text-fg-default" aria-live="polite">
        {task.text}
      </span>
    </div>
  );
}

type ControlKind = Extract<PrimaryAction['kind'], 'pause' | 'resume' | 'start'>;

function EmployeeCard({ employee, onConnect }: { employee: EmployeeSummary; onConnect: (providerId: string) => void }) {
  const pending = useWorkflowControlPending(employee.workflow_id);
  const view = presentEmployee(employee, pending);
  const actions = useWebSocketActions();
  const queryClient = useQueryClient();
  const openSettings = useHomeStore((s) => s.openSettings);
  const openWorkspace = useHomeStore((s) => s.openWorkspace);
  const setWorkspaceTab = useHomeStore((s) => s.setWorkspaceTab);
  const helpInBrowser = employee.browser_request !== null;
  const [running, setRunning] = useState<ControlKind | null>(null);
  const [awaiting, setAwaiting] = useState<{ kind: ControlKind; target: WorkflowControlStatus } | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const countRef = useRef<HTMLSpanElement>(null);

  // One more done today: the count bumps in green.
  const shownDone = useRef(employee.done_today);
  useLayoutEffect(() => {
    const previous = shownDone.current;
    shownDone.current = employee.done_today;
    if (employee.done_today <= previous) return;
    animate(
      countRef.current,
      [
        { transform: 'none', color: 'var(--status-working-ink)' },
        { transform: 'translateY(-3px) scale(1.25)', color: 'var(--status-working-ink)', offset: 0.35 },
        { transform: 'none', color: 'var(--fg-default)' },
      ],
      { duration: 700, fill: 'none' },
    );
  }, [employee.done_today]);

  // Caught up once the summary's control is at least as new as the result.
  const caughtUp = !awaiting || mergeWorkflowControlStatus(awaiting.target, employee.control) === employee.control;
  const waiting = Boolean(awaiting) && !caughtUp;
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setTimeout(() => {
      setAwaiting(null);
      invalidateEmployees(queryClient);
    }, SYNC_WAIT_MS);
    return () => window.clearTimeout(timer);
  }, [waiting, queryClient]);

  const control = async (kind: ControlKind) => {
    const { workflow_id: id, control: current } = employee;
    setRunning(kind);
    try {
      const run =
        kind === 'pause'
          ? actions.pauseWorkflow(id, current.revision)
          : kind === 'resume'
            ? actions.resumeWorkflow(id, current.revision)
            : actions.startEmployee(id, current.revision);
      setAwaiting({ kind, target: await run });
    } catch (error) {
      pillToast(controlErrorMessage(error), { tone: 'error' });
      if (error instanceof Error && error.message === 'control_revision_conflict') invalidateEmployees(queryClient);
    } finally {
      setRunning(null);
    }
  };

  const act = () => {
    const { primary } = view;
    if (primary.kind === 'connect_app') onConnect(primary.app.provider_id);
    else if (primary.kind === 'connect_ai') openSettings('connectors', 'ai');
    else if (primary.kind === 'open_workflow') void enterDev({ workflowId: employee.workflow_id });
    else {
      animate(cardRef.current, [{ transform: 'scale(1)' }, { transform: 'scale(.97)', offset: 0.3 }, { transform: 'scale(1)' }], {
        duration: 420,
        easing: 'spring',
        fill: 'none',
      });
      void control(primary.kind);
    }
  };

  const inFlight = running ?? (waiting ? awaiting?.kind : null) ?? null;
  const label = view.busyLabel ?? (inFlight ? busyLabelFor(inFlight) : primaryActionLabel(view.primary));
  const busy = Boolean(pending) || Boolean(inFlight);
  const quiet = view.primary.kind === 'pause' || view.primary.kind === 'open_workflow';

  return (
    <div
      ref={cardRef}
      data-employee={employee.workflow_id}
      className="flex w-full flex-col gap-4.5 rounded-draft border border-border-default bg-bg-elevated p-5.5 shadow-float"
    >
      <div className="flex flex-wrap items-center gap-3.5">
        <Avatar name={employee.name} colorRole={employee.color_role} size="lg" />
        <div className="flex min-w-40 flex-1 flex-col gap-0.75">
          <span className="text-title font-semibold tracking-[-0.02em] text-fg-default">{employee.name}</span>
          <span className="text-base text-fg-muted">{employee.role}</span>
        </div>
        <StatusPill tone={view.pill.tone} label={view.pill.label} pulse={view.pulse} />
      </div>

      <TaskBox employee={employee} />

      <DraftsSection
        workflowId={employee.workflow_id}
        employeeName={employee.name}
        paused={employee.control.state === 'paused' || employee.control.state === 'pausing'}
      />

      <div className="flex flex-wrap items-center gap-2">
        {employee.apps.map((app) => (
          <span
            key={app.app_id}
            className={cn(
              'flex h-7.5 items-center gap-1.5 rounded-pill border bg-bg-app pr-2.5 pl-1 text-meta text-fg-default',
              app.connected ? 'border-border-default' : 'border-status-attention-border',
            )}
            title={app.connected ? `${app.name} is connected` : `${app.name} is not connected`}
          >
            <AppMark name={app.name} iconRef={app.icon_ref} size="xs" />
            {app.name}
          </span>
        ))}
        <span className="ml-auto font-mono text-sm whitespace-nowrap text-fg-muted">
          <span ref={countRef} data-count={employee.workflow_id} className="inline-block text-fg-default">
            {employee.done_today}
          </span>{' '}
          done today
        </span>
      </div>

      {employee.unsupported_apps.length > 0 && (
        <p className="m-0 text-xs text-fg-muted">
          Not available yet: {employee.unsupported_apps.join(', ')}. They work without it for now.
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        {quiet ? (
          <Button
            variant="quiet"
            disabled={busy}
            onClick={act}
            className="h-9 gap-2 rounded-row border-border-strong px-4 font-semibold text-fg-default"
          >
            {view.primary.kind === 'open_workflow' && !busy && <Code aria-hidden className="size-3.25" />}
            {label}
          </Button>
        ) : (
          <ActionButton intent="run" disabled={busy} onClick={act} className="h-9 rounded-row px-4">
            {label}
          </ActionButton>
        )}
        {view.primary.kind !== 'open_workflow' && (
          <Button
            variant="quiet"
            onClick={() => void enterDev({ workflowId: employee.workflow_id })}
            className="h-9 gap-2 rounded-row border-border-strong px-3.5 font-semibold text-fg-default"
          >
            <Code aria-hidden className="size-3.25" />
            Open workflow
          </Button>
        )}
        <ActionButton
          intent="tools"
          onClick={() => {
            openWorkspace(employee.workflow_id);
            if (helpInBrowser) setWorkspaceTab('browser');
          }}
          className="h-9 gap-2 rounded-row px-3.5"
        >
          <Monitor aria-hidden className="size-3.5" />
          {helpInBrowser ? 'Help in browser' : 'Watch live'}
        </ActionButton>
      </div>
    </div>
  );
}

export function EmployeeView({ workflowId, onConnect }: { workflowId: string; onConnect: (providerId: string) => void }) {
  const list = useEmployeesQuery();
  const fromList = list.data?.find((e) => e.workflow_id === workflowId) ?? null;
  // Only when the list has no row for it (just hired, or the list failed).
  const detailId = fromList || list.isPending ? null : workflowId;
  const detail = useEmployeeDetailQuery(detailId);
  const showHire = useHomeStore((s) => s.showHire);
  const employee = fromList ?? detail.data ?? null;

  if (!employee) {
    if (list.isPending || (detailId !== null && detail.isPending)) {
      return (
        <section aria-busy className="flex w-full max-w-(--w-employee-card) flex-col items-center gap-4.5">
          <OrbSlot size="employee" />
          <Skeleton className="h-72 w-full rounded-draft" />
        </section>
      );
    }
    return (
      <section className="flex w-full max-w-(--w-employee-card) flex-col items-center gap-3 pt-16 text-center">
        <p className="m-0 text-md text-fg-default">
          {detail.isError ? 'Couldn’t load this employee.' : 'This employee is no longer on the team.'}
        </p>
        <div className="flex gap-2">
          {detail.isError && (
            <Button variant="quiet" onClick={() => void detail.refetch()} className="border-border-default text-fg-default">
              Try again
            </Button>
          )}
          <Button variant="quiet" onClick={() => showHire()} className="border-border-default text-fg-default">
            Back to hiring
          </Button>
        </div>
      </section>
    );
  }

  return (
    <section aria-label={employee.name} className="flex w-full max-w-(--w-employee-card) flex-col items-center gap-4.5">
      <OrbSlot size="employee" />
      <EmployeeCard employee={employee} onConnect={onConnect} />
    </section>
  );
}

export default EmployeeView;
