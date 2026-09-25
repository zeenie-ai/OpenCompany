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
vi.mock('../settings/ConnectorsTab', () => ({
  ConnectorsTab: ({ initialCategory }: { initialCategory?: string }) => <p>Connectors page ({initialCategory})</p>,
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
});
