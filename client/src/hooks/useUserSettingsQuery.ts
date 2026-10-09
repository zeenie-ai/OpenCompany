/**
 * User settings query + save mutation.
 *
 * The `user_settings` row holds onboarding state, UI defaults, and a
 * handful of other per-user prefs. Owning it in TanStack Query means
 * onboarding, settings panel, and any future consumer share one cached
 * read instead of duplicating useState+useEffect bootstraps.
 */

import {
  useQuery,
  useMutation,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useWebSocket } from '../contexts/WebSocketContext';

export type UserSettings = Record<string, any>;

export const USER_SETTINGS_QUERY_KEY = ['userSettings'] as const;

type SendRequest = <T = any>(type: string, data?: Record<string, any>, timeoutMs?: number) => Promise<T>;

export function useUserSettingsQuery(): UseQueryResult<UserSettings, Error> {
  const { sendRequest, isReady } = useWebSocket();
  return useUserSettingsQueryCore(sendRequest, isReady);
}

/** The settings query without a context read, for callers holding the
 *  stable `useWebSocketActions()` (Normal mode). Same cache. */
export function useUserSettingsQueryCore(sendRequest: SendRequest, isReady: boolean): UseQueryResult<UserSettings, Error> {
  return useQuery<UserSettings, Error>({
    queryKey: USER_SETTINGS_QUERY_KEY,
    queryFn: async () => {
      const response = await sendRequest<{ settings: UserSettings }>(
        'get_user_settings',
        {},
      );
      return response?.settings ?? {};
    },
    enabled: isReady,
    staleTime: 60_000,
  });
}

export function useSaveUserSettingsMutation() {
  const { sendRequest } = useWebSocket();
  return useSaveUserSettingsMutationCore(sendRequest);
}

/** A save the server refused (`success: false`): `code` is its error, and
 *  `detail`, when it gave one, says why in the owner's words (a chat model
 *  the picker no longer offers). */
export class SettingsSaveError extends Error {
  readonly code: string;
  readonly detail: string | null;

  constructor(code: string, detail: string | null = null) {
    super(detail || code);
    this.name = 'SettingsSaveError';
    this.code = code;
    this.detail = detail;
  }
}

type SaveReply = { success?: boolean; error?: string; detail?: string };

/** The save mutation without a context read (see useUserSettingsQueryCore).
 *  The change shows in the cached settings at once and is put back when the
 *  save fails or the server refuses it (`SettingsSaveError`). */
export function useSaveUserSettingsMutationCore(sendRequest: SendRequest) {
  const qc = useQueryClient();
  return useMutation<UserSettings, Error, UserSettings, { previous: UserSettings | undefined }>({
    mutationFn: async (patch) => {
      const reply = await sendRequest<SaveReply>('save_user_settings', { settings: patch });
      if (reply?.success === false) throw new SettingsSaveError(reply.error || 'save_failed', reply.detail ?? null);
      return patch;
    },
    onMutate: async (patch) => {
      await qc.cancelQueries({ queryKey: USER_SETTINGS_QUERY_KEY });
      const previous = qc.getQueryData<UserSettings>(USER_SETTINGS_QUERY_KEY);
      qc.setQueryData<UserSettings>(USER_SETTINGS_QUERY_KEY, (prev) => ({ ...(prev ?? {}), ...patch }));
      return { previous };
    },
    onError: (_error, _patch, context) => {
      qc.setQueryData(USER_SETTINGS_QUERY_KEY, context?.previous);
      // And read what the server kept (there was nothing to put back when
      // the settings had not loaded yet).
      void qc.invalidateQueries({ queryKey: USER_SETTINGS_QUERY_KEY });
    },
  });
}
