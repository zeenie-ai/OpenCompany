/**
 * The employee page: the conversation, with their name in the header (not
 * on the page). Their main action shows above the message box while they
 * cannot read messages
 * (Connect goes to the provider's connect dialog, Resume sends the
 * summary's revision and says "Resuming…" until the summary shows them
 * running, never flashing "Resume" again in between), never the reason
 * they stopped; Help in browser while they wait for the owner there; and
 * the drafts after the messages.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const actions = {
  isReady: true,
  sendRequest: vi.fn(),
  addEventListener: () => () => {},
  pauseWorkflow: vi.fn(),
  resumeWorkflow: vi.fn(),
  startEmployee: vi.fn(),
};

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => actions,
}));

vi.mock('../../../app/useShellActions', () => ({ enterDev: vi.fn() }));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { enterDev } from '../../../app/useShellActions';
import { EMPLOYEES_QUERY_KEY } from '../data/employees';
import { parseEmployee, type EmployeeSummary } from '../data/schemas';
import { ThemeProvider } from '@/contexts/ThemeContext';
import { EmployeeView } from '../employee/EmployeeView';
import { useHomeStore } from '../state/homeStore';
import { useShellDialogsStore } from '@/stores/shellDialogsStore';
import { pillToast } from '../ui/pillToast';

const TALK_ON = { state: 'on', agent_node_id: 'w1:talk' };

function summary(patch: Record<string, unknown>, control: Record<string, unknown>): EmployeeSummary {
  return parseEmployee({
    workflow_id: 'w1',
    name: 'Maya',
    role: 'Receptionist',
    control: normalizeWorkflowControlStatus({ generation: 1, ...control }, 'w1'),
    ...patch,
  })!;
}

function renderPage(employee: EmployeeSummary, onConnect = vi.fn()) {
  const sendOtherRequest = actions.sendRequest.getMockImplementation();
  actions.sendRequest.mockImplementation(async (type: string, data?: Record<string, unknown>) => {
    if (type === 'list_employees') return { success: true, employees: [employee] };
    if (type === 'get_employee') {
      return data?.workflow_id === employee.workflow_id
        ? { success: true, employee }
        : { success: false, error: 'not_found' };
    }
    return sendOtherRequest?.(type, data);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(EMPLOYEES_QUERY_KEY, [employee]);
  render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <EmployeeView workflowId="w1" onConnect={onConnect} />
      </QueryClientProvider>
    </ThemeProvider>,
  );
  return { client, onConnect };
}

beforeEach(() => {
  actions.sendRequest.mockReset();
  actions.pauseWorkflow.mockReset();
  actions.resumeWorkflow.mockReset();
  actions.startEmployee.mockReset();
  vi.mocked(enterDev).mockClear();
  vi.mocked(pillToast).mockClear();
});

describe('EmployeeView', () => {
  it('keeps the page to the conversation while they work', () => {
    actions.sendRequest.mockResolvedValue({ success: true, messages: [] });
    const whatsapp = { app_id: 'whatsapp', provider_id: 'whatsapp', name: 'WhatsApp', connected: true, supported: true };
    renderPage(
      summary(
        { status: 'working', talk: TALK_ON, task: { label: 'Now', text: 'Waiting for new WhatsApp messages' }, done_today: 2, apps: [whatsapp] },
        { state: 'running' },
      ),
    );
    expect(screen.queryByRole('heading', { name: 'Maya' })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Message Maya' })).toBeInTheDocument();
    for (const name of ['Pause', 'Resume', 'Start', 'Watch live']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    }
    for (const text of ['Receptionist', 'Working', 'Waiting for new WhatsApp messages', 'WhatsApp']) {
      expect(screen.queryByText(text)).not.toBeInTheDocument();
    }
    expect(screen.queryByText(/done today/)).not.toBeInTheDocument();
  });

  it('connects the missing app through its provider', () => {
    const whatsapp = { app_id: 'whatsapp', provider_id: 'whatsapp', name: 'WhatsApp', connected: false, supported: true };
    const { onConnect } = renderPage(
      summary({ status: 'ready', talk: TALK_ON, apps: [whatsapp], missing_apps: [whatsapp] }, { state: 'never_started' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Connect WhatsApp' }));
    expect(onConnect).toHaveBeenCalledWith('whatsapp');
  });

  it('resumes with the summary’s revision and waits for the summary to catch up', async () => {
    let done!: (status: unknown) => void;
    actions.resumeWorkflow.mockReturnValue(new Promise((resolve) => (done = resolve)));
    const { client } = renderPage(summary({ status: 'paused', talk: TALK_ON }, { state: 'paused', revision: 7 }));

    fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
    expect(actions.resumeWorkflow).toHaveBeenCalledWith('w1', 7);
    expect(screen.getByRole('button', { name: 'Resuming…' })).toBeDisabled();

    await act(async () => done(normalizeWorkflowControlStatus({ state: 'running', revision: 8, generation: 1 }, 'w1')));
    // The summary still says paused: keep the in-flight label.
    expect(screen.getByRole('button', { name: 'Resuming…' })).toBeInTheDocument();

    await act(async () => {
      client.setQueryData(EMPLOYEES_QUERY_KEY, [summary({ status: 'working', talk: TALK_ON }, { state: 'running', revision: 8 })]);
    });
    expect(screen.queryByRole('button', { name: /^Resum/ })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Message Maya' })).toBeInTheDocument();
  });

  it('offers Resume after a problem without saying what went wrong', () => {
    const why = 'Paused after OpenCompany stopped unexpectedly. Resume when you’re ready.';
    renderPage(summary({ status: 'attention', talk: TALK_ON, task: { label: 'Paused', text: why } }, { state: 'paused', pause_reason: 'crash' }));
    expect(screen.queryByText(why)).not.toBeInTheDocument();
    expect(screen.getByText('Maya is paused. They’ll read your message when you resume them.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resume' })).toBeEnabled();
  });

  it('keeps the recovery hint visible for a failure-paused employee after reopening', () => {
    const why = 'Spending cap reached. Review billing before resuming.';
    renderPage(summary({ status: 'attention', talk: TALK_ON }, { state: 'paused', pause_reason: 'failures', pause_detail: why }));
    expect(screen.getByRole('alert')).toHaveTextContent(why);
    expect(screen.getByRole('button', { name: 'Resume' })).toBeEnabled();
  });

  it('says to connect an AI model, and opens that dialog, when Start is refused for want of one', async () => {
    useShellDialogsStore.setState({ credentialsOpen: false });
    actions.startEmployee.mockRejectedValue(new Error('needs_ai'));
    renderPage(summary({ status: 'ready', talk: TALK_ON }, { state: 'never_started' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('Connect an AI model first.', { tone: 'error' }));
    expect(useShellDialogsStore.getState()).toMatchObject({
      credentialsOpen: true,
      credentialsOptions: { categoryId: 'ai', intent: 'connect' },
    });
  });

  it.each([
    ['team_temporal_required', 'Their team is saved, but team work is not ready on this installation. Ask your administrator to finish setup, then press Start again.'],
    ['team_agent_workflow_required', 'Their team is saved, but team work is not ready on this installation. Ask your administrator to finish setup, then press Start again.'],
    ['team_runtime_not_ready', 'Their team is saved and waiting for the service to be ready. Try Start again in a moment.'],
    ['teams_disabled', 'Team hiring is not available on this installation yet. Ask your administrator to enable it.'],
  ])('explains %s without runtime terminology', async (code, message) => {
    actions.startEmployee.mockRejectedValue(new Error(code));
    renderPage(summary({ status: 'ready', talk: TALK_ON }, { state: 'never_started' }));
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(pillToast).toHaveBeenCalledWith(message, { tone: 'error' }));
  });

  it('opens the "Connect an AI model" dialog from the conversation', () => {
    useShellDialogsStore.setState({ credentialsOpen: false });
    renderPage(summary({ status: 'ready', talk: TALK_ON, needs_ai: true }, { state: 'never_started' }));
    fireEvent.click(screen.getByRole('button', { name: 'Connect an AI model' }));
    expect(useShellDialogsStore.getState()).toMatchObject({
      credentialsOpen: true,
      credentialsOptions: { categoryId: 'ai', intent: 'connect' },
    });
  });

  it('offers Start again above the message box after a failure', () => {
    actions.startEmployee.mockReturnValue(new Promise(() => {}));
    renderPage(summary({ status: 'attention', talk: TALK_ON }, { state: 'failed', can_resume: false, revision: 7 }));
    expect(screen.queryByRole('button', { name: 'Open in Dev mode' })).not.toBeInTheDocument();
    expect(screen.getByText('Maya isn’t running, so they can’t read messages right now.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Start again' }));
    expect(actions.startEmployee).toHaveBeenCalledWith('w1', 7);
    expect(enterDev).not.toHaveBeenCalled();
  });

  it('offers Start above the message box while the employee is not running', async () => {
    actions.sendRequest.mockResolvedValue({ success: true, messages: [] });
    actions.startEmployee.mockReturnValue(new Promise(() => {}));
    renderPage(summary({ status: 'ready', talk: TALK_ON }, { state: 'never_started', revision: 3 }));
    const talk = await screen.findByRole('region', { name: 'Chat with Maya' });
    expect(talk).toHaveTextContent('Maya isn’t running, so they can’t read messages right now.');
    expect(screen.queryByRole('textbox', { name: 'Message Maya' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(actions.startEmployee).toHaveBeenCalledWith('w1', 3);
    expect(screen.getByRole('button', { name: 'Starting…' })).toBeDisabled();
  });

  it('opens the Workspace on its Browser tab while they need you there', () => {
    useHomeStore.setState({ workspaceOpen: false, workspaceFor: null, workspaceTab: 'board' });
    renderPage(
      summary(
        {
          status: 'working',
          talk: TALK_ON,
          browser_request: { node_id: 'w1:browser:1', reason: 'login', since: null },
          task: { label: 'Waiting', text: 'Needs you to sign in to a site in the browser' },
        },
        { state: 'running' },
      ),
    );
    expect(screen.getByText('Needs you to sign in to a site in the browser')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Help in browser' }));
    expect(useHomeStore.getState()).toMatchObject({ workspaceOpen: true, workspaceFor: 'w1', workspaceTab: 'browser' });
  });

  it('shows the drafts waiting for the owner after the messages', async () => {
    const draft = {
      approval_id: 'a1',
      workflow_id: 'w1',
      node_id: 'n',
      status: 'pending',
      channel: 'WhatsApp',
      channel_label: 'WhatsApp',
      recipient: '447700900123',
      recipient_label: 'Priya',
      body: 'Yes! Saturday at 10 works.',
      context_excerpt: 'Can I book Saturday?',
      created_at: null,
      revision: 0,
      max_length: 400,
    };
    actions.sendRequest.mockImplementation(async (type: string) =>
      type === 'list_approvals' ? { success: true, approvals: [draft] } : { success: true, messages: [] },
    );
    renderPage(summary({ status: 'working', talk: TALK_ON, pending_approvals: 1 }, { state: 'running' }));
    const drafts = await screen.findByRole('region', { name: 'Drafts waiting for you' });
    expect(drafts).toHaveTextContent('Yes! Saturday at 10 works.');
    expect(drafts).toHaveTextContent('Maya wants to send a message');
    expect(screen.getByRole('region', { name: 'Chat with Maya' })).toContainElement(drafts);
  });

  it('notes when the employee cannot take messages', () => {
    renderPage(summary({ status: 'working' }, { state: 'running' }));
    expect(screen.getByText('You can’t message Maya here. Their setup has no way to answer you.')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Message Maya' })).not.toBeInTheDocument();
  });

  it('says so when the employee is gone', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    client.setQueryData(EMPLOYEES_QUERY_KEY, []);
    actions.sendRequest.mockResolvedValue({ success: false, error: 'not_found' });
    render(
      <QueryClientProvider client={client}>
        <EmployeeView workflowId="gone" onConnect={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('This employee is no longer on the team.')).toBeInTheDocument();
  });
});
