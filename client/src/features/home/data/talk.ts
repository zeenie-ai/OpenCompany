/**
 * Home's side of talking to an employee. The conversation itself is the
 * shared chat (features/chat); this is what only an employee has:
 *
 * - `useEnableTalk()` / `useApplyChanges()`: Turn on Talk and Apply. Both
 *   apply the saved graph after current work finishes, then refresh
 *   the team from the database.
 * - `useRetryNote(...)`: while the talk agent waits to retry after a failed
 *   attempt, what it said, for the chat's status line. It reads the agent's
 *   node status (`useNodeStatusStore` directly, like `useLiveTask`: the
 *   editor's hooks see only the workflow open in Dev mode).
 */

import { useCallback } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { WORKFLOW_CONTROL_REQUEST_TIMEOUT, useWebSocketActions } from '@/contexts/WebSocketContext';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';
import { refreshEmployee } from './employees';

/** Both request a safe graph handoff. Refresh the queries afterwards,
 *  including failures that may have changed the employee before failing.
 *  Every click sends its own idempotency key. Errors carry the server's code. */
function useEmployeeChange(type: 'enable_employee_talk' | 'apply_employee_changes') {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  return useMutation<{ activation_state?: string }, Error, string>({
    mutationFn: async (workflowId) => {
      const response = await sendRequest<{ success?: boolean; error?: string; activation_state?: string }>(
        type,
        { workflow_id: workflowId, idempotency_key: crypto.randomUUID() },
        WORKFLOW_CONTROL_REQUEST_TIMEOUT,
      );
      if (response?.success === false) throw new Error(response.error || 'failed');
      return { activation_state: response?.activation_state };
    },
    onSettled: (_data, _error, workflowId) => refreshEmployee(queryClient, workflowId),
  });
}

/** Adds a talk line without clearing the employee's conversation or work. */
export function useEnableTalk() {
  return useEmployeeChange('enable_employee_talk');
}

/** Applies the employee's latest graph after current work finishes. */
export function useApplyChanges() {
  return useEmployeeChange('apply_employee_changes');
}

/** Explicitly interrupt current work while preserving queued requests and conversation. */
export function useStopAndApply() {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({ mutationFn: async (workflowId) => {
    const response = await sendRequest<{ success?: boolean; error?: string }>('apply_employee_changes',
      { workflow_id: workflowId, idempotency_key: crypto.randomUUID(), stop_work: true }, WORKFLOW_CONTROL_REQUEST_TIMEOUT);
    if (response?.success === false) throw new Error(response.error || 'failed');
  }, onSettled: (_data, _error, workflowId) => refreshEmployee(queryClient, workflowId) });
}

/** "{why} Retrying automatically…" while the talk agent waits to try again;
 *  null otherwise, and whenever no run of theirs is going (`live`). */
export function useRetryNote(workflowId: string, agentNodeId: string | null, live: boolean): string | null {
  const status = useNodeStatusStore(
    useCallback(
      (state) => (agentNodeId ? state.allStatuses[workflowId]?.[agentNodeId] : undefined),
      [workflowId, agentNodeId],
    ),
  );
  if (!live || status?.status !== 'executing' || status.data?.phase !== 'retry_wait') return null;
  const message = status.data.retry_message;
  return typeof message === 'string' && message ? `${message} Retrying automatically…` : null;
}
