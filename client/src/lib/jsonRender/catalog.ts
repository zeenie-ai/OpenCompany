/**
 * Prop schemas for a generated UI's catalogue, shared by the chat's
 * (features/chat/genui/catalog.ts) and the setup screen's
 * (features/home/genui/catalog.ts). They read what a model wrote
 * forgivingly: an odd value degrades that one prop (a default, a clamped
 * string) instead of failing the element. The guard (guard.tsx) reads every
 * element's props through them before a view draws it.
 *
 * Imports zod only, so modules in Home's first chunk can use it.
 */

import { z } from 'zod';

function clampLine(value: string, max: number): string {
  const flat = value.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max - 1).trimEnd()}…` : flat;
}

function clampBlock(value: string, max: number): string {
  const trimmed = value.trim();
  return trimmed.length > max ? `${trimmed.slice(0, max - 1).trimEnd()}…` : trimmed;
}

/** A string, number or boolean, as text. */
export const scalar = z.union([z.string(), z.number(), z.boolean()]).transform(String);

/** A one-line string, whitespace collapsed and clamped; '' when unusable. */
export const line = (max: number) => scalar.catch('').transform((s) => clampLine(s, max));

/** A string that keeps its line breaks (message bodies). */
export const block = (max: number) => scalar.catch('').transform((s) => clampBlock(s, max));

export const optionalLine = (max: number) => line(max).optional().catch(undefined);

/** The items of a list that read, at most `max` of them; [] when not a list. */
export function listOf<T extends z.ZodType>(item: T, max: number) {
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

/**
 * A catalogue as json-render's defineCatalog takes it: every component with
 * its props schema, and the containers with their children (the default
 * slot). No actions are declared: the host passes its handlers when it
 * renders, and setState is json-render's own.
 */
export function catalogOf<S extends Record<string, z.ZodType>>(schemas: S, containers: readonly string[]) {
  const components = Object.fromEntries(
    Object.entries(schemas).map(([type, props]) => [type, containers.includes(type) ? { props, slots: ['default'] } : { props }]),
  ) as { [T in keyof S]: { props: S[T]; slots?: string[] } };
  return { components, actions: {} };
}
