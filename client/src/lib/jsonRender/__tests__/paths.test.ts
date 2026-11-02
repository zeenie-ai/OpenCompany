import { describe, expect, it } from 'vitest';
import { bindingPath, parseStatePath, readPath, writePath } from '../paths';

describe('state paths', () => {
  it('refuses prototype keys and odd input', () => {
    expect(parseStatePath('/rules/askFirst')).toEqual(['rules', 'askFirst']);
    expect(parseStatePath('/__proto__/x')).toBeNull();
    expect(parseStatePath('/a/constructor')).toBeNull();
    expect(parseStatePath('/')).toBeNull();
    expect(parseStatePath(42)).toBeNull();
  });

  it('reads own values only', () => {
    const state = { a: { b: [10, 20] } };
    expect(readPath(state, '/a/b/1')).toBe(20);
    expect(readPath(state, '/a/toString')).toBeUndefined();
    expect(readPath(state, '/missing/x')).toBeUndefined();
  });

  it('writes a copy along the path and keeps arrays as arrays', () => {
    const state = { list: ['x', 'y'], rules: { keep: true } };
    const next = writePath(state, '/list/1', 'z');
    expect(next.list).toEqual(['x', 'z']);
    expect(Array.isArray(next.list)).toBe(true);
    expect(state.list).toEqual(['x', 'y']);
    expect(next.rules).toBe(state.rules);
    expect(writePath(state, '/rules/askFirst', false)).toEqual({ list: ['x', 'y'], rules: { keep: true, askFirst: false } });
  });

  it('ignores writes through prototype keys', () => {
    const state = { a: 1 };
    expect(writePath(state, '/__proto__/polluted', true)).toBe(state);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it('reads the path a control writes to', () => {
    expect(bindingPath({ $bindState: '/choices/freq' })).toBe('/choices/freq');
    expect(bindingPath({ $bindState: '/__proto__' })).toBeNull();
    expect(bindingPath('literal')).toBeNull();
  });
});
