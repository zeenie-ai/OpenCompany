/**
 * The model picker in Home's employee chat (docs-internal/chat_protocol.md,
 * "Model and thinking"): what one employee's chat offers (`get_chat_models`:
 * Auto, the models of each connected provider, the thinking levels) and the
 * owner's choice, saved with their settings (`chat_model` / `chat_effort`).
 * Every row's words come from the server: Auto's row says what it uses now,
 * a model's row names it and its provider, and a row the owner can't pick
 * says why.
 *
 * A message, an edit or a retry carries the choice as `options.model` /
 * `options.effort` (send.ts `sendOptions`); the server resolves Auto and
 * refuses a model that can't answer.
 *
 * The hire composer's model picker has rows of the same shape and its own
 * saved choice (`HIRE_CHOICE`).
 */

import { useQuery, type QueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import {
  SettingsSaveError,
  USER_SETTINGS_QUERY_KEY,
  useSaveUserSettingsMutationCore,
  useUserSettingsQueryCore,
  type UserSettings,
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
  /** The provider a model runs on, and its name (the row's group); null
   *  for Auto. */
  provider: string | null;
  providerName: string | null;
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
    provider: z.string().nullable().catch(null),
    provider_name: z.string().nullable().catch(null),
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
      provider: raw.provider,
      providerName: raw.provider_name,
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

/** Where a picker's choice is saved with the owner's settings. */
export interface ChoiceKeys {
  model: string;
  effort: string;
}

/** The chat's picker: the model and level of the employee answering. */
export const CHAT_CHOICE: ChoiceKeys = { model: 'chat_model', effort: 'chat_effort' };
/** The hire composer's: the model a new employee's setup is written with and
 *  that it runs on, and each of its agents' Effort. */
export const HIRE_CHOICE: ChoiceKeys = { model: 'hire_model', effort: 'hire_effort' };

const savedModel = z.string().min(1).catch(AUTO);
const savedEffort = effortId.catch('');

/** A picker's choice in the owner's settings. */
export function choiceOf(settings: UserSettings, keys: ChoiceKeys): ChatChoice {
  return { model: savedModel.parse(settings[keys.model]), effort: savedEffort.parse(settings[keys.effort]) };
}

/** The choice the cached settings hold as a request goes; null until they
 *  have loaded. */
export function savedChoice(queryClient: QueryClient, keys: ChoiceKeys): ChatChoice | null {
  const settings = queryClient.getQueryData<UserSettings>(USER_SETTINGS_QUERY_KEY);
  return settings ? choiceOf(settings, keys) : null;
}

/** A picked model's provider and model (its id is `provider::model`); null
 *  for Auto. */
export function modelParts(id: string): { provider: string; model: string } | null {
  const at = id.indexOf('::');
  return at > 0 ? { provider: id.slice(0, at), model: id.slice(at + 2) } : null;
}

/**
 * A picker's choice, from the owner's settings (null until they have loaded),
 * and `choose`, which saves a change at once and puts it back when the save
 * fails; `onRefused` hears why, in the server's words when it gave them.
 */
export function useModelChoice(keys: ChoiceKeys, onRefused: (message: string) => void) {
  const { sendRequest, isReady } = useWebSocketActions();
  const settings = useUserSettingsQueryCore(sendRequest, isReady);
  const save = useSaveUserSettingsMutationCore(sendRequest);
  const choice = settings.data ? choiceOf(settings.data, keys) : null;
  const choose = (change: Partial<ChatChoice>) =>
    save.mutate(
      {
        ...(change.model !== undefined ? { [keys.model]: change.model } : {}),
        ...(change.effort !== undefined ? { [keys.effort]: change.effort } : {}),
      },
      {
        onError: (error) =>
          onRefused(error instanceof SettingsSaveError && error.detail ? error.detail : 'Couldn’t save that. Try again.'),
      },
    );
  return { choice, pending: settings.isPending, choose };
}
