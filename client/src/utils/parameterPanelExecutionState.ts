import type { ExecutionResult } from '../services/executionService';

export interface NodeExecutionEntry {
  running: boolean;
  results: ExecutionResult[];
}

export type NodeExecutionState = Record<string, NodeExecutionEntry>;

export type NodeExecutionAction =
  | { type: 'start'; nodeId: string }
  | { type: 'append'; nodeId: string; result: ExecutionResult }
  | { type: 'finish'; nodeId: string }
  | { type: 'clear'; nodeId: string };

export function nodeExecutionReducer(
  state: NodeExecutionState,
  action: NodeExecutionAction,
): NodeExecutionState {
  const current = state[action.nodeId] ?? { running: false, results: [] };
  switch (action.type) {
    case 'start':
      return { ...state, [action.nodeId]: { ...current, running: true } };
    case 'append':
      return {
        ...state,
        [action.nodeId]: {
          running: current.running,
          results: [action.result, ...current.results],
        },
      };
    case 'finish':
      return { ...state, [action.nodeId]: { ...current, running: false } };
    case 'clear':
      return { ...state, [action.nodeId]: { ...current, results: [] } };
  }
}
