/**
 * What Hire sends: the employee the setup screen describes, read from the
 * screen as the owner left it (their toggles, choices and answers, and when
 * they work), or a starter's own setup when it is hired in one click.
 *
 * The server builds the workflow from this (services/employees/builder.py)
 * and never takes node types from it. `HIRE_PAYLOAD_KEYS` must match the
 * server's HireEmployeeRequest; tests/test_hire_payload_contract.py reads it
 * off disk and checks. The trigger lists are server/config/genui_catalog.json's,
 * whose times and weekdays are the builder's own
 * (tests/test_genui_catalog_sync.py), so a schedule shown here is the one
 * that runs.
 */

import { trigger as triggerLists } from '../../../../../server/config/genui_catalog.json';
import type { Starter } from '../hire/templates';
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

export const TRIGGER_KINDS: readonly string[] = triggerLists.kinds;
export const SCHEDULE_EVERY: readonly string[] = triggerLists.every;
/** When a schedule can run, in the owner's time zone. */
export const SCHEDULE_TIMES: readonly string[] = triggerLists.times;
/** The days a weekly schedule can run on, in week order. */
export const WEEKDAYS: readonly string[] = triggerLists.days;
/** The last day of the month a monthly schedule can run on (every month has one). */
export const LAST_MONTH_DAY = 28;

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

// ----- when they work -----

function minutesOf(time: string): number {
  return Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5));
}

/** The schedule time closest to `at` (09:00 when `at` is not a time); of
 *  two equally close, the earlier, as the builder decides. */
export function nearestTime(at: unknown): string {
  const match = typeof at === 'string' ? /^(\d{1,2}):([0-5]\d)$/.exec(at.trim()) : null;
  if (!match || Number(match[1]) > 23) return '09:00';
  const wanted = Number(match[1]) * 60 + Number(match[2]);
  let best: string = SCHEDULE_TIMES[0];
  let bestGap = Infinity;
  let bestLater = true;
  for (const slot of SCHEDULE_TIMES) {
    const after = (minutesOf(slot) - wanted + 1440) % 1440;
    const gap = Math.min(after, 1440 - after);
    const later = after > 0 && after <= 720;
    if (gap < bestGap || (gap === bestGap && bestLater && !later)) {
      best = slot;
      bestGap = gap;
      bestLater = later;
    }
  }
  return best;
}

function weekdayOf(value: unknown): string {
  const text = typeof value === 'string' ? value.trim().toLowerCase() : '';
  return (text.length >= 3 && WEEKDAYS.find((day) => day.startsWith(text.slice(0, 3)))) || 'monday';
}

function monthDayOf(value: unknown): string {
  const day = typeof value === 'string' || typeof value === 'number' ? Number(String(value).trim()) : NaN;
  return Number.isInteger(day) && day >= 1 && day <= LAST_MONTH_DAY ? String(day) : '1';
}

/** A trigger the server builds exactly as it reads: an unknown kind is the
 *  owner messaging them, and a schedule gets a frequency, a time it can run
 *  at and a day it can run on. Fields that do not apply to the kind go. */
export function snapTrigger(raw: unknown): HireTrigger {
  const value = isRecord(raw) ? raw : {};
  const kind = (TRIGGER_KINDS as readonly unknown[]).includes(value.kind) ? (value.kind as HireTrigger['kind']) : 'manual';
  if (kind === 'manual') return { kind };
  if (kind === 'app_event') {
    const app = clip(value.app, 40);
    return app ? { kind, app } : { kind };
  }
  const every = (SCHEDULE_EVERY as readonly unknown[]).includes(value.every)
    ? (value.every as NonNullable<HireTrigger['every']>)
    : 'day';
  if (every === 'hour') return { kind, every };
  const trigger: HireTrigger = { kind, every, at: nearestTime(value.at) };
  if (every === 'week') trigger.day = weekdayOf(value.day);
  else if (every === 'month') trigger.day = monthDayOf(value.day);
  return trigger;
}

/** A trigger the screen or the hire button actually gives, snapped;
 *  undefined when it gives none. */
export function readTrigger(raw: unknown): HireTrigger | undefined {
  return isRecord(raw) && (TRIGGER_KINDS as readonly unknown[]).includes(raw.kind) ? snapTrigger(raw) : undefined;
}

export function sameTrigger(a: HireTrigger, b: HireTrigger): boolean {
  return (
    a.kind === b.kind &&
    (a.app ?? '').toLowerCase() === (b.app ?? '').toLowerCase() &&
    a.every === b.every &&
    a.at === b.at &&
    a.day === b.day
  );
}

/** "Every weekday at 09:00", in the words the employee's summary uses for
 *  the same schedule (services/employees/summaries.py). `app` is the app's own
 *  name when it is known. */
