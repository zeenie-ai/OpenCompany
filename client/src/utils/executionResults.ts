import type { ExecutionResult } from '../services/executionService';

/** Return the newest result owned by a physical node (inputs are newest-first). */
export function latestResultForNode(
  results: ExecutionResult[],
  nodeId: string,
): ExecutionResult | undefined {
  return results.find((result) => result.nodeId === nodeId);
}
