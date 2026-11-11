/**
 * Creating or deleting a user skill saves the Master Skill node's row
 * directly, and that save replaces the whole row. It used to send the
 * folder as `skillFolder`, a key nothing reads, so a node set to any folder
 * but "assistant" lost its folder on every create or delete.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { makeTestQueryClient, renderWithProviders } from '../../../test/providers';
import type { AvailableSkill } from '../../../hooks/useFolderSkills';

const sendRequest = vi.hoisted(() => vi.fn());
const folder = vi.hoisted(() => ({ skills: [] as AvailableSkill[] }));
const nodeStatus = vi.hoisted(() => ({ value: undefined as unknown }));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ sendRequest }),
  useNodeStatus: () => nodeStatus.value,
}));
vi.mock('../../../hooks/useFolderSkills', () => ({
  useFolderSkills: () => ({ data: folder.skills, isLoading: false }),
}));
vi.mock('../../../hooks/useUserSkills', () => ({
  USER_SKILLS_QUERY_KEY: ['userSkills'],
  useUserSkillsQuery: () => ({
    data: [{ name: 'triage', display_name: 'Triage', description: 'Sort the inbox', instructions: 'Sort it', icon: '', color: '#6366F1' }],
    isLoading: false,
  }),
}));
vi.mock('../../../hooks/useNodeAllowlist', () => ({
  useNodeAllowlist: () => ({ isSkillFolderDisabled: () => false }),
}));
vi.mock('../../../assets/icons', () => ({ NodeIcon: () => null }));

import MasterSkillEditor from '../MasterSkillEditor';
import { skillContentQueryKey } from '../../../lib/skillContentQueryKey';

beforeEach(() => {
  sendRequest.mockReset();
  folder.skills = [];
  nodeStatus.value = undefined;
  sendRequest.mockImplementation(async (type: string) => (
    type === 'list_skill_folders'
      ? { success: true, folders: [{ name: 'coding', skill_count: 0 }] }
      : { success: true }
  ));
});

describe('MasterSkillEditor', () => {
  it('keeps legacy cache identity and isolates defaults and Reset by the saved v1 Master Skill node', async () => {
    expect(skillContentQueryKey('browser', 'legacy', 0)).toEqual(['skillContent', 'browser']);
    folder.skills = [{ type: 'browser', skillName: 'browser', displayName: 'Browser', description: 'Browse pages', icon: '', color: '' }];
    sendRequest.mockImplementation(async (type: string, payload: Record<string, string>) => type === 'get_skill_content'
      ? { success: true, instructions: `defaults:${payload.master_skill_node_id}` }
      : { success: true, folders: [] });
    const queryClient = makeTestQueryClient();
    // This assertion compares two inactive defaults entries. The shared test
    // client otherwise deliberately garbage-collects inactive queries at once.
    queryClient.setQueryDefaults(['skillContent'], { gcTime: Infinity });
    const onConfigChange = vi.fn();
    const props = { skillFolder: 'coding', skillsConfig: { browser: { enabled: true, instructions: 'custom', isCustomized: true } }, onConfigChange, runtimeBehaviorVersion: 1 };
    const view = renderWithProviders(<MasterSkillEditor {...props} nodeId="master-a" />, { queryClient });
    await waitFor(() => expect(queryClient.getQueryData(skillContentQueryKey('browser', 'master-a', 1))).toBe('defaults:master-a'));
    view.rerender(<MasterSkillEditor {...props} nodeId="master-b" />);
    await waitFor(() => expect(queryClient.getQueryData(skillContentQueryKey('browser', 'master-b', 1))).toBe('defaults:master-b'));
    fireEvent.click(await screen.findByRole('button', { name: 'Reset' }));
    await waitFor(() => expect(onConfigChange).toHaveBeenCalledWith({ browser: { enabled: true, instructions: 'defaults:master-b', isCustomized: false } }));
    expect(queryClient.getQueryData(skillContentQueryKey('browser', 'master-a', 1))).toBe('defaults:master-a');
    expect(sendRequest.mock.calls.filter(([type]) => type === 'get_skill_content').every(([, payload]) => payload.master_skill_node_id)).toBe(true);
  });
  // Colour marks state or role (docs-internal/theme_system.md): a skill's
  // state in the run is a dot beside a neutral word, and its tags are neutral.
  it('marks a skill’s state in the run with a dot, and draws its tags neutral', async () => {
    folder.skills = [
      { type: 'browser', skillName: 'browser', displayName: 'Browser', description: 'Browse pages', icon: '', color: '' },
      { type: 'search', skillName: 'search', displayName: 'Search', description: 'Find things', icon: '', color: '' },
    ];
    nodeStatus.value = { data: { active_skills: [{ name: 'browser', state: 'loading' }, { name: 'search', state: 'failed' }] } };
    const { container } = renderWithProviders(
      <MasterSkillEditor
        nodeId="7:masterSkill:1"
        skillFolder="coding"
        skillsConfig={{ triage: { enabled: true, instructions: 'Sort it', isCustomized: false } }}
        onConfigChange={vi.fn()}
      />,
    );

    const loading = await screen.findByText('Loading');
    expect(loading).toHaveClass('text-fg-muted');
    expect(loading.querySelector('.home-pip')).toHaveClass('bg-status-working-dot');
    expect(loading.querySelector('.home-pip')).toHaveAttribute('data-pip', 'on');
    expect(screen.getByText('Failed').querySelector('.home-pip')).toHaveClass('bg-status-attention-dot');
    expect(screen.getByText('Custom')).toHaveClass('border-border-default', 'text-fg-muted');
    expect(container.innerHTML).not.toMatch(/bg-(info|success|destructive|warning|agent)\/20|text-(info|success|agent)\b/);
  });

  it('keeps the folder in the row it saves after deleting a user skill', async () => {
    renderWithProviders(
      <MasterSkillEditor
        nodeId="7:masterSkill:1"
        skillFolder="coding"
        skillsConfig={{ triage: { enabled: true, instructions: 'Sort it', isCustomized: false } }}
        onConfigChange={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    const dialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('save_node_parameters', {
      node_id: '7:masterSkill:1',
      parameters: { skills_config: {}, skill_folder: 'coding' },
    }));
  });
});
