/**
 * Which app events wake an employee: the "Wake me when" rule (server
 * services/employees/handlers.py `set_employee_wake_rule`, used by
 * services/employees/triage.py). An empty rule clears it, and the employee
 * works on every event again. Saving refreshes the employee; a running one
 * takes the rule on Apply, like any other change to its team.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { refreshEmployee } from './employees';

/** Mirrors `RULE_MAX` in server/services/employees/triage.py. */
export const WAKE_RULE_MAX = 200;

type Reply = { success?: boolean; error?: string };

/** Save an employee's rule ("" clears it). Errors carry the server's code. */
export function useSetWakeRule() {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  return useMutation<void, Error, { workflowId: string; rule: string }>({
    mutationFn: async ({ workflowId, rule }) => {
      const reply = await sendRequest<Reply>('set_employee_wake_rule', { workflow_id: workflowId, wake_rule: rule });
      if (reply?.success === false) throw new Error(reply.error || 'failed');
    },
    onSettled: (_data, _error, { workflowId }) => refreshEmployee(queryClient, workflowId),
  });
}
