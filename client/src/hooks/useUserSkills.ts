/**
 * The owner's skill library (`get_user_skills`, every row, on or off). The
 * Dev editor's Master Skill panel and Home's Settings > Skills read this one
 * cache; `skill_lifecycle` broadcasts invalidate it (WebSocketContext).
 * `is_active` means "on for new hires" (docs-internal/normal_mode.md).
 */

import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useWebSocket, type WebSocketActions } from '../contexts/WebSocketContext';

export interface UserSkill {
  name: string;
  display_name: string;
  description: string;
  instructions: string;
  icon: string;
  color: string;
  category: string;
  is_active: boolean;
  metadata?: Record<string, unknown> | null;
}

export const USER_SKILLS_QUERY_KEY = ['userSkills'] as const;

export async function fetchUserSkills(sendRequest: WebSocketActions['sendRequest']): Promise<UserSkill[]> {
  const response = await sendRequest<{ skills?: UserSkill[] }>('get_user_skills', { active_only: false });
  return response?.skills ?? [];
}

export function useUserSkillsQueryCore(
  sendRequest: WebSocketActions['sendRequest'],
  isReady: boolean,
): UseQueryResult<UserSkill[], Error> {
  return useQuery<UserSkill[], Error>({
    queryKey: USER_SKILLS_QUERY_KEY,
    queryFn: () => fetchUserSkills(sendRequest),
    enabled: isReady,
    staleTime: 60_000,
  });
}

/** For the editor, which already reads the full socket context. */
export function useUserSkillsQuery(): UseQueryResult<UserSkill[], Error> {
  const { sendRequest, isReady } = useWebSocket();
  return useUserSkillsQueryCore(sendRequest, isReady);
}
