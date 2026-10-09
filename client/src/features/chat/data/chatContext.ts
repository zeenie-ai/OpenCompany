/**
 * What a chat's message box offers (`get_chat_context`): slash commands (the
 * generic ones and those the employee's apps add; the ones marked `suggest`
 * also show as suggestions in an empty chat), whether files can be attached
 * (a workflow's chat), whether the Web chip has search to turn off, whether
 * the employee takes the model picker's choice (`model_choice`: its agent
 * runs as an AgentWorkflow), and the limits. Dictation (`dictation_status`): whether a recording can be turned
 * into text, which needs a speech provider's key; `transcribe` sends one.
 */

import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { useWebSocketActions } from '@/contexts/WebSocketContext';

export interface ChatCommand {
  command: string;
  description: string;
  fill: string;
  suggest: boolean;
}

export interface ChatContext {
  commands: ChatCommand[];
  attachments: boolean;
  web: boolean;
  modelChoice: boolean;
  maxAttachments: number;
}

const commandSchema = z
  .object({
    command: z.string().regex(/^\/[a-z0-9-]{1,30}$/),
    description: z.string().catch(''),
    fill: z.string().catch(''),
    suggest: z.boolean().catch(false),
  })
  .transform((command): ChatCommand => command);

const contextSchema = z
  .object({
    commands: z.array(z.unknown()).catch([]),
    capabilities: z
      .object({ attachments: z.boolean().catch(false), web: z.boolean().catch(false), model_choice: z.boolean().catch(false) })
      .catch({ attachments: false, web: false, model_choice: false }),
    limits: z.object({ max_attachments: z.number().int().positive().catch(6) }).catch({ max_attachments: 6 }),
  })
  .transform(
    (raw): ChatContext => ({
      commands: raw.commands.flatMap((item) => {
        const parsed = commandSchema.safeParse(item);
        return parsed.success ? [parsed.data] : [];
      }),
      attachments: raw.capabilities.attachments,
      web: raw.capabilities.web,
      modelChoice: raw.capabilities.model_choice,
      maxAttachments: raw.limits.max_attachments,
    }),
  );

export const NO_CHAT_CONTEXT: ChatContext = { commands: [], attachments: false, web: false, modelChoice: false, maxAttachments: 6 };

export function parseChatContext(reply: unknown): ChatContext {
  const parsed = contextSchema.safeParse(reply);
  return parsed.success ? parsed.data : NO_CHAT_CONTEXT;
}

export function useChatContext(sessionId: string) {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<ChatContext, Error>({
    queryKey: ['chatContext', sessionId],
    enabled: isReady && Boolean(sessionId),
    staleTime: 60_000,
    queryFn: async () => {
      const reply = await sendRequest<{ success?: boolean; error?: string }>('get_chat_context', { session_id: sessionId });
      if (reply?.success === false) throw new Error(reply.error || 'read_failed');
      return parseChatContext(reply);
    },
  });
}

/** Whether the owner can dictate in this chat. */
export function useDictation(sessionId: string, enabled: boolean) {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<boolean, Error>({
    queryKey: ['dictation', sessionId],
    enabled: enabled && isReady && Boolean(sessionId),
    staleTime: 60_000,
    queryFn: async () => {
      const reply = await sendRequest<{ success?: boolean; available?: boolean }>('dictation_status', { session_id: sessionId });
      return reply?.success !== false && reply?.available === true;
    },
  });
}

/** Turn a recording uploaded to the chat (`uploads/...`) into text. */
export async function transcribe(
  sendRequest: <T>(type: string, data?: Record<string, unknown>) => Promise<T>,
  sessionId: string,
  path: string,
): Promise<string> {
  const reply = await sendRequest<{ success?: boolean; error?: string; text?: string }>('transcribe_audio', { session_id: sessionId, path });
  if (!reply || reply.success === false) throw new Error(reply?.error || 'transcribe_failed');
  return typeof reply.text === 'string' ? reply.text : '';
}

/** Whether this browser can record from a microphone at all. */
export function canRecord(): boolean {
  return typeof window !== 'undefined' && typeof window.MediaRecorder === 'function' && Boolean(navigator.mediaDevices?.getUserMedia);
}
