/**
 * A new hire's first day on their page (EmployeeChat with FirstDay): one
 * slim strip at the top of the conversation that follows their real start
 * (starting, ready, held for an app, failed, paused) and says their status
 * as the header does. The box waits while they start; the strip says why a
 * start did not go ahead without the line above the box saying it again,
 * its action green only for starting them; their routine shows on one line
 * until the first message, and the strip stays for the session.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));

vi.mock('../../../../app/useShellActions', () => ({ enterDev: vi.fn() }));
vi.mock('../../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { useComposerStore } from '@/features/chat/state/composerStore';
import { resetChatRunStore } from '@/stores/chatRunStore';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';
import { EMPLOYEES_QUERY_KEY } from '../../data/employees';
import { presentEmployee, primaryActionLabel } from '../../data/presentation';
import { parseEmployee, type EmployeeSummary } from '../../data/schemas';
import { useHomeStore } from '../../state/homeStore';
import { EmployeeChat } from '../EmployeeChat';
import type { EmployeeControl } from '../useEmployeeControl';
// Loaded up front so the turns' lazy markdown resolves from the module cache.
import '@/features/chat/markdown/ReplyMarkdown';

type Wire = Record<string, unknown>;

const PLAN = [
  { title: 'When a message arrives', detail: 'On WhatsApp', role: 'trigger', app: 'WhatsApp' },
  { title: 'Answer the question', role: 'agent' },
  { title: 'Book the visit', detail: 'In your calendar', role: 'tool' },
];

const MORNING = { id: 1, role: 'user', message: 'Morning!', timestamp: new Date(2026, 9, 9, 9).toISOString(), run_key: 'g1' };

const whatsapp = { app_id: 'whatsapp', provider_id: 'whatsapp', name: 'WhatsApp', connected: false, supported: true };

function employee(state: string, patch: Wire = {}): EmployeeSummary {
  return parseEmployee({
    workflow_id: 'w1',
    name: 'Maya',
    role: 'Receptionist',
    status: state === 'running' || state === 'starting' ? 'working' : 'ready',
    talk: { state: 'on', agent_node_id: 'w1:talk' },
    control: normalizeWorkflowControlStatus({ generation: 1, revision: 4, state }, 'w1'),
    activation_state: 'saved',
    ...patch,
  })!;
}

function controlFor(summary: EmployeeSummary): EmployeeControl {
  const view = presentEmployee(summary);
  return { view, label: primaryActionLabel(view.primary), busy: false, act: vi.fn() };
}

let server: { messages: Wire[] };

function renderPage(summary: EmployeeSummary) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(EMPLOYEES_QUERY_KEY, [summary]);
  const view = (next: EmployeeSummary) => (
    <QueryClientProvider client={client}>
      <EmployeeChat employee={next} control={controlFor(next)} />
    </QueryClientProvider>
  );
  const utils = render(view(summary));
  return { rerender: (next: EmployeeSummary) => utils.rerender(view(next)) };
}

const strip = () => screen.findByRole('region', { name: 'Maya’s first day' });

beforeEach(() => {
  resetChatRunStore();
  useComposerStore.setState({ drafts: {} });
  useNodeStatusStore.setState({ allStatuses: {} });
  useHomeStore.setState({ firstDays: { w1: { started: true, nodeCount: 9 } }, hireNotice: null });
  server = { messages: [] };
  sendRequest.mockReset().mockImplementation(async (type: string) => {
    if (type === 'chat_subscribe') return { success: true, hub_epoch: 'e1', active_runs: [] };
    if (type === 'get_chat_messages') return { success: true, messages: server.messages };
    if (type === 'get_employee') return { success: true, employee: { ...employee('starting'), plan: PLAN } };
    return { success: true };
  });
});

describe('a new hire’s first day', () => {
  it('shows them starting on one strip, with the box waiting and nothing to say yet', async () => {
    renderPage(employee('starting'));
    const day = await strip();
    expect(within(day).getByText('Maya')).toBeInTheDocument();
    expect(within(day).getByText('· Receptionist')).toBeInTheDocument();
    expect(within(day).getByText('Starting Maya…')).toBeInTheDocument();
    expect(within(day).getByText('Starting…').querySelector('.bg-status-ready-dot')).not.toBeNull();
    // The header's avatar, its accent face at 32px.
    expect(within(day).getByText('M')).toHaveClass('bg-accent', 'size-8');
    expect(screen.queryByText('Maya joined your team')).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('Maya is starting…')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Hi! What can you do?' })).not.toBeInTheDocument();
  });

  it('counts a hire that started them as starting before Start made a control row', async () => {
    renderPage(employee('never_started'));
    const day = await strip();
    expect(within(day).getByText('Starting Maya…')).toBeInTheDocument();
    // The header still says Not started; the strip knows the hire's start.
    expect(within(day).getByText('Starting…')).toBeInTheDocument();
    expect(within(day).queryByText('Not started')).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('Maya is starting…')).toBeDisabled();
  });

  it('says they are ready to talk once they run, and lets a message go', async () => {
    const page = renderPage(employee('starting'));
    await screen.findByText('Starting Maya…');
    page.rerender(employee('running'));
    expect(await screen.findByText('Ready to talk · 9 blocks')).toBeInTheDocument();
    expect(screen.getByText('Ready').querySelector('.bg-status-ready-dot')).not.toBeNull();
    expect(screen.getByRole('textbox', { name: 'Message Maya' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Hi! What can you do?' })).not.toBeInTheDocument();
  });

  it('says Working while they answer, as the header does', async () => {
    useNodeStatusStore.setState({ allStatuses: { w1: { 'w1:talk': { status: 'executing' } } } });
    renderPage(employee('running'));
    const day = await strip();
    expect(within(day).getByText('Working').querySelector('.bg-status-working-dot')).not.toBeNull();
  });

  it('says what to connect when the hire could not start them, and only once', async () => {
    useHomeStore.setState({ firstDays: { w1: { started: false, nodeCount: 9 } } });
    renderPage(employee('never_started', { status: 'ready', missing_apps: [whatsapp], activation_state: 'blocked' }));
    const day = await strip();
    expect(within(day).getByText('WhatsApp isn’t connected yet.')).toBeInTheDocument();
    // Connecting is not starting them: a neutral button.
    expect(within(day).getByRole('button', { name: 'Connect WhatsApp' })).not.toHaveClass('bg-action-run-soft');
    expect(within(day).getByText('Not started')).toBeInTheDocument();
    // The strip says it, so the line above the box does not.
    expect(screen.queryByText('Maya isn’t running.')).not.toBeInTheDocument();
    expect(screen.queryByText(/isn’t running, so they can’t read messages/)).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Message Maya' })).not.toBeInTheDocument();
  });

  it('says a start that failed did not go ahead, never spinning', async () => {
    renderPage(employee('never_started', { activation_state: 'failed' }));
    const day = await strip();
    expect(within(day).getByText('Maya couldn’t start.')).toBeInTheDocument();
    expect(within(day).getByRole('button', { name: 'Start' })).toHaveClass('bg-action-run-soft');
    expect(within(day).getByText('Not started')).toBeInTheDocument();
    expect(within(day).queryByText('Starting Maya…')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Message Maya' })).not.toBeInTheDocument();
  });

  it('waits for Resume while paused, a message queueing', async () => {
    renderPage(employee('paused', { status: 'paused' }));
    const day = await strip();
    expect(within(day).getByText('Maya is paused.')).toBeInTheDocument();
    expect(within(day).getByText('Stopped')).toBeInTheDocument();
    expect(within(day).getByRole('button', { name: 'Resume' })).toHaveClass('bg-action-run-soft');
    expect(screen.getByRole('textbox', { name: 'Message Maya' })).toBeEnabled();
    expect(screen.queryByText(/They’ll read your message when you resume them/)).not.toBeInTheDocument();
  });

  it('shows what they do on one line, from the hire’s plan', async () => {
    renderPage(employee('starting'));
    const list = await screen.findByRole('list', { name: 'What Maya does' });
    for (const text of ['When', 'They', 'Using', 'When a message arrives', 'Answer the question', 'Book the visit']) {
      expect(within(list).getByText(text)).toBeInTheDocument();
    }
    expect(within(list).getByText('When')).toHaveClass('text-fg-muted');
    // Titles only, and no role colour.
    expect(within(list).queryByText(/On WhatsApp/)).not.toBeInTheDocument();
    expect(list.querySelector('[class*="node-"]')).toBeNull();
  });

  it('keeps the strip after the first message, without the routine', async () => {
    server.messages = [MORNING];
    renderPage(employee('running'));
    expect(await screen.findByText('Morning!')).toBeInTheDocument();
    expect(screen.getByText('Ready to talk · 9 blocks')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'What Maya does' })).not.toBeInTheDocument();
    expect(useHomeStore.getState().firstDays.w1).toBeDefined();
  });

  it('lets a message go while they resume after talking', async () => {
    server.messages = [MORNING];
    renderPage(employee('resuming', { status: 'working' }));
    expect(await screen.findByText('Morning!')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Message Maya' })).toBeEnabled());
    expect(screen.queryByPlaceholderText('Maya is starting…')).not.toBeInTheDocument();
  });

  it('is not shown for an employee hired before this session', async () => {
    useHomeStore.setState({ firstDays: {} });
    renderPage(employee('running'));
    expect(await screen.findByRole('textbox', { name: 'Message Maya' })).toBeEnabled();
    expect(screen.queryByRole('region', { name: 'Maya’s first day' })).not.toBeInTheDocument();
  });
});
