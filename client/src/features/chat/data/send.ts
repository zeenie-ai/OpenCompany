/**
 * Sending the owner's message (`send_chat_message`) and clearing a
 * conversation (`clear_chat_messages`), for every host.
 *
 * A message shows in the thread at once, marked pending, and becomes the
 * saved row once the server answers (the refetch finds it by its
 * `client_message_id`). The run it started is registered in the run store
 * straight away, so the thread shows the employee working before the run's
 * first event arrives. A refusal (`run_in_progress`, `not_running`,
 * `save_failed`, ...) or a failure in transit takes the message back out
 * and puts its text back in the box (composerStore `restore`, so a retry
 * after a failure in transit is the same message); `onRefused` decides what
 * to tell the owner (`ChatSendError`); `onSent` hears that a message went.
 * Both run at the hook level, so they still happen when the owner has moved
 * to another conversation meanwhile.
 *
 * Files from the box (state/attachmentStore.ts) go as the paths their
 * uploads returned; the server rebuilds them. A send that does not go
 * through puts them back in the box too.
 *
 * What the owner chose for the message rides `options` (`sendOptions`, the
 * same for a message, an edit and a retry): `web: false` keeps the employee
 * off web search, and Home's model picker adds the model and the effort
 * (data/models.ts). A model that can't answer is refused with
 * `model_unavailable`, whose `detail` the owner reads.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { queryKeys } from '@/lib/queryConfig';
import { useChatRunStore } from '@/stores/chatRunStore';
import { useAttachmentStore, type BoxAttachment } from '../state/attachmentStore';
import { useComposerStore, type TakenDraft } from '../state/composerStore';
import type { ChatChoice } from './models';
import type { ChatMessage, ChatThreadData } from './schemas';
import { chatThreadKey, type ThreadScope } from './thread';

export type Delivery = 'now' | 'queued';

export interface SendResult {
  messageId: string;
  runId: string | null;
  /** Null for the editor's chat with no workflow open. */
  delivery: Delivery | null;
}

/** A send that did not go through. `transport`: it may or may not have
 *  reached the server, so a retry should reuse the same `client_message_id`.
 *  `detail`: the server's own words, where it gave them. */
export class ChatSendError extends Error {
  readonly code: string;
  readonly runId: string | null;
  readonly transport: boolean;
  readonly detail: string | null;

  constructor(
    code: string,
    { runId = null, transport = false, detail = null }: { runId?: string | null; transport?: boolean; detail?: string | null } = {},
  ) {
    super(code);
    this.name = 'ChatSendError';
    this.code = code;
    this.runId = runId;
    this.transport = transport;
    this.detail = detail;
  }
}

type SendReply = {
  success?: boolean;
  error?: string;
  detail?: string;
  run_id?: string | null;
  message_id?: string;
  delivery?: string;
};

/** What a message, an edit or a retry carries as `options`. */
export interface SendOptions {
  web?: false;
  model?: string;
  effort?: 'low' | 'high';
}

/** The options for the next message: Web when the owner turned it off, and
 *  the picker's choice where the chat has one (`choice`, null otherwise).
 *  Undefined when there is nothing to say. */
export function sendOptions(web: boolean, choice: ChatChoice | null): SendOptions | undefined {
  const options: SendOptions = {};
  if (!web) options.web = false;
  if (choice) {
    options.model = choice.model;
    if (choice.effort) options.effort = choice.effort;
  }
  return Object.keys(options).length > 0 ? options : undefined;
}

/** A button pressed in an interface the employee showed: sent back to it
 *  as the owner's next turn (`send_chat_message`'s `ui_event`). */
export interface UiEventSend {
  partId: string;
  elementId: string;
  action: string;
  params: Record<string, unknown>;
}

/** What one send carries: the text shown as the owner's message, and for a
 *  button press, the press. */
export type SendDraft = TakenDraft & {
  uiEvent?: UiEventSend;
  /** Finished uploads from the box. */
  attachments?: BoxAttachment[];
  options?: SendOptions;
};

function refsOf(attachments: readonly BoxAttachment[] | undefined) {
  return (attachments ?? []).flatMap((item) => (item.ref ? [item.ref] : []));
}

function localId(clientMessageId: string): string {
  return `local:${clientMessageId}`;
}

