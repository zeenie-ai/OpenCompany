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

/** The save mutation without a context read (see useUserSettingsQueryCore). */
export function useSaveUserSettingsMutationCore(sendRequest: SendRequest) {
  const qc = useQueryClient();
  return useMutation<UserSettings, Error, UserSettings>({
    mutationFn: async (patch) => {
      await sendRequest('save_user_settings', { settings: patch });
      return patch;
    },
    onSuccess: (patch) => {
      qc.setQueryData<UserSettings>(USER_SETTINGS_QUERY_KEY, (prev) => ({
        ...(prev ?? {}),
        ...patch,
      }));
    },
  });
}
