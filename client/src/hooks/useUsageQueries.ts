/**
 * What model calls and paid tool calls cost, read from the server's usage
 * package (`server/services/usage/handlers.py`). The figures are estimates:
 * the configured prices applied to the usage each provider reported.
 *
 * `useChatRunCostsQuery(workflowId)` is USD per chat run of one workflow
 * (`get_chat_run_costs`), for the figure under a reply. WebSocketContext
 * invalidates it on `chat.updated`, which the server sends when a reply is
 * saved and when a run ends.
 *
 * `useWorkflowCostsQuery()` is this month's cost by employee
 * (`get_workflow_costs`), for the cost panel. It is read again whenever the
 * panel opens, because calls are booked while it is closed.
 */

import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { queryKeys } from '@/lib/queryConfig';

/** USD by chat run id. A run with no booked call is absent. */
export type ChatRunCosts = Record<string, number>;

const runCostsSchema = z.object({ runs: z.record(z.string(), z.number()) });

export function useChatRunCostsQuery(workflowId: string) {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<ChatRunCosts, Error>({
    queryKey: queryKeys.chatRunCosts.bySession(workflowId).queryKey,
    enabled: isReady && Boolean(workflowId),
    queryFn: async () => {
      const reply = await sendRequest<{ success?: boolean; error?: string }>('get_chat_run_costs', { workflow_id: workflowId });
      if (reply?.success === false) throw new Error(reply.error || 'read_failed');
      const parsed = runCostsSchema.safeParse(reply);
      return parsed.success ? parsed.data.runs : {};
    },
  });
}

export interface WorkflowCost {
  /** Null for the last row: calls booked for no workflow, or for one deleted since. */
  workflowId: string | null;
  name: string | null;
  totalCost: number;
}

export interface WorkflowCosts {
  /** The first day of the counted month in the owner's time zone, as YYYY-MM-DD. */
  since: string;
  totalCost: number;
  /** Highest cost first, then the unnamed row. */
  workflows: WorkflowCost[];
}

export const WORKFLOW_COSTS_QUERY_KEY = ['workflowCosts'] as const;

const workflowCostsSchema = z.object({
  since: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  total_cost: z.number(),
  workflows: z.array(z.object({ workflow_id: z.string().nullable(), name: z.string().nullable(), total_cost: z.number() })),
});

export function useWorkflowCostsQuery() {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<WorkflowCosts, Error>({
    queryKey: WORKFLOW_COSTS_QUERY_KEY,
    enabled: isReady,
    refetchOnMount: 'always',
    queryFn: async () => {
      const reply = await sendRequest<{ success?: boolean; error?: string }>('get_workflow_costs', {});
      if (reply?.success === false) throw new Error(reply.error || 'read_failed');
      const parsed = workflowCostsSchema.safeParse(reply);
      if (!parsed.success) throw new Error('read_failed');
      return {
        since: parsed.data.since,
        totalCost: parsed.data.total_cost,
        workflows: parsed.data.workflows.map((workflow) => ({
          workflowId: workflow.workflow_id,
          name: workflow.name,
          totalCost: workflow.total_cost,
        })),
      };
    },
  });
}
