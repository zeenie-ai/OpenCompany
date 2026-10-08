/**
 * A new hire's first day on their page (EmployeeChat with FirstDay): the
 * card follows their real start (starting, ready, held for an app, failed),
 * the box waits while they start and the greetings come once they can read
 * messages, the card says why a start did not go ahead without the line
 * above the box saying it again, their routine shows from the hire's plan,
 * and the first day ends with their first conversation.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  it('shows them starting, with the box waiting and no greetings yet', async () => {
    renderPage(employee('starting'));
    expect(await screen.findByText('Maya joined your team')).toBeInTheDocument();
    expect(screen.getByText('Getting Maya ready')).toBeInTheDocument();
    expect(screen.getByText('Starting…')).toBeInTheDocument();
    expect(screen.getByText('Setup saved')).toBeInTheDocument();
    expect(screen.getByText('Workflow built · 9 blocks')).toBeInTheDocument();
    expect(screen.getByText('Starting Maya')).toBeInTheDocument();
    expect(screen.getByText('Opening their conversation')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Maya is starting…')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Hi! What can you do?' })).not.toBeInTheDocument();
  });

  it('counts a hire that started them as starting before Start made a control row', async () => {
    renderPage(employee('never_started'));
    expect(await screen.findByText('Starting Maya')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Maya is starting…')).toBeDisabled();
  });

  it('says they are ready once they run, then offers something to say', async () => {
    const page = renderPage(employee('starting'));
    await screen.findByText('Getting Maya ready');
    page.rerender(employee('running'));
    expect(await screen.findByText('Maya is ready')).toBeInTheDocument();
    expect(screen.getByText('Maya is running')).toBeInTheDocument();
    expect(screen.getByText('Ready to talk')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: 'Hi! What can you do?' }));
    expect(screen.getByRole('textbox', { name: 'Message Maya' })).toHaveValue('Hi! What can you do?');
  });

  it('says what to connect when the hire could not start them, and only once', async () => {
    useHomeStore.setState({ firstDays: { w1: { started: false, nodeCount: 9 } } });
    renderPage(employee('never_started', { status: 'ready', missing_apps: [whatsapp], activation_state: 'blocked' }));
    expect(await screen.findByText('WhatsApp isn’t connected yet.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Connect WhatsApp' })).toBeInTheDocument();
    expect(screen.getByText('Not started')).toBeInTheDocument();
    // The card says it, so the line above the box does not.
    expect(screen.queryByText('Maya isn’t running.')).not.toBeInTheDocument();
    expect(screen.queryByText(/isn’t running, so they can’t read messages/)).not.toBeInTheDocument();
  });

  it('says a start that failed did not go ahead, never spinning', async () => {
    renderPage(employee('never_started', { activation_state: 'failed' }));
    expect(await screen.findByText('Maya couldn’t start.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start' })).toBeInTheDocument();
    expect(screen.getByText('Starting Maya')).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('Maya is starting…')).not.toBeInTheDocument();
  });

  it('shows what they do, from the hire’s plan', async () => {
    renderPage(employee('starting'));
    expect(await screen.findByText('What Maya does')).toBeInTheDocument();
    expect(screen.getByText('3 steps')).toBeInTheDocument();
    expect(screen.getByText('Answer the question')).toBeInTheDocument();
    expect(screen.getByText('When')).toBeInTheDocument();
  });

  it('ends with their first conversation', async () => {
    server.messages = [{ id: 1, role: 'user', message: 'Morning!', timestamp: new Date(2026, 9, 9, 9).toISOString(), run_key: 'g1' }];
    renderPage(employee('running'));
    expect(await screen.findByText('Morning!')).toBeInTheDocument();
    await waitFor(() => expect(useHomeStore.getState().firstDays.w1).toBeUndefined());
    expect(screen.queryByText('Maya joined your team')).not.toBeInTheDocument();
  });

  it('is not shown for an employee hired before this session', async () => {
    useHomeStore.setState({ firstDays: {} });
    renderPage(employee('running'));
    expect(await screen.findByRole('textbox', { name: 'Message Maya' })).toBeEnabled();
    expect(screen.queryByText('Maya joined your team')).not.toBeInTheDocument();
  });
});
