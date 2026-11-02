/**
 * The setup screen's prop schemas (genui/catalog.ts) against the server's
 * manifest (server/config/genui_catalog.json), which the model's prompt is
 * written from: a schema for each of its components, in its order, reading
 * the props the prompt offers, so nothing the model is told to write is
 * dropped on the screen.
 */

import { describe, expect, it } from 'vitest';
import manifest from '../../../../../../server/config/genui_catalog.json';
import { COLOR_ROLES } from '../../data/schemas';
import { ACTION_TYPES, CONTAINER_TYPES, CONTROL_TYPES, PROP_SCHEMAS, isComponentType } from '../catalog';

/** The top-level prop names in a manifest props string such as
 *  `{label, options:[string], value:{"$bindState":"/choices/<key>"}}`. */
function propNames(text: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = '';
  for (const ch of text.trim().slice(1, -1)) {
    if ('{['.includes(ch)) depth += 1;
    if ('}]'.includes(ch)) depth -= 1;
    if (ch === ',' && depth === 0) {
      parts.push(current);
      current = '';
    } else {
      current += ch;
    }
  }
  parts.push(current);
  return parts.map((part) => /^\s*"?(\w+)/.exec(part)?.[1] ?? '').filter(Boolean);
}

/** Props the renderer reads that the model never writes. */
const RENDERER_ONLY: Record<string, string[]> = { Plan: ['trigger'] };

describe('the setup screen catalogue', () => {
  it('has a schema for each of the manifest’s components, in its order', () => {
    expect(Object.keys(PROP_SCHEMAS)).toEqual(Object.keys(manifest.components));
  });

  it('reads the props the prompt offers', () => {
    for (const [type, component] of Object.entries(manifest.components)) {
      const offered = [...propNames(component.props), ...(RENDERER_ONLY[type] ?? [])].sort();
      expect(Object.keys(PROP_SCHEMAS[type as keyof typeof PROP_SCHEMAS].shape).sort(), type).toEqual(offered);
    }
  });

  it('knows every container, control and aliased action the manifest names', () => {
    for (const type of [...CONTAINER_TYPES, ...CONTROL_TYPES]) expect(isComponentType(type), type).toBe(true);
    for (const action of Object.values(manifest.action_aliases)) expect(ACTION_TYPES).toContain(action);
  });

  it('draws every step role in the routine’s colours', () => {
    for (const role of manifest.step_roles) expect(COLOR_ROLES).toContain(role);
  });
});
