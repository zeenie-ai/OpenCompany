/**
 * What the employee wants to send and waits for the owner's OK, and the Ask
 * first rule (docs-internal/chat_protocol.md, "Approvals"), as TanStack
 * queries over `list_approvals` / `decide_approval` / `get_ask_first` /
 * `set_ask_first`.
 *
 * - `useChatApprovals(workflowId)`: the session's recent drafts by id, open
 *   and settled (a reply's cards read theirs here), and how far the server's
 *   clock is from this one, so countdowns end when the server's windows do.
 * - `useDecideApproval(workflowId)`: send, undo, discard, restore, retry.
 *   The card moves at once and comes back if the server refuses; every click
 *   carries a fresh `decision_key`, so a replayed request acts once. Errors
 *   reach the owner through the hook (`onError`), never the click.
 * - `useAskFirst` / `useSetAskFirst`: the workflow's live rule.
 * - `applyApprovalEvent`: an `approval_lifecycle` broadcast (identity only)
 *   refetches the session's drafts.
 */

import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { useWebSocketActions } from '@/contexts/WebSocketContext';

export const chatApprovalsKey = (workflowId: string) => ['chatApprovals', workflowId] as const;
export const askFirstKey = (workflowId: string) => ['askFirst', workflowId] as const;

export type ApprovalStatus = 'pending' | 'approved' | 'sending' | 'sent' | 'failed' | 'discarded' | 'expired' | 'cancelled';

const STATUSES: readonly ApprovalStatus[] = ['pending', 'approved', 'sending', 'sent', 'failed', 'discarded', 'expired', 'cancelled'];

const optionalText = z
  .string()
  .nullable()
  .optional()
  .catch(undefined)
  .transform((value) => (typeof value === 'string' && value ? value : null));

const summarySchema = z
  .object({
    approval_id: z.string().min(1),
    workflow_id: z.string().catch(''),
    kind: z.enum(['gate', 'tool_call']).catch('gate'),
    status: z.enum(STATUSES as [ApprovalStatus, ...ApprovalStatus[]]).catch('pending'),
    channel: z.string().catch(''),
    action: optionalText,
    recipient: z.string().catch(''),
    recipient_label: z.string().catch(''),
    subject: optionalText,
    body: z.string().catch(''),
    context_excerpt: optionalText,
    details: z.array(z.object({ label: z.string(), value: z.string() })).catch([]),
    created_at: optionalText,
    expires_at: optionalText,
    revision: z.number().catch(0),
    max_length: z.number().int().positive().catch(20000),
    editable: z.boolean().catch(false),
    approved_by: z.enum(['owner', 'auto']).optional().catch(undefined),
    edited: z.boolean().optional().catch(undefined),
    undo_until: optionalText,
    restore_until: optionalText,
    consumed_at: optionalText,
    outcome: z
      .object({
        certainty: z.enum(['sent', 'not_sent', 'unknown']).catch('unknown'),
        error: z.string().optional().catch(undefined),
        at: z.string().optional().catch(undefined),
      })
      .optional()
      .catch(undefined),
    outcome_labels: z.object({ sent: z.string(), failed: z.string() }).catch({ sent: 'Message sent', failed: 'Message not sent' }),
    run_id: optionalText,
    tool_call_id: optionalText,
    ui_part_id: optionalText,
    deployment_state: optionalText,
  })
  .transform((raw) => ({
    id: raw.approval_id,
    workflowId: raw.workflow_id,
    kind: raw.kind,
    status: raw.status,
    channel: raw.channel,
    action: raw.action,
    recipient: raw.recipient,
    recipientLabel: raw.recipient_label || raw.recipient,
    subject: raw.subject,
    body: raw.body,
    contextExcerpt: raw.context_excerpt,
    details: raw.details,
    createdAt: raw.created_at,
    expiresAt: raw.expires_at,
    revision: raw.revision,
    maxLength: raw.max_length,
    editable: raw.editable,
    approvedBy: raw.approved_by ?? null,
    edited: raw.edited === true,
    undoUntil: raw.undo_until,
    restoreUntil: raw.restore_until,
    consumedAt: raw.consumed_at,
    outcome: raw.outcome ? { certainty: raw.outcome.certainty, error: raw.outcome.error ?? null, at: raw.outcome.at ?? null } : null,
    /** What the card says once it went, and when it did not (the server's words). */
    outcomeLabels: raw.outcome_labels,
    runId: raw.run_id,
    toolCallId: raw.tool_call_id,
    uiPartId: raw.ui_part_id,
    deploymentState: raw.deployment_state,
  }));

export type ChatApproval = z.output<typeof summarySchema>;

export function parseApproval(raw: unknown): ChatApproval | null {
  const parsed = summarySchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}

export interface ChatApprovals {
  byId: ReadonlyMap<string, ChatApproval>;
  /** Newest first. */
  order: readonly string[];
  /** The server's clock minus this one's, in ms. */
  offsetMs: number;
  /** When this list arrived (this clock). */
  receivedAt: number;
}

