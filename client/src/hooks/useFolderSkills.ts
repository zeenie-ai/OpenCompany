/**
 * Per-folder skill metadata query.
 *
 * Wraps the `scan_skill_folder` WebSocket request in TanStack Query so:
 *  - sibling surfaces (MasterSkillEditor, MiddleSection, Home's Settings >
 *    Skills) reading the same folder share one network call
 *  - SKILL.md frontmatter (icon / color / description) is the single
 *    source of truth -- the frontend no longer maintains a per-skill
 *    icon override table
 *
 * Every reader of a folder's key goes through `fetchFolderSkills`, so the
 * cache holds one shape whichever surface loads it first.
 */

import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useWebSocket, type WebSocketActions } from '../contexts/WebSocketContext';
import { STALE_TIME } from '../lib/queryConfig';
import { dracula } from '../styles/theme';

export interface AvailableSkill {
  type: string;
  skillName: string;
  displayName: string;
  icon: string;
  color: string;
  description: string;
  /** The SKILL.md `metadata` block as served (author, title, summary, …). */
  metadata?: Record<string, unknown>;
}

export const folderSkillsQueryKey = (folder: string) =>
  ['folderSkills', folder] as const;

interface ScanSkillFolderResponse {
  success: boolean;
  skills?: Array<{
    name: string;
    description: string;
    metadata?: Record<string, any>;
  }>;
  error?: string;
}

const titleCase = (slug: string): string =>
  slug
    .split('-')
    .map((w) => (w.length ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');

export async function fetchFolderSkills(
  sendRequest: WebSocketActions['sendRequest'],
  folder: string,
): Promise<AvailableSkill[]> {
  if (!folder) return [];
  const response = await sendRequest<ScanSkillFolderResponse>('scan_skill_folder', { folder });
  if (!response?.success || !response.skills) return [];
  return response.skills.map((s) => ({
    type: s.name,
    skillName: s.name,
    displayName: titleCase(s.name),
    icon: s.metadata?.icon ?? '',
    color: s.metadata?.color ?? dracula.purple,
    description: s.description ?? '',
    metadata: s.metadata ?? {},
  }));
}

export function useFolderSkillsCore(
  folder: string | undefined | null,
  sendRequest: WebSocketActions['sendRequest'],
  isReady: boolean,
): UseQueryResult<AvailableSkill[], Error> {
  return useQuery<AvailableSkill[], Error>({
    queryKey: folderSkillsQueryKey(folder ?? ''),
    queryFn: () => fetchFolderSkills(sendRequest, folder ?? ''),
    enabled: !!folder && isReady,
    staleTime: STALE_TIME.MEDIUM,
  });
}

export function useFolderSkills(
  folder: string | undefined | null,
): UseQueryResult<AvailableSkill[], Error> {
  const { sendRequest, isReady } = useWebSocket();
  return useFolderSkillsCore(folder, sendRequest, isReady);
}
