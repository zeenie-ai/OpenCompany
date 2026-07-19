/**
 * Workflow API Service
 * Handles workflow CRUD operations with the backend database
 */

import { API_CONFIG } from '../config/api';

const getApiBase = () => `${API_CONFIG.PYTHON_BASE_URL}/api/database`;

export interface WorkflowSummary {
  id: string;
  name: string;
  slug: string;
  nodeCount: number;
  createdAt: string;
  lastModified: string;
}

export interface WorkflowData {
  id: string;
  name: string;
  slug: string;
  data: {
    nodes: any[];
    edges: any[];
  };
  createdAt: string;
  lastModified: string;
}

export interface SaveWorkflowResult {
  success: boolean;
  /** Backend-allocated canonical decimal id (also returned for updates). */
  id: string;
  slug?: string;
  name?: string;
  nodeIdAliases?: Record<string, string>;
}

export const workflowApi = {
  async saveWorkflow(workflowId: string, name: string, data: { nodes: any[]; edges: any[] }): Promise<SaveWorkflowResult | null> {
    try {
      const response = await fetch(`${getApiBase()}/workflows`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ workflow_id: workflowId, name, data })
      });
      const result = await response.json();
      if (!result.success) return null;
      const canonicalId = result.id ?? result.workflow_id;
      if (!canonicalId && workflowId === 'new') return null;
      return {
        success: true,
        id: canonicalId ?? workflowId,
        slug: result.slug,
        name: result.name,
        nodeIdAliases: result.node_id_aliases ?? {},
      };
    } catch (error) {
      console.error('Failed to save workflow:', error);
      return null;
    }
  },

  async getWorkflow(workflowId: string): Promise<WorkflowData | null> {
    try {
      const response = await fetch(`${getApiBase()}/workflows/${workflowId}`, {
        credentials: 'include'
      });
      const result = await response.json();
      if (result.success && result.workflow) {
        return result.workflow;
      }
      return null;
    } catch (error) {
      console.error('Failed to get workflow:', error);
      return null;
    }
  },

  async getAllWorkflows(): Promise<WorkflowSummary[]> {
    try {
      const response = await fetch(`${getApiBase()}/workflows`, {
        credentials: 'include'
      });
      const result = await response.json();
      if (result.success && result.workflows) {
        return result.workflows;
      }
      return [];
    } catch (error) {
      console.error('Failed to get workflows:', error);
      return [];
    }
  },

  async deleteWorkflow(workflowId: string): Promise<boolean> {
    try {
      const response = await fetch(`${getApiBase()}/workflows/${workflowId}`, {
        method: 'DELETE',
        credentials: 'include'
      });
      const result = await response.json();
      return result.success;
    } catch (error) {
      console.error('Failed to delete workflow:', error);
      return false;
    }
  }
};
