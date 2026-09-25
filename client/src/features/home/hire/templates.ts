/**
 * Starter jobs (design handoff "Template chips" and Settings > Plugins).
 * One list, in starters.json, serves both: the chips under the composer
 * send a starter's job, and Plugins installs a starter (its skills into the
 * library, then its job to the setup model).
 *
 * Each starter names the apps its job needs; the job text names them too,
 * since it is all the setup model reads. The skills are built-ins from the
 * employee folder. server/tests/test_home_catalog_contract.py checks both.
 */

import { z } from 'zod';
import { COLOR_ROLES } from '../data/schemas';
import starters from './starters.json';

const starterSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  role: z.enum(COLOR_ROLES),
  summary: z.string().min(1),
  job: z.string().min(1),
  apps: z.array(z.string().min(1)),
  skills: z.array(z.string().min(1)).min(1),
});

export type Starter = z.infer<typeof starterSchema>;

export const STARTERS: readonly Starter[] = z.array(starterSchema).parse(starters);

/** The composer's chips read the same list. */
export type HireTemplate = Starter;
export const HIRE_TEMPLATES = STARTERS;
