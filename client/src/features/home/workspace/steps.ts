/**
 * An employee's Workspace steps (server: services/workspace_steps.py): what
 * they did on each surface, oldest first, worded by the server (Browser
 * site actions and screenshots, Canvas displays, phone actions, and the
 * owner taking the phone over and handing it back). Read through
 * `workspace_steps_list` and again on each `workspace_step` broadcast for
 * this workflow, which carries only identity.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { z } from 'zod';
import { useWebSocketActions } from '@/contexts/WebSocketContext';

export type StepSurface = 'browser' | 'canvas' | 'mobile';

export interface WorkspaceStep {
  id: number;
  surface: StepSurface;
  text: string;
  /** ISO time it happened. */
  at: string;
}

const stepSchema = z.object({
  id: z.number().int(),
  surface: z.enum(['browser', 'canvas', 'mobile']),
  text: z.string(),
  at: z.string(),
});

const listSchema = z.object({ steps: z.array(z.unknown()).catch([]) }).transform((raw) =>
  raw.steps.flatMap((item): WorkspaceStep[] => {
    const parsed = stepSchema.safeParse(item);
    return parsed.success ? [parsed.data] : [];
  }),
);

export const workspaceStepsKey = (workflowId: string) => ['workspaceSteps', workflowId] as const;

export function useWorkspaceSteps(workflowId: string) {
  const { sendRequest, isReady, addEventListener } = useWebSocketActions();
  const queryClient = useQueryClient();
  useEffect(
    () =>
      addEventListener('workspace_step', (event: { data?: { workflow_id?: unknown } } | undefined) => {
        if (event?.data?.workflow_id === workflowId) void queryClient.invalidateQueries({ queryKey: workspaceStepsKey(workflowId) });
      }),
    [addEventListener, queryClient, workflowId],
  );
  return useQuery<WorkspaceStep[], Error>({
    queryKey: workspaceStepsKey(workflowId),
    enabled: isReady,
    // Runs take steps while no timeline shows them: always look again on open.
    refetchOnMount: 'always',
    staleTime: 0,
    queryFn: async () => {
      const reply = await sendRequest<{ success?: boolean; error?: string }>('workspace_steps_list', { workflow_id: workflowId });
      if (reply?.success === false) throw new Error(reply.error || 'read_failed');
      return listSchema.parse(reply);
    },
  });
}