export const NO_APPROVALS: ChatApprovals = { byId: new Map(), order: [], offsetMs: 0, receivedAt: 0 };

/** Still the owner's to act on, or on its way. */
export function isOpenApproval(approval: ChatApproval, nowMs: number): boolean {
  switch (approval.status) {
    case 'pending':
    case 'sending':
    case 'failed':
      return true;
    case 'approved':
      return approval.consumedAt === null;
    case 'discarded':
      return approval.restoreUntil !== null && Date.parse(approval.restoreUntil) > nowMs;
    default:
      return false;
  }
}

/** Seconds left until `until` on the server's clock, never below 0. */
export function secondsLeft(until: string | null, offsetMs: number, nowMs = Date.now()): number {
  if (!until) return 0;
  const end = Date.parse(until);
  if (Number.isNaN(end)) return 0;
  return Math.max(0, Math.ceil((end - (nowMs + offsetMs)) / 1000));
}

type ListReply = { success?: boolean; approvals?: unknown; server_time?: string; error?: string };

export function readApprovals(reply: ListReply | undefined, receivedAt: number): ChatApprovals {
  const list = Array.isArray(reply?.approvals) ? reply.approvals : [];
  const byId = new Map<string, ChatApproval>();
  const order: string[] = [];
  for (const raw of list) {
    const approval = parseApproval(raw);
    if (!approval || byId.has(approval.id)) continue;
    byId.set(approval.id, approval);
    order.push(approval.id);
  }
  const serverMs = reply?.server_time ? Date.parse(reply.server_time) : Number.NaN;
  return { byId, order, offsetMs: Number.isNaN(serverMs) ? 0 : serverMs - receivedAt, receivedAt };
}

export function useChatApprovals(workflowId: string | null) {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<ChatApprovals, Error>({
    queryKey: chatApprovalsKey(workflowId ?? ''),
    queryFn: async () => {
      const reply = await sendRequest<ListReply>('list_approvals', { workflow_id: workflowId, status: 'recent', limit: 100 });
      if (reply?.success === false) throw new Error(reply.error || 'Could not load the drafts');
      return readApprovals(reply, Date.now());
    },
    enabled: isReady && Boolean(workflowId),
    // Runs make drafts while no chat is open: always look again on open.
    refetchOnMount: 'always',
    staleTime: 0,
  });
}

export type Decision = 'send' | 'undo' | 'discard' | 'restore' | 'retry';

export interface DecideInput {
  approval: ChatApproval;
  decision: Decision;
  text?: string;
  subject?: string;
  confirm?: boolean;
}

export class DecideError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

const DECIDE_ERRORS: Record<string, string> = {
  already_decided: 'That draft was already handled.',
  expired: 'That draft expired before it was sent.',
  cancelled: 'That draft was cancelled.',
  too_late: 'Too late: it has already gone.',
  approval_conflict: 'That draft changed meanwhile. Look again.',
  send_unavailable: 'Nothing can send it right now. Try again in a moment.',
  not_found: 'That draft is gone.',
  invalid_request: 'That can’t be sent as it is.',
};

export function decisionKey(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  } catch {
    // Not a secure context.
  }
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** What the card shows while the server answers. */
export function optimistic(approval: ChatApproval, decision: Decision, nowMs: number, offsetMs: number): ChatApproval {
  const serverNow = nowMs + offsetMs;
  const soon = (seconds: number) => new Date(serverNow + seconds * 1000).toISOString();
  switch (decision) {
    case 'send':
    case 'retry':
      return { ...approval, status: 'approved', undoUntil: soon(5), outcome: null };
    case 'undo':
    case 'restore':
      return { ...approval, status: 'pending', undoUntil: null, restoreUntil: null };
    case 'discard':
      return { ...approval, status: 'discarded', restoreUntil: approval.kind === 'gate' ? soon(5) : approval.expiresAt };
  }
}

type DecideReply = { success?: boolean; error?: string; detail?: string; approval?: unknown };

function replace(data: ChatApprovals | undefined, approval: ChatApproval): ChatApprovals | undefined {
  if (!data) return data;
  const byId = new Map(data.byId);
  byId.set(approval.id, approval);
  const order = data.order.includes(approval.id) ? data.order : [approval.id, ...data.order];
  return { ...data, byId, order };
}

