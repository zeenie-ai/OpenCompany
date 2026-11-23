import { buildApiUrl } from '../config/api';
import type { WorkflowOperation } from '../lib/workflowOps';
import { CreationError } from './agentCreationApi';

export interface BrowserAgentCreation {
  workflow_id: string;
  browser_node_id?: string;
  parent_node_id?: string;
  position?: [number, number];
  provider?: string;
  model?: string;
}

export async function createBrowserAgent(input: BrowserAgentCreation, mutationId: string = crypto.randomUUID()): Promise<{ node_ids: Record<string, string>; operations: WorkflowOperation[] }> {
  const body = JSON.stringify({ ...input, mutation_id: mutationId });
  // A lost response retries the same saved mutation, never a second graph.
  for (let attempt = 0; ; attempt++) {
    let response: Response;
    try {
      response = await fetch(buildApiUrl('/api/browser/agents'), {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body,
      });
    } catch (error) {
      if (attempt === 0) continue;
      throw error;
    }
    const result = await response.json();
    if (!response.ok) throw new CreationError(typeof result.detail === 'string' ? result.detail : result.detail?.message || 'Could not add Browser AI Agent.', result.detail?.code);
    return result;
  }
}
