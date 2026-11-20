/**
 * What model calls and paid tool calls cost, read from the server's usage
 * package (`server/services/usage/handlers.py`). The figures are estimates:
 * the configured prices applied to the usage each provider reported.
 *
 * `useChatRunCostsQuery(workflowId)` is USD per chat run of one workflow
 * (`get_chat_run_costs`), for the figure under a reply. WebSocketContext
 * invalidates it on `chat.updated`, which the server sends when a reply is
 * saved and when a run ends.
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
