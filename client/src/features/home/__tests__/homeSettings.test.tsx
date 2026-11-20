/**
 * Settings: the nav search keeps the pages whose label or keywords match
 * and drops a group left empty; changing page clears the category a page
 * was opened on; Billing shows this month's tasks and the team size, a
 * dash when the count can't be read, and the cost panel.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
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
import { installWaapiStub } from '@/test/waapi';

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

const USAGE = { success: true, tasks_this_month: 12, since: '2026-10-01' };
const COSTS = { success: true, since: '2026-10-01', total_cost: 4.5, workflows: [] };

/** The server's answers by command; `usage` is what `get_employee_usage` says. */
function serve(usage: unknown) {
  sendRequest.mockReset().mockImplementation(async (type: string) => {
    if (type === 'get_employee_usage') return usage;
    if (type === 'get_workflow_costs') return COSTS;
    if (type === 'get_user_settings') return { settings: {} };
    return { success: true };
  });
}

beforeEach(() => {
  serve(USAGE);
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
    const since = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date('2026-10-01T00:00:00Z'));
    expect(screen.getByText(`Since ${since}, across your whole team`)).toBeInTheDocument();
    expect(screen.getByText('In your sidebar now')).toBeInTheDocument();
    expect(sendRequest).toHaveBeenCalledWith('get_employee_usage', {});
  });

  it('slides a page picked in the nav in from the right', async () => {
    const waapi = installWaapiStub();
    try {
      useHomeStore.getState().openSettings();
      renderSettings();
      waapi.calls.length = 0;
      await userEvent.click(screen.getByRole('tab', { name: 'Billing' }));
      const slide = waapi.calls.find((call) => JSON.stringify(call.keyframes).includes('translateX(14px)'));
      expect(slide?.options).toMatchObject({ duration: 320 });
    } finally {
      waapi.restore();
    }
  });

  it('shows a dash when this month’s count can’t be read', async () => {
    serve({ success: false, error: 'database locked' });
    useHomeStore.getState().openSettings('billing');
    renderSettings();
    expect(await screen.findByText('—')).toBeInTheDocument();
  });

  it('shows the cost panel on Billing', async () => {
    useHomeStore.getState().openSettings('billing');
    renderSettings();
    const cost = within(await screen.findByRole('region', { name: 'Cost' }));
    expect(await cost.findByText('$4.50')).toBeInTheDocument();
    expect(await cost.findByLabelText('Spend limit per reply')).toBeInTheDocument();
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
