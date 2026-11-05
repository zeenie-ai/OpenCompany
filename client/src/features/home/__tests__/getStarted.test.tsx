/**
 * The Get started checklist on Home (onboarding/GetStartedChecklist): it
 * shows once the Welcome guide is finished and stays out of the way while
 * the guide or the AI models dialog is open; Connect an AI model and Hire
 * your first employee follow the team as it is now, and their rows take the
 * owner there (the AI models dialog, kept open after a connect); Say
 * hello and Approve a first draft are latched in the owner's settings,
 * each written once; it folds to a pill, the fold kept in homeStore; Done
 * and hiding it say where it comes back. On an employee's page the header's
 * pill (onboarding/GetStartedPill) stands in for it under the same rule,
 * writes the same latches, and opens the card on the hire view.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';

const sendRequest = vi.fn();
const listeners = new Map<string, Set<(data: unknown) => void>>();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({
    sendRequest,
    isReady: true,
    addEventListener: (type: string, listener: (data: unknown) => void) => {
      const set = listeners.get(type) ?? new Set();
      set.add(listener);
      listeners.set(type, set);
      return () => set.delete(listener);
    },
  }),
}));

let ai: { id: string; name: string; consumer_category: string; connected: boolean }[] = [];

vi.mock('../data/connectors', () => ({
  useConnectors: () => ({ providers: ai, categories: [], connectedApps: [], hasAi: ai.some((p) => p.connected), isLoading: false }),
  isConnected: (provider: { connected?: boolean }) => Boolean(provider.connected),
}));

vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { USER_SETTINGS_QUERY_KEY } from '@/hooks/useUserSettingsQuery';
import { GetStartedChecklist } from '../onboarding/GetStartedChecklist';
import { GetStartedPill } from '../onboarding/GetStartedPill';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';

type Wire = Record<string, unknown>;

let server: { settings: Wire; employees: Wire[]; messages: Wire[] };

const MAYA = {
  workflow_id: 'w1',
  name: 'Maya',
  role: 'Receptionist',
  derived: false,
  hired_at: '2026-10-08T09:00:00+00:00',
  pending_approvals: 0,
  control: { state: 'running' },
};

function saves(): Wire[] {
  return sendRequest.mock.calls.filter(([type]) => type === 'save_user_settings').map(([, data]) => (data as Wire).settings as Wire);
}

function renderWith(ui: ReactElement): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return client;
}

const renderChecklist = () => renderWith(<GetStartedChecklist />);
const renderPill = () => renderWith(<GetStartedPill />);

function emit(type: string, data: unknown) {
  act(() => {
    for (const listener of listeners.get(type) ?? []) listener(data);
  });
}

const step = (name: RegExp) => screen.getByRole('button', { name });

beforeEach(() => {
  listeners.clear();
  ai = [];
  vi.mocked(pillToast).mockClear();
  useHomeStore.setState((state) => ({
    view: { kind: 'hire' },
    guide: { ...state.guide, open: false },
    connectAI: { open: false, provider: null, closeOnConnect: true },
    getStartedFolded: false,
  }));
  server = { settings: { onboarding_completed: true }, employees: [], messages: [] };
  sendRequest.mockReset().mockImplementation(async (type: string, data: Wire) => {
    if (type === 'get_user_settings') return { settings: server.settings };
    if (type === 'save_user_settings') {
      server.settings = { ...server.settings, ...(data.settings as Wire) };
      return { success: true };
    }
    if (type === 'list_employees') return { success: true, employees: server.employees };
    if (type === 'get_chat_messages') return { success: true, messages: server.messages };
    return { success: true };
  });
});

describe('Get started on Home', () => {
  it('waits for the Welcome guide to be finished, and for it to close', async () => {
    server.settings = { onboarding_completed: false };
    renderChecklist();
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('get_user_settings', {}));
    expect(screen.queryByRole('region', { name: 'Get started' })).not.toBeInTheDocument();
  });

  it('stays hidden while the guide is open', async () => {
    renderChecklist();
    await screen.findByRole('region', { name: 'Get started' });
    act(() => useHomeStore.setState((state) => ({ guide: { ...state.guide, open: true } })));
    expect(screen.queryByRole('region', { name: 'Get started' })).not.toBeInTheDocument();
  });

  it('stays hidden while the AI models dialog is open', async () => {
    renderChecklist();
    await screen.findByRole('region', { name: 'Get started' });
    act(() => useHomeStore.setState((state) => ({ connectAI: { ...state.connectAI, open: true } })));
    expect(screen.queryByRole('region', { name: 'Get started' })).not.toBeInTheDocument();
  });

  it('offers the first steps, each taking the owner there', async () => {
    renderChecklist();
    const card = await screen.findByRole('region', { name: 'Get started' });
    expect(within(card).getByText('0 of 4 done')).toBeInTheDocument();
    expect(within(card).getByText('So your team can think')).toBeInTheDocument();
    fireEvent.click(step(/Connect an AI model/));
    // The AI models dialog, which stays on its list after a connect.
    expect(useHomeStore.getState().connectAI).toEqual({ open: true, provider: null, closeOnConnect: false });
    expect(useHomeStore.getState().guide.open).toBe(false);
    act(() => useHomeStore.setState((state) => ({ connectAI: { ...state.connectAI, open: false }, view: { kind: 'employee', workflowId: 'x' } })));
    fireEvent.click(await screen.findByRole('button', { name: /Hire your first employee/ }));
    expect(useHomeStore.getState().view).toEqual({ kind: 'hire' });
  });

  it('ticks what is done now: a model connected, someone hired', async () => {
    ai = [{ id: 'openai', name: 'OpenAI', consumer_category: 'ai', connected: true }];
    server.employees = [MAYA];
    renderChecklist();
    expect(await screen.findByText('Maya joined your team')).toBeInTheDocument();
    expect(screen.getByText('OpenAI is connected')).toBeInTheDocument();
    expect(screen.getByText('2 of 4 done')).toBeInTheDocument();
    fireEvent.click(step(/Say hello/));
    expect(useHomeStore.getState().view).toEqual({ kind: 'employee', workflowId: 'w1' });
  });

  it('takes the owner to whoever has a draft waiting, for Approve a first draft', async () => {
    const sam = { ...MAYA, workflow_id: 'w2', name: 'Sam', hired_at: '2026-10-08T10:00:00+00:00', pending_approvals: 1 };
    server.employees = [MAYA, sam];
    renderChecklist();
    expect(await screen.findByText('Maya joined your team')).toBeInTheDocument();
    fireEvent.click(step(/Approve a first draft/));
    expect(useHomeStore.getState().view).toEqual({ kind: 'employee', workflowId: 'w2' });
  });

  it('latches Say hello once the owner has written to their first hire', async () => {
    server.employees = [MAYA];
    server.messages = [{ id: 1, role: 'user', message: 'Hi!', timestamp: '2026-10-08T09:05:00+00:00', run_key: 'g1' }];
    renderChecklist();
    await waitFor(() => expect(saves()).toEqual([{ getting_started_said_hello: true }]));
    expect(await screen.findByText('2 of 4 done')).toBeInTheDocument();
  });

  it('latches Approve a first draft when the owner sends one, and not for a discard', async () => {
    server.employees = [MAYA];
    renderChecklist();
    await screen.findByRole('region', { name: 'Get started' });
    emit('approval_lifecycle', { type: 'com.opencompany.approval.decided', data: { approval_id: 'a1', workflow_id: 'w1', status: 'discarded' } });
    expect(saves()).toEqual([]);
    emit('approval_lifecycle', { type: 'com.opencompany.approval.decided', data: { approval_id: 'a2', workflow_id: 'w1', status: 'approved' } });
    await waitFor(() => expect(saves()).toEqual([{ getting_started_approved_draft: true }]));
    // Latched: the next one writes nothing.
    emit('approval_lifecycle', { type: 'com.opencompany.approval.decided', data: { approval_id: 'a3', workflow_id: 'w1', status: 'approved' } });
    expect(saves()).toHaveLength(1);
  });

  it('folds to a pill and opens again', async () => {
    renderChecklist();
    await screen.findByRole('region', { name: 'Get started' });
    fireEvent.click(screen.getByRole('button', { name: 'Fold the checklist' }));
    // In the store, so the fold outlives the card (it remounts on each return to the hire view).
    expect(useHomeStore.getState().getStartedFolded).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /Get started · 0\/4/ }));
    expect(useHomeStore.getState().getStartedFolded).toBe(false);
    expect(screen.getByRole('region', { name: 'Get started' })).toBeInTheDocument();
  });

  it('says where it comes back when hidden', async () => {
    renderChecklist();
    await screen.findByRole('region', { name: 'Get started' });
    fireEvent.click(screen.getByRole('button', { name: 'Hide the checklist' }));
    await waitFor(() => expect(saves()).toEqual([{ getting_started_dismissed: true }]));
    expect(pillToast).toHaveBeenCalledWith('Get started hidden. Reopen it from Settings → Help.');
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Get started' })).not.toBeInTheDocument());
  });

  it('ends with Done, which hides it the same way', async () => {
    ai = [{ id: 'openai', name: 'OpenAI', consumer_category: 'ai', connected: true }];
    server.employees = [MAYA];
    server.settings = { onboarding_completed: true, getting_started_said_hello: true, getting_started_approved_draft: true };
    renderChecklist();
    const card = await screen.findByRole('region', { name: 'Get started' });
    expect(await within(card).findByRole('heading', { name: 'You’re all set!' })).toBeInTheDocument();
    // A done step is a state, so its disc takes the working colour.
    expect(card.querySelectorAll('.bg-status-working-fill')).toHaveLength(4);
    // Done is neutral, never an action's colour.
    const done = within(card).getByRole('button', { name: 'Done' });
    expect(done).toHaveClass('border-border-default');
    expect(done).not.toHaveClass('bg-action-run-soft');
    fireEvent.click(done);
    await waitFor(() => expect(saves()).toEqual([{ getting_started_dismissed: true }]));
    expect(pillToast).toHaveBeenCalledWith('Get started hidden. Reopen it from Settings → Help.');
  });
});

describe('The Get started pill on an employee’s page', () => {
  it('goes back to the hire view with the card open', async () => {
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' }, getStartedFolded: true });
    renderPill();
    fireEvent.click(await screen.findByRole('button', { name: 'Get started · 0/4' }));
    expect(useHomeStore.getState()).toMatchObject({ view: { kind: 'hire' }, getStartedFolded: false });
  });

  it('shows by the card’s rule', async () => {
    const client = renderPill();
    const pill = () => screen.queryByRole('button', { name: /Get started/ });
    expect(await screen.findByRole('button', { name: /Get started/ })).toBeInTheDocument();
    // Only once the guide is finished.
    act(() => {
      client.setQueryData(USER_SETTINGS_QUERY_KEY, { onboarding_completed: false });
    });
    await waitFor(() => expect(pill()).not.toBeInTheDocument());
    act(() => {
      client.setQueryData(USER_SETTINGS_QUERY_KEY, { onboarding_completed: true });
    });
    await waitFor(() => expect(pill()).toBeInTheDocument());
    // Never while the guide or the AI models dialog is open.
    act(() => useHomeStore.setState((state) => ({ guide: { ...state.guide, open: true } })));
    expect(pill()).not.toBeInTheDocument();
    act(() => useHomeStore.setState((state) => ({ guide: { ...state.guide, open: false }, connectAI: { ...state.connectAI, open: true } })));
    expect(pill()).not.toBeInTheDocument();
    act(() => useHomeStore.setState((state) => ({ connectAI: { ...state.connectAI, open: false } })));
    expect(pill()).toBeInTheDocument();
    // Until the owner hides it.
    act(() => {
      client.setQueryData(USER_SETTINGS_QUERY_KEY, { onboarding_completed: true, getting_started_dismissed: true });
    });
    await waitFor(() => expect(pill()).not.toBeInTheDocument());
  });

  it('writes both latches, as the card does on the hire view', async () => {
    server.employees = [MAYA];
    server.messages = [{ id: 1, role: 'user', message: 'Hi!', timestamp: '2026-10-08T09:05:00+00:00', run_key: 'g1' }];
    renderPill();
    await waitFor(() => expect(saves()).toEqual([{ getting_started_said_hello: true }]));
    emit('approval_lifecycle', { type: 'com.opencompany.approval.decided', data: { approval_id: 'a2', workflow_id: 'w1', status: 'approved' } });
    await waitFor(() => expect(saves()).toEqual([{ getting_started_said_hello: true }, { getting_started_approved_draft: true }]));
  });
});
