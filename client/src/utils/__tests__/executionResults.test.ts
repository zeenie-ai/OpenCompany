import { describe, expect, it } from 'vitest';

import type { ExecutionResult } from '../../services/executionService';
import { latestResultForNode } from '../executionResults';

const result = (nodeId: string, marker: string): ExecutionResult => ({
  success: true,
  nodeId,
  nodeType: 'pythonExecutor',
  nodeName: nodeId,
  timestamp: marker,
  executionTime: 1,
  outputs: { stdout: marker },
  nodeData: [[{ json: { stdout: marker } }]],
});

describe('latestResultForNode', () => {
  it('ignores newer results owned by another node', () => {
    const results = [result('node-a', 'a-new'), result('node-b', 'b-old')];
    expect(latestResultForNode(results, 'node-b')?.outputs?.stdout).toBe('b-old');
  });

  it('returns undefined when the selected node has no result', () => {
    expect(latestResultForNode([result('node-a', 'a')], 'node-b')).toBeUndefined();
  });
});
