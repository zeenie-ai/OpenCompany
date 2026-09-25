/**
 * Settings > Plugins: Install adds a bundle's missing skills, switches on
 * the ones that are off, then starts a hire from its job on the hire view;
 * a bundle whose skills are all in the library counts as installed; nothing
 * installs while a setup is being written. The starter list itself parses,
 * and the composer's chips still read it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { UserSkill } from '@/hooks/useUserSkills';

let library: UserSkill[] = [];
const builtIns = ['write-like-a-person', 'short-reports', 'social-posts'].map((name) => ({
  name,
  description: `Use for ${name}.`,
  metadata: { title: name, summary: name },
}));

const sendRequest = vi.fn(async (type: string, data: Record<string, any> = {}) => {
  switch (type) {
    case 'get_user_skills':
      return { skills: library };
    case 'scan_skill_folder':
      return { success: true, skills: builtIns };
    case 'get_skill_content':
      return { success: true, instructions: `Do ${data.skill_name}.` };
    case 'create_user_skill':
      library = [...library, row(data.name)];
      return { skill: library.at(-1) };
    case 'update_user_skill':
      library = library.map((entry) => (entry.name === data.name ? { ...entry, is_active: data.is_active } : entry));
      return { skill: library.find((entry) => entry.name === data.name) };
    default:
      return {};
  }
});

const draft = { busy: false, start: vi.fn() };

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));
vi.mock('../genui', () => ({ useStartHire: () => draft }));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { ThemeProvider } from '@/contexts/ThemeContext';
import { HIRE_TEMPLATES, STARTERS } from '../hire/templates';
import { PluginsTab } from '../settings/PluginsTab';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';

function row(name: string, patch: Partial<UserSkill> = {}): UserSkill {
  return { name, display_name: name, description: '', instructions: 'x', icon: '', color: '', category: 'custom', is_active: true, ...patch };
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <PluginsTab />
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

const inbox = STARTERS.find((starter) => starter.id === 'inbox-assistant')!;

beforeEach(() => {
  library = [];
  sendRequest.mockClear();
  draft.busy = false;
  draft.start.mockReset();
  vi.mocked(pillToast).mockClear();
  useHomeStore.setState({ settingsOpen: true, view: { kind: 'employee', workflowId: 'w1' } });
});

describe('PluginsTab', () => {
  it('installs a bundle: its skills into the library, then a hire from its job', async () => {
    library = [row('short-reports', { is_active: false })];
    renderTab();
    fireEvent.click(await screen.findByRole('button', { name: 'Install Inbox assistant' }));
    await waitFor(() => expect(draft.start).toHaveBeenCalledWith(inbox.job));
    expect(sendRequest).toHaveBeenCalledWith('create_user_skill', expect.objectContaining({ name: 'write-like-a-person' }));
    expect(sendRequest).toHaveBeenCalledWith('update_user_skill', { name: 'short-reports', is_active: true });
    expect(useHomeStore.getState().settingsOpen).toBe(false);
    expect(useHomeStore.getState().view).toEqual({ kind: 'hire' });
  });

  it('counts a bundle as installed when all its skills are in the library', async () => {
    library = [row('social-posts'), row('write-like-a-person', { is_active: false })];
    renderTab();
    expect(await screen.findByRole('img', { name: 'Social media helper added' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: /Yours/ }));
    expect(screen.getByText('Social media helper')).toBeInTheDocument();
    expect(screen.queryByText('Bookkeeper')).not.toBeInTheDocument();
  });

  it('waits while a setup is being written', async () => {
    draft.busy = true;
    renderTab();
    fireEvent.click(await screen.findByRole('button', { name: 'Install Bookkeeper' }));
    expect(pillToast).toHaveBeenCalledWith('Finish the setup you are working on first', { tone: 'info' });
    expect(sendRequest).not.toHaveBeenCalledWith('create_user_skill', expect.anything());
    expect(draft.start).not.toHaveBeenCalled();
  });
});

describe('starters', () => {
  it('parse, and the composer chips read the same list', () => {
    expect(HIRE_TEMPLATES).toBe(STARTERS);
    expect(STARTERS.map((starter) => starter.label)).toEqual(['Receptionist', 'Inbox assistant', 'Bookkeeper', 'Social media helper']);
    for (const starter of STARTERS) expect(starter.skills.length).toBeGreaterThan(0);
  });
});
