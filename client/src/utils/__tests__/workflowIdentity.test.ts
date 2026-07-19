import { describe, expect, it } from 'vitest';
import type { Node } from 'reactflow';

import { nextNodeInstanceId } from '../workflow';

const node = (id: string, type: string): Pick<Node, 'id' | 'type'> => ({ id, type });

describe('nextNodeInstanceId', () => {
  it('uses the workflow and fixed plugin type with a one-based ordinal', () => {
    expect(nextNodeInstanceId('7', 'aiAgent', [])).toBe('7:aiAgent:1');
  });

  it('allows multiple instances and never reuses a lower ordinal', () => {
    const nodes = [
      node('7:aiAgent:1', 'aiAgent'),
      node('7:aiAgent:4', 'aiAgent'),
      node('7:taskManager:1', 'taskManager'),
    ];
    expect(nextNodeInstanceId('7', 'aiAgent', nodes)).toBe('7:aiAgent:5');
    expect(nextNodeInstanceId('7', 'taskManager', nodes)).toBe('7:taskManager:2');
  });

  it('accounts for legacy instances while a workflow is being migrated', () => {
    const nodes = [node('aiAgent-old-a', 'aiAgent'), node('aiAgent-old-b', 'aiAgent')];
    expect(nextNodeInstanceId('9', 'aiAgent', nodes)).toBe('9:aiAgent:3');
  });
});
