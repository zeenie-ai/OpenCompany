/**
 * Settings: the nav search keeps the pages whose label or keywords match
 * and drops a group left empty; changing page clears the category a page
 * was opened on; Billing shows this month's tasks and the team size, and a
 * dash when the count can't be read.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));
vi.mock('../settings/ProfileTab', () => ({ ProfileTab: () => <p>Profile page</p> }));
vi.mock('@/components/credentials/CredentialsBrowser', () => ({
  CredentialsBrowser: ({ initialCategory }: { initialCategory?: string }) => <p>Connectors page ({initialCategory})</p>,
}));
vi.mock('../data/employees', () => ({ useEmployeesQuery: () => ({ data: [{}, {}, {}], isPending: false }) }));

import { ThemeProvider } from '@/contexts/ThemeContext';
import { HomeSettings } from '../settings/HomeSettings';
import { useHomeStore } from '../state/homeStore';

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <HomeSettings onConnect={vi.fn()} />
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  sendRequest.mockReset().mockResolvedValue({ success: true, tasks_this_month: 12 });
  useHomeStore.setState({ settingsOpen: false, settingsTab: 'profile', settingsCategory: 'all' });
});

describe('HomeSettings', () => {
  it('keeps the pages the nav search matches and drops an empty group', () => {
    useHomeStore.getState().openSettings();
    renderSettings();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search settings' }), { target: { value: 'invoices' } });
    expect(screen.getByRole('tab', { name: 'Billing' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Profile' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Connectors' })).not.toBeInTheDocument();
    expect(screen.queryByText('Customize')).not.toBeInTheDocument();
  });

  it('clears the category a page was opened on when the page changes', async () => {
    useHomeStore.getState().openSettings('connectors', 'ai');
    renderSettings();
    expect(screen.getByText('Connectors page (ai)')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: 'Profile' }));
    expect(useHomeStore.getState().settingsCategory).toBe('all');
    await userEvent.click(screen.getByRole('tab', { name: 'Connectors' }));
    expect(screen.getByText('Connectors page (all)')).toBeInTheDocument();
  });

  it('shows this month’s tasks and the team size on Billing', async () => {
    useHomeStore.getState().openSettings('billing');
    renderSettings();
    expect(await screen.findByText('12')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(sendRequest).toHaveBeenCalledWith('get_employee_usage', {});
  });

  it('shows a dash when this month’s count can’t be read', async () => {
    sendRequest.mockResolvedValue({ success: false, error: 'database locked' });
    useHomeStore.getState().openSettings('billing');
    renderSettings();
    expect(await screen.findByText('—')).toBeInTheDocument();
  });

  it('replays the Welcome guide from Help, closing Settings', async () => {
    useHomeStore.setState({ guide: { open: false, step: 'first-hire', furthest: 2, checked: true, provider: null, pendingDraft: false } });
    useHomeStore.getState().openSettings('help');
    renderSettings();
    await userEvent.click(screen.getByRole('button', { name: 'Replay' }));
    expect(useHomeStore.getState().settingsOpen).toBe(false);
    expect(useHomeStore.getState().guide).toMatchObject({ open: true, step: 'welcome', furthest: 2 });
  });

  it('shows the hidden Get started checklist again from Help, closing Settings', async () => {
    useHomeStore.getState().openSettings('help');
    renderSettings();
    await userEvent.click(screen.getByRole('button', { name: 'Show' }));
    expect(useHomeStore.getState().settingsOpen).toBe(false);
    expect(sendRequest).toHaveBeenCalledWith('save_user_settings', { settings: { getting_started_dismissed: false } });
  });

  it('finds Help by searching for the guide', () => {
    useHomeStore.getState().openSettings();
    renderSettings();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search settings' }), { target: { value: 'guide' } });
    expect(screen.getByRole('tab', { name: 'Help' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Billing' })).not.toBeInTheDocument();
  });
});
