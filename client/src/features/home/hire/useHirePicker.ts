/**
 * The hire composer's model picker: the employee chat's
 * (features/chat/composer/ModelPicker.tsx) over the hire's own rows and
 * choice. The rows (`get_hire_models`) are Auto, saying which model a hire
 * gets without a pick, each connected provider's models and the thinking
 * levels; the choice is saved with the owner's settings (`hire_model` /
 * `hire_effort`). The picked model writes a new employee's setup and is the
 * one every agent of theirs runs on, and the level becomes each agent's
 * Effort (genui/draftStore.ts and genui/hirePayload.ts send them).
 */

import { useQuery } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { HIRE_CHOICE, parseChatModels, useModelChoice, type ChatModels } from '@/features/chat';
import { useConnectors } from '../data/connectors';
import { pillToast } from '../ui/pillToast';
import type { ComposerModelPicker } from './Composer';

export const HIRE_MODELS_QUERY_KEY = ['hireModels'] as const;

/** The picker's rows. Read again each time it opens (`refetch`), since what
 *  Auto uses and which providers are connected change elsewhere. */
export function useHireModels(enabled: boolean) {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<ChatModels, Error>({
    queryKey: HIRE_MODELS_QUERY_KEY,
    enabled: enabled && isReady,
    staleTime: 30_000,
    queryFn: async () => {
      const reply = await sendRequest<{ success?: boolean; error?: string }>('get_hire_models', {});
      if (reply?.success === false) throw new Error(reply.error || 'read_failed');
      return parseChatModels(reply);
    },
  });
}

/** What the composer's picker shows. It has no rows, so no button, until
 *  an AI model is set up; the composer wraps its box in the picker from the
 *  first render all the same, so the box is never mounted again. */
export function useHirePicker(): ComposerModelPicker {
  const { hasAi } = useConnectors();
  const models = useHireModels(hasAi);
  const { choice, choose } = useModelChoice(HIRE_CHOICE, (message) => pillToast(message, { tone: 'error' }));
  return {
    models: hasAi ? models.data : undefined,
    choice,
    onModel: (model) => choose({ model }),
    onEffort: (effort) => choose({ effort }),
    onOpen: () => void models.refetch(),
  };
}
