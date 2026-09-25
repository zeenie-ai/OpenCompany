/**
 * How an employee looks and what its main button does, from its summary.
 *
 * A table over server-decided fields: the server owns `status`, the task
 * text, `missing_apps`, `needs_ai` and the control capabilities; this only
 * picks labels, colours and the primary action from them. It derives no new
 * rules.
 *
 * | Server state          | Pill (status tone)        | Primary action                  |
 * |-----------------------|---------------------------|---------------------------------|
 * | working               | Working                   | Pause                           |
 * | ready                 | Ready                     | Connect {App} / Connect AI / Start |
 * | paused                | Paused                    | Resume                          |
 * | attention             | Needs attention           | Resume when possible, else Open workflow |
 * | pending_approvals > 0 | Needs you (overrides pill) | (the drafts are the action)    |
 */

import type { AppRef, ColorRole, EmployeeSummary } from './schemas';
import type { WorkflowControlPendingMutation } from '@/contexts/WebSocketContext';

/** `live` is the Workspace's "watching now" pill, not an employee state. */
export type StatusTone = 'working' | 'ready' | 'paused' | 'attention' | 'waiting' | 'live';

export type PrimaryAction =
  | { kind: 'pause' }
  | { kind: 'resume' }
  | { kind: 'start' }
  | { kind: 'connect_app'; app: AppRef }
  | { kind: 'connect_ai' }
  | { kind: 'open_workflow' };

export interface EmployeePresentation {
  pill: { label: string; tone: StatusTone };
  /** The working pip pulses; nothing else does. */
  pulse: boolean;
  primary: PrimaryAction;
  /** Label for the primary button while a lifecycle change is in flight. */
  busyLabel: string | null;
}

const PILL: Record<EmployeeSummary['status'], { label: string; tone: StatusTone }> = {
  working: { label: 'Working', tone: 'working' },
  ready: { label: 'Ready', tone: 'ready' },
  paused: { label: 'Paused', tone: 'paused' },
  attention: { label: 'Needs attention', tone: 'attention' },
};

const BUSY: Record<WorkflowControlPendingMutation['action'], string> = {
  start: 'Starting…',
  pause: 'Pausing…',
  resume: 'Resuming…',
  reset: 'Resetting…',
};

function primaryAction(employee: EmployeeSummary): PrimaryAction {
  const { control } = employee;
  switch (employee.status) {
    case 'working':
      return control.can_pause ? { kind: 'pause' } : { kind: 'open_workflow' };
    case 'paused':
      return control.can_resume ? { kind: 'resume' } : { kind: 'open_workflow' };
    case 'attention':
      return control.can_resume ? { kind: 'resume' } : { kind: 'open_workflow' };
    case 'ready':
    default:
      if (employee.missing_apps.length > 0) return { kind: 'connect_app', app: employee.missing_apps[0] };
      if (employee.needs_ai) return { kind: 'connect_ai' };
      return control.can_start ? { kind: 'start' } : { kind: 'open_workflow' };
  }
}

export function presentEmployee(
  employee: EmployeeSummary,
  pending?: WorkflowControlPendingMutation,
): EmployeePresentation {
  const pill = employee.pending_approvals > 0 ? { label: 'Needs you', tone: 'waiting' as const } : PILL[employee.status];
  return {
    pill,
    // Only a green working dot pulses (its ring is green).
    pulse: pill.tone === 'working',
    primary: primaryAction(employee),
    busyLabel: pending ? BUSY[pending.action] : null,
  };
}

/** The button label while a lifecycle change is on its way. */
export function busyLabelFor(action: WorkflowControlPendingMutation['action']): string {
  return BUSY[action];
}

export function primaryActionLabel(action: PrimaryAction): string {
  switch (action.kind) {
    case 'pause':
      return 'Pause';
    case 'resume':
      return 'Resume';
    case 'start':
      return 'Start';
    case 'connect_app':
      return `Connect ${action.app.name}`;
    case 'connect_ai':
      return 'Connect an AI model';
    case 'open_workflow':
      return 'Open workflow';
  }
}

// ----- token classes (Tailwind scans these literals) -----

/** Status pill: tinted fill and border, readable ink. */
export const STATUS_PILL_CLASS: Record<StatusTone, string> = {
  working: 'bg-status-working-fill border-status-working-border text-status-working-ink',
  ready: 'bg-status-ready-fill border-status-ready-border text-status-ready-ink',
  paused: 'bg-status-paused-fill border-status-paused-border text-status-paused-ink',
  attention: 'bg-status-attention-fill border-status-attention-border text-status-attention-ink',
  waiting: 'bg-status-waiting-fill border-status-waiting-border text-status-waiting-ink',
  live: 'bg-node-trigger-fill border-node-trigger-edge text-node-trigger-ink',
};

/** Status dot (the sidebar pip, the pill's dot). */
export const STATUS_DOT_CLASS: Record<StatusTone, string> = {
  working: 'bg-status-working-dot',
  ready: 'bg-status-ready-dot',
  paused: 'bg-status-paused-dot',
  attention: 'bg-status-attention-dot',
  waiting: 'bg-status-waiting-dot',
  // Blinks rather than rings (design handoff "Live"); animations.css
  // stops it under reduced motion.
  live: 'bg-node-trigger opencompany-pip-pulse',
};

/** Avatar: the employee's role colour at the soft-button tint. */
export const AVATAR_CLASS: Record<ColorRole, string> = {
  agent: 'bg-node-agent-fill border-node-agent-edge text-node-agent-ink',
  model: 'bg-node-model-fill border-node-model-edge text-node-model-ink',
  tool: 'bg-node-tool-fill border-node-tool-edge text-node-tool-ink',
  trigger: 'bg-node-trigger-fill border-node-trigger-edge text-node-trigger-ink',
  workflow: 'bg-node-workflow-fill border-node-workflow-edge text-node-workflow-ink',
};

/** The avatar's letter: the first letter of the name, "?" when empty. */
export function initialOf(name: string): string {
  const letter = Array.from(name.trim())[0];
  return letter ? letter.toLocaleUpperCase() : '?';
}
