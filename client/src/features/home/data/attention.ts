/**
 * Home's attention items (server: services/attention.py): work that keeps
 * retrying past its limit, or runs stuck on failing workflow tasks, open
 * until it clears and dropped an hour after it was last seen. Read through `list_attention_items`, again on each
 * `attention_lifecycle` broadcast (identity only), and once a minute, since
 * nothing announces the drop-off.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { z } from 'zod';
import { useWebSocketActions } from '@/contexts/WebSocketContext';

export interface AttentionItem {
  id: string;
  /** `stuck_retry`: an activity still retrying past its limit; `stuck_runs`:
   * runs whose workflow tasks keep failing. */
  kind: string;
  /** Sightings since it opened. */
  count: number;
}

const itemSchema = z.object({ id: z.string(), kind: z.string(), count: z.number().int() });

const listSchema = z.object({ items: z.array(z.unknown()).catch([]) }).transform((raw) =>
  raw.items.flatMap((item): AttentionItem[] => {
    const parsed = itemSchema.safeParse(item);
    return parsed.success ? [parsed.data] : [];
  }),
);

export const ATTENTION_QUERY_KEY = ['attentionItems'] as const;

/** Items drop off an hour after they were last seen, which no event says. */
const REFETCH_MS = 60_000;

export function useAttentionItems() {
  const { sendRequest, isReady, addEventListener } = useWebSocketActions();
  const queryClient = useQueryClient();
  useEffect(
    () => addEventListener('attention_lifecycle', () => void queryClient.invalidateQueries({ queryKey: ATTENTION_QUERY_KEY })),
    [addEventListener, queryClient],
  );
  return useQuery<AttentionItem[], Error>({
    queryKey: ATTENTION_QUERY_KEY,
    enabled: isReady,
    refetchOnMount: 'always',
    staleTime: 0,
    refetchInterval: REFETCH_MS,
    queryFn: async () => {
      const reply = await sendRequest<{ success?: boolean; error?: string }>('list_attention_items', {});
      if (reply?.success === false) throw new Error(reply.error || 'read_failed');
      return listSchema.parse(reply);
    },
  });
}