export function triggerSentence(trigger: HireTrigger, app: string | undefined = trigger.app): string {
  if (trigger.kind === 'manual') return 'When you message them';
  if (trigger.kind === 'app_event') return app ? `When something new arrives in ${app}` : 'When something new arrives';
  const at = trigger.at ? ` at ${trigger.at}` : '';
  switch (trigger.every) {
    case 'hour':
      return 'Every hour';
    case 'weekday':
      return `Every weekday${at}`;
    case 'week':
      return trigger.day ? `Every ${trigger.day.charAt(0).toUpperCase()}${trigger.day.slice(1)}${at}` : `Every week${at}`;
    case 'month':
      return trigger.day ? `On day ${trigger.day} of every month${at}` : `Every month${at}`;
    default:
      return `Every day${at}`;
  }
}

interface RoutineStep {
  title: string;
  detail?: string;
  role: StepRole;
  app?: string;
}

/** The routine once the owner has changed when they work: its first "When"
 *  step says the new trigger, so the routine never contradicts it.
 *  `written` is the trigger the screen started with. */
export function routineSteps(
  steps: readonly RoutineStep[],
  written: HireTrigger,
  current: HireTrigger,
  app?: string,
): RoutineStep[] {
  const at = steps.findIndex((step) => step.role === 'trigger');
  if (at === -1 || sameTrigger(written, current)) return [...steps];
  const next = [...steps];
  next[at] = {
    title: triggerSentence(current, app),
    role: 'trigger',
    ...(current.kind === 'app_event' && current.app ? { app: current.app } : {}),
  };
  return next;
}

// ----- the setup screen's employee -----

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

function stepRole(role: string): StepRole {
  return (STEP_ROLES as readonly string[]).includes(role) ? (role as StepRole) : 'agent';
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

  // When they work: the screen's own answer, else the hire button's.
  const trigger = readTrigger(getPath(state, STATE_PATHS.trigger)) ?? readTrigger(params.trigger);
  const written = snapTrigger(getPath(spec.state, STATE_PATHS.trigger));
  const steps: HireStep[] = routineSteps(
    (plan?.steps ?? []).filter((step) => step.title),
    written,
    trigger ?? written,
  )
    .slice(0, HIRE_LIMITS.steps)
    .map((step) => ({
      title: step.title,
      detail: step.detail ?? '',
      role: stepRole(step.role),
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
    // A Toggle binds `checked`; a Choice and an Input bind `value`.
    const path = bindingPath(element.type === 'Toggle' ? element.props.checked : element.props.value);
    if (element.type === 'Toggle') {
      const key = path === STATE_PATHS.askFirst ? null : keyUnder(path, STATE_PATHS.rules);
      const props = resolved('Toggle', element, state);
      if (key && props && rules.items.length < HIRE_LIMITS.items) {
        rules.items.push({ key, label: props.label.slice(0, HIRE_LIMITS.label), value: props.checked });
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
  if (trigger) payload.trigger = trigger;
  const sendsVia = clip(params.sendsVia ?? params.sends_via, 40);
  if (sendsVia) payload.sends_via = sendsVia;
  if (source?.provider) payload.source.provider = source.provider;
  if (source?.model) payload.source.model = source.model;
  return payload;
}

// ----- a starter, hired in one click -----

/** A starter's own setup (hire/starters.json), hired as it stands and
 *  asking first. Its name is the first of its names nobody on the team
 *  has, else its first name, numbered. */
export function starterHirePayload(
  starter: Starter,
  { idempotencyKey, taken }: { idempotencyKey: string; taken: readonly string[] },
): HireEmployeePayload {
  const { hire } = starter;
  const used = new Set(taken.map((name) => name.trim().toLowerCase()));
  let name = hire.names.find((candidate) => !used.has(candidate.toLowerCase()));
  for (let n = 2; !name; n++) if (!used.has(`${hire.names[0]} ${n}`.toLowerCase())) name = `${hire.names[0]} ${n}`;
  const payload: HireEmployeePayload = {
    idempotency_key: idempotencyKey,
    job: starter.job,
    name,
    role: hire.role,
    description: hire.description,
    apps: [...starter.apps],
    steps: hire.steps.map((step) => ({
      title: step.title,
      detail: step.detail ?? '',
      role: stepRole(step.role),
      ...(step.app ? { app: step.app } : {}),
    })),
    rules: { ask_first: true, items: [] },
    choices: [],
    inputs: [],
    trigger: snapTrigger(hire.trigger),
    source: { spec_version: 1 },
  };
  if (hire.sends_via) payload.sends_via = hire.sends_via;
  return payload;
}

/** Characters, as a stand-in for bytes: the server refuses requests over 32 KB. */
export const MAX_HIRE_REQUEST_CHARS = 32_000;

export function fitsHireLimit(payload: HireEmployeePayload): boolean {
  return JSON.stringify(payload).length <= MAX_HIRE_REQUEST_CHARS;
}
