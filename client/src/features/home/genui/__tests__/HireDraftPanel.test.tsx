/**
 * The draft panel end to end: the screen renders, a double-clicked Hire
 * sends one request, a finished hire clears the draft and lands the
 * employee first in the team, and "Change something" hands the composer
 * the draft to edit.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { setReducedMotion } from '@/test/waapi';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/contexts/WebSocketContext')>();
  return {
    ...actual,
    useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
  };
});

vi.mock('../../data/connectors', () => ({
  useConnectors: () => ({
    providers: [{ id: 'whatsapp', name: 'WhatsApp', consumer_category: 'messages', connected: false }],
    categories: [],
    connectedApps: [],
    hasAi: true,
    isLoading: false,
  }),
  isConnected: (provider: { connected?: boolean }) => Boolean(provider.connected),
}));

import corpus from '../__fixtures__/replies.json';
import { EMPLOYEES_QUERY_KEY } from '../../data/employees';
import { useHomeStore } from '../../state/homeStore';
import { HireDraftPanel } from '../HireDraftPanel';
import { resetDraftForTests, useDraftStore } from '../draftStore';
import { normalizeSpec } from '../normalize';
import { parseReply } from '../parse';

const reply = (corpus as unknown as { name: string; reply: string }[]).find((c) => c.name === 'clean minified reply')!.reply;

function seedReadyDraft() {
  const parsed = parseReply(reply);
  const spec = normalizeSpec(parsed.spec)!;
  useDraftStore.setState({
    status: 'ready',
    job: 'Answer WhatsApp',
    turns: [{ change: null, reply }],
    spec,
    intro: parsed.text,
    uiState: spec.state,
    version: 1,
  });
}

function renderPanel(onConnect = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryData(EMPLOYEES_QUERY_KEY, []);
  render(
    <QueryClientProvider client={queryClient}>
      <HireDraftPanel onConnect={onConnect} />
    </QueryClientProvider>,
  );
  return { queryClient, onConnect };
}

let restoreMotion: () => void;

beforeEach(() => {
  restoreMotion = setReducedMotion(true);
  sendRequest.mockReset();
  resetDraftForTests();
  seedReadyDraft();
});

afterEach(() => {
  restoreMotion();
  resetDraftForTests();
});

describe('HireDraftPanel', () => {
  it('shows the introduction and the setup screen', () => {
    renderPanel();
    expect(screen.getByText('Meet Maya, your new receptionist.')).toBeInTheDocument();
    expect(screen.getByText('Their routine')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Ask me before sending anything' })).toBeChecked();
  });

  it('sends one hire for a double click, then clears the draft', async () => {
    let finish!: (value: unknown) => void;
    sendRequest.mockImplementation((type: string) =>
      type === 'hire_employee' ? new Promise((resolve) => (finish = resolve)) : Promise.resolve({}),
    );
    const { queryClient } = renderPanel();
    const hire = screen.getByRole('button', { name: 'Hire Maya' });
    fireEvent.click(hire);
    fireEvent.click(hire);
    const hires = sendRequest.mock.calls.filter(([type]) => type === 'hire_employee');
    expect(hires).toHaveLength(1);
    expect(hires[0][1]).toMatchObject({ name: 'Maya', rules: { ask_first: true } });

    await act(async () => {
      finish({
        success: true,
        started: true,
        employee: { workflow_id: 'w1', name: 'Maya', role: 'Receptionist', status: 'working', control: {}, revision: 1 },
      });
    });
    expect(useDraftStore.getState().status).toBe('idle');
    expect(queryClient.getQueryData<{ workflow_id: string }[]>(EMPLOYEES_QUERY_KEY)?.[0]?.workflow_id).toBe('w1');
    expect(useHomeStore.getState().glow?.workflowId).toBe('w1');
  });

  it('opens a hire that could not start on the employee card', async () => {
    sendRequest.mockResolvedValue({
      success: true,
      started: false,
      employee: { workflow_id: 'w2', name: 'Maya', role: 'Receptionist', status: 'ready', control: {}, revision: 1 },
    });
    renderPanel();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Hire Maya' }));
    });
    expect(useHomeStore.getState().view).toEqual({ kind: 'employee', workflowId: 'w2' });
  });

  it('hands the draft to the composer for a change', () => {
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Change something' }));
    expect(useDraftStore.getState().refining).toBe(true);
  });

  it('writes a toggle back into the screen state', () => {
    renderPanel();
    fireEvent.click(screen.getByRole('switch', { name: 'Only reply 9 to 6' }));
    expect(useDraftStore.getState().uiState).toMatchObject({ rules: { hours: true } });
  });
});
