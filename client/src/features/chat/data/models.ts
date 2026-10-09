/**
 * The model picker in Home's employee chat (docs-internal/chat_protocol.md,
 * "Model and thinking"): what one employee's chat offers (`get_chat_models`:
 * Auto, the offered models, the thinking levels) and the owner's choice,
 * saved with their settings (`chat_model` / `chat_effort`). Every word comes
 * from the server: Auto's row says what it uses now, and a row the owner
 * can't pick says why.
 *
 * A message, an edit or a retry carries the choice as `options.model` /
 * `options.effort` (send.ts `sendOptions`); the server resolves Auto and
 * refuses a model that can't answer.
 */

import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import {
  SettingsSaveError,
  useSaveUserSettingsMutationCore,
  useUserSettingsQueryCore,
} from '@/hooks/useUserSettingsQuery';

/** How hard the model thinks: Quick (`low`), Balanced (none sent) or
 *  Thorough (`high`). */
export type Effort = '' | 'low' | 'high';

export interface ChatModel {
  /** `auto`, or `provider::model`. */
  id: string;
  name: string;
  /** On the picker's button. */
  short: string;
  description: string;
  /** It takes the thinking levels; otherwise `effortNote` says so. */
  effort: boolean;
  effortNote: string | null;
  available: boolean;
  /** Why it can't be picked. */
  reason: string | null;
}

export interface ChatLevel {
  id: Effort;
  label: string;
  hint: string;
}

export interface ChatModels {
  auto: ChatModel;
  models: ChatModel[];
  levels: ChatLevel[];
}

export interface ChatChoice {
  model: string;
  effort: Effort;
}

export const AUTO = 'auto';

const effortId = z.enum(['', 'low', 'high']);

const modelSchema = z
  .object({
    id: z.string().min(1),
    name: z.string(),
    short: z.string(),
    description: z.string().catch(''),
    effort: z.boolean().catch(false),
    effort_note: z.string().nullable().catch(null),
    available: z.boolean().catch(false),
    reason: z.string().nullable().catch(null),
  })
  .transform(
    (raw): ChatModel => ({
      id: raw.id,
      name: raw.name,
      short: raw.short,
      description: raw.description,
      effort: raw.effort,
      effortNote: raw.effort_note,
      available: raw.available,
      reason: raw.reason,
    }),
  );

const modelsSchema = z
  .object({
    auto: modelSchema,
    models: z.array(modelSchema),
    efforts: z.array(z.object({ id: effortId, label: z.string(), hint: z.string().catch('') })).min(1),
  })
  .transform((raw): ChatModels => ({ auto: raw.auto, models: raw.models, levels: raw.efforts }));

export function parseChatModels(reply: unknown): ChatModels {
  return modelsSchema.parse(reply);
}

/** One employee's picker. Read again each time it opens (`refetch`), since
 *  what Auto uses and which providers are connected change elsewhere. */
export function useChatModels(sessionId: string, enabled: boolean) {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<ChatModels, Error>({
    queryKey: ['chatModels', sessionId],
    enabled: enabled && isReady && sessionId !== 'default',
    staleTime: 30_000,
    queryFn: async () => {
      const reply = await sendRequest<{ success?: boolean; error?: string }>('get_chat_models', { session_id: sessionId });
      if (reply?.success === false) throw new Error(reply.error || 'read_failed');
      return parseChatModels(reply);
    },
  });
}

const choiceSchema = z.object({ chat_model: z.string().min(1).catch(AUTO), chat_effort: effortId.catch('') });

/**
 * The owner's choice, from their settings (null until they have loaded), and
 * `choose`, which saves a change at once and puts it back when the save
 * fails; `onRefused` hears why, in the server's words when it gave them.
 */
export function useChatChoice(onRefused: (message: string) => void) {
  const { sendRequest, isReady } = useWebSocketActions();
  const settings = useUserSettingsQueryCore(sendRequest, isReady);
  const save = useSaveUserSettingsMutationCore(sendRequest);
  const parsed = settings.data ? choiceSchema.parse(settings.data) : null;
  const choice: ChatChoice | null = parsed ? { model: parsed.chat_model, effort: parsed.chat_effort } : null;
  const choose = (change: Partial<ChatChoice>) =>
    save.mutate(
      {
        ...(change.model !== undefined ? { chat_model: change.model } : {}),
        ...(change.effort !== undefined ? { chat_effort: change.effort } : {}),
      },
      {
        onError: (error) =>
          onRefused(error instanceof SettingsSaveError && error.detail ? error.detail : 'Couldn’t save that. Try again.'),
      },
    );
  return { choice, pending: settings.isPending, choose };
}
