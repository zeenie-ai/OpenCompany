/**
 * An employee's main action (Pause, Resume, Start, connect what they are
 * missing, or open them in Dev mode): the Workspace header offers it, and
 * their page's message box while they cannot read messages.
 *
 * Start / Pause / Resume send the summary's control revision. After one
 * returns, the label stays "Pausing…" until the summary has caught up with
 * the new state (or a few seconds pass and the list is refetched), so the
 * button never flashes the old label in between. A change that went through
 * says so in a toast, and a start or resume spikes the orb.
 */

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import {
  mergeWorkflowControlStatus,
  useWebSocketActions,
  type WorkflowControlStatus,
} from '@/contexts/WebSocketContext';
import { useWorkflowControlPending } from '@/stores/workflowControlStore';
import { enterDev } from '../../../app/useShellActions';
import { invalidateEmployees } from '../data/employees';
import { busyLabelFor, presentEmployee, primaryActionLabel, type EmployeePresentation, type PrimaryAction } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import { SPIKE, spikeOrb } from '../orb/orb';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';

/** How long a finished Start / Pause / Resume waits for the summary to catch up. */
const SYNC_WAIT_MS = 4000;

const CONTROL_ERRORS: Record<string, string> = {
  control_revision_conflict: 'That changed a moment ago. Try again.',
  missing_apps: 'Connect the apps they use first.',
  needs_ai: 'Connect an AI model first.',
  team_temporal_required: 'Their team is saved, but team work is not ready on this installation. Ask your administrator to finish setup, then press Start again.',
  team_agent_workflow_required: 'Their team is saved, but team work is not ready on this installation. Ask your administrator to finish setup, then press Start again.',
  team_runtime_not_ready: 'Their team is saved and waiting for the service to be ready. Try Start again in a moment.',
  teams_disabled: 'Team hiring is not available on this installation yet. Ask your administrator to enable it.',
};

export function controlErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : '';
  return CONTROL_ERRORS[message] ?? 'That did not work. Try again.';
}

type ControlKind = Extract<PrimaryAction['kind'], 'pause' | 'resume' | 'start'>;

/** What the toast says once a change went through (the button says Stop
 *  for `pause`). */
const CONTROL_DONE: Record<ControlKind, string> = { start: 'started', resume: 'resumed', pause: 'stopped' };

export interface EmployeeControl {
  /** The pill and the primary action, from the summary. */
  view: EmployeePresentation;
  /** The primary button's label, "Pausing…" while a change is on its way. */
  label: string;
  busy: boolean;
  act: () => void;
}

export function useEmployeeControl(employee: EmployeeSummary, onConnect: (providerId: string) => void): EmployeeControl {
  const pending = useWorkflowControlPending(employee.workflow_id);
  const view = presentEmployee(employee, pending);
  const actions = useWebSocketActions();
  const queryClient = useQueryClient();
  const openConnectAI = useHomeStore((s) => s.openConnectAI);
  const [running, setRunning] = useState<ControlKind | null>(null);
  const [awaiting, setAwaiting] = useState<{ kind: ControlKind; target: WorkflowControlStatus } | null>(null);

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
      pillToast(`${employee.name} ${CONTROL_DONE[kind]}`);
      if (kind !== 'pause') spikeOrb(SPIKE.start);
    } catch (error) {
      pillToast(controlErrorMessage(error), { tone: 'error' });
      if (error instanceof Error && error.message === 'control_revision_conflict') invalidateEmployees(queryClient);
      if (error instanceof Error && error.message === 'needs_ai') openConnectAI();
    } finally {
      setRunning(null);
    }
  };

  const act = () => {
    const { primary } = view;
    if (primary.kind === 'connect_app') onConnect(primary.app.provider_id);
    else if (primary.kind === 'connect_ai') openConnectAI();
    else if (primary.kind === 'open_workflow') void enterDev({ workflowId: employee.workflow_id });
    else void control(primary.kind);
  };

  const inFlight = running ?? (waiting ? awaiting?.kind : null) ?? null;
  return {
    view,
    label: view.busyLabel ?? (inFlight ? busyLabelFor(inFlight) : primaryActionLabel(view.primary)),
    busy: Boolean(pending) || Boolean(inFlight) || employee.control.state === 'pausing' || employee.control.state === 'resuming',
    act,
  };
}
