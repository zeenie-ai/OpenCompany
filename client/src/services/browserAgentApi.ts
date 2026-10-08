import { buildApiUrl } from '../config/api';
import type { WorkflowOperation } from '../lib/workflowOps';

export interface BrowserAgentCreation {
  workflow_id: string;
  browser_node_id?: string;
  position?: [number, number];
  provider?: string;
  model?: string;
}

export async function createBrowserAgent(input: BrowserAgentCreation): Promise<{ node_ids: Record<string, string>; operations: WorkflowOperation[] }> {
  const body = JSON.stringify({ ...input, mutation_id: crypto.randomUUID() });
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
    if (!response.ok) throw new Error(result.detail || 'Could not add Browser AI Agent.');
    return result;
  }
}