export function useSendChatMessage(
  sessionId: string,
  scope: ThreadScope,
  onRefused?: (error: ChatSendError) => void,
  onSent?: () => void,
) {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  const key = chatThreadKey(sessionId, scope);

  const replace = (update: (messages: ChatMessage[]) => ChatMessage[]) =>
    queryClient.setQueryData<ChatThreadData>(key, (data) => (data ? { ...data, messages: update(data.messages) } : data));

  return useMutation<SendResult, ChatSendError, SendDraft>({
    mutationFn: async ({ text, clientMessageId, uiEvent, attachments, options }) => {
      let reply: SendReply | undefined;
      try {
        reply = await sendRequest<SendReply>('send_chat_message', {
          session_id: sessionId,
          message: text,
          role: 'user',
          timestamp: new Date().toISOString(),
          client_message_id: clientMessageId,
          ...(attachments?.length ? { attachments: refsOf(attachments).map((ref) => ({ path: ref.path })) } : {}),
          ...(options ? { options } : {}),
          ...(uiEvent
            ? {
                ui_event: {
                  part_id: uiEvent.partId,
                  element_id: uiEvent.elementId,
                  action: uiEvent.action,
                  params: uiEvent.params,
                },
              }
            : {}),
        });
      } catch {
        throw new ChatSendError('transport', { transport: true });
      }
      if (reply?.success === false || !reply?.message_id) {
        throw new ChatSendError(reply?.error || 'send_failed', { runId: reply?.run_id ?? null, detail: reply?.detail ?? null });
      }
      return {
        messageId: reply.message_id,
        runId: reply.run_id ?? null,
        delivery: reply.delivery === 'queued' ? 'queued' : reply.delivery === 'now' ? 'now' : null,
      };
    },
    onMutate: async ({ text, clientMessageId, uiEvent, attachments }) => {
      await queryClient.cancelQueries({ queryKey: key });
      const pending: ChatMessage = {
        id: localId(clientMessageId),
        legacyId: null,
        role: 'user',
        kind: uiEvent ? 'action' : 'text',
        text,
        timestamp: new Date().toISOString(),
        runKey: null,
        runId: null,
        parentId: null,
        status: 'complete',
        attachments: refsOf(attachments),
        parts: {},
        clientMessageId,
        run: null,
        siblings: null,
        editable: false,
        feedback: null,
        pending: true,
      };
      replace((messages) => [...messages.filter((message) => message.id !== pending.id), pending]);
    },
    onSuccess: (result, { clientMessageId, attachments }) => {
      // The files are on the server now: the box's thumbnails can go.
      for (const item of attachments ?? []) if (item.preview) URL.revokeObjectURL(item.preview);
      replace((messages) =>
        // A resent message the server already had is in the thread as that
        // row: the local copy goes rather than show it twice.
        messages.some((message) => message.id === result.messageId && !message.pending)
          ? messages.filter((message) => message.id !== localId(clientMessageId))
          : messages.map((message) =>
              message.id === localId(clientMessageId)
                ? { ...message, id: result.messageId, runId: result.runId, pending: false }
                : message,
            ),
      );
      if (result.runId) {
        useChatRunStore.getState().admit(sessionId, {
          runId: result.runId,
          userMessageId: result.messageId,
          state: result.delivery === 'queued' ? 'queued' : 'pending',
        });
      }
      onSent?.();
    },
    onError: (error, draft) => {
      replace((messages) => messages.filter((message) => message.id !== localId(draft.clientMessageId)));
      // A button press is not the owner's writing: nothing goes back in the box.
      if (!draft.uiEvent) useComposerStore.getState().restore(sessionId, draft, error.transport);
      if (draft.attachments?.length) useAttachmentStore.getState().restore(sessionId, draft.attachments);
      onRefused?.(error);
    },
    // The refetch swaps the local copy for the saved row.
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.chatThread.bySession(sessionId).queryKey }),
  });
}

/** "New conversation": clears the thread, and the employee's memory of it. */
export function useClearChat(sessionId: string) {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  return useMutation<void, Error>({
    mutationFn: async () => {
      const reply = await sendRequest<{ success?: boolean; error?: string }>('clear_chat_messages', { session_id: sessionId });
      if (reply?.success === false) throw new Error(reply.error || 'clear_failed');
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.chatThread.bySession(sessionId).queryKey }),
  });
}
