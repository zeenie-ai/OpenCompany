/**
 * The setup screen's vocabulary: the components and actions a model may
 * use when it describes a new employee, and what each component's props
 * look like once resolved.
 *
 * The server writes the model's prompt from server/config/genui_catalog.json;
 * tests/test_genui_catalog_sync.py reads the literal lists in this file off
 * disk and checks they match that manifest, so keep them plain `[...] as
 * const` literals.
 *
 * Prop schemas are forgiving by design: a model's odd value degrades that
 * one prop (a default, a clamped string) instead of failing the element.
 */

import { z } from 'zod';

export const COMPONENT_TYPES = [
  'Stack',
  'Grid',
  'Card',
  'Heading',
  'Text',
  'Metric',
  'Badge',
  'Plan',
  'AgentCard',
  'List',
  'Draft',
  'Progress',
  'Toggle',
  'Choice',
  'Input',
  'Button',
  'Divider',
] as const;
export type ComponentType = (typeof COMPONENT_TYPES)[number];

/** Components that lay out children; every other component is a leaf. */
export const CONTAINER_TYPES = ['Stack', 'Grid', 'Card'] as const;

/** Controls that write to UI state through `{"$bindState": "/path"}`. */
export const CONTROL_TYPES = ['Toggle', 'Choice', 'Input'] as const;

export const ACTION_TYPES = ['setState', 'hire_employee', 'refine', 'connect_app', 'open_connectors'] as const;
export type ActionType = (typeof ACTION_TYPES)[number];

/** Old action names the model sometimes uses, and what they mean now. */
export const ACTION_ALIASES: Readonly<Record<string, ActionType>> = { ask: 'refine' };

export const TONES = ['agent', 'model', 'tool', 'trigger', 'workflow', 'neutral'] as const;
export type Tone = (typeof TONES)[number];

export const STEP_ROLES = ['trigger', 'agent', 'tool', 'workflow'] as const;
export type StepRole = (typeof STEP_ROLES)[number];

/** Where the setup screen keeps what the owner decides. */
export const STATE_PATHS = {
  rules: '/rules',
  askFirst: '/rules/askFirst',
  choices: '/choices',
  inputs: '/inputs',
} as const;

export const ASK_FIRST_LABEL = 'Ask me before sending anything';

export const LIMITS = {
  maxElements: 16,
  maxReplyChars: 65536,
  maxLine: 120,
  maxText: 400,
  maxBody: 2000,
  maxSteps: 6,
  maxItems: 8,
  maxOptions: 4,
  maxApps: 6,
  maxInput: 200,
  maxIdLength: 40,
} as const;

// ----- prop schemas (resolved values) -----

function clampLine(value: string, max: number): string {
  const flat = value.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max - 1).trimEnd()}…` : flat;
}

function clampBlock(value: string, max: number): string {
  const trimmed = value.trim();
  return trimmed.length > max ? `${trimmed.slice(0, max - 1).trimEnd()}…` : trimmed;
}

const scalar = z.union([z.string(), z.number(), z.boolean()]).transform(String);

/** A one-line string, whitespace collapsed and clamped; '' when unusable. */
const line = (max: number = LIMITS.maxLine) => scalar.catch('').transform((s) => clampLine(s, max));
/** A string that keeps its line breaks (message bodies). */
const block = (max: number) => scalar.catch('').transform((s) => clampBlock(s, max));
const optionalLine = (max: number = LIMITS.maxLine) => line(max).optional().catch(undefined);

const tone = z.enum(TONES).optional().catch(undefined);

function listOf<T extends z.ZodType>(item: T, max: number) {
  return z
    .array(z.unknown())
    .catch([])
    .transform((items) =>
      items
        .map((raw) => item.safeParse(raw))
        .filter((result) => result.success)
        .map((result) => result.data as z.output<T>)
        .slice(0, max),
    );
}

const planStep = z.object({
  title: line(60),
  detail: optionalLine(80),
  role: z.enum(STEP_ROLES).catch('agent'),
  app: optionalLine(40),
});

const listItem = z.object({
  title: line(),
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
  Card: z.object({ title: optionalLine(), subtitle: optionalLine(LIMITS.maxText), tone }),
  Heading: z.object({ text: line() }),
  Text: z.object({ text: block(LIMITS.maxText), muted: z.boolean().catch(false) }),
  Metric: z.object({ label: line(60), value: line(40), hint: optionalLine(), tone }),
  Badge: z.object({ label: line(40), tone }),
  Plan: z.object({ title: optionalLine(60), steps: listOf(planStep, LIMITS.maxSteps) }),
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
    subject: optionalLine(),
    body: block(LIMITS.maxBody),
  }),
  Progress: z.object({
    label: line(60),
    value: z.coerce.number().catch(0).transform((v) => Math.max(0, Math.min(100, Number.isFinite(v) ? v : 0))),
    tone,
  }),
  Toggle: z.object({ label: line(), description: optionalLine(LIMITS.maxText), value: z.boolean().catch(false) }),
  Choice: z.object({
    label: line(),
    options: listOf(line(40), LIMITS.maxOptions).transform((options) => [...new Set(options.filter(Boolean))]),
    value: optionalLine(40),
  }),
  Input: z.object({
    label: line(),
    placeholder: optionalLine(),
    value: scalar.catch('').transform((s) => s.slice(0, LIMITS.maxInput)),
  }),
  Button: z.object({
    label: line(40),
    variant: z.enum(['primary', 'secondary']).catch('secondary'),
    action: z.string().catch(''),
  }),
  Divider: z.object({}),
} satisfies Record<ComponentType, z.ZodType>;

export type PropsOf<T extends ComponentType> = z.output<(typeof PROP_SCHEMAS)[T]>;

export function isComponentType(value: unknown): value is ComponentType {
  return typeof value === 'string' && (COMPONENT_TYPES as readonly string[]).includes(value);
}

export function isContainer(type: ComponentType): boolean {
  return (CONTAINER_TYPES as readonly string[]).includes(type);
}

export function isControl(type: ComponentType): boolean {
  return (CONTROL_TYPES as readonly string[]).includes(type);
}

/** The action a Button runs, with old names mapped; null when unknown. */
export function actionOf(value: unknown): ActionType | null {
  if (typeof value !== 'string') return null;
  const name = ACTION_ALIASES[value] ?? value;
  return (ACTION_TYPES as readonly string[]).includes(name) ? (name as ActionType) : null;
}
