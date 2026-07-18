/**
 * Tests for shouldShowParameter (extracted from MiddleSection.tsx for testability).
 *
 * The original inline `shouldShowParameter` function lived inside MiddleSection.tsx
 * and could not be tested directly without mounting the entire panel (which OOMs
 * jsdom because of the deep transitive dependency tree -- antd, prismjs,
 * MasterSkillEditor, reactflow types, etc.).
 *
 * The function is pure -- given a parameter definition and the current values of
 * all parameters, it returns whether the parameter should render.  Locks in
 * invariant 8 from docs-internal/node_panels.md.
 */

import { describe, it, expect } from 'vitest';
import { shouldShowParameter } from '../../../utils/parameterVisibility';
import type { INodeProperties } from '../../../types/INodeProperties';


function param(overrides: Partial<INodeProperties> = {}): INodeProperties {
  return {
    name: 'p',
    displayName: 'P',
    type: 'string',
    default: '',
    ...overrides,
  } as INodeProperties;
}


describe('shouldShowParameter -- always-visible cases', () => {
  it('returns true when displayOptions is undefined', () => {
    expect(shouldShowParameter(param(), {})).toBe(true);
  });

  it('returns true when displayOptions.show is missing', () => {
    expect(shouldShowParameter(param({ displayOptions: {} }), {})).toBe(true);
  });

  it('returns true when displayOptions.show is empty object', () => {
    expect(
      shouldShowParameter(param({ displayOptions: { show: {} } }), {}),
    ).toBe(true);
  });
});


describe('shouldShowParameter -- array (allowed values)', () => {
  const p = param({
    name: 'textBody',
    displayOptions: { show: { messageType: ['text'] } },
  });

  it('hides parameter when current value is not in allowed array', () => {
    expect(shouldShowParameter(p, { messageType: 'image' })).toBe(false);
  });

  it('shows parameter when current value is in allowed array', () => {
    expect(shouldShowParameter(p, { messageType: 'text' })).toBe(true);
  });

  it('shows parameter when current value matches one of multiple allowed values', () => {
    const p2 = param({
      displayOptions: { show: { operation: ['create', 'update'] } },
    });
    expect(shouldShowParameter(p2, { operation: 'update' })).toBe(true);
    expect(shouldShowParameter(p2, { operation: 'delete' })).toBe(false);
  });

  it('hides parameter when current value is undefined', () => {
    expect(shouldShowParameter(p, {})).toBe(false);
  });
});


describe('shouldShowParameter -- scalar (single allowed value)', () => {
  const p = param({
    displayOptions: { show: { mode: 'advanced' } } as any,
  });

  it('shows parameter when scalar matches', () => {
    expect(shouldShowParameter(p, { mode: 'advanced' })).toBe(true);
  });

  it('hides parameter when scalar does not match', () => {
    expect(shouldShowParameter(p, { mode: 'simple' })).toBe(false);
  });

  it('hides parameter when key is undefined', () => {
    expect(shouldShowParameter(p, {})).toBe(false);
  });

  it('treats null and undefined as distinct values from empty string', () => {
    const p2 = param({ displayOptions: { show: { x: '' } } as any });
    expect(shouldShowParameter(p2, { x: '' })).toBe(true);
    expect(shouldShowParameter(p2, { x: null })).toBe(false);
    expect(shouldShowParameter(p2, {})).toBe(false);
  });
});


describe('shouldShowParameter -- multiple AND-conditions', () => {
  const p = param({
    displayOptions: {
      show: { provider: ['openai'], useProxy: [true] },
    },
  });

  it('hides when one condition fails', () => {
    expect(
      shouldShowParameter(p, { provider: 'openai', useProxy: false }),
    ).toBe(false);
  });

  it('hides when multiple conditions fail', () => {
    expect(
      shouldShowParameter(p, { provider: 'anthropic', useProxy: false }),
    ).toBe(false);
  });

  it('shows when ALL conditions hold', () => {
    expect(
      shouldShowParameter(p, { provider: 'openai', useProxy: true }),
    ).toBe(true);
  });
});


describe('shouldShowParameter -- type coercion edges', () => {
  it('boolean true matches true', () => {
    const p = param({ displayOptions: { show: { flag: [true] } } });
    expect(shouldShowParameter(p, { flag: true })).toBe(true);
    expect(shouldShowParameter(p, { flag: false })).toBe(false);
    // String "true" must not loosely-match boolean true
    expect(shouldShowParameter(p, { flag: 'true' })).toBe(false);
  });

  it('number 0 matches 0', () => {
    const p = param({ displayOptions: { show: { count: [0] } } });
    expect(shouldShowParameter(p, { count: 0 })).toBe(true);
    expect(shouldShowParameter(p, { count: '0' })).toBe(false);
  });

  it('handles mixed-type allowed-values array', () => {
    const p = param({ displayOptions: { show: { x: [true, 'yes', 1] } } });
    expect(shouldShowParameter(p, { x: true })).toBe(true);
    expect(shouldShowParameter(p, { x: 'yes' })).toBe(true);
    expect(shouldShowParameter(p, { x: 1 })).toBe(true);
    expect(shouldShowParameter(p, { x: false })).toBe(false);
  });
});
