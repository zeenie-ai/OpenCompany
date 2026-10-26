/**
 * The employee's always-allow rules (RFC-0007 §4.4), for "Allowed without
 * asking…" in the Ask first menu, as TanStack queries over
 * `list_approval_allows` / `add_approval_allow` / `remove_approval_allow`.
 *
 * - `useApprovalAllows(workflowId)`: the rules the owner made with Always
 *   allow on a card, and a switch for each of the employee's money tools
 *   and operations, on while a rule lets it run unasked.
 * - `useAddApprovalAllow` turns a money switch on; `useRemoveApprovalAllow`
 *   takes a rule away, or turns a money switch off. Either applies from the
 *   employee's next call.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { useWebSocketActions } from '@/contexts/WebSocketContext';

export const approvalAllowsKey = (workflowId: string) => ['approvalAllows', workflowId] as const;

const ruleSchema = z
  .object({ id: z.string().min(1), label: z.string().catch(''), created_at: z.string().nullable().optional().catch(null) })
  .transform((raw) => ({ id: raw.id, label: raw.label, createdAt: raw.created_at ?? null }));

const moneySchema = z
  .object({
    node_type: z.string().min(1),
    operation: z.string().catch(''),
    label: z.string().catch(''),
    allow_id: z.string().nullable().optional().catch(null),
  })
  .transform((raw) => ({ nodeType: raw.node_type, operation: raw.operation, label: raw.label, allowId: raw.allow_id ?? null }));

export type ApprovalAllow = z.output<typeof ruleSchema>;
export type MoneySwitch = z.output<typeof moneySchema>;

export interface ApprovalAllows {
  /** Oldest first, without the money switches' own rules. */
  rules: ApprovalAllow[];
  money: MoneySwitch[];
}

function parseList<T>(schema: z.ZodType<T>, list: unknown): T[] {
  return (Array.isArray(list) ? list : []).flatMap((raw) => {
    const parsed = schema.safeParse(raw);
    return parsed.success ? [parsed.data] : [];
  });
}

type ListReply = { success?: boolean; allows?: unknown; money?: unknown; error?: string };

export function readApprovalAllows(reply: ListReply | undefined): ApprovalAllows {
  const money = parseList(moneySchema, reply?.money);
  const switches = new Set(money.flatMap((option) => (option.allowId ? [option.allowId] : [])));
  return { rules: parseList(ruleSchema, reply?.allows).filter((rule) => !switches.has(rule.id)), money };
}

export function useApprovalAllows(workflowId: string) {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<ApprovalAllows, Error>({
    queryKey: approvalAllowsKey(workflowId),
    queryFn: async () => {
      const reply = await sendRequest<ListReply>('list_approval_allows', { workflow_id: workflowId });
      if (reply?.success === false) throw new Error(reply.error || 'Could not load the list');
      return readApprovalAllows(reply);
    },
    enabled: isReady && Boolean(workflowId),
    // Cards add rules while the list is closed: look again on every open.
    refetchOnMount: 'always',
    staleTime: 0,
  });
}

type ChangeReply = { success?: boolean; error?: string } | undefined;

function useChangeAllows<T>(workflowId: string, send: (input: T) => Promise<ChangeReply>, onError?: (message: string) => void) {
  const queryClient = useQueryClient();
  return useMutation<void, Error, T>({
    mutationFn: async (input) => {
      const reply = await send(input);
      if (reply?.success === false) throw new Error('That didn’t change. Try again.');
    },
    onError: (error) => onError?.(error.message),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: approvalAllowsKey(workflowId) });
    },
  });
}

export function useAddApprovalAllow(workflowId: string, onError?: (message: string) => void) {
  const { sendRequest } = useWebSocketActions();
  return useChangeAllows<MoneySwitch>(
    workflowId,
    (option) =>
      sendRequest<ChangeReply>('add_approval_allow', { workflow_id: workflowId, node_type: option.nodeType, operation: option.operation }),
    onError,
  );
}

export function useRemoveApprovalAllow(workflowId: string, onError?: (message: string) => void) {
  const { sendRequest } = useWebSocketActions();
  return useChangeAllows<string>(
    workflowId,
    (allowId) => sendRequest<ChangeReply>('remove_approval_allow', { workflow_id: workflowId, allow_id: allowId }),
    onError,
  );
}
