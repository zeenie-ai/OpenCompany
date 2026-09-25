/**
 * The owner's profile (Settings > Profile): the `profile_*` fields of the
 * user settings row, plus the two switches. Same cache as the editor's
 * Settings panel; the server trims and bounds every field on save
 * (services/settings/profile.py), and the browser's timezone rides along.
 */

import { z } from 'zod';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import {
  useSaveUserSettingsMutationCore,
  useUserSettingsQueryCore,
  type UserSettings,
} from '@/hooks/useUserSettingsQuery';

/** Field bounds mirror models.database.UserSettings (max_length). */
export const profileSchema = z.object({
  profile_full_name: z.string().trim().max(100, 'Keep the name under 100 characters'),
  profile_call_name: z.string().trim().max(60, 'Keep it under 60 characters'),
  profile_role: z.string().trim().max(100, 'Keep the role under 100 characters'),
  profile_preferences: z.string().max(2000, 'Keep preferences under 2,000 characters'),
  memory_across_chats: z.boolean(),
  prefer_local_ai: z.boolean(),
});

export type ProfileForm = z.infer<typeof profileSchema>;

export function profileFromSettings(settings: UserSettings | undefined): ProfileForm {
  const s = settings ?? {};
  return {
    profile_full_name: String(s.profile_full_name ?? ''),
    profile_call_name: String(s.profile_call_name ?? ''),
    profile_role: String(s.profile_role ?? ''),
    profile_preferences: String(s.profile_preferences ?? ''),
    memory_across_chats: s.memory_across_chats !== false,
    prefer_local_ai: s.prefer_local_ai !== false,
  };
}

/** What agents call the owner: the call name, else the first name. */
export function callName(settings: UserSettings | undefined): string {
  const explicit = String(settings?.profile_call_name ?? '').trim();
  if (explicit) return explicit;
  return String(settings?.profile_full_name ?? '').trim().split(/\s+/)[0] ?? '';
}

export function browserTimezone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

export function useOwnerSettings() {
  const { sendRequest, isReady } = useWebSocketActions();
  return useUserSettingsQueryCore(sendRequest, isReady);
}

export function useSaveProfile() {
  const { sendRequest } = useWebSocketActions();
  const mutation = useSaveUserSettingsMutationCore(sendRequest);
  return {
    ...mutation,
    saveProfile: (form: ProfileForm) => {
      const zone = browserTimezone();
      return mutation.mutateAsync({ ...form, ...(zone ? { profile_timezone: zone } : {}) });
    },
  };
}
