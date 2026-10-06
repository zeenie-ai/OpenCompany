/**
 * Talking to an employee on their page (EmployeeChat over the shared chat):
 * the thread (every generation, with a divider at each restart), sending
 * (taken back with the owner's words when the server refuses it), the
 * employee working until their run ends, a retry on the status line, one
 * message waiting while paused, the box giving way to Start, the footnote
 * on asking first, Turn on Talk, and Apply.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));

vi.mock('../../../app/useShellActions', () => ({ enterDev: vi.fn() }));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { WORKFLOW_CONTROL_REQUEST_TIMEOUT, normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { useComposerStore } from '@/features/chat/state/composerStore';
import { resetChatRunStore, useChatRunStore } from '@/stores/chatRunStore';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';
import { EMPLOYEES_QUERY_KEY, employeeDetailKey, removeEmployee, useEmployeesQuery } from '../data/employees';
import { presentEmployee } from '../data/presentation';
import { parseEmployee, type EmployeeSummary } from '../data/schemas';
import { EmployeeChat } from '../employee/EmployeeChat';
import type { EmployeeControl } from '../employee/useEmployeeControl';
import { pillToast } from '../ui/pillToast';
// Loaded up front so the turns' lazy markdown resolves from the module cache.
import '@/features/chat/markdown/ReplyMarkdown';

const AGENT = 'w1:talk';
type Wire = Record<string, unknown>;

function employee(patch: Record<string, unknown> = {}, state = 'running'): EmployeeSummary {
  return parseEmployee({
    workflow_id: 'w1',
    name: 'Maya',
    role: 'Receptionist',
    status: state === 'running' ? 'working' : state === 'paused' ? 'paused' : 'ready',
    talk: { state: 'on', agent_node_id: AGENT },
    control: normalizeWorkflowControlStatus({ generation: 1, revision: 4, state }, 'w1'),
    ...patch,
  })!;
}

function controlFor(summary: EmployeeSummary): EmployeeControl {
  const view = presentEmployee(summary);
  return { view, label: view.primary.kind === 'resume' ? 'Resume' : 'Start', busy: false, act: vi.fn() };
}

let server: { messages: Wire[]; send: Wire };
let client: QueryClient;

function TeamSubscription() {
  useEmployeesQuery();
  return null;
}

function renderChat(summary: EmployeeSummary) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(EMPLOYEES_QUERY_KEY, [summary]);
  client.setQueryData(employeeDetailKey(summary.workflow_id), summary);
  const view = (next: EmployeeSummary) => (
    <QueryClientProvider client={client}>
      <TeamSubscription />
      <EmployeeChat employee={next} control={controlFor(next)} />
    </QueryClientProvider>
  );
  const utils = render(view(summary));
  return { rerender: (next: EmployeeSummary) => utils.rerender(view(next)) };
}

function row(id: number, role: 'user' | 'assistant', message: string, patch: Wire = {}): Wire {
  return { id, role, message, timestamp: new Date(2026, 8, 28, 9, id).toISOString(), run_key: 'g1', ...patch };
}

function frame(seq: number, suffix: string, data: Wire = {}, runId = 'r1') {
  return {
    specversion: '1.0',
    id: `${runId}:${seq}`,
    source: 'opencompany://services/chat',
    type: `com.opencompany.chat.run.${suffix}`,
    subject: runId,
    data: { workflow_id: 'w1', session_id: 'w1', run_id: runId, seq, hub_epoch: 'e1', ...data },
  };
}

function runEvents(...frames: unknown[]) {
  act(() => {
    const store = useChatRunStore.getState();
    for (const item of frames) store.receive(item);
    store.flush();
  });
}

async function write(text: string) {
  fireEvent.change(await screen.findByRole('textbox', { name: 'Message Maya' }), { target: { value: text } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled());
}

beforeEach(() => {
  resetChatRunStore();
  useComposerStore.setState({ drafts: {} });
  useNodeStatusStore.setState({ allStatuses: {} });
  vi.mocked(pillToast).mockClear();
  server = { messages: [], send: { success: true, message_id: 'm_new', run_id: 'r1', delivery: 'now' } };
  sendRequest.mockReset().mockImplementation(async (type: string, data: Wire) => {
    if (type === 'chat_subscribe') return { success: true, hub_epoch: 'e1', active_runs: [] };
    if (type === 'get_chat_messages') return { success: true, messages: server.messages };
    if (type === 'send_chat_message') {
      if (server.send.success !== false) {
        server.messages = [...server.messages, row(server.messages.length + 1, 'user', String(data.message), { run_id: server.send.run_id })];
      }
      return server.send;
    }
    return { success: true };
  });
});

describe('the thread', () => {
  it('reads every generation and marks the restart', async () => {
    server.messages = [
      row(1, 'user', 'Morning!'),
      row(2, 'assistant', 'Hi! Two **bookings** today, see [the calendar](https://example.com/cal).'),
      row(3, 'user', 'Still there?', { run_key: 'g2' }),
    ];
    renderChat(employee());
    expect(await screen.findByText('Still there?')).toBeInTheDocument();
    expect(sendRequest).toHaveBeenCalledWith('get_chat_messages', { session_id: 'w1', limit: 200, all_generations: true });
    expect(screen.getByText('Maya restarted — they start fresh from here')).toBeInTheDocument();
    // An answer is markdown, and its links leave the conversation open.
    expect(await screen.findByText('bookings', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'the calendar' })).toHaveAttribute('target', '_blank');
  });

  it('starts as just the message box, with whether they ask first under it', async () => {
    renderChat(employee({ asks_first: true }));
    expect(await screen.findByRole('textbox', { name: 'Message Maya' })).toHaveValue('');
    expect(screen.getByRole('log', { name: 'Conversation with Maya' })).toBeEmptyDOMElement();
    expect(screen.getByText('Maya asks before sending anything on your behalf.')).toBeInTheDocument();
  });

  it('puts the cursor in the message box on Cmd/Ctrl+K', async () => {
    renderChat(employee());
    const box = await screen.findByRole('textbox', { name: 'Message Maya' });
    expect(box).not.toHaveFocus();
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    expect(box).toHaveFocus();
  });
});

describe('sending', () => {
  it('works until the run ends, whatever else lands meanwhile', async () => {
    renderChat(employee());
    await write('Any bookings today?');
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Message Maya' }), { key: 'Enter' });
    expect(await screen.findByText('Thinking')).toBeInTheDocument();

    // A routine report from another run does not end the wait (Talk bug 9).
    server.messages = [...server.messages, row(8, 'assistant', 'Daily summary: all quiet.', { run_id: 'r0' })];
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['chatThread'] });
    });
    expect(await screen.findByText('Daily summary: all quiet.')).toBeInTheDocument();
    expect(screen.getByText('Thinking')).toBeInTheDocument();

    runEvents(frame(1, 'started'), frame(2, 'finished', { outcome: { type: 'success' } }));
    server.messages = [...server.messages, row(9, 'assistant', 'Two, at 10 and at 3.', { run_id: 'r1' })];
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['chatThread'] });
    });
    expect(await screen.findByText('Two, at 10 and at 3.')).toBeInTheDocument();
    expect(screen.queryByText('Thinking')).not.toBeInTheDocument();
  });

  it('says on the status line when the employee will retry', async () => {
    server.messages = [row(1, 'user', 'Hello', { run_id: 'r1' })];
    sendRequest.mockImplementation(async (type: string) =>
      type === 'chat_subscribe'
        ? { success: true, hub_epoch: 'e1', active_runs: [{ run_id: 'r1', session_id: 'w1', state: 'running', seq: 1, hub_epoch: 'e1' }] }
        : { success: true, messages: server.messages },
    );
    act(() => useNodeStatusStore.getState().setStatus('w1', AGENT, {
      status: 'executing', data: { phase: 'retry_wait', retry_message: 'Gemini is temporarily unavailable.' },
    }));
    renderChat(employee());
    expect(await screen.findByText('Gemini is temporarily unavailable. Retrying automatically…')).toBeInTheDocument();
    act(() => useNodeStatusStore.getState().setStatus('w1', AGENT, { status: 'executing' }));
    expect(screen.queryByText(/Retrying automatically/)).not.toBeInTheDocument();
  });

  it('takes a refused message back, keeps the text and says they are not running', async () => {
    server.send = { success: false, error: 'not_running' };
    renderChat(employee());
    await write('Hello?');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('Maya isn’t running. Start them first.', { tone: 'error' }));
    expect(screen.getByRole('log', { name: 'Conversation with Maya' })).not.toHaveTextContent('Hello?');
    expect(screen.getByRole('textbox', { name: 'Message Maya' })).toHaveValue('Hello?');
    expect(screen.queryByText('Thinking')).not.toBeInTheDocument();
  });

  it('keeps a message the employee cannot take yet, saying they are still working', async () => {
    server.send = { success: false, error: 'run_in_progress', run_id: 'r0' };
    renderChat(employee());
    await write('And Sunday?');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() =>
      expect(pillToast).toHaveBeenCalledWith('Maya is still working on your last message. Send this once they answer.', { tone: 'info' }),
    );
    expect(screen.getByRole('textbox', { name: 'Message Maya' })).toHaveValue('And Sunday?');
  });

  it('lets one message wait while the employee is paused', async () => {
    server.send = { success: true, message_id: 'm_new', run_id: 'r1', delivery: 'queued' };
    const { rerender } = renderChat(employee({}, 'paused'));
    expect(await screen.findByText('Maya is paused. They’ll read your message when you resume them.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument();

    await write('Call me back');
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Message Maya' }), { key: 'Enter' });
    expect(await screen.findByText('Your message is waiting. Maya will read it when you resume them.')).toBeInTheDocument();
    expect(screen.getByText('Waiting for you to resume Maya.')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Maya' }), { target: { value: 'One more' } });
    // The waiting message can be withdrawn meanwhile: Send is Stop.
    expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stop reply' })).toBeEnabled();

    // Resumed: the waiting message goes to them now.
    rerender(employee({}, 'running'));
    runEvents(frame(1, 'started'));
    expect(await screen.findByText('Thinking')).toBeInTheDocument();
  });

  it('gives the box to Start while the employee is not running', async () => {
    renderChat(employee({}, 'never_started'));
    expect(await screen.findByText('Maya isn’t running, so they can’t read messages right now.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start' })).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Message Maya' })).not.toBeInTheDocument();
  });

  it('notes an employee whose setup cannot answer', async () => {
    renderChat(employee({ talk: { state: 'unsupported', agent_node_id: null } }));
    expect(await screen.findByText('You can’t message Maya here. Their setup has no way to answer you.')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Message Maya' })).not.toBeInTheDocument();
  });

  it('still offers their main action without Talk (Talk bug 7)', async () => {
    renderChat(employee({ talk: { state: 'off', agent_node_id: null } }, 'never_started'));
    expect(await screen.findByText('Maya isn’t running.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Turn on Talk' })).toBeInTheDocument();
  });
});

describe('Turn on Talk', () => {
  it('reviews Talk without losing drafts, then turns it on', async () => {
    const user = userEvent.setup();
    const off = employee({ talk: { state: 'off', agent_node_id: null }, pending_approvals: 2 });
    const fromDatabase = employee({ revision: 10, talk: { state: 'on', agent_node_id: AGENT } });
    sendRequest.mockImplementation(async (type: string) =>
      type === 'enable_employee_talk'
        ? { success: true, employee: { ...off, control: undefined, talk: { state: 'on', agent_node_id: AGENT }, revision: 9 } }
        : type === 'list_employees'
          ? { success: true, employees: [fromDatabase] }
          : type === 'get_employee'
            ? { success: true, employee: fromDatabase }
          : type === 'chat_subscribe'
            ? { success: true, hub_epoch: 'e1', active_runs: [] }
            : { success: true, messages: [] },
    );
    renderChat(off);
    expect(screen.queryByRole('textbox', { name: 'Message Maya' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Turn on Talk' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(dialog).toHaveTextContent('Their conversations and drafts waiting for approval stay in place.');
    await user.click(screen.getByRole('button', { name: 'Turn on Talk' }));

    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('Talk is on. Say hello to Maya.'));
    const [, payload, timeout] = sendRequest.mock.calls.find(([type]) => type === 'enable_employee_talk')!;
    expect(payload).toEqual({ workflow_id: 'w1', idempotency_key: expect.any(String) });
    expect(timeout).toBe(WORKFLOW_CONTROL_REQUEST_TIMEOUT);
    await waitFor(() => expect(client.getQueryData<EmployeeSummary>(employeeDetailKey('w1'))).toMatchObject({ revision: 10, talk: { state: 'on' } }));
    await waitFor(() => expect(client.getQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY)?.[0]).toMatchObject({ revision: 10, talk: { state: 'on' } }));
    expect(sendRequest).toHaveBeenCalledWith('list_employees', {});
  });
});

describe('Apply', () => {
  function applyServer(apply: (resolve: (value: unknown) => void) => unknown, employees: () => unknown[]) {
    sendRequest.mockImplementation((type: string) => {
      if (type === 'apply_employee_changes') return new Promise((resolve) => apply(resolve));
      if (type === 'list_employees') return Promise.resolve({ success: true, employees: employees() });
      if (type === 'chat_subscribe') return Promise.resolve({ success: true, hub_epoch: 'e1', active_runs: [] });
      return Promise.resolve({ success: true, messages: [] });
    });
  }

  it('applies the saved abilities after current work finishes', async () => {
    const summary = employee({ pending_changes: true });
    const fromDatabase = employee({ pending_changes: false, revision: 14 });
    applyServer((resolve) => resolve({ success: true, employee: { ...summary, control: undefined, pending_changes: false, revision: 12 } }), () => [fromDatabase]);
    renderChat(summary);
    expect(await screen.findByText(/Maya has new abilities saved\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('Maya will use the new abilities after current work finishes.'));
    expect(sendRequest).toHaveBeenCalledWith(
      'apply_employee_changes',
      { workflow_id: 'w1', idempotency_key: expect.any(String) },
      WORKFLOW_CONTROL_REQUEST_TIMEOUT,
    );
    await waitFor(() => expect(client.getQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY)?.[0]).toMatchObject({ revision: 14, pending_changes: false }));
    expect(sendRequest).toHaveBeenCalledWith('list_employees', {});
  });

  it('refreshes database state when the handoff fails instead of merging its response summary', async () => {
    const summary = employee({ pending_changes: true });
    const fromDatabase = employee({ pending_changes: true, revision: 15 }, 'ready');
    applyServer(
      (resolve) => resolve({ success: false, error: 'apply_failed', employee: { ...summary, control: { state: 'ready' }, revision: 13 } }),
      () => [fromDatabase],
    );
    renderChat(summary);
    fireEvent.click(await screen.findByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('Maya couldn’t apply the new abilities yet. Their current setup is still in place.', { tone: 'error' }));
    await waitFor(() => expect(client.getQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY)?.[0]).toMatchObject({ revision: 15, control: { state: 'ready' } }));
    expect(sendRequest).toHaveBeenCalledWith('list_employees', {});
  });

  it('does not recreate a deleted employee when an earlier Apply response arrives', async () => {
    const summary = employee({ pending_changes: true });
    let finish!: (response: unknown) => void;
    applyServer((resolve) => { finish = resolve; }, () => []);
    renderChat(summary);
    fireEvent.click(await screen.findByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(finish).toBeTypeOf('function'));
    await act(async () => { removeEmployee(client, 'w1'); });
    await act(async () => { finish({ success: true, employee: { ...summary, revision: 99 } }); });
    await waitFor(() => expect(sendRequest.mock.calls.filter(([type]) => type === 'list_employees')).toHaveLength(2));
    expect(client.getQueryData(EMPLOYEES_QUERY_KEY)).toEqual([]);
    expect(client.getQueryData(employeeDetailKey('w1'))).toBeUndefined();
  });
});
