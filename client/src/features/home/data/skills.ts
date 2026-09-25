/**
 * Settings > Skills: the owner's skill library, and the built-in skills
 * they can add to it.
 *
 * - The library is the user-skills table (`get_user_skills`). A row that is
 *   on (`is_active`) goes to every employee hired afterwards, copied into
 *   that employee's Skills node, so later changes here never alter an
 *   employee already hired.
 * - Discover is the built-in `employee` folder: short, tool-free skills
 *   written for AI employees (server/skills/employee/).
 * - Adding a built-in copies its text into the library under the same name.
 *   A skill the owner writes gets a name of its own, never a built-in's.
 *
 * Refreshes come from the `skill_lifecycle` broadcast (WebSocketContext);
 * each action also refetches the library itself.
 */

import { useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useWebSocketActions, type WebSocketActions } from '@/contexts/WebSocketContext';
import { useFolderSkillsCore, type AvailableSkill } from '@/hooks/useFolderSkills';
import { USER_SKILLS_QUERY_KEY, useUserSkillsQueryCore, type UserSkill } from '@/hooks/useUserSkills';

/** The built-in folder Settings > Skills offers. */
export const DISCOVER_SKILL_FOLDER = 'employee';

/** Names a skill must never take in the library: `skill` is the Skill
 *  tool's own entry, and a `*-personality` skill replaces an employee's
 *  whole instructions. */
export function isReservedSkillName(name: string): boolean {
  return name === 'skill' || name.endsWith('-personality');
}

type Send = WebSocketActions['sendRequest'];

export function useSkillLibrary() {
  const { sendRequest, isReady } = useWebSocketActions();
  return useUserSkillsQueryCore(sendRequest, isReady);
}

export function useDiscoverSkills() {
  const { sendRequest, isReady } = useWebSocketActions();
  return useFolderSkillsCore(DISCOVER_SKILL_FOLDER, sendRequest, isReady);
}

/** A skill's name on its card: the SKILL.md title, else the row's name. */
export function skillTitle(skill: AvailableSkill): string {
  const title = skill.metadata?.title;
  return typeof title === 'string' && title.trim() ? title.trim() : skill.displayName;
}

/** A skill's card line, in the owner's words: the SKILL.md summary, else its description. */
export function skillSummary(skill: AvailableSkill | UserSkill): string {
  const summary = skill.metadata?.summary;
  return typeof summary === 'string' && summary.trim() ? summary.trim() : skill.description;
}

/** The first sentence of plain-words instructions, for a card. */
export function firstSentence(text: string): string {
  const line = text
    .split('\n')
    .map((part) => part.replace(/^[#>*\-\s]+/, '').trim())
    .find(Boolean);
  if (!line) return '';
  const end = line.search(/[.!?](\s|$)/);
  const sentence = end >= 0 ? line.slice(0, end + 1) : line;
  return sentence.length > 120 ? `${sentence.slice(0, 119).trimEnd()}…` : sentence;
}

/** A free library name for a skill titled `title`: its slug, then -2 to -9,
 *  skipping reserved names, names in the library, and built-in names. */
export async function librarySkillName(send: Send, title: string, library: UserSkill[]): Promise<string> {
  const slug =
    title
      .toLowerCase()
      .normalize('NFKD')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60)
      .replace(/-+$/g, '') || 'my-skill';
  const candidates = [slug, ...Array.from({ length: 8 }, (_, i) => `${slug}-${i + 2}`)].filter(
    (name) => !isReservedSkillName(name),
  );
  const taken = new Set(library.map((row) => row.name));
  const lookup = await send<{ skills?: Array<{ name: string }> }>('lookup_skill_metadata', { names: candidates });
  for (const found of lookup?.skills ?? []) taken.add(found.name);
  const name = candidates.find((candidate) => !taken.has(candidate));
  if (!name) throw new Error('Pick a different name for this skill');
  return name;
}

function failed(response: { success?: boolean; skill?: unknown } | undefined): boolean {
  return !response || response.success === false || (response.skill === undefined && response.success !== true);
}

/** The Skills page's actions, bound to the socket. Each one throws with a
 *  message the page can show. */
export function useSkillActions() {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  return useMemo(() => {
    const refresh = () => queryClient.invalidateQueries({ queryKey: USER_SKILLS_QUERY_KEY });
    const library = () => queryClient.getQueryData<UserSkill[]>(USER_SKILLS_QUERY_KEY) ?? [];

    const setOn = async (name: string, on: boolean) => {
      const before = queryClient.getQueryData<UserSkill[]>(USER_SKILLS_QUERY_KEY);
      queryClient.setQueryData<UserSkill[]>(USER_SKILLS_QUERY_KEY, (rows) =>
        rows?.map((row) => (row.name === name ? { ...row, is_active: on } : row)),
      );
      try {
        const response = await sendRequest<{ success?: boolean; skill?: unknown; error?: string }>('update_user_skill', {
          name,
          is_active: on,
        });
        if (failed(response)) throw new Error(response?.error || "Couldn't change that skill");
      } catch (error) {
        queryClient.setQueryData(USER_SKILLS_QUERY_KEY, before);
        throw error;
      } finally {
        void refresh();
      }
    };

    /** Add a built-in skill to the library (or switch its copy back on). */
    const add = async (skill: AvailableSkill) => {
      const title = skillTitle(skill);
      if (library().some((row) => row.name === skill.skillName)) {
        await setOn(skill.skillName, true);
        return;
      }
      const content = await sendRequest<{ success?: boolean; instructions?: string; error?: string }>('get_skill_content', {
        skill_name: skill.skillName,
      });
      if (!content?.success || !content.instructions) throw new Error(`Couldn't read ${title}`);
      const created = await sendRequest<{ success?: boolean; skill?: unknown; error?: string }>('create_user_skill', {
        name: skill.skillName,
        display_name: title,
        description: skill.description,
        instructions: content.instructions,
        category: typeof skill.metadata?.category === 'string' ? skill.metadata.category : 'custom',
        icon: skill.icon,
        color: skill.color,
        metadata: skill.metadata ?? {},
      });
      if (failed(created)) {
        // Another tab may have added it a moment ago: switch that copy on.
        await refresh();
        if (library().some((row) => row.name === skill.skillName)) {
          await setOn(skill.skillName, true);
          return;
        }
        throw new Error(created?.error || `Couldn't add ${title}`);
      }
      await refresh();
    };

    /** Write a new skill: a title and plain-words instructions. */
    const create = async (title: string, instructions: string) => {
      const name = await librarySkillName(sendRequest, title, library());
      const summary = firstSentence(instructions);
      const created = await sendRequest<{ success?: boolean; skill?: unknown; error?: string }>('create_user_skill', {
        name,
        display_name: title,
        description: summary ? `${title}: ${summary}` : title,
        instructions,
        category: 'custom',
        // No icon: the card shows the skill's initials (the handler's
        // default, "star", is not an icon reference).
        icon: '',
        metadata: { title, summary },
      });
      if (failed(created)) throw new Error(created?.error || `Couldn't create ${title}`);
      await refresh();
      return name;
    };

    const remove = async (name: string) => {
      const response = await sendRequest<{ success?: boolean; error?: string }>('delete_user_skill', { name });
      if (!response?.success) throw new Error(response?.error || "Couldn't remove that skill");
      await refresh();
    };

    return { add, create, setOn, remove };
  }, [sendRequest, queryClient]);
}
