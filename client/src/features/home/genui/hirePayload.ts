/**
 * What Hire sends: the employee the setup screen describes, read from the
 * screen as the owner left it (their toggles, choices and answers).
 *
 * The server builds the workflow from this (services/employees/builder.py)
 * and never takes node types from it. `HIRE_PAYLOAD_KEYS` must match the
 * server's HireEmployeeRequest; tests/test_hire_payload_contract.py reads it
 * off disk and checks.
 */

import { PROP_SCHEMAS, STATE_PATHS, STEP_ROLES, type ComponentType, type PropsOf, type StepRole } from './catalog';
import { bindingPath, getPath, resolveValue, type UiState } from './expressions';
import type { NormalizedSpec, SpecElement } from './normalize';

export const HIRE_PAYLOAD_KEYS = [
  'idempotency_key',
  'job',
  'name',
  'role',
  'description',
  'apps',
  'steps',
  'rules',
  'choices',
  'inputs',
  'trigger',
  'sends_via',
  'source',
] as const;

export const TRIGGER_KINDS = ['app_event', 'schedule', 'manual'] as const;
export const SCHEDULE_EVERY = ['hour', 'day', 'weekday', 'week', 'month'] as const;

export const HIRE_LIMITS = {
  name: 40,
  role: 60,
  description: 280,
  job: 2000,
  apps: 6,
  steps: 6,
  items: 8,
  key: 40,
  label: 120,
  value: 200,
} as const;

export interface HireStep {
  title: string;
  detail: string;
  role: StepRole;
  app?: string;
}

export interface HireTrigger {
  kind: (typeof TRIGGER_KINDS)[number];
  app?: string;
  every?: (typeof SCHEDULE_EVERY)[number];
  at?: string;
  day?: string;
}

export interface HireEmployeePayload {
  idempotency_key: string;
  job: string;
  name: string;
  role: string;
  description: string;
  apps: string[];
  steps: HireStep[];
  rules: { ask_first: boolean; items: { key: string; label: string; value: boolean }[] };
  choices: { key: string; label: string; value: string }[];
  inputs: { key: string; label: string; value: string }[];
  trigger?: HireTrigger;
  sends_via?: string;
  source: { spec_version: 1; provider?: string; model?: string };
}

function clip(value: unknown, max: number): string {
  if (typeof value !== 'string' && typeof value !== 'number') return '';
  return String(value).replace(/\s+/g, ' ').trim().slice(0, max);
}

function elementsOf(spec: NormalizedSpec, type: SpecElement['type']): SpecElement[] {
  return spec.order.map((id) => spec.elements[id]).filter((element) => element?.type === type);
}

function resolved<T extends ComponentType>(type: T, element: SpecElement | undefined, state: UiState): PropsOf<T> | null {
  if (!element) return null;
  const parsed = PROP_SCHEMAS[type].safeParse(resolveValue(element.props, state) ?? {});
  return parsed.success ? (parsed.data as PropsOf<T>) : null;
}

