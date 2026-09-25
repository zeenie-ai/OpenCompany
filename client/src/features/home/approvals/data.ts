/**
 * Drafts waiting for the owner (the approval step), as TanStack queries over
 * `list_approvals` / `decide_approval`.
 *
 * - `useApprovalsQuery(workflowId)`: one employee's pending drafts.
 * - `useDecideApproval()`: Send or Discard. The draft leaves the list at
 *   once and comes back if the server refuses; every click carries a fresh
 *   `decision_key`, so a replayed request settles the draft only once.
 * - `useApprovalLifecycle()`: `approval_lifecycle` broadcasts (identity
 *   only) refetch the affected list, and a new draft says so in a toast.
 */

import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { EMPLOYEES_QUERY_KEY, invalidateEmployees } from '../data/employees';
import type { EmployeeSummary } from '../data/schemas';
import { pillToast } from '../ui/pillToast';

export const approvalsKey = (workflowId: string) => ['approvals', workflowId] as const;

export const approvalSchema = z.object({
  approval_id: z.string().min(1),
  workflow_id: z.string(),
  node_id: z.string().catch(''),
  status: z.string().catch('pending'),
  channel: z.string().catch(''),
  channel_label: z.string().catch(''),
  recipient: z.string().catch(''),
  recipient_label: z.string().catch(''),
  subject: z.string().optional().catch(undefined),
  body: z.string().catch(''),
  context_excerpt: z.string().optional().catch(undefined),
  created_at: z.string().nullable().catch(null),
  expires_at: z.string().nullable().optional().catch(null),
  revision: z.number().catch(0),
  max_length: z.number().int().positive().catch(20000),
  deployment_state: z.string().nullable().optional().catch(null),
});
export type Approval = z.infer<typeof approvalSchema>;

export function parseApprovals(raw: unknown): Approval[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    const parsed = approvalSchema.safeParse(item);
    return parsed.success ? [parsed.data] : [];
  });
}

export function useApprovalsQuery(workflowId: string | null) {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<Approval[], Error>({
    queryKey: approvalsKey(workflowId ?? ''),
    queryFn: async () => {
      const response = await sendRequest<{ success?: boolean; approvals?: unknown; error?: string }>('list_approvals', {
        workflow_id: workflowId,
        status: 'pending',
      });
      if (response?.success === false) throw new Error(response.error || 'Could not load the drafts');
      return parseApprovals(response?.approvals);
    },
    enabled: isReady && Boolean(workflowId),
    staleTime: 10_000,
    // Drafts arrive while the card is closed; always look again on open.
    refetchOnMount: 'always',
  });
}

export type Decision = 'send' | 'discard';

export interface DecideInput {
  approval: Approval;
  decision: Decision;
  text?: string;
}

const DECIDE_ERRORS: Record<string, string> = {
  already_decided: 'That draft was already handled.',
  expired: 'That draft expired before it was sent.',
  cancelled: 'That draft was cancelled.',
  not_found: 'That draft is gone.',
  invalid_request: "That can't be sent as it is.",
};

function decisionKey(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  } catch {
    // Not a secure context.
  }
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function useDecideApproval() {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  return useMutation<
    { will_send_on_resume?: boolean },
    Error,
    DecideInput,
    { previous: Approval[] | undefined; key: readonly string[] }
  >({
    mutationFn: async ({ approval, decision, text }) => {
      const response = await sendRequest<{ success?: boolean; error?: string; detail?: string; will_send_on_resume?: boolean }>(
        'decide_approval',
        {
          approval_id: approval.approval_id,
          decision,
          decision_key: decisionKey(),
          ...(decision === 'send' && text !== undefined ? { text } : {}),
        },
      );
      if (response?.success === false) {
        throw new Error(response.detail || DECIDE_ERRORS[response.error ?? ''] || 'That did not work. Try again.');
      }
      return response ?? {};
    },
    onMutate: async ({ approval }) => {
      const key = approvalsKey(approval.workflow_id);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<Approval[]>(key);
      queryClient.setQueryData<Approval[]>(key, (list) => list?.filter((item) => item.approval_id !== approval.approval_id));
      return { previous, key };
    },
    onError: (error, _input, context) => {
      if (context) queryClient.setQueryData(context.key, context.previous);
      pillToast(error.message, { tone: 'error' });
    },
    onSettled: (_data, _error, { approval }) => {
      void queryClient.invalidateQueries({ queryKey: approvalsKey(approval.workflow_id) });
      invalidateEmployees(queryClient);
    },
  });
}

interface ApprovalEnvelope {
  specversion?: string;
  type?: string;
  data?: { workflow_id?: string; approval_id?: string } | null;
}

/** Apply one `approval_lifecycle` envelope. Exported for tests. */
export function applyApprovalLifecycle(queryClient: QueryClient, event: ApprovalEnvelope): void {
  if (!event || event.specversion !== '1.0') return;
  const workflowId = event.data?.workflow_id;
  if (!workflowId) return;
  void queryClient.invalidateQueries({ queryKey: approvalsKey(workflowId) });
  if (event.type?.endsWith('.requested')) {
    const employee = queryClient
      .getQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY)
      ?.find((item) => item.workflow_id === workflowId);
    pillToast(`${employee?.name ?? 'An employee'} has a draft for you to check`, { tone: 'info' });
  }
}

export function useApprovalLifecycle(): void {
  const queryClient = useQueryClient();
  const { addEventListener } = useWebSocketActions();
  useEffect(() => addEventListener('approval_lifecycle', (data) => applyApprovalLifecycle(queryClient, data)), [addEventListener, queryClient]);
}
