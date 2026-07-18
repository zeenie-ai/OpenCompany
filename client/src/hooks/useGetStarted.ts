/**
 * Get Started checklist state.
 *
 * Each item's completion is `persisted latch || live signal`. Latches are
 * one-way booleans in `user_settings` (written once via the shared settings
 * mutation, guarded by the flag itself) so completion survives the live
 * signal disappearing later (key deleted, chat cleared, theme reverted).
 */

import { useEffect, useRef } from 'react';
import { useUserSettingsQuery, useSaveUserSettingsMutation } from './useUserSettingsQuery';
import { useStoredProviderCount } from './useCatalogueQuery';
import { useWebSocket } from '../contexts/WebSocketContext';
import { useTheme } from '../contexts/ThemeContext';
import { useAppStore } from '../store/useAppStore';
import {
  GET_STARTED_ITEMS,
  EXAMPLE_WORKFLOW_NAMES,
  type GetStartedItemId,
} from '../components/onboarding/getStartedItems';

const LATCH_FIELDS: Partial<Record<GetStartedItemId, string>> = {
  'add-key': 'getting_started_added_key',
  'chat-example': 'getting_started_ran_example',
  'build-workflow': 'getting_started_built_workflow',
  'try-theme': 'getting_started_tried_theme',
};

export interface GetStartedItemState {
  id: GetStartedItemId;
  completed: boolean;
}

export interface GetStartedState {
  visible: boolean;
  items: GetStartedItemState[];
  completedCount: number;
  totalCount: number;
  dismiss: () => void;
  restore: () => void;
}

export function useGetStarted(): GetStartedState {
  const settingsQuery = useUserSettingsQuery();
  const saveSettings = useSaveUserSettingsMutation();
  const settings = settingsQuery.data;

  // Live signals.
  const storedProviderCount = useStoredProviderCount();
  const { chatMessages } = useWebSocket();
  const { theme } = useTheme();
  const initialThemeRef = useRef(theme);
  const currentWorkflowName = useAppStore((s) => s.currentWorkflow?.name);
  const currentWorkflowNodeCount = useAppStore((s) => s.currentWorkflow?.nodes.length ?? 0);
  const hasUnsavedChanges = useAppStore((s) => s.hasUnsavedChanges);

  const liveSignals: Partial<Record<GetStartedItemId, boolean>> = {
    'add-key': storedProviderCount > 0,
    'chat-example': chatMessages.some((m) => m.role === 'assistant'),
    'build-workflow':
      !hasUnsavedChanges &&
      currentWorkflowNodeCount > 0 &&
      currentWorkflowName !== undefined &&
      !EXAMPLE_WORKFLOW_NAMES.some(
        (example) => example.toLowerCase() === currentWorkflowName.toLowerCase(),
      ),
    'try-theme': theme !== initialThemeRef.current,
  };

  const completedById = Object.fromEntries(
    GET_STARTED_ITEMS.map((item) => {
      const latchField = LATCH_FIELDS[item.id];
      const latched = latchField ? Boolean(settings?.[latchField]) : true;
      return [item.id, latched || Boolean(liveSignals[item.id])];
    }),
  ) as Record<GetStartedItemId, boolean>;

  // Persist newly-true live signals as one-way latches.
  const { mutate: saveMutate } = saveSettings;
  useEffect(() => {
    if (!settingsQuery.isSuccess) return;
    for (const [itemId, field] of Object.entries(LATCH_FIELDS)) {
      if (liveSignals[itemId as GetStartedItemId] && !settings?.[field]) {
        saveMutate({ [field]: true });
      }
    }
    // The settings flag is the dedup guard once the mutation settles
    // (onSuccess patches the query cache). Renders before settlement may
    // re-send the same boolean patch — harmless, the write is idempotent.
  });

  const items = GET_STARTED_ITEMS.map((item) => ({
    id: item.id,
    completed: completedById[item.id],
  }));
  const completedCount = items.filter((item) => item.completed).length;

  return {
    visible:
      settingsQuery.isSuccess &&
      Boolean(settings?.onboarding_completed) &&
      !settings?.getting_started_dismissed,
    items,
    completedCount,
    totalCount: items.length,
    dismiss: () => saveMutate({ getting_started_dismissed: true }),
    restore: () => saveMutate({ getting_started_dismissed: false }),
  };
}
