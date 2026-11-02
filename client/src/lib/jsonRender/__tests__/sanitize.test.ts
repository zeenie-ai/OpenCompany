/**
 * What reaches json-render: prototype keys and paths refused everywhere,
 * the features a model must not reach (watch, repeat, slots, $computed,
 * confirm, onSuccess / onError) stripped, and sizes capped.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  DEFAULT_SANITIZE_LIMITS,
  sanitizeActionBinding,
  sanitizeCondition,
  sanitizeElement,
  sanitizeExpressions,
  sanitizeSpec,
  sanitizeState,
} from '../sanitize';

const FIXTURES = join(__dirname, '..', '..', '..', 'features', 'chat', '__fixtures__');

function fixture(name: string): unknown {
  return JSON.parse(readFileSync(join(FIXTURES, `${name}.spec.json`), 'utf-8'));
}

describe('prototype keys', () => {
  it('drops them as object keys, element ids and state keys', () => {
    const raw = JSON.parse(
      '{"root":"r","state":{"__proto__":{"polluted":true},"ok":1,"constructor":{"prototype":{"x":1}}},' +
        '"elements":{"__proto__":{"type":"Text","props":{"text":"evil"}},"constructor":{"type":"Text","props":{}},' +
        '"r":{"type":"Stack","props":{"__proto__":{"polluted":true},"gap":"md","nested":{"prototype":1,"keep":2}},"children":["__proto__","a"]},' +
        '"a":{"type":"Text","props":{"text":"hi"}}}}',
    );
    const spec = sanitizeSpec(raw)!;
    expect(Object.keys(spec.elements)).toEqual(['r', 'a']);
    expect(Object.getPrototypeOf(spec.elements)).toBeNull();
    expect(spec.elements.r).toEqual({ type: 'Stack', props: { gap: 'md', nested: { keep: 2 } }, children: ['a'] });
    expect(spec.state).toEqual({ ok: 1 });
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it('refuses a path through one in $state, $bindState, $template, visible and action params', () => {
    expect(sanitizeExpressions({ a: { $state: '/__proto__/x' }, b: { $bindState: '/x/constructor' }, c: { $state: '/ok' } })).toEqual({
      c: { $state: '/ok' },
    });
    expect(sanitizeExpressions({ $template: 'A ${/prototype/x} B ${ /name } C ${field}' })).toEqual({
      $template: 'A  B ${/name} C ${/field}',
    });
    expect(sanitizeCondition({ $state: '/a/__proto__', eq: 1 })).toBe(false);
    expect(sanitizeCondition({ $state: '/ok', eq: { $state: '/constructor' } })).toEqual({ $state: '/ok', eq: null });
    expect(sanitizeActionBinding({ action: 'go', params: { to: { $state: '/__proto__' }, n: 1 } })).toEqual({
      action: 'go',
      params: { n: 1 },
    });
    // Escaped segments are read unescaped, as json-render reads them.
    expect(sanitizeExpressions({ $state: '/a~1b/c' })).toEqual({ $state: '/a~1b/c' });
    expect(sanitizeExpressions({ $state: '/prototype~0' })).toEqual({ $state: '/prototype~0' });
  });

  it('refuses them as action and event names', () => {
    expect(sanitizeActionBinding({ action: 'constructor' })).toBeNull();
    // JSON.parse makes "__proto__" an own key, as a model's reply would.
    const on = JSON.parse('{"__proto__":{"action":"go"},"press":{"action":"go"}}');
    expect(sanitizeElement({ type: 'Button', props: {}, on })).toEqual({
      type: 'Button',
      props: {},
      on: { press: { action: 'go' } },
    });
  });
});

describe('features a model must not reach', () => {
  it('strips watch, repeat and slots from an element, and keeps type, props, children, visible and on', () => {
    const element = sanitizeElement({
      type: 'Card',
      props: { title: 'T' },
      children: ['a'],
      visible: { $state: '/show' },
      on: { press: { action: 'go' } },
      watch: { '/x': { action: 'go' } },
      repeat: { statePath: '/items' },
      slots: { footer: ['b'] },
      key: 'k',
    });
    expect(element).toEqual({
      type: 'Card',
      props: { title: 'T' },
      children: ['a'],
      visible: { $state: '/show' },
      on: { press: { action: 'go' } },
    });
  });

  it('drops $computed, and $item, $index and $bindItem, which only work inside repeat', () => {
    expect(
      sanitizeExpressions({
        a: { $computed: 'secret', args: { x: 1 } },
        b: { $item: 'name' },
        c: { $index: true },
        d: { $bindItem: 'done' },
        e: [{ $computed: 'f' }, 'kept'],
        f: { $cond: { $state: '/x' }, $then: { $computed: 'f' }, $else: 'no' },
      }),
    ).toEqual({ e: ['kept'], f: { $cond: { $state: '/x' }, $then: null, $else: 'no' } });
  });

  it('keeps an action binding to its action and params', () => {
    expect(
      sanitizeActionBinding({
        action: 'save',
        params: { id: 1 },
        confirm: { title: 'Sure?', message: 'Really?' },
        onSuccess: { set: { '/__proto__/x': 1 } },
        onError: { action: 'other' },
        preventDefault: true,
      }),
    ).toEqual({ action: 'save', params: { id: 1 } });
    expect(sanitizeActionBinding({ action: '' })).toBeNull();
    expect(sanitizeActionBinding({ action: 'has space' })).toBeNull();
  });
});

describe('where json-render writes state', () => {
  it('keeps a usable state path, written canonically', () => {
    expect(sanitizeActionBinding({ action: 'setState', params: { statePath: 'form//name', value: 'Ana' } })).toEqual({
      action: 'setState',
      params: { statePath: '/form/name', value: 'Ana' },
    });
  });

  it('drops a binding whose state path is refused or not written out', () => {
    expect(sanitizeActionBinding({ action: 'setState', params: { statePath: '/__proto__/x', value: 1 } })).toBeNull();
    expect(sanitizeActionBinding({ action: 'setState', params: { statePath: { $state: '/p' }, value: 1 } })).toBeNull();
    expect(sanitizeActionBinding({ action: 'pushState', params: { statePath: '/list', clearStatePath: '/constructor' } })).toBeNull();
  });
});

describe('conditions', () => {
  it('canonicalizes what json-render reads differently', () => {
    expect(sanitizeCondition(undefined)).toBeUndefined();
    expect(sanitizeCondition(null)).toBeUndefined();
    expect(sanitizeCondition(true)).toBe(true);
    expect(sanitizeCondition([{ $state: '/a' }, { $state: 'b', not: 1 }])).toEqual({
      $and: [{ $state: '/a' }, { $state: '/b', not: true }],
    });
    expect(sanitizeCondition({ $or: [false, { $state: '/c', gt: 2, lt: { $state: '/d' } }] })).toEqual({
      $or: [false, { $state: '/c', gt: 2, lt: { $state: '/d' } }],
    });
    // Only meaningful inside repeat, or no shape at all: hidden.
    expect(sanitizeCondition({ $item: 'done' })).toBe(false);
    expect(sanitizeCondition({ eq: 1 })).toBe(false);
    expect(sanitizeCondition({ $and: 'nope' })).toBe(false);
  });
});

describe('sanitizeSpec', () => {
  it('needs a usable root and an elements object', () => {
    expect(sanitizeSpec(null)).toBeNull();
    expect(sanitizeSpec({ root: 'a', elements: [] })).toBeNull();
    expect(sanitizeSpec({ root: '__proto__', elements: {} })).toBeNull();
    expect(sanitizeSpec({ root: 'r', elements: {} })).toEqual({ root: 'r', state: {}, elements: {} });
  });

  it('caps elements, children, strings, arrays and depth', () => {
    const elements: Record<string, Record<string, unknown>> = Object.fromEntries(
      Array.from({ length: 80 }, (_, i) => [`e${i}`, { type: 'Text', props: { text: 'x'.repeat(5000) }, children: [] }]),
    );
    elements.e0.children = Array.from({ length: 100 }, (_, i) => `c${i}`);
    let deep: unknown = 'leaf';
    for (let i = 0; i < 20; i++) deep = { next: deep };
    elements.e1.props = { list: Array.from({ length: 80 }, (_, i) => i), deep };
    const spec = sanitizeSpec({ root: 'e0', elements })!;
    expect(Object.keys(spec.elements)).toHaveLength(DEFAULT_SANITIZE_LIMITS.maxElements);
    expect(spec.elements.e0.children).toHaveLength(DEFAULT_SANITIZE_LIMITS.maxChildren);
    expect(String(spec.elements.e2.props.text)).toHaveLength(DEFAULT_SANITIZE_LIMITS.maxString);
    expect(spec.elements.e1.props.list).toHaveLength(DEFAULT_SANITIZE_LIMITS.maxArray);
    expect(JSON.stringify(spec.elements.e1.props.deep).match(/next/g)!.length).toBeLessThan(20);
  });

  it('keeps the chat fixtures as they are, adding only empty props', () => {
    for (const name of ['saturday-booking', 'reply-insights', 'reminders']) {
      const raw = fixture(name) as { elements: Record<string, Record<string, unknown>> };
      const spec = sanitizeSpec(raw)!;
      expect(Object.keys(spec.elements)).toEqual(Object.keys(raw.elements));
      for (const [id, element] of Object.entries(raw.elements)) {
        expect(spec.elements[id]).toEqual({ props: {}, ...element });
      }
    }
  });
});

describe('sanitizeState', () => {
  it('is plain JSON, always an object', () => {
    expect(sanitizeState([1, 2])).toEqual({});
    expect(sanitizeState({ n: Number.NaN, f: () => 1, s: 'x', ok: [true, null] })).toEqual({ s: 'x', ok: [true, null] });
  });
});
