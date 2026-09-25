/**
 * Wire shapes for Normal mode's employees (server:
 * services/employees/summaries.py), parsed with zod.
 *
 * Parsing is forgiving per field (`.catch`) so one odd value degrades that
 * field instead of dropping the whole team list, but an entry without its
 * identity (`workflow_id`, `name`) is dropped. `control` goes through the
 * same normalizer the editor uses, so both screens read one control shape.
 */

import { z } from 'zod';
import { normalizeWorkflowControlStatus, type WorkflowControlStatus } from '@/contexts/WebSocketContext';

export const COLOR_ROLES = ['agent', 'model', 'tool', 'trigger', 'workflow'] as const;
export type ColorRole = (typeof COLOR_ROLES)[number];

export const EMPLOYEE_STATUSES = ['working', 'ready', 'paused', 'attention'] as const;
export type EmployeeStatus = (typeof EMPLOYEE_STATUSES)[number];

export const TASK_LABELS = ['Now', 'Next', 'Paused', 'Waiting'] as const;

export const appRefSchema = z.object({
  app_id: z.string(),
  provider_id: z.string(),
  name: z.string(),
  icon_ref: z.string().nullable().catch(null).optional(),
  connected: z.boolean().catch(false),
  supported: z.boolean().catch(true),
});
export type AppRef = z.infer<typeof appRefSchema>;

const appList = z.array(appRefSchema).catch([]);

export const employeeSummarySchema = z.object({
  workflow_id: z.string().min(1),
  name: z.string(),
  role: z.string().catch(''),
  color_role: z.enum(COLOR_ROLES).catch('agent'),
  derived: z.boolean().catch(true),
  status: z.enum(EMPLOYEE_STATUSES).catch('ready'),
  task: z
    .object({ label: z.enum(TASK_LABELS).catch('Now'), text: z.string().catch('') })
    .catch({ label: 'Next', text: '' }),
  done_today: z.number().int().nonnegative().catch(0),
  pending_approvals: z.number().int().nonnegative().catch(0),
  apps: appList,
  missing_apps: appList,
  unsupported_apps: z.array(z.string()).catch([]),
  needs_ai: z.boolean().catch(false),
  // Optional: zod 4 requires a `z.unknown()` key to be present. A summary
  // without one normalizes to "never started".
  control: z.unknown().optional(),
  watch_node_ids: z.array(z.string()).catch([]),
  revision: z.number().catch(0),
  hired_at: z.string().nullable().catch(null),
});

export type EmployeeSummary = Omit<z.infer<typeof employeeSummarySchema>, 'control'> & {
  control: WorkflowControlStatus;
};

const planStepSchema = z.object({
  title: z.string(),
  detail: z.string().optional().catch(undefined),
  role: z.enum(['trigger', 'agent', 'tool', 'workflow']).catch('agent'),
  app: z.string().optional().catch(undefined),
});

const ruleItemSchema = z.object({ key: z.string(), label: z.string(), value: z.boolean() });

export const employeeDetailSchema = employeeSummarySchema.extend({
  description: z.string().catch(''),
  job: z.string().catch(''),
  plan: z.array(planStepSchema).catch([]),
  rules: z
    .object({ ask_first: z.boolean().catch(true), items: z.array(ruleItemSchema).catch([]) })
    .partial()
    .catch({}),
  choices: z.array(z.object({ key: z.string(), label: z.string(), value: z.string() })).catch([]),
  trigger_text: z.string().catch(''),
  latest_report: z.string().nullable().catch(null),
  last_run: z.record(z.string(), z.unknown()).nullable().catch(null),
});

export type EmployeeDetail = Omit<z.infer<typeof employeeDetailSchema>, 'control'> & {
  control: WorkflowControlStatus;
};

function withControl<T extends { workflow_id: string; control?: unknown }>(parsed: T): Omit<T, 'control'> & {
  control: WorkflowControlStatus;
} {
  return { ...parsed, control: normalizeWorkflowControlStatus(parsed.control, parsed.workflow_id) };
}

/** One summary, or null when it lacks its identity. */
export function parseEmployee(raw: unknown): EmployeeSummary | null {
  const result = employeeSummarySchema.safeParse(raw);
  return result.success ? withControl(result.data) : null;
}

/** A team list, dropping entries that lack their identity. */
export function parseEmployees(raw: unknown): EmployeeSummary[] {
  if (!Array.isArray(raw)) return [];
  const out: EmployeeSummary[] = [];
  for (const item of raw) {
    const parsed = parseEmployee(item);
    if (parsed) out.push(parsed);
  }
  return out;
}

export function parseEmployeeDetail(raw: unknown): EmployeeDetail | null {
  const result = employeeDetailSchema.safeParse(raw);
  return result.success ? withControl(result.data) : null;
}
