/**
 * Settings > Plugins: Hire hires a bundle's starter in one click and closes
 * Settings on the new employee; Discover offers Hire on every bundle, even
 * one already installed (a bundle can be hired again), while Yours lists
 * the installed ones (all their skills in the library); nothing starts
 * while another hire is going through. The starter list itself parses,
 * each starter carries its own setup, and the composer's chips read it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { UserSkill } from '@/hooks/useUserSkills';

let library: UserSkill[] = [];

const sendRequest = vi.fn(async (type: string) => (type === 'get_user_skills' ? { skills: library } : {}));

const starterHire = { busy: false, hire: vi.fn(async () => true) };

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));
vi.mock('../genui', () => ({ useStarterHire: () => starterHire }));
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
  starterHire.busy = false;
  starterHire.hire.mockReset().mockResolvedValue(true);
  vi.mocked(pillToast).mockClear();
  useHomeStore.setState({ settingsOpen: true, view: { kind: 'employee', workflowId: 'w1' } });
});

describe('PluginsTab', () => {
  it('hires the reviewed starter team, then closes Settings on them', async () => {
    renderTab();
    fireEvent.click(await screen.findByRole('button', { name: 'Hire Inbox assistant' }));
    expect(starterHire.hire).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole('button', { name: 'Hire employee' }));
    await waitFor(() => expect(starterHire.hire).toHaveBeenCalledWith(inbox, inbox.hire.team));
    await waitFor(() => expect(useHomeStore.getState().settingsOpen).toBe(false));
  });

  it('keeps Settings open when the hire did not go through', async () => {
    starterHire.hire.mockResolvedValue(false);
    renderTab();
    fireEvent.click(await screen.findByRole('button', { name: 'Hire Inbox assistant' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Hire employee' }));
    await waitFor(() => expect(starterHire.hire).toHaveBeenCalled());
    expect(useHomeStore.getState().settingsOpen).toBe(true);
  });

  it('offers Hire on an installed bundle too, and lists it under Yours', async () => {
    library = [row('social-posts'), row('write-like-a-person', { is_active: false })];
    renderTab();
    expect(await screen.findByRole('button', { name: 'Hire Social media helper' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: /Yours/ }));
    await waitFor(() => expect(screen.getByText('Social media helper')).toBeInTheDocument());
    expect(screen.queryByText('Daily briefer')).not.toBeInTheDocument();
  });

  it('waits while another hire is going through', async () => {
    starterHire.busy = true;
    renderTab();
    fireEvent.click(await screen.findByRole('button', { name: 'Hire Daily briefer' }));
    expect(pillToast).toHaveBeenCalledWith('Another hire is still going through. Try again in a moment.', { tone: 'info' });
    expect(starterHire.hire).not.toHaveBeenCalled();
  });
});

describe('starters', () => {
  it('parse, each with its own setup, and the composer chips read the same list', () => {
    expect(HIRE_TEMPLATES).toBe(STARTERS);
    expect(STARTERS.map((starter) => starter.label)).toEqual(['Receptionist', 'Inbox assistant', 'Social media helper', 'Daily briefer']);
    for (const starter of STARTERS) {
      expect(starter.skills.length).toBeGreaterThan(0);
      expect(starter.hire.names.length).toBeGreaterThan(0);
      expect(starter.hire.steps[0].role).toBe('trigger');
    }
  });
});
