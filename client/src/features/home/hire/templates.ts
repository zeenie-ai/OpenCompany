/**
 * Starter jobs (design handoff "Template chips" and Settings > Plugins).
 * One list, in starters.json, serves both: a chip puts a starter's job in
 * the composer, and "Hire now" opens its reporting lines for review.
 *
 * Each starter names the apps its job needs; the job text names them too,
 * since it is all the setup model reads. The skills are built-ins from the
 * employee folder. `hire` is the starter's own setup, the one "Hire now"
 * sends (genui/hirePayload.ts starterHirePayload): a few names to pick an
 * unused one from, the role, a one-line description, the routine, what
 * starts the work and the app they answer through. It always asks first.
 * server/tests/test_home_catalog_contract.py checks all of it, and that
 * each starter builds that way without losing an app.
 */

import { z } from 'zod';
import { COLOR_ROLES } from '../data/schemas';
import starters from './starters.json';

const starterHireSchema = z.object({
  names: z.array(z.string().min(1)).min(1),
  role: z.string().min(1),
  team: z.array(z.object({
    key: z.string(), name: z.string(), responsibility: z.string(),
    kind: z.enum(['employee', 'specialist']), reports_to: z.string(),
  })).min(1).max(2),
  description: z.string().min(1),
  steps: z
    .array(z.object({ title: z.string().min(1), detail: z.string().optional(), role: z.string(), app: z.string().optional() }))
    .min(1),
  /** Snapped like the setup screen's before it is sent. */
  trigger: z.record(z.string(), z.unknown()),
  sends_via: z.string().optional(),
});

const starterSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  role: z.enum(COLOR_ROLES),
  summary: z.string().min(1),
  job: z.string().min(1),
  apps: z.array(z.string().min(1)),
  skills: z.array(z.string().min(1)).min(1),
  hire: starterHireSchema,
});

export type Starter = z.infer<typeof starterSchema>;

export const STARTERS: readonly Starter[] = z.array(starterSchema).parse(starters);

/** The composer's chips read the same list. */
export type HireTemplate = Starter;
export const HIRE_TEMPLATES = STARTERS;