/** The key a control writes under a state root: "/rules/replyHours" -> "replyHours". */
function keyUnder(path: string | null, root: string): string | null {
  if (!path || !path.startsWith(`${root}/`)) return null;
  const key = path.slice(root.length + 1).replace(/\//g, '.');
  return key ? key.slice(0, HIRE_LIMITS.key) : null;
}

function readTrigger(raw: unknown): HireTrigger | undefined {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return undefined;
  const value = raw as Record<string, unknown>;
  if (!(TRIGGER_KINDS as readonly unknown[]).includes(value.kind)) return undefined;
  const trigger: HireTrigger = { kind: value.kind as HireTrigger['kind'] };
  const app = clip(value.app, 40);
  if (app) trigger.app = app;
  if ((SCHEDULE_EVERY as readonly unknown[]).includes(value.every)) trigger.every = value.every as HireTrigger['every'];
  const at = clip(value.at, 5);
  if (/^([01]\d|2[0-3]):[0-5]\d$/.test(at)) trigger.at = at;
  const day = clip(value.day, 12);
  if (day) trigger.day = day;
  return trigger;
}

function uniqueStrings(values: unknown, max: number, each: number): string[] {
  if (!Array.isArray(values)) return [];
  const out: string[] = [];
  for (const value of values) {
    const text = clip(value, each);
    if (text && !out.some((seen) => seen.toLowerCase() === text.toLowerCase())) out.push(text);
    if (out.length >= max) break;
  }
  return out;
}

export interface HireInput {
  spec: NormalizedSpec;
  state: UiState;
  /** The hire button's params, resolved. */
  params: Record<string, unknown>;
  job: string;
  idempotencyKey: string;
  source?: { provider?: string | null; model?: string | null } | null;
}

export function buildHirePayload({ spec, state, params, job, idempotencyKey, source }: HireInput): HireEmployeePayload {
  const agent = resolved('AgentCard', elementsOf(spec, 'AgentCard')[0], state);
  const plan = resolved('Plan', elementsOf(spec, 'Plan')[0], state);

  const name = clip(params.name, HIRE_LIMITS.name) || agent?.name.slice(0, HIRE_LIMITS.name) || 'New employee';
  const role = clip(params.role, HIRE_LIMITS.role) || agent?.role.slice(0, HIRE_LIMITS.role) || 'Assistant';
  const description = (agent?.description ?? '').slice(0, HIRE_LIMITS.description);
  const apps = uniqueStrings(Array.isArray(params.apps) ? params.apps : agent?.apps, HIRE_LIMITS.apps, 40);

  const steps: HireStep[] = (plan?.steps ?? [])
    .filter((step) => step.title)
    .slice(0, HIRE_LIMITS.steps)
    .map((step) => ({
      title: step.title,
      detail: step.detail ?? '',
      role: (STEP_ROLES as readonly string[]).includes(step.role) ? step.role : 'agent',
      ...(step.app ? { app: step.app } : {}),
    }));
  if (steps.length === 0) steps.push({ title: description || `Work as ${role.toLowerCase()}`, detail: '', role: 'agent' });

  const askFirst = getPath(state, STATE_PATHS.askFirst);
  const rules: HireEmployeePayload['rules'] = { ask_first: askFirst !== false, items: [] };
  const choices: HireEmployeePayload['choices'] = [];
  const inputs: HireEmployeePayload['inputs'] = [];
  for (const id of spec.order) {
    const element = spec.elements[id];
    if (!element) continue;
    const path = bindingPath(element.props.value);
    if (element.type === 'Toggle' && path !== STATE_PATHS.askFirst) {
      const key = keyUnder(path, STATE_PATHS.rules);
      const props = resolved('Toggle', element, state);
      if (key && props && rules.items.length < HIRE_LIMITS.items) {
        rules.items.push({ key, label: props.label.slice(0, HIRE_LIMITS.label), value: props.value });
      }
    } else if (element.type === 'Choice') {
      const key = keyUnder(path, STATE_PATHS.choices);
      const props = resolved('Choice', element, state);
      if (key && props?.value && choices.length < HIRE_LIMITS.items) {
        choices.push({ key, label: props.label.slice(0, HIRE_LIMITS.label), value: props.value.slice(0, HIRE_LIMITS.value) });
      }
    } else if (element.type === 'Input') {
      const key = keyUnder(path, STATE_PATHS.inputs) ?? (path ? path.replace(/^\//, '').replace(/\//g, '.').slice(0, HIRE_LIMITS.key) : null);
      const props = resolved('Input', element, state);
      const value = props?.value.trim() ?? '';
      if (key && props && value && inputs.length < HIRE_LIMITS.items) {
        inputs.push({ key, label: props.label.slice(0, HIRE_LIMITS.label), value: value.slice(0, HIRE_LIMITS.value) });
      }
    }
  }

  const payload: HireEmployeePayload = {
    idempotency_key: idempotencyKey,
    job: job.trim().slice(0, HIRE_LIMITS.job),
    name,
    role,
    description,
    apps,
    steps,
    rules,
    choices,
    inputs,
    source: { spec_version: 1 },
  };
  const trigger = readTrigger(params.trigger);
  if (trigger) payload.trigger = trigger;
  const sendsVia = clip(params.sendsVia ?? params.sends_via, 40);
  if (sendsVia) payload.sends_via = sendsVia;
  if (source?.provider) payload.source.provider = source.provider;
  if (source?.model) payload.source.model = source.model;
  return payload;
}

/** Characters, as a stand-in for bytes: the server refuses requests over 32 KB. */
export const MAX_HIRE_REQUEST_CHARS = 32_000;

export function fitsHireLimit(payload: HireEmployeePayload): boolean {
  return JSON.stringify(payload).length <= MAX_HIRE_REQUEST_CHARS;
}
