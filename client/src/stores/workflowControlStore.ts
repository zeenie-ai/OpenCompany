/**
 * Workflow control-plane status, one slot per workflow.
 *
 * WebSocketContext owns the control plane (requests, broadcasts, the
 * generation/revision merge rule) and keeps `workflowControlStatuses` in its
 * context value for the editor. That value is rebuilt on every console, chat
 * and terminal line, so Normal mode reads this mirror instead:
 * WebSocketContext writes it at the exact points it updates its own state,
 * and a slice selector re-renders only when the workflow it names changes.
 *
 * Read-only for everyone else. Never write here directly; go through the
 * control mutations on WebSocketActionsContext.
 */

import { create } from 'zustand';
import type { WorkflowControlPendingMutation, WorkflowControlStatus } from '../contexts/WebSocketContext';

interface WorkflowControlStoreState {
  /** workflowId -> latest accepted control status */
  statuses: Record<string, WorkflowControlStatus>;
  /** workflowId -> the lifecycle mutation in flight for it, if any */
  pending: Record<string, WorkflowControlPendingMutation>;
  /** WebSocketContext only: replace the whole map (it already merged). */
  replaceStatuses: (next: Record<string, WorkflowControlStatus>) => void;
  /** WebSocketContext only: set or clear one workflow's pending mutation. */
  setPending: (workflowId: string, pending: WorkflowControlPendingMutation | null) => void;
}

export const useWorkflowControlStore = create<WorkflowControlStoreState>((set) => ({
  statuses: {},
  pending: {},
  replaceStatuses: (next) => set((state) => (state.statuses === next ? state : { statuses: next })),
  setPending: (workflowId, pending) =>
    set((state) => {
      if (pending) {
        if (state.pending[workflowId] === pending) return state;
        return { pending: { ...state.pending, [workflowId]: pending } };
      }
      if (!(workflowId in state.pending)) return state;
      const next = { ...state.pending };
      delete next[workflowId];
      return { pending: next };
    }),
}));

/** The latest control status for one workflow; undefined until the server
 *  has reported it. */
export function useWorkflowControl(workflowId: string | null | undefined): WorkflowControlStatus | undefined {
  return useWorkflowControlStore((s) => (workflowId ? s.statuses[workflowId] : undefined));
}

/** The lifecycle mutation in flight for one workflow, if any. */
export function useWorkflowControlPending(
  workflowId: string | null | undefined,
): WorkflowControlPendingMutation | undefined {
  return useWorkflowControlStore((s) => (workflowId ? s.pending[workflowId] : undefined));
}
