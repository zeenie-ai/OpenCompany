import { describe, expect, it } from 'vitest';
import { bindingPath, evalCondition, getPath, pathKeys, resolveValue, sanitizeState, setPath } from '../expressions';

describe('paths', () => {
  it('refuses prototype keys and odd input', () => {
    expect(pathKeys('/rules/askFirst')).toEqual(['rules', 'askFirst']);
    expect(pathKeys('/__proto__/x')).toBeNull();
    expect(pathKeys('/a/constructor')).toBeNull();
    expect(pathKeys('/')).toBeNull();
    expect(pathKeys(42)).toBeNull();
  });

  it('reads own values only', () => {
    const state = { a: { b: [10, 20] } };
    expect(getPath(state, '/a/b/1')).toBe(20);
    expect(getPath(state, '/a/toString')).toBeUndefined();
    expect(getPath(state, '/missing/x')).toBeUndefined();
  });

  it('writes a copy along the path and keeps arrays as arrays', () => {
    const state = { list: ['x', 'y'], rules: { keep: true } };
    const next = setPath(state, '/list/1', 'z');
    expect(next.list).toEqual(['x', 'z']);
    expect(Array.isArray(next.list)).toBe(true);
    expect(state.list).toEqual(['x', 'y']);
    expect(next.rules).toBe(state.rules);
    expect(setPath(state, '/rules/askFirst', false)).toEqual({ list: ['x', 'y'], rules: { keep: true, askFirst: false } });
  });

  it('ignores writes through prototype keys', () => {
    const state = { a: 1 };
    expect(setPath(state, '/__proto__/polluted', true)).toBe(state);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it('reads the path a control writes to', () => {
    expect(bindingPath({ $bindState: '/choices/freq' })).toBe('/choices/freq');
    expect(bindingPath({ $bindState: '/__proto__' })).toBeNull();
    expect(bindingPath('literal')).toBeNull();
  });
});

describe('dynamic values', () => {
  const state = { freq: 'weekly', on: true, n: 3, rules: { askFirst: false } };

  it('resolves state, templates and conditionals', () => {
    expect(resolveValue({ $state: '/freq' }, state)).toBe('weekly');
    expect(resolveValue({ $bindState: '/rules/askFirst' }, state)).toBe(false);
    expect(resolveValue({ $template: 'Reports ${/freq}, ${/n} times, ${/nope}' }, state)).toBe('Reports weekly, 3 times, ');
    expect(resolveValue({ $cond: { $state: '/on' }, $then: 'yes', $else: 'no' }, state)).toBe('yes');
    expect(resolveValue({ label: { $state: '/freq' }, list: [{ $state: '/n' }] }, state)).toEqual({ label: 'weekly', list: [3] });
  });

  it('never fills a template with an object', () => {
    expect(resolveValue({ $template: 'Rules: ${/rules}' }, state)).toBe('Rules: ');
  });

  it('evaluates conditions', () => {
    expect(evalCondition({ $state: '/freq', eq: 'weekly' }, state)).toBe(true);
    expect(evalCondition({ $state: '/freq', neq: 'weekly' }, state)).toBe(false);
    expect(evalCondition({ $state: '/on', not: true }, state)).toBe(false);
    expect(evalCondition([{ $state: '/on' }, { $state: '/n', eq: 3 }], state)).toBe(true);
    expect(evalCondition(undefined, state)).toBe(true);
  });

  it('stops on runaway nesting', () => {
    let deep: unknown = 'leaf';
    for (let i = 0; i < 50; i++) deep = { $cond: true, $then: deep, $else: null };
    expect(() => resolveValue(deep, state)).not.toThrow();
  });
});

describe('sanitizeState', () => {
  it('keeps plain JSON, clamps strings and drops prototype keys', () => {
    const raw = JSON.parse('{"__proto__":{"x":1},"a":"abcdef","b":[1,{"c":true}],"d":{"e":null}}');
    expect(sanitizeState(raw, 3)).toEqual({ a: 'abc', b: [1, { c: true }], d: { e: null } });
  });
});
