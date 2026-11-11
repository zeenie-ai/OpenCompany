/**
 * A team task as the server sends it (`Database._team_task_dict`), and the
 * small pure helpers the Task Manager panel's list and its opened task
 * share.
 */

/** In lifecycle order (blocked -> queued -> running -> submitted ->
 *  accepted), then the two ways out. */
export const TASK_STATUSES = ['blocked', 'queued', 'running', 'submitted', 'accepted', 'failed', 'cancelled'] as const;

export type TeamTaskStatus = (typeof TASK_STATUSES)[number];

export interface TeamTaskAttempt {
  id: string;
  attempt_number: number;
  assignee_node_id?: string;
  status: string;
  result?: unknown;
  error?: string;
  started_at?: string;
  completed_at?: string;
  workflow_id?: string;
  run_id?: string;
  trace_id?: string;
}

export interface TeamTaskView {
  id: string;
  title: string;
  mission?: string;
  description?: string;
  context?: unknown;
  acceptance_criteria?: unknown;
  status: TeamTaskStatus | string;
  assigned_to?: string;
  assignee_label?: string;
  assignee_type?: string;
  queue_position?: number;
  queue_sequence?: number;
  depends_on?: string[];
  current_attempt?: number;
  retry_count?: number;
  revision?: number;
  result?: unknown;
  error?: string;
  usage?: { input_tokens?: number; output_tokens?: number; total_tokens?: number; cost?: number };
  trace_id?: string;
  child_workflow_id?: string;
  child_run_id?: string;
  created_at?: string;
  started_at?: string;
  completed_at?: string;
  attempts?: TeamTaskAttempt[];
  allowed_actions?: string[];
}

export const jsonText = (value: unknown): string => {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
};

/** Nothing to show: unset, blank, or an empty list or object. */
export const isEmpty = (value: unknown): boolean => {
  if (value == null) return true;
  if (typeof value === 'string') return value.trim() === '';
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === 'object') return Object.keys(value).length === 0;
  return false;
};

const timestampMs = (value?: string): number | null => {
  if (!value) return null;
  // SQLite commonly returns UTC timestamps without a trailing zone. Browsers
  // otherwise interpret those as local time and skew elapsed durations.
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value.replace(' ', 'T')}Z`;
  const parsed = new Date(normalized).getTime();
  return Number.isFinite(parsed) ? parsed : null;
};

export const formatTimestamp = (value?: string): string => {
  const parsed = timestampMs(value);
  return parsed == null ? '—' : new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium', timeStyle: 'medium',
  }).format(new Date(parsed));
};

/** How long the task has run: to its end, or to now while it runs. */
export const elapsed = (task: TeamTaskView, nowMs: number): string => {
  const startMs = timestampMs(task.started_at);
  if (startMs == null) return '—';
  const end = timestampMs(task.completed_at) ?? nowMs;
  const seconds = Math.max(0, Math.floor((end - startMs) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
};

export const taskUsage = (task: TeamTaskView) => {
  const nested = task.result && typeof task.result === 'object'
    ? (task.result as { usage?: TeamTaskView['usage'] }).usage : undefined;
  const usage = task.usage || nested || {};
  const input = Number(usage.input_tokens || 0);
  const output = Number(usage.output_tokens || 0);
  const total = Number(usage.total_tokens ?? (input + output));
  return { input, output, total, cost: Number(usage.cost || 0) };
};

/** What the owner may do to a task, by its status, when the server sent no
 *  `allowed_actions`. */
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  blocked: ['modify', 'cancel'], queued: ['modify', 'cancel'], running: ['cancel'],
  submitted: ['accept', 'retry', 'reassign'], failed: ['retry', 'reassign'],
  cancelled: ['retry', 'reassign'], accepted: [],
};

/** The task's actions as operation names (`accept_task` and `accept` are the
 *  same operation). */
export const actionsFor = (task: TeamTaskView): string[] =>
  (task.allowed_actions || ACTIONS_BY_STATUS[task.status] || []).map((action) => action.replace('_task', ''));

const ACTION_LABELS: Record<string, string> = {
  accept: 'Accept',
  retry: 'Retry',
  reassign: 'Reassign',
  modify: 'Edit mission',
  cancel: 'Cancel task',
};

/** A wire name as words: `retention_expired` reads "Retention expired". */
export const sentenceCase = (name: string): string => {
  const words = name.replace(/_/g, ' ');
  return `${words.charAt(0).toUpperCase()}${words.slice(1)}`;
};

/** A button's words for an operation; an operation this panel does not know
 *  keeps its own name. */
export const actionLabel = (operation: string): string => ACTION_LABELS[operation] ?? sentenceCase(operation);
