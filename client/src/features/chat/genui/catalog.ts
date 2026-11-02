/**
 * What an employee may show inside a chat reply (design handoff chat,
 * "Generated UI"): how each component's props read once resolved. The
 * vocabulary itself is server/config/chat_genui_catalog.json, which the
 * server checks every spec against: the client imports its limits
 * (prepare.ts) and what each layout holds (registry.ts), and a test holds
 * the schemas below to its components and props.
 *
 * Prop schemas are forgiving by design: an odd value degrades that one prop
 * (a default, a clamped string) instead of failing the element.
 */

import { z } from 'zod';
import { line, listOf, optionalLine, scalar } from '@/lib/jsonRender/catalog';

const flag = z.boolean().optional().catch(undefined);
const gap = z.enum(['sm', 'md']).catch('md');

const slotOption = z.object({
  id: line(40),
  time: line(40),
  detail: optionalLine(60),
  recommended: flag,
  unavailable: flag,
  note: optionalLine(80),
});

const stat = z.object({
  label: line(40),
  value: line(40),
  delta: optionalLine(40),
  tone: z.enum(['up', 'down']).optional().catch(undefined),
});

const bar = z.object({
  label: line(40),
  value: z.coerce
    .number()
    .catch(0)
    .transform((value) => (Number.isFinite(value) && value > 0 ? value : 0)),
});

export const CHAT_PROP_SCHEMAS = {
  Stack: z.object({ gap }),
  Row: z.object({ gap }),
  Card: z.object({ title: line(80), description: optionalLine(240) }),
  SlotPicker: z.object({
    label: line(80),
    hint: optionalLine(120),
    value: optionalLine(40),
    options: listOf(slotOption, 8).transform((options) => options.filter((option) => option.id && option.time)),
  }),
  Select: z.object({
    label: line(80),
    value: optionalLine(40),
    options: listOf(line(40), 6).transform((options) => [...new Set(options.filter(Boolean))]),
  }),
  Toggle: z.object({ label: line(80), hint: optionalLine(120), checked: z.boolean().catch(false) }),
  TextField: z.object({
    label: line(80),
    placeholder: optionalLine(80),
    value: scalar.catch('').transform((s) => s.slice(0, 400)),
  }),
  Text: z.object({ text: scalar.catch('').transform((s) => s.trim().slice(0, 600)) }),
  StatGrid: z.object({ items: listOf(stat, 6).transform((items) => items.filter((item) => item.label)) }),
  BarChart: z.object({ title: line(80), bars: listOf(bar, 12).transform((bars) => bars.filter((item) => item.label)) }),
  Callout: z.object({ tone: z.enum(['info', 'warning', 'danger', 'success']).catch('info'), text: line(400) }),
  Button: z.object({ label: line(40), variant: z.enum(['primary', 'secondary']).catch('secondary') }),
};

export type ChatComponentType = keyof typeof CHAT_PROP_SCHEMAS;

export type ChatPropsOf<T extends ChatComponentType> = z.output<(typeof CHAT_PROP_SCHEMAS)[T]>;

export function isChatComponentType(value: unknown): value is ChatComponentType {
  return typeof value === 'string' && Object.hasOwn(CHAT_PROP_SCHEMAS, value);
}
