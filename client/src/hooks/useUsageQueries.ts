/**
 * What model calls and paid tool calls cost, read from the server's usage
 * package (`server/services/usage/handlers.py`). The figures are estimates:
 * the configured prices applied to the usage each provider reported.
 *
 * `useChatRunCostsQuery(workflowId)` is USD per chat run of one workflow
 * (`get_chat_run_costs`), for the figure under a reply. WebSocketContext
 * invalidates it on `chat.updated`, which the server sends when a reply is
 * saved and when a run ends. A reply's figure includes the team it handed
 * work to, and the team works on after the reply has finished, so the hook
 * also reads again on `agent.instance.updated` for its workflow.
 *
 * `useWorkflowCostsQuery()` is this month's cost and model tokens by
 * employee (`get_workflow_costs`), for the cost panel, the cost under each
 * name in Home's team list and an employee's usage. It is read again whenever
 * one of them opens, because calls are booked while it is closed.
 *
 * `useEmployeeUsageQuery(workflowId?)` is this month's finished tasks
 * (`get_employee_usage`): the whole team's for Settings > Billing, one
 * employee's for its usage.
 *
 * `invalidateUsage(queryClient)` reads both again, once per burst; Home's
 * employee broadcasts call it, since a run that finishes, a rename and a
 * removal all change them.
 */

import { useEffect } from 'react';
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { makeDebouncedInvalidator } from '@/lib/debouncedInvalidate';
import { queryKeys } from '@/lib/queryConfig';

/** USD by chat run id. A run with no booked call is absent. */
export type ChatRunCosts = Record<string, number>;

const runCostsSchema = z.object({ runs: z.record(z.string(), z.number()) });

export function useChatRunCostsQuery(workflowId: string) {
  const { sendRequest, isReady, addEventListener } = useWebSocketActions();
  const queryClient = useQueryClient();

  useEffect(
    () =>
      addEventListener('agent.instance.updated', (event) => {
        if (event?.workflow_id === workflowId) {
          void queryClient.invalidateQueries({ queryKey: queryKeys.chatRunCosts.bySession(workflowId).queryKey });
        }
      }),
    [addEventListener, queryClient, workflowId],
  );

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
  /** The model calls' tokens, cached ones included; paid tools add none. */
  totalTokens: number;
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
  workflows: z.array(
    z.object({
      workflow_id: z.string().nullable(),
      name: z.string().nullable(),
      total_cost: z.number(),
      total_tokens: z.number().int().min(0),
    }),
  ),
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
          totalTokens: workflow.total_tokens,
        })),
      };
    },
  });
}

export interface EmployeeUsage {
  tasksThisMonth: number;
  /** The first day of the counted month in the owner's time zone, as YYYY-MM-DD. */
  since: string;
}

/** The team's count; one employee's is under its workflow id. */
export const EMPLOYEE_USAGE_QUERY_KEY = ['employeeUsage'] as const;

const employeeUsageSchema = z.object({
  tasks_this_month: z.number().int().min(0),
  since: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
});

/** Finished tasks this month, counted on the server from the run records in
 *  the owner's time zone: one employee's with a `workflowId`, else the whole
 *  team's. Read again whenever a reader opens, because runs finish while it
 *  is closed. */
export function useEmployeeUsageQuery(workflowId?: string) {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<EmployeeUsage, Error>({
    queryKey: workflowId ? [...EMPLOYEE_USAGE_QUERY_KEY, workflowId] : EMPLOYEE_USAGE_QUERY_KEY,
    enabled: isReady,
    refetchOnMount: 'always',
    queryFn: async () => {
      const reply = await sendRequest<{ success?: boolean; error?: string }>('get_employee_usage', workflowId ? { workflow_id: workflowId } : {});
      if (reply?.success === false) throw new Error(reply.error || "Couldn't count this month's tasks");
      const parsed = employeeUsageSchema.safeParse(reply);
      if (!parsed.success) throw new Error("Couldn't count this month's tasks");
      return { tasksThisMonth: parsed.data.tasks_this_month, since: parsed.data.since };
    },
  });
}

const USAGE_INVALIDATE_DEBOUNCE_MS = 300;
const invalidateCosts = makeDebouncedInvalidator(WORKFLOW_COSTS_QUERY_KEY, USAGE_INVALIDATE_DEBOUNCE_MS, true);
const invalidateTasks = makeDebouncedInvalidator(EMPLOYEE_USAGE_QUERY_KEY, USAGE_INVALIDATE_DEBOUNCE_MS);

/** Read this month's costs and task counts again, once per burst. Only the
 *  ones on screen are read. */
export function invalidateUsage(queryClient: QueryClient): void {
  invalidateCosts(queryClient);
  invalidateTasks(queryClient);
}
