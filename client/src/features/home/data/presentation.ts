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
 * | attention             | Needs attention           | Resume when possible; after a failure Start again (the server resets first); else Open in Dev mode |
 * | pending_approvals > 0 | Needs you (overrides pill) | (the drafts are the action)    |
 * | browser_request       | Needs you (overrides pill) | (Help in browser opens the Workspace) |
 *
 * The message box follows the control state the way the server's
 * `send_chat_message` does (its `delivery`): a message goes now while the
 * employee runs, starts or resumes, waits while it is paused or pausing,
 * and cannot be sent otherwise.
 */

import type { AppRef, EmployeeSummary } from './schemas';
import type {
  WorkflowControlPendingMutation,
  WorkflowControlState,
  WorkflowControlStatus,
} from '@/contexts/WebSocketContext';

/** `live` is the Workspace's "watching now" pill, not an employee state. */
export type StatusTone = 'working' | 'ready' | 'paused' | 'attention' | 'waiting' | 'live';

export type PrimaryAction =
  | { kind: 'pause' }
  | { kind: 'resume' }
  /** `again`: the last run failed; `start_employee` resets it first. */
  | { kind: 'start'; again?: boolean }
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
  paused: { label: 'Stopped', tone: 'paused' },
  attention: { label: 'Needs attention', tone: 'attention' },
};

const BUSY: Record<WorkflowControlPendingMutation['action'], string> = {
  start: 'Starting…',
  pause: 'Stopping…',
  resume: 'Resuming…',
  reset: 'Resetting…',
};

/** What starting needs first: an app to connect, or an AI model. */
function startBlocker(employee: EmployeeSummary): PrimaryAction | null {
  if (employee.missing_apps.length > 0) return { kind: 'connect_app', app: employee.missing_apps[0] };
  if (employee.needs_ai) return { kind: 'connect_ai' };
  return null;
}

function primaryAction(employee: EmployeeSummary): PrimaryAction {
  const { control } = employee;
  switch (employee.status) {
    case 'working':
      return control.can_pause ? { kind: 'pause' } : { kind: 'open_workflow' };
    case 'paused':
      return control.can_resume ? { kind: 'resume' } : { kind: 'open_workflow' };
    case 'attention':
      if (control.can_resume) return { kind: 'resume' };
      // A failed run has neither can_start nor can_resume, but
      // start_employee resets a failed employee and starts it again.
      if (control.state === 'failed') return startBlocker(employee) ?? { kind: 'start', again: true };
      return { kind: 'open_workflow' };
    case 'ready':
    default:
      return startBlocker(employee) ?? (control.can_start ? { kind: 'start' } : { kind: 'open_workflow' });
  }
}

export function presentEmployee(
  employee: EmployeeSummary,
  pending?: WorkflowControlPendingMutation,
): EmployeePresentation {
  const needsYou = employee.pending_approvals > 0 || employee.browser_request !== null;
  const pill = needsYou ? { label: 'Needs you', tone: 'waiting' as const } : PILL[employee.status];
  return {
    pill,
    // Only a green working dot pulses (its ring is green).
    pulse: pill.tone === 'working',
    primary: primaryAction(employee),
    busyLabel: pending ? BUSY[pending.action]
      : employee.control.state === 'pausing' ? 'Stopping…'
      : employee.control.state === 'resuming' ? 'Resuming…' : null,
  };
}

/** The button label while a lifecycle change is on its way. */
export function busyLabelFor(action: WorkflowControlPendingMutation['action']): string {
  return BUSY[action];
}

/** Opening the employee's workflow in the editor. */
export const OPEN_IN_DEV_LABEL = 'Open in Dev mode';

export function primaryActionLabel(action: PrimaryAction): string {
  switch (action.kind) {
    case 'pause':
      return 'Stop';
    case 'resume':
      return 'Resume';
    case 'start':
      return action.again ? 'Start again' : 'Start';
    case 'connect_app':
      return `Connect ${action.app.name}`;
    case 'connect_ai':
      return 'Connect an AI model';
    case 'open_workflow':
      return OPEN_IN_DEV_LABEL;
  }
}

// ----- the message box -----

/** `send`: delivered now. `queue`: waits for Resume. `start`: nothing to send to yet. */
export type TalkMode = 'send' | 'queue' | 'start';

const TALK_MODE: Record<WorkflowControlState, TalkMode> = {
  running: 'send',
  starting: 'send',
  resuming: 'send',
  paused: 'queue',
  pausing: 'queue',
  never_started: 'start',
  ready: 'start',
  resetting: 'start',
  failed: 'start',
};

export function talkMode(control: WorkflowControlStatus): TalkMode {
  return TALK_MODE[control.state] ?? 'start';
}

/** The line above the message box, or null while messages go straight through.
 *  `queued`: a message sent while paused is waiting. */
export function talkNoticeText(mode: TalkMode, name: string, queued = false): string | null {
  switch (mode) {
    case 'send':
      return null;
    case 'queue':
      return queued
        ? `Your message is waiting. ${name} will read it when you resume them.`
        : `${name} is paused. They’ll read your message when you resume them.`;
    case 'start':
      return `${name} isn’t running, so they can’t read messages right now.`;
  }
}

/** The line beside their main action for an employee who cannot take
 *  messages here at all (Talk off or unsupported): only their state. */
export function stateNoticeText(mode: TalkMode, name: string): string | null {
  switch (mode) {
    case 'send':
      return null;
    case 'queue':
      return `${name} is paused.`;
    case 'start':
      return `${name} isn’t running.`;
  }
}

/** Said before anything that restarts an employee (Turn on Talk, Apply): a
 *  restart cancels the drafts waiting for the owner. */
export function restartDraftsWarning(name: string, drafts: number): string {
  const them = drafts === 1 ? 'it' : 'them';
  return `${name} has ${drafts} ${drafts === 1 ? 'draft' : 'drafts'} waiting for you. Restarting throws ${them} away, so check ${them} first.`;
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

export { AVATAR_CLASS, initialOf } from '@/components/catalog/presentation';
