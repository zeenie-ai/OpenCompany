/**
 * Contract sweep over the NodeSpec → INodeTypeDescription adapter.
 *
 * Replaces the pre-Wave 11 `nodeDefinitions/__tests__/contract.test.ts`
 * which validated handwritten registry entries. After Wave 10's
 * schema-driven migration the adapter is the only seam that produces
 * an `INodeTypeDescription` for downstream consumers, so the contract
 * sweep moved here. A regression in `nodeSpecToDescription` (missing
 * required field, wrong handle shape, broken JSON Schema lift) now
 * fails loudly before any consumer sees the bad output.
 */

import { describe, expect, it } from 'vitest';
import { nodeSpecToDescription, type NodeSpec } from '../nodeSpecToDescription';
import {
  formatIssues,
  validateNodeDefinition,
} from '../../test/nodeDefinitionContract';

const expectValid = (spec: NodeSpec): void => {
  const def = nodeSpecToDescription(spec);
  const issues = validateNodeDefinition(spec.type, def);
  expect(issues, formatIssues(issues)).toEqual([]);
};

describe('nodeSpecToDescription contract', () => {
  it('produces a contract-valid description for a minimal spec', () => {
    expectValid({
      type: 'minimalNode',
      displayName: 'Minimal Node',
      icon: '🟢',
      group: ['utility'],
      version: 1,
    });
  });

  it('produces a contract-valid description with full optional fields', () => {
    expectValid({
      type: 'fullNode',
      displayName: 'Full Node',
      icon: '🔧',
      group: ['utility', 'tool'],
      version: 2,
      subtitle: 'A node with everything',
      description: 'Covers every adapter codepath.',
      credentials: ['openai', 'anthropic'],
      uiHints: { hasSkills: true },
      color: '#bd93f9',
      componentKind: 'tool',
      hideOutputHandle: false,
    });
  });

  it('lifts JSON Schema input properties into INodeProperties[]', () => {
    const spec: NodeSpec = {
      type: 'paramNode',
      displayName: 'Param Node',
      icon: '⚙️',
      group: ['utility'],
      version: 1,
      inputs: {
        properties: {
          mode: { type: 'string', enum: ['fast', 'slow'], default: 'fast' },
          count: { type: 'integer', minimum: 1, maximum: 10 },
          enabled: { type: 'boolean', default: true },
          notes: { type: 'string' },
        },
        required: ['mode', 'count'],
      },
    };
    expectValid(spec);

    const def = nodeSpecToDescription(spec);
    expect(def.properties).toHaveLength(4);

    const mode = def.properties!.find((p) => p.name === 'mode')!;
    expect(mode.type).toBe('options');
    expect(mode.required).toBe(true);
    expect(mode.options).toEqual([
      { name: 'fast', value: 'fast' },
      { name: 'slow', value: 'slow' },
    ]);

    const count = def.properties!.find((p) => p.name === 'count')!;
    expect(count.type).toBe('number');
    expect(count.required).toBe(true);
    expect(count.typeOptions).toEqual({ minValue: 1, maxValue: 10 });

    const enabled = def.properties!.find((p) => p.name === 'enabled')!;
    expect(enabled.type).toBe('boolean');
    expect(enabled.default).toBe(true);
    expect(enabled.required).toBeUndefined();
  });

  it('takes the non-null branch of Pydantic Optional[T] anyOf', () => {
    const spec: NodeSpec = {
      type: 'optNode',
      displayName: 'Optional Node',
      icon: '🔵',
      group: ['utility'],
      version: 1,
      inputs: {
        properties: {
          maybe: { anyOf: [{ type: 'string' }, { type: 'null' }] },
        },
      },
    };
    expectValid(spec);
    const def = nodeSpecToDescription(spec);
    expect(def.properties![0].type).toBe('string');
  });

  it.each([
    ['code editor hint', { editor: 'code' }, 'code'],
    ['json editor hint', { editor: 'json' }, 'json'],
    ['file widget hint', { widget: 'file' }, 'file'],
    ['format binary', { format: 'binary' }, 'file'],
    ['format date-time', { format: 'date-time' }, 'dateTime'],
    ['format date', { format: 'date' }, 'dateTime'],
  ] as const)('maps editor/widget/format hints (%s)', (_label, hint, expected) => {
    const spec: NodeSpec = {
      type: 'hintNode',
      displayName: 'Hint Node',
      icon: '✨',
      group: ['utility'],
      version: 1,
      inputs: {
        properties: { field: { type: 'string', ...hint } },
      },
    };
    expectValid(spec);
    expect(nodeSpecToDescription(spec).properties![0].type).toBe(expected);
  });

  it('lifts uiHints-nested editor hint as an alternate location', () => {
    const spec: NodeSpec = {
      type: 'nestedHintNode',
      displayName: 'Nested Hint',
      icon: '🪄',
      group: ['utility'],
      version: 1,
      inputs: {
        properties: { script: { type: 'string', uiHints: { editor: 'code' } } },
      },
    };
    expectValid(spec);
    expect(nodeSpecToDescription(spec).properties![0].type).toBe('code');
  });

  it('lifts displayOptions, placeholder, validation, and typeOptions hints', () => {
    const spec: NodeSpec = {
      type: 'fancyNode',
      displayName: 'Fancy Node',
      icon: '✨',
      group: ['utility'],
      version: 1,
      inputs: {
        properties: {
          host: {
            type: 'string',
            placeholder: 'api.example.com',
            displayOptions: { show: { mode: ['custom'] } },
            validation: [{ type: 'regex', properties: { regex: '^https?://' } }],
            loadOptionsMethod: 'getHosts',
            loadOptionsDependsOn: ['mode'],
            password: false,
            rows: 1,
            noDataExpression: true,
          },
        },
      },
    };
    expectValid(spec);

    const host = nodeSpecToDescription(spec).properties![0];
    expect(host.placeholder).toBe('api.example.com');
    expect(host.displayOptions).toEqual({ show: { mode: ['custom'] } });
    expect(host.validation).toEqual([
      { type: 'regex', properties: { regex: '^https?://' } },
    ]);
    expect(host.noDataExpression).toBe(true);
    expect(host.typeOptions).toMatchObject({
      loadOptionsMethod: 'getHosts',
      loadOptionsDependsOn: ['mode'],
      password: false,
      rows: 1,
    });
  });

  it('emits credentials in the required {name} object form', () => {
    const def = nodeSpecToDescription({
      type: 'credNode',
      displayName: 'Cred Node',
      icon: '🔑',
      group: ['integration'],
      version: 1,
      credentials: ['openai', 'gemini'],
    });
    expect(def.credentials).toEqual([{ name: 'openai' }, { name: 'gemini' }]);
  });

  it('uses uiHints.options (rich labels) over enum-derived bare options', () => {
    const spec: NodeSpec = {
      type: 'richOptionsNode',
      displayName: 'Rich Options',
      icon: '🎨',
      group: ['utility'],
      version: 1,
      inputs: {
        properties: {
          provider: {
            type: 'string',
            enum: ['openai', 'anthropic'],
            uiHints: {
              options: [
                { name: 'OpenAI GPT-4', value: 'openai' },
                { name: 'Anthropic Claude', value: 'anthropic' },
              ],
            },
          },
        },
      },
    };
    expectValid(spec);
    const provider = nodeSpecToDescription(spec).properties![0];
    expect(provider.options).toEqual([
      { name: 'OpenAI GPT-4', value: 'openai' },
      { name: 'Anthropic Claude', value: 'anthropic' },
    ]);
  });

  it('always emits a single main input and output handle', () => {
    const def = nodeSpecToDescription({
      type: 'handleNode',
      displayName: 'Handle Node',
      icon: '🔌',
      group: ['utility'],
      version: 1,
    });
    expect(def.inputs).toEqual(['main']);
    expect(def.outputs).toEqual(['main']);
  });

  it('preserves uiHints object as-is on the description', () => {
    const def = nodeSpecToDescription({
      type: 'hintsNode',
      displayName: 'Hints Node',
      icon: '💡',
      group: ['utility'],
      version: 1,
      uiHints: { isMasterSkillEditor: true, hasCodeEditor: false },
    });
    expect(def.uiHints).toEqual({ isMasterSkillEditor: true, hasCodeEditor: false });
  });

  // ---- json_schema_extra group convention ---------------------------------

  it('lifts group="X" fields into a synthetic type="collection" parent', () => {
    const def = nodeSpecToDescription({
      type: 'groupedNode',
      displayName: 'Grouped Node',
      icon: '🗂️',
      group: ['utility'],
      version: 1,
      inputs: {
        properties: {
          prompt: { type: 'string', title: 'Prompt' },
          temperature: { type: 'number', default: 0.7, group: 'options' },
          max_tokens: { type: 'integer', default: 1000, group: 'options' },
        },
      },
    });
    // prompt stays top-level; temperature + max_tokens absorbed into Options.
    expect(def.properties).toHaveLength(2);
    expect(def.properties[0].name).toBe('prompt');
    const options = def.properties[1];
    expect(options.name).toBe('options');
    expect(options.type).toBe('collection');
    expect(options.displayName).toBe('Options');  // title-cased default
    expect(options.placeholder).toBe('Add Option');
    expect(options.default).toEqual({});
    expect(options.options?.map(o => o.name)).toEqual(['temperature', 'max_tokens']);
    // Children don't carry the private marker.
    expect((options.options?.[0] as any).__group).toBeUndefined();
  });

  it('honours class-level groups metadata overrides', () => {
    const def = nodeSpecToDescription({
      type: 'customGroupsNode',
      displayName: 'Custom Groups',
      icon: '🗄️',
      group: ['utility'],
      version: 1,
      inputs: {
        properties: {
          name: { type: 'string' },
          x: { type: 'string', group: 'advanced' },
          y: { type: 'string', group: 'advanced' },
        },
        groups: {
          advanced: { display_name: 'Advanced', placeholder: 'Add Advanced' },
        },
      },
    });
    const advanced = def.properties.find(p => p.name === 'advanced')!;
    expect(advanced).toBeDefined();
    expect(advanced.displayName).toBe('Advanced');
    expect(advanced.placeholder).toBe('Add Advanced');
    expect(advanced.options?.map(o => o.name)).toEqual(['x', 'y']);
  });

  it('places the collection at the position of the first grouped field', () => {
    const def = nodeSpecToDescription({
      type: 'orderedNode',
      displayName: 'Ordered Node',
      icon: '📑',
      group: ['utility'],
      version: 1,
      inputs: {
        properties: {
          a: { type: 'string' },
          grouped1: { type: 'string', group: 'g' },
          b: { type: 'string' },
          grouped2: { type: 'string', group: 'g' },
          c: { type: 'string' },
        },
      },
    });
    // Expected order: a, g (takes grouped1's slot), b, c
    expect(def.properties.map(p => p.name)).toEqual(['a', 'g', 'b', 'c']);
    const g = def.properties.find(p => p.name === 'g')!;
    expect(g.options?.map(o => o.name)).toEqual(['grouped1', 'grouped2']);
  });

  it('leaves ungrouped schemas flat (no regression for legacy plugins)', () => {
    const def = nodeSpecToDescription({
      type: 'flatNode',
      displayName: 'Flat',
      icon: '▫️',
      group: ['utility'],
      version: 1,
      inputs: {
        properties: {
          a: { type: 'string' },
          b: { type: 'number' },
        },
      },
    });
    expect(def.properties.map(p => p.name)).toEqual(['a', 'b']);
    expect(def.properties.every(p => p.type !== 'collection')).toBe(true);
  });
});
