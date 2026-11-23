import { buildApiUrl } from '../config/api';
import type { WorkflowOperation } from '../lib/workflowOps';
import { useAppStore } from '../store/useAppStore';
import { serializeWorkflowMutation } from '../lib/workflowMutationQueue';

export interface SavedCreation { node_ids: Record<string, string>; operations: WorkflowOperation[] }
export class CreationError extends Error {
  constructor(message: string, readonly code?: string) { super(message); }
}

/** Retry a lost acknowledgement with the same mutation identity and body. */
export async function postCreation(path: string, input: Record<string, unknown>, mutationId: string = crypto.randomUUID()): Promise<SavedCreation> {
  const body = JSON.stringify({ ...input, mutation_id: mutationId });
  for (let attempt = 0; ; attempt++) {
    let response: Response;
    let result: any;
    try {
      response = await fetch(buildApiUrl(path), { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body });
      result = await response.json();
    } catch (error) {
      if (attempt === 0) continue;
      throw error;
    }
    if (!response.ok) throw new CreationError(typeof result.detail === 'string' ? result.detail : result.detail?.message || 'Could not create the agent resource.', result.detail?.code);
    return result;
  }
}

export function createAgent(input: { workflow_id: string; node_type: string; position: [number, number]; parent_node_id?: string; parameters?: Record<string, unknown> }, mutationId?: string) {
  return postCreation('/api/agents', input, mutationId);
}
export function createPrivateBrowser(agentId: string, input: { workflow_id: string; position?: [number, number] }, mutationId?: string) {
  return postCreation(`/api/browser/agents/${encodeURIComponent(agentId)}/tools`, input, mutationId);
}

/** Save/create/adopt is one serialized write path for the open graph. */
export function createSavedGraphResource(workflowId: string, flush: () => void, create: () => Promise<SavedCreation>, options?: { allowClosedWorkflow?: boolean }): Promise<SavedCreation | null> {
  const run = async () => {
    const isOpen = useAppStore.getState().currentWorkflow?.id === workflowId;
    if (!isOpen && !options?.allowClosedWorkflow) return null;
    if (isOpen) {
      flush();
      const store = useAppStore.getState();
      if (!await (store.saveWorkflowWithinMutation ?? store.saveWorkflow)()) return null;
      if (useAppStore.getState().currentWorkflow?.id !== workflowId) return null;
    }
    const result = await create();
    useAppStore.getState().adoptSavedOperations(workflowId, result.operations);
    return result;
  };
  return serializeWorkflowMutation(workflowId, run);
}
