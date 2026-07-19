/**
 * Listens for backend-pushed `workflow_ops_apply` events and applies
 * them to the live React Flow canvas via the standard
 * `applyOperations` reconciler.
 *
 * Source of events: `services/status_broadcaster.send_custom_event`
 * called by the Agent Builder's tool functions
 * (`server/nodes/tool/agent_builder.py`) after a successful mutation.
 *
 * Filtering:
 *   - Events whose `workflow_id` matches the current workflow apply
 *     to the canvas in-place.
 *   - Events for OTHER workflows (e.g. `create_workflow` returning a
 *     fresh id) surface as a sonner toast with a Switch action so the
 *     user can jump to the new workflow without losing their place.
 *
 * Mounted once in Dashboard.
 */

import { useEffect } from 'react';
import type { Node, Edge } from 'reactflow';
import { toast } from 'sonner';

import { useWebSocket } from '../contexts/WebSocketContext';
import { useAppStore } from '../store/useAppStore';
import { applyOperations, type WorkflowOperation } from '../lib/workflowOps';

interface WorkflowOpsApplyEvent {
  workflow_id?: string | null;
  caller_node_id?: string | null;
  operations?: WorkflowOperation[];
}

interface UseWorkflowOpsListenerProps {
  nodes: Node[];
  edges: Edge[];
  setNodes: (updater: (ns: Node[]) => Node[]) => void;
  setEdges: (updater: (es: Edge[]) => Edge[]) => void;
}

export function useWorkflowOpsListener({
  nodes,
  edges,
  setNodes,
  setEdges,
}: UseWorkflowOpsListenerProps) {
  const { addEventListener, saveNodeParameters } = useWebSocket();
  const currentWorkflowId = useAppStore(s => s.currentWorkflow?.id);
  const loadWorkflow = useAppStore(s => s.loadWorkflow);

  useEffect(() => {
    const unsubscribe = addEventListener('workflow_ops_apply', (raw: WorkflowOpsApplyEvent) => {
      const ops = raw?.operations ?? [];
      if (ops.length === 0 && !raw?.workflow_id) return;

      // Different workflow -- surface a toast with a switch action.
      // Used by `create_workflow`, which persists a new workflow but
      // doesn't try to mutate the canvas the user is currently on.
      if (raw.workflow_id && raw.workflow_id !== currentWorkflowId) {
        toast.message('New workflow created', {
          description: `Workflow ${raw.workflow_id} is ready.`,
          action: {
            label: 'Switch',
            onClick: () => loadWorkflow(raw.workflow_id!),
          },
        });
        return;
      }

      if (ops.length === 0) return;

      void applyOperations(ops, {
        workflowId: currentWorkflowId,
        nodes,
        edges,
        setNodes,
        setEdges,
        saveNodeParameters,
      }).then(result => {
        if (result.errors.length > 0) {
          console.warn('[workflow_ops_apply] some ops failed:', result.errors);
        }
      });
    });

    return unsubscribe;
  }, [
    addEventListener, currentWorkflowId, loadWorkflow,
    nodes, edges, setNodes, setEdges, saveNodeParameters,
  ]);
}
