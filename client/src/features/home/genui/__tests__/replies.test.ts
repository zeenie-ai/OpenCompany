/**
 * The shared corpus of model replies (genui/__fixtures__/replies.json):
 * each one parses and normalizes to a renderable screen exactly when it is
 * marked salvageable, and every such screen is in json-render's shape, can
 * be hired and changed, asks first by default, says when they work and is
 * laid out as the setup card (who they are, the routine, the rest, then the
 * footer strip).
 * Replies come in both shapes a model writes: the older one (a Toggle's
 * `value`, a Button's `action` / `actionParams` props) and json-render's
 * (`checked`, `on.press`). The server's salvage check runs the same corpus
 * (server/tests/services/employees/test_setup_salvage_parity.py), so the
 * two sides agree on which replies are worth retrying.
 */

import { describe, expect, it } from 'vitest';
import corpus from '../__fixtures__/replies.json';
import { ACTION_TYPES, STATE_PATHS, isContainer } from '../catalog';
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
const ELEMENT_KEYS = new Set(['type', 'props', 'children', 'visible', 'on']);

function read(reply: string): { text: string; spec: NormalizedSpec | null } {
  const parsed = parseReply(reply);
  return { text: parsed.text, spec: parsed.failed ? null : normalizeSpec(parsed.spec) };
}

function elements(spec: NormalizedSpec) {
  return Object.entries(spec.elements);
}

function buttons(spec: NormalizedSpec, action: string) {
  return elements(spec).filter(([, element]) => element.type === 'Button' && element.on?.press.action === action);
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

/** json-render's element shape, and nothing a model may not reach. */
function assertJsonRenderShape(spec: NormalizedSpec) {
  for (const [id, element] of elements(spec)) {
    for (const key of Object.keys(element)) expect(ELEMENT_KEYS.has(key), `${id} has ${key}`).toBe(true);
    if (element.type === 'Button') {
      expect(ACTION_TYPES).toContain(element.on?.press.action);
      expect(Object.keys(element.on!.press).every((key) => key === 'action' || key === 'params')).toBe(true);
      expect(element.props).not.toHaveProperty('action');
      expect(element.props).not.toHaveProperty('actionParams');
    } else {
      expect(element.on).toBeUndefined();
    }
    if (element.type === 'Toggle') expect(element.props).not.toHaveProperty('value');
  }
}

describe('model reply corpus', () => {
  it.each(cases.map((c) => [c.name, c] as const))('%s', (_name, entry) => {
    const { text, spec } = read(entry.reply);
    expect(Boolean(spec)).toBe(entry.salvageable);
    const want = entry.expect ?? {};
    if ('text' in want) expect(text).toBe(want.text);
    if (!spec) return;

    assertTree(spec);
    assertJsonRenderShape(spec);
    // Every screen can be hired, changed, and asks first by default.
    expect(buttons(spec, 'hire_employee')).toHaveLength(1);
    expect(buttons(spec, 'refine')).toHaveLength(1);
    const askFirst = elements(spec).filter(
      ([, element]) => element.type === 'Toggle' && bindingPath(element.props.checked) === STATE_PATHS.askFirst,
    );
    expect(askFirst).toHaveLength(1);
    expect(typeof getPath(spec.state, STATE_PATHS.askFirst)).toBe('boolean');
    // And says when they work: the routine's When row, else one Schedule.
    const plan = elements(spec).find(([, element]) => element.type === 'Plan');
    const schedule = elements(spec).filter(([, element]) => element.type === 'Schedule');
    if (plan) {
      expect(schedule).toHaveLength(0);
      expect(bindingPath(plan[1].props.trigger)).toBe(STATE_PATHS.trigger);
    } else {
      expect(schedule).toHaveLength(1);
      expect(bindingPath(schedule[0][1].props.value)).toBe(STATE_PATHS.trigger);
    }
    expect(getPath(spec.state, STATE_PATHS.trigger)).toMatchObject({ kind: expect.any(String) });
    expect(Object.keys(spec.elements).length).toBeLessThanOrEqual(16);

    // Laid out as the card: who they are, the routine, the rest, the footer.
    const { layout } = spec;
    expect(spec.elements[spec.root].children).toEqual([
      ...(layout.identity ? [layout.identity] : []),
      ...layout.body,
      layout.askFirst,
      layout.actions,
    ]);
    expect(['Plan', 'Schedule']).toContain(spec.elements[layout.body[0]].type);
    expect(layout.askFirst).toBe(askFirst[0][0]);
    const footer = spec.elements[layout.actions].children.map((id) => spec.elements[id].on?.press.action);
    expect(footer).toEqual(['refine', 'hire_employee']);

    if ('hasAgent' in want) {
      const agent = elements(spec).find(([, element]) => element.type === 'AgentCard');
      expect(agent?.[1].props.name).toBe(want.hasAgent);
    }
    if ('hireLabel' in want) expect(buttons(spec, 'hire_employee')[0][1].props.label).toBe(want.hireLabel);
    if ('refineHasNoParams' in want) expect(buttons(spec, 'refine')[0][1].on?.press.params).toBeUndefined();
    if ('askFirstValue' in want) expect(getPath(spec.state, STATE_PATHS.askFirst)).toBe(want.askFirstValue);
    if ('trigger' in want) expect(getPath(spec.state, STATE_PATHS.trigger)).toEqual(want.trigger);
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
    if ('noForbiddenPaths' in want) {
      expect(JSON.stringify({ state: spec.state, elements: spec.elements })).not.toMatch(/__proto__|constructor|prototype/);
    }
    if ('strippedExtras' in want) {
      expect(JSON.stringify(spec)).not.toMatch(/\$computed|\$item|confirm|onSuccess|onError|preventDefault|watch|repeat|slots/);
    }
    if ('sameAs' in want) {
      const other = read(cases.find((c) => c.name === want.sameAs)!.reply).spec;
      expect(spec).toEqual(other);
    }
  });

  it('keeps the first of two parents and breaks the cycle', () => {
    const { spec } = read(cases.find((c) => c.name === 'cycle between containers')!.reply);
    expect(spec).not.toBeNull();
    expect(spec!.elements.b.children).toEqual(['c']);
    expect(spec!.elements.c.children).toEqual(['d']);
  });

  it('files unlisted controls into the rules card and the buttons into the footer row', () => {
    const { spec } = read(cases.find((c) => c.name === 'nothing lists its children')!.reply);
    const rules = elements(spec!).find(([, element]) => element.type === 'Card');
    expect(rules?.[1].children).toEqual(['t1']);
    expect(spec!.elements[spec!.layout.actions].children).toEqual(['edit', 'hire']);
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