export function useDecideApproval(workflowId: string, onError?: (error: DecideError, input: DecideInput) => void) {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  const key = chatApprovalsKey(workflowId);
  return useMutation<ChatApproval | null, DecideError, DecideInput, { previous: ChatApprovals | undefined }>({
    mutationFn: async ({ approval, decision, text, subject, confirm }) => {
      let reply: DecideReply | undefined;
      try {
        reply = await sendRequest<DecideReply>('decide_approval', {
          approval_id: approval.id,
          decision,
          decision_key: decisionKey(),
          ...(text !== undefined ? { text } : {}),
          ...(subject !== undefined ? { subject } : {}),
          ...(confirm ? { confirm: true } : {}),
        });
      } catch {
        throw new DecideError('transport', 'That didn’t reach the server. Try again.');
      }
      const current = parseApproval(reply?.approval);
      if (reply?.success === false) {
        if (current) queryClient.setQueryData<ChatApprovals>(key, (data) => replace(data, current));
        const code = reply.error || 'failed';
        throw new DecideError(code, reply.detail || DECIDE_ERRORS[code] || 'That didn’t work. Try again.');
      }
      return current;
    },
    onMutate: async ({ approval, decision }) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<ChatApprovals>(key);
      const offsetMs = previous?.offsetMs ?? 0;
      queryClient.setQueryData<ChatApprovals>(key, (data) => replace(data, optimistic(approval, decision, Date.now(), offsetMs)));
      return { previous };
    },
    onSuccess: (approval) => {
      if (approval) queryClient.setQueryData<ChatApprovals>(key, (data) => replace(data, approval));
    },
    onError: (error, input, context) => {
      // The refusal carried the draft as it is: keep that. Otherwise put
      // the card back.
      if (context && error.code === 'transport') queryClient.setQueryData(key, context.previous);
      onError?.(error, input);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: key });
    },
  });
}

export interface AskFirst {
  /** null: the workflow has no rule (its tool calls are never held). */
  askFirst: boolean | null;
  revision: number;
}

export function useAskFirst(workflowId: string | null) {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<AskFirst, Error>({
    queryKey: askFirstKey(workflowId ?? ''),
    queryFn: async () => {
      const reply = await sendRequest<{ success?: boolean; ask_first?: unknown; revision?: unknown; error?: string }>('get_ask_first', {
        workflow_id: workflowId,
      });
      if (reply?.success === false) throw new Error(reply.error || 'Could not read Ask first');
      return {
        askFirst: typeof reply?.ask_first === 'boolean' ? reply.ask_first : null,
        revision: typeof reply?.revision === 'number' ? reply.revision : 0,
      };
    },
    enabled: isReady && Boolean(workflowId),
    refetchOnMount: 'always',
    staleTime: 0,
  });
}

export interface SetAskFirstResult extends AskFirst {
  repliesGated: boolean;
  needsApply: boolean;
}

export function useSetAskFirst(workflowId: string, onError?: (message: string) => void) {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  const key = askFirstKey(workflowId);
  return useMutation<SetAskFirstResult, Error, boolean, { previous: AskFirst | undefined }>({
    mutationFn: async (askFirst) => {
      const current = queryClient.getQueryData<AskFirst>(key);
      const reply = await sendRequest<{
        success?: boolean;
        error?: string;
        ask_first?: unknown;
        revision?: unknown;
        replies_gated?: unknown;
        needs_apply?: unknown;
      }>('set_ask_first', {
        workflow_id: workflowId,
        ask_first: askFirst,
        ...(current ? { expected_revision: current.revision } : {}),
      });
      if (reply?.success === false) {
        throw new Error(reply.error === 'rule_conflict' ? 'Ask first changed meanwhile. Look again.' : 'Couldn’t change Ask first. Try again.');
      }
      return {
        askFirst: typeof reply?.ask_first === 'boolean' ? reply.ask_first : askFirst,
        revision: typeof reply?.revision === 'number' ? reply.revision : 0,
        repliesGated: reply?.replies_gated === true,
        needsApply: reply?.needs_apply === true,
      };
    },
    onMutate: async (askFirst) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<AskFirst>(key);
      queryClient.setQueryData<AskFirst>(key, { askFirst, revision: previous?.revision ?? 0 });
      return { previous };
    },
    onSuccess: (result) => {
      queryClient.setQueryData<AskFirst>(key, { askFirst: result.askFirst, revision: result.revision });
    },
    onError: (error, _value, context) => {
      if (context) queryClient.setQueryData(key, context.previous);
      onError?.(error.message);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: key });
    },
  });
}

interface ApprovalEnvelope {
  specversion?: string;
  type?: string;
  data?: { workflow_id?: string; approval_id?: string } | null;
}

/** Apply one `approval_lifecycle` envelope. Exported for tests. */
export function applyApprovalEvent(queryClient: QueryClient, event: ApprovalEnvelope): void {
  if (!event || event.specversion !== '1.0') return;
  const workflowId = event.data?.workflow_id;
  if (!workflowId) return;
  void queryClient.invalidateQueries({ queryKey: chatApprovalsKey(workflowId) });
}

/** Keep a chat's drafts current while it is mounted. */
export function useApprovalEvents(): void {
  const queryClient = useQueryClient();
  const { addEventListener } = useWebSocketActions();
  useEffect(() => addEventListener('approval_lifecycle', (data) => applyApprovalEvent(queryClient, data)), [addEventListener, queryClient]);
}
