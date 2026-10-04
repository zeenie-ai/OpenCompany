/**
 * Changing the conversation's path (docs-internal/chat_protocol.md,
 * "Branches"): editing one of the owner's messages (`edit_chat_message`),
 * trying the latest answer again (`regenerate_chat_reply`), moving to
 * another version of a message or an answer (`switch_chat_branch`), and
 * rating an answer (`set_chat_feedback`).
 *
 * Each names the thread's revision it was made against, so a change made
 * meanwhile elsewhere (another tab, the employee answering) refuses it
 * (`revision_conflict`) instead of moving a path the owner did not see. An
 * edit or a retry starts a run, registered in the run store at once, like a
 * sent message's. Refusals reach `onRefused` with the server's code
 * (`ChatBranchError`); the thread is read again either way.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { queryKeys } from '@/lib/queryConfig';
import { useChatRunStore } from '@/stores/chatRunStore';
import type { ChatThreadData, Feedback } from './schemas';
import { chatThreadKey, type ThreadScope } from './thread';

/** A change to the conversation that did not go through: `code` is the
 *  server's (`revision_conflict`, `run_in_progress`, `cannot_rewind`,
 *  `older_generation`, `branch_unavailable`, `not_editable`, `not_running`,
 *  ...) or `transport`. */
export class ChatBranchError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = 'ChatBranchError';
    this.code = code;
  }
}

type Reply = {
  success?: boolean;
  error?: string;
  run_id?: string | null;
  message_id?: string | null;
  delivery?: string;
};

export interface StartedRun {
  runId: string | null;
  /** The owner's message the run answers. */
  messageId: string | null;
  delivery: 'now' | 'queued' | null;
}

function useBranchRequest(sessionId: string, scope: ThreadScope) {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  const revision = () => queryClient.getQueryData<ChatThreadData>(chatThreadKey(sessionId, scope))?.thread.revision ?? 0;
  const request = async (kind: string, data: Record<string, unknown>): Promise<Reply> => {
    let reply: Reply | undefined;
    try {
      reply = await sendRequest<Reply>(kind, { session_id: sessionId, expected_revision: revision(), ...data });
    } catch {
      throw new ChatBranchError('transport');
    }
    if (!reply || reply.success === false) throw new ChatBranchError(reply?.error || 'failed');
    return reply;
  };
  const refetch = () => queryClient.invalidateQueries({ queryKey: queryKeys.chatThread.bySession(sessionId).queryKey });
  return { request, refetch, queryClient };
}

function started(sessionId: string, reply: Reply): StartedRun {
  const result: StartedRun = {
    runId: reply.run_id ?? null,
    messageId: reply.message_id ?? null,
    delivery: reply.delivery === 'queued' ? 'queued' : reply.delivery === 'now' ? 'now' : null,
  };
  if (result.runId && result.messageId) {
    useChatRunStore.getState().admit(sessionId, {
      runId: result.runId,
      userMessageId: result.messageId,
      state: result.delivery === 'queued' ? 'queued' : 'pending',
    });
  }
  return result;
}

/** Edit one of the owner's messages: the edit goes after the same earlier
 *  message, as a new version of it, and the employee answers it. */
export function useEditChatMessage(sessionId: string, scope: ThreadScope, onRefused?: (error: ChatBranchError) => void) {
  const { request, refetch } = useBranchRequest(sessionId, scope);
  return useMutation<StartedRun, ChatBranchError, { messageId: string; text: string; clientMessageId: string }>({
    mutationFn: async ({ messageId, text, clientMessageId }) =>
      started(sessionId, await request('edit_chat_message', { message_id: messageId, message: text, client_message_id: clientMessageId })),
    onError: (error) => onRefused?.(error),
    onSettled: refetch,
  });
}

/** Try the latest answer again: a new version of it, answering the same
 *  message. */
export function useRegenerateChatReply(sessionId: string, scope: ThreadScope, onRefused?: (error: ChatBranchError) => void) {
  const { request, refetch } = useBranchRequest(sessionId, scope);
  return useMutation<StartedRun, ChatBranchError, { messageId: string }>({
    mutationFn: async ({ messageId }) => started(sessionId, await request('regenerate_chat_reply', { message_id: messageId })),
    onError: (error) => onRefused?.(error),
    onSettled: refetch,
  });
}

/** Show another version of a message or an answer, and the conversation
 *  that followed it. */
export function useSwitchChatBranch(sessionId: string, scope: ThreadScope, onRefused?: (error: ChatBranchError) => void) {
  const { request, refetch } = useBranchRequest(sessionId, scope);
  return useMutation<void, ChatBranchError, { messageId: string }>({
    mutationFn: async ({ messageId }) => {
      await request('switch_chat_branch', { message_id: messageId });
    },
    onError: (error) => onRefused?.(error),
    onSettled: refetch,
  });
}

/** Rate an answer good or bad, or take the rating back (`null`). Shown at
 *  once; put back when the server refuses. The employee reads a rating
 *  before its next answer. */
export function useSetChatFeedback(sessionId: string, scope: ThreadScope, onRefused?: (error: ChatBranchError) => void) {
  const { request, refetch, queryClient } = useBranchRequest(sessionId, scope);
  const key = chatThreadKey(sessionId, scope);
  const setLocal = (messageId: string, value: Feedback | null) =>
    queryClient.setQueryData<ChatThreadData>(key, (data) =>
      data ? { ...data, messages: data.messages.map((message) => (message.id === messageId ? { ...message, feedback: value } : message)) } : data,
    );
  return useMutation<void, ChatBranchError, { messageId: string; value: Feedback | null }, { before: Feedback | null }>({
    mutationFn: async ({ messageId, value }) => {
      await request('set_chat_feedback', { message_id: messageId, value });
    },
    onMutate: ({ messageId, value }) => {
      const before = queryClient.getQueryData<ChatThreadData>(key)?.messages.find((message) => message.id === messageId)?.feedback ?? null;
      setLocal(messageId, value);
      return { before };
    },
    onError: (error, { messageId }, context) => {
      setLocal(messageId, context?.before ?? null);
      onRefused?.(error);
    },
    onSettled: refetch,
  });
}
