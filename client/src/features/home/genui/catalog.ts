/**
 * The setup screen's vocabulary: the components and actions a model may
 * use when it describes a new employee, and what each component's props
 * look like once resolved. The vocabulary is
 * server/config/genui_catalog.json, which `bun run genui:prompts` writes
 * the model's prompt from (lib/jsonRender/prompts.ts); the lists below are
 * imported from it, and a test holds the prop schemas to its components and
 * props. The screen is drawn by json-render; HIRE_CATALOG is the vocabulary
 * in the shape its defineCatalog takes.
 *
 * Prop schemas are forgiving by design: a model's odd value degrades that
 * one prop (a default, a clamped string) instead of failing the element.
 * A Toggle binds `checked`; a Button names its action in the element's
 * `on.press` (json-render's event binding), not in its props.
 */

import { z } from 'zod';
import { block, catalogOf, line, listOf, optionalLine, scalar } from '@/lib/jsonRender/catalog';
import {
  action_aliases,
  actions,
  ask_first_label,
  container_types,
  control_types,
  limits,
  state_paths,
  step_roles,
  tones,
} from '../../../../../server/config/genui_catalog.json';
import type { RoutineRole } from '../ui/routine';

/** Components that lay out children; every other component is a leaf. */
export const CONTAINER_TYPES: readonly string[] = container_types;

/** Controls that write to UI state through `{"$bindState": "/path"}`.
 *  Schedule writes one too, but the normalizer places it itself. */
export const CONTROL_TYPES: readonly string[] = control_types;

export type ActionType = keyof typeof actions;
export const ACTION_TYPES = Object.keys(actions) as ActionType[];

/** Old action names the model sometimes uses, and what they mean now. */
export const ACTION_ALIASES = action_aliases as Readonly<Record<string, ActionType>>;

export const TONES: readonly string[] = tones;
export type Tone = string;

export type StepRole = RoutineRole;
export const STEP_ROLES = step_roles as readonly StepRole[];

/** Where the setup screen keeps what the owner decides. */
export const STATE_PATHS = state_paths;

export const ASK_FIRST_LABEL = ask_first_label;

export const LIMITS = limits;

// ----- prop schemas (resolved values) -----

const tone = z.enum(tones).optional().catch(undefined);

const planStep = z.object({
  title: line(60),
  detail: optionalLine(80),
  role: z.enum(STEP_ROLES).catch('agent'),
  app: optionalLine(40),
});

const listItem = z.object({
  title: line(LIMITS.maxLine),
  detail: optionalLine(LIMITS.maxText),
  meta: optionalLine(40),
  tone,
});

export const PROP_SCHEMAS = {
  Stack: z.object({
    direction: z.enum(['vertical', 'horizontal']).catch('vertical'),
    gap: z.enum(['sm', 'md', 'lg']).catch('md'),
  }),
  Grid: z.object({ columns: z.coerce.number().pipe(z.union([z.literal(2), z.literal(3)])).catch(2) }),
  Card: z.object({ title: optionalLine(LIMITS.maxLine), subtitle: optionalLine(LIMITS.maxText), tone }),
  Heading: z.object({ text: line(LIMITS.maxLine) }),
  Text: z.object({ text: block(LIMITS.maxText), muted: z.boolean().catch(false) }),
  Metric: z.object({ label: line(60), value: line(40), hint: optionalLine(LIMITS.maxLine), tone }),
  Badge: z.object({ label: line(40), tone }),
  // `trigger`: bound to /trigger by the normaliser (the When row's Change); the model never writes it.
  Plan: z.object({ title: optionalLine(60), steps: listOf(planStep, LIMITS.maxSteps), trigger: z.unknown().optional() }),
  // What starts the work (the state at /trigger), read through hirePayload's snapTrigger.
  Schedule: z.object({ value: z.unknown().optional() }),
  AgentCard: z.object({
    name: line(40),
    role: line(60),
    description: optionalLine(LIMITS.maxText),
    apps: listOf(line(40), LIMITS.maxApps).transform((apps) => apps.filter(Boolean)),
    status: z.enum(['ready', 'working', 'paused']).catch('ready'),
  }),
  List: z.object({ items: listOf(listItem, LIMITS.maxItems) }),
  Draft: z.object({
    channel: optionalLine(30),
    to: optionalLine(80),
    subject: optionalLine(LIMITS.maxLine),
    body: block(LIMITS.maxBody),
  }),
  Progress: z.object({
    label: line(60),
    value: z.coerce.number().catch(0).transform((v) => Math.max(0, Math.min(100, Number.isFinite(v) ? v : 0))),
    tone,
  }),
  Toggle: z.object({ label: line(LIMITS.maxLine), description: optionalLine(LIMITS.maxText), checked: z.boolean().catch(false) }),
  Choice: z.object({
    label: line(LIMITS.maxLine),
    options: listOf(line(40), LIMITS.maxOptions).transform((options) => [...new Set(options.filter(Boolean))]),
    value: optionalLine(40),
  }),
  Input: z.object({
    label: line(LIMITS.maxLine),
    placeholder: optionalLine(LIMITS.maxLine),
    value: scalar.catch('').transform((s) => s.slice(0, LIMITS.maxInput)),
  }),
  // What it runs is the element's on.press, which json-render resolves at the press.
  Button: z.object({
    label: line(40),
    variant: z.enum(['primary', 'secondary']).catch('secondary'),
  }),
  Divider: z.object({}),
};

export type ComponentType = keyof typeof PROP_SCHEMAS;

export type PropsOf<T extends ComponentType> = z.output<(typeof PROP_SCHEMAS)[T]>;

export function isComponentType(value: unknown): value is ComponentType {
  return typeof value === 'string' && Object.hasOwn(PROP_SCHEMAS, value);
}

export function isContainer(type: ComponentType): boolean {
  return CONTAINER_TYPES.includes(type);
}

export function isControl(type: ComponentType): boolean {
  return CONTROL_TYPES.includes(type);
}

/** The action a Button runs, with old names mapped; null when unknown. */
export function actionOf(value: unknown): ActionType | null {
  if (typeof value !== 'string') return null;
  const name = Object.hasOwn(ACTION_ALIASES, value) ? ACTION_ALIASES[value] : value;
  return Object.hasOwn(actions, name) ? (name as ActionType) : null;
}

/**
 * What json-render's defineCatalog takes for the setup screen. registry.ts
 * defines the catalogue from it there, beside the renderer, because this
 * file is in Home's first chunk and json-render loads only with the screen.
 */
export const HIRE_CATALOG = catalogOf(PROP_SCHEMAS, CONTAINER_TYPES);
