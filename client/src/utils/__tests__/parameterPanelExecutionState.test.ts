import { describe, expect, it } from 'vitest';

import type { ExecutionResult } from '../../services/executionService';
import { nodeExecutionReducer, type NodeExecutionState } from '../parameterPanelExecutionState';

const result = (nodeId: string): ExecutionResult => ({
  success: true,
  nodeId,
  nodeType: 'test',
  nodeName: nodeId,
  timestamp: nodeId,
  executionTime: 1,
  nodeData: [[{ json: { nodeId } }]],
});

describe('nodeExecutionReducer', () => {
  it('keeps out-of-order parallel node completions independent', () => {
    let state: NodeExecutionState = {};
    state = nodeExecutionReducer(state, { type: 'start', nodeId: 'a' });
    state = nodeExecutionReducer(state, { type: 'start', nodeId: 'b' });
    state = nodeExecutionReducer(state, { type: 'append', nodeId: 'b', result: result('b') });
    state = nodeExecutionReducer(state, { type: 'finish', nodeId: 'b' });
    state = nodeExecutionReducer(state, { type: 'append', nodeId: 'a', result: result('a') });
    state = nodeExecutionReducer(state, { type: 'finish', nodeId: 'a' });

    expect(state.a).toMatchObject({ running: false });
    expect(state.b).toMatchObject({ running: false });
    expect(state.a.results.map((entry) => entry.nodeId)).toEqual(['a']);
    expect(state.b.results.map((entry) => entry.nodeId)).toEqual(['b']);
  });

  it('clears only the requested node', () => {
    const state: NodeExecutionState = {
      a: { running: true, results: [result('a')] },
      b: { running: false, results: [result('b')] },
    };
    const next = nodeExecutionReducer(state, { type: 'clear', nodeId: 'b' });
    expect(next.a).toEqual(state.a);
    expect(next.b.results).toEqual([]);
  });
});
