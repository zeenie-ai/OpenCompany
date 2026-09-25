/**
 * The shared corpus of model replies (genui/__fixtures__/replies.json):
 * each one parses and normalizes to a renderable screen exactly when it is
 * marked salvageable. The server's salvage check runs the same corpus
 * (server/tests/services/employees/test_setup_salvage_parity.py), so the
 * two sides agree on which replies are worth retrying.
 */

import { describe, expect, it } from 'vitest';
import corpus from '../__fixtures__/replies.json';
import { STATE_PATHS, isContainer } from '../catalog';
import { bindingPath, getPath } from '../expressions';
import { normalizeSpec, type NormalizedSpec } from '../normalize';
import { parseReply } from '../parse';

interface Case {
  name: string;
  reply: string;
  salvageable: boolean;
  expect?: Record<string, unknown>;
}

const cases = corpus as unknown as Case[];

function read(reply: string): { text: string; spec: NormalizedSpec | null } {
  const parsed = parseReply(reply);
  return { text: parsed.text, spec: parsed.failed ? null : normalizeSpec(parsed.spec) };
}

function elements(spec: NormalizedSpec) {
  return Object.entries(spec.elements);
}

function buttons(spec: NormalizedSpec, action: string) {
  return elements(spec).filter(([, element]) => element.type === 'Button' && element.props.action === action);
}

function assertTree(spec: NormalizedSpec) {
  const root = spec.elements[spec.root];
  expect(root.type).toBe('Stack');
  expect(root.props.direction).not.toBe('horizontal');
  const parents = new Map<string, string>();
  for (const [id, element] of elements(spec)) {
    if (!isContainer(element.type)) expect(element.children).toEqual([]);
    for (const child of element.children) {
      expect(spec.elements[child], `${id} lists missing ${child}`).toBeDefined();
      expect(parents.has(child), `${child} has two parents`).toBe(false);
      parents.set(child, id);
    }
  }
  // Every element is reached exactly once: no cycles, no orphans.
  expect(new Set(spec.order).size).toBe(spec.order.length);
  expect(spec.order.length).toBe(Object.keys(spec.elements).length);
  expect(spec.order[0]).toBe(spec.root);
}

describe('model reply corpus', () => {
  it.each(cases.map((c) => [c.name, c] as const))('%s', (_name, entry) => {
    const { text, spec } = read(entry.reply);
    expect(Boolean(spec)).toBe(entry.salvageable);
    const want = entry.expect ?? {};
    if ('text' in want) expect(text).toBe(want.text);
    if (!spec) return;

    assertTree(spec);
    // Every screen can be hired, changed, and asks first by default.
    expect(buttons(spec, 'hire_employee')).toHaveLength(1);
    expect(buttons(spec, 'refine')).toHaveLength(1);
    const askFirst = elements(spec).filter(
      ([, element]) => element.type === 'Toggle' && bindingPath(element.props.value) === STATE_PATHS.askFirst,
    );
    expect(askFirst).toHaveLength(1);
    expect(typeof getPath(spec.state, STATE_PATHS.askFirst)).toBe('boolean');
    expect(Object.keys(spec.elements).length).toBeLessThanOrEqual(16);

    if ('hasAgent' in want) {
      const agent = elements(spec).find(([, element]) => element.type === 'AgentCard');
      expect(agent?.[1].props.name).toBe(want.hasAgent);
    }
    if ('hireLabel' in want) expect(buttons(spec, 'hire_employee')[0][1].props.label).toBe(want.hireLabel);
    if ('refineHasNoParams' in want) expect(buttons(spec, 'refine')[0][1].props.actionParams).toBeUndefined();
    if ('askFirstValue' in want) expect(getPath(spec.state, STATE_PATHS.askFirst)).toBe(want.askFirstValue);
    if ('togglesInRulesCard' in want) {
      const card = elements(spec).find(([, element]) => element.type === 'Card' && /rule/i.test(String(element.props.title)));
      const toggles = card?.[1].children.filter((child) => spec.elements[child].type === 'Toggle') ?? [];
      expect(toggles).toHaveLength(want.togglesInRulesCard as number);
    }
    if ('metricValue' in want) {
      const metric = elements(spec).find(([, element]) => element.type === 'Metric');
      expect(metric?.[1].props.value ?? '').toBe(want.metricValue);
    }
    if ('noPollution' in want) {
      expect(({} as Record<string, unknown>).polluted).toBeUndefined();
      expect(Object.getPrototypeOf(spec.elements)).toBeNull();
      expect(Object.keys(spec.elements)).not.toContain('__proto__');
      for (const [, element] of elements(spec)) expect(Object.keys(element.props)).not.toContain('constructor');
      expect(Object.keys(spec.state)).not.toContain('__proto__');
    }
  });

  it('keeps the first of two parents and breaks the cycle', () => {
    const { spec } = read(cases.find((c) => c.name === 'cycle between containers')!.reply);
    expect(spec).not.toBeNull();
    expect(spec!.elements.b.children).toEqual(['c']);
    expect(spec!.elements.c.children).toEqual(['d']);
  });

  it('files unlisted controls into the rules card and buttons into one row', () => {
    const { spec } = read(cases.find((c) => c.name === 'nothing lists its children')!.reply);
    const row = elements(spec!).find(
      ([, element]) => element.type === 'Stack' && element.props.direction === 'horizontal',
    );
    expect(row?.[1].children.map((child) => spec!.elements[child].props.action)).toEqual(['hire_employee', 'refine']);
  });

  it('keeps the model order when capping a long screen', () => {
    const { spec } = read(cases.find((c) => c.name === 'thirty elements')!.reply);
    const texts = spec!.order.filter((id) => spec!.elements[id].type === 'Text');
    expect(texts[0]).toBe('t0');
    expect(texts).toEqual([...texts].sort((a, b) => Number(a.slice(1)) - Number(b.slice(1))));
  });

  it('reads a reply cut at 64K characters without hanging', () => {
    const huge = `{"text":"x","spec":{"root":"r","elements":{"r":{"type":"Text","props":{"text":"${'a'.repeat(70_000)}"}}}}}`;
    const started = performance.now();
    parseReply(huge);
    expect(performance.now() - started).toBeLessThan(2000);
  });
});
