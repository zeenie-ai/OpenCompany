/**
 * The draft panel end to end: the screen renders through json-render (its
 * code loads lazily, so the first look waits for it) as the setup card (who
 * they are with Discard beside it, the routine with Change on its When row,
 * the model's rules, then the footer strip), a double-clicked Hire
 * sends one request, a finished hire clears the draft and opens the new
 * employee's page (with what the hire said, and the AI connect dialog when
 * there is no model), "Change something" hands the composer the draft,
 * controls write back into the draft, changing when they work reaches the
 * hire, a reply in json-render's own shape works the same, the wait is
 * shown honestly with a way to cancel, and the spec inspector is for
 * developers only.
 */

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { setReducedMotion } from '@/test/waapi';
import { useShellDialogsStore } from '@/stores/shellDialogsStore';
import { useAppStore } from '@/store/useAppStore';

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

vi.mock('../../ui/pillToast', () => ({ pillToast: vi.fn() }));

import corpus from '../__fixtures__/replies.json';
import { EMPLOYEES_QUERY_KEY } from '../../data/employees';
import { useHomeStore } from '../../state/homeStore';
import { pillToast } from '../../ui/pillToast';
import { HireDraftPanel } from '../HireDraftPanel';
import { resetDraftForTests, useDraftStore } from '../draftStore';
import { normalizeSpec } from '../normalize';
import { parseReply } from '../parse';

const replies = corpus as unknown as { name: string; reply: string }[];
const reply = replies.find((c) => c.name === 'clean minified reply')!.reply;

function seedReadyDraft(text = reply) {
  const parsed = parseReply(text);
  const spec = normalizeSpec(parsed.spec)!;
  useDraftStore.setState({
    status: 'ready',
    job: 'Answer WhatsApp',
    turns: [{ change: null, reply: text }],
    spec,
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

function hired(patch: Record<string, unknown> = {}) {
  return {
    success: true,
    started: true,
    node_count: 14,
    warnings: [],
    needs_ai: false,
    employee: { workflow_id: 'w1', name: 'Maya', role: 'Receptionist', status: 'working', control: {}, revision: 1 },
    ...patch,
  };
}

/** The screen has loaded and shows everything (reduced motion: no reveal). */
function hireButton() {
  return screen.findByRole('button', { name: 'Hire Maya' });
}

let restoreMotion: () => void;

beforeAll(async () => {
  // Await the heavy json-render chunk before starting UI assertion timers.
  // HireDraftPanel still renders through its real lazy/Suspense boundary.
  await import('../HireScreen');
}, 30_000);

beforeEach(() => {
  restoreMotion = setReducedMotion(true);
  sendRequest.mockReset();
  vi.mocked(pillToast).mockClear();
  resetDraftForTests();
  seedReadyDraft();
  useHomeStore.setState({ view: { kind: 'hire' }, hireNotice: null, firstDays: {} });
  useShellDialogsStore.setState({ credentialsOpen: false });
  useAppStore.setState({ shellMode: 'normal' });
});

afterEach(() => {
  restoreMotion();
  resetDraftForTests();
  vi.useRealTimers();
  vi.unstubAllEnvs();
});

describe('HireDraftPanel', () => {
  it('keeps team details optional and allows hiring without choosing specialists', async () => {
    useDraftStore.setState({ team: [{ responsibility: 'Checks your calendar' }, { responsibility: 'Researches information' }] });
    renderPanel();
    expect(await hireButton()).toBeEnabled();
    const summary = screen.getByText('Their team');
    const details = summary.closest('details');
    expect(details).not.toHaveAttribute('open');
    fireEvent.click(summary);
    expect(screen.getByText('Checks your calendar')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Change something' })).toBeEnabled();
    expect(screen.queryByText('productivity_agent')).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: /specialist/i })).not.toBeInTheDocument();
  });

  it('shows the setup card: who they are, their routine, their rules, then the footer', async () => {
    renderPanel();
    expect(await screen.findByText('Maya')).toBeInTheDocument();
    expect(screen.getByText('Receptionist · WhatsApp, Google Calendar')).toBeInTheDocument();
    expect(screen.getByText('Answers WhatsApp and books visits.')).toBeInTheDocument();
    // The card says who they are, so the header, the model's introduction
    // and the routine's own title stay out of it.
    expect(screen.queryByText('New employee')).not.toBeInTheDocument();
    expect(screen.queryByText('Meet Maya, your new receptionist.')).not.toBeInTheDocument();
    expect(screen.queryByText('Their routine')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Discard draft' })).toBeInTheDocument();
    // The routine, its When row first, with Change.
    expect(screen.getByText('When a message arrives')).toBeInTheDocument();
    expect(screen.getByText('Book the visit')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Change' })).toHaveAttribute('aria-expanded', 'false');
    // The model's own rules stay in view.
    expect(screen.getByRole('switch', { name: 'Only reply 9 to 6' })).not.toBeChecked();
    expect(screen.getByRole('radio', { name: 'Daily' })).toBeChecked();
    // The footer: Ask first, then Change something before Hire.
    expect(screen.getByRole('switch', { name: 'Ask before sending' })).toBeChecked();
    const change = screen.getByRole('button', { name: 'Change something' });
    const hire = screen.getByRole('button', { name: 'Hire Maya' });
    expect(change.compareDocumentPosition(hire) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('keeps the header for a card with no one to introduce', async () => {
    const setup = JSON.parse(reply);
    delete setup.spec.elements.b;
    setup.spec.elements.a.children = ['c', 'd', 'e'];
    seedReadyDraft(JSON.stringify(setup));
    renderPanel();
    expect(await hireButton()).toBeInTheDocument();
    expect(screen.getByText('New employee')).toBeInTheDocument();
    expect(screen.getByText('Answer WhatsApp')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Discard draft' })).toHaveLength(1);
  });

  it('discards the draft from beside their name', async () => {
    renderPanel();
    await hireButton();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Discard draft' }));
    });
    await waitFor(() => expect(useDraftStore.getState().status).toBe('idle'));
  });

  it('sends one hire for a double click, then clears the draft and opens the employee', async () => {
    let finish!: (value: unknown) => void;
    sendRequest.mockImplementation((type: string) =>
      type === 'hire_employee' ? new Promise((resolve) => (finish = resolve)) : Promise.resolve({}),
    );
    const { queryClient } = renderPanel();
    const hire = await hireButton();
    fireEvent.click(hire);
    fireEvent.click(hire);
    const hires = sendRequest.mock.calls.filter(([type]) => type === 'hire_employee');
    expect(hires).toHaveLength(1);
    expect(hires[0][1]).toMatchObject({ name: 'Maya', rules: { ask_first: true } });
    expect(screen.getByText('Hiring your employee…')).toHaveAttribute('role', 'status');

    await act(async () => {
      finish(hired());
    });
    expect(useDraftStore.getState().status).toBe('idle');
    // Hiring selects the response's identity, but the list is refreshed from
    // the database rather than populated with the mutation's summary.
    expect(queryClient.getQueryData(EMPLOYEES_QUERY_KEY)).toEqual([]);
    await waitFor(() => expect(queryClient.getQueryState(EMPLOYEES_QUERY_KEY)?.isInvalidated).toBe(true));
    expect(useHomeStore.getState().glow?.workflowId).toBe('w1');
    expect(useHomeStore.getState().view).toEqual({ kind: 'employee', workflowId: 'w1' });
    expect(useHomeStore.getState().hireNotice).toBeNull();
    // Their page opens on their first day, which knows the hire started them.
    expect(useHomeStore.getState().firstDays.w1).toEqual({ started: true, nodeCount: 14 });
    expect(screen.queryByText('Hiring your employee…')).not.toBeInTheDocument();
  });

  it('opens Arjun’s page after a scheduled hire even when WhatsApp is not connected', async () => {
    const setup = JSON.parse(reply);
    setup.text = 'Meet Arjun, your weather reporter.';
    setup.spec.elements.b.props = {
      name: 'Arjun', role: 'Weather Reporter', description: 'Monitors Bangalore weather and sends your daily forecast.',
      apps: ['Web browser', 'WhatsApp'], status: 'ready',
    };
    setup.spec.elements.c.props.steps = [
      { title: 'Every day at 08:00', detail: 'Starts daily forecast routine.', role: 'trigger' },
      { title: 'Check Bangalore weather', detail: 'Looks up temperatures and rain chances.', role: 'tool', app: 'Web browser' },
      { title: 'Prepare brief forecast', detail: 'Summarizes conditions.', role: 'agent' },
      { title: 'Send morning summary', detail: 'Delivers to WhatsApp.', role: 'tool', app: 'WhatsApp' },
    ];
    setup.spec.elements.i.props = {
      label: 'Hire Arjun', variant: 'primary', action: 'hire_employee',
      actionParams: { name: 'Arjun', role: 'Weather Reporter', apps: ['Web browser', 'WhatsApp'],
        trigger: { kind: 'schedule', every: 'day', at: '08:00' }, sendsVia: 'WhatsApp' },
    };
    seedReadyDraft(JSON.stringify(setup));
    useDraftStore.setState({ job: 'Monitor Bangalore weather and send a daily forecast to WhatsApp' });
    sendRequest.mockImplementation((_type: string, payload: Record<string, unknown>) => Promise.resolve(hired({
      type: 'hire_employee_result', request_id: 'wire-hire-arjun', operation_request_id: payload.idempotency_key,
      started: false, activation_state: 'blocked',
      employee: { workflow_id: 'arjun-workflow', name: 'Arjun', role: 'Weather Reporter', status: 'ready', control: {}, revision: 1 },
      missing_apps: [{ app_id: 'whatsapp', name: 'WhatsApp', connected: false }],
    })));
    renderPanel();
    const hire = await screen.findByRole('button', { name: 'Hire Arjun' });
    await act(async () => { fireEvent.click(hire); });
    await waitFor(() => expect(useHomeStore.getState().view).toEqual({ kind: 'employee', workflowId: 'arjun-workflow' }));
    expect(useDraftStore.getState()).toMatchObject({ status: 'idle', hiring: false });
    // A hire held for WhatsApp did not start them; the first day says why.
    expect(useHomeStore.getState().firstDays['arjun-workflow']).toEqual({ started: false, nodeCount: 14 });
    expect(sendRequest).toHaveBeenCalledWith('hire_employee', expect.objectContaining({
      name: 'Arjun', trigger: { kind: 'schedule', every: 'day', at: '08:00' }, rules: expect.objectContaining({ ask_first: true }),
    }), expect.any(Number));
  });

  it('keeps what the hire said for the new employee’s page, and asks for an AI model when there is none', async () => {
    const warnings = ['Google Calendar is left out while they ask before sending anything'];
    sendRequest.mockResolvedValue(hired({ started: false, needs_ai: true, warnings }));
    renderPanel();
    const hire = await hireButton();
    await act(async () => {
      fireEvent.click(hire);
    });
    const home = useHomeStore.getState();
    expect(home.view).toEqual({ kind: 'employee', workflowId: 'w1' });
    expect(home.hireNotice).toEqual({ workflowId: 'w1', name: 'Maya', warnings });
    expect(useShellDialogsStore.getState()).toMatchObject({
      credentialsOpen: true,
      credentialsOptions: { categoryId: 'ai', intent: 'connect' },
    });
  });

  it.each(['team_temporal_required', 'team_agent_workflow_required', 'team_runtime_not_ready'])('shows a saved hire waiting for %s in plain words', async (issue) => {
    sendRequest.mockResolvedValue(hired({ started: false, activation_state: 'blocked', readiness_issue: issue }));
    renderPanel();
    const hire = await hireButton();
    await act(async () => { fireEvent.click(hire); });
    const notice = useHomeStore.getState().hireNotice;
    expect(notice?.warnings[0]).toMatch(/They are hired and their team is saved/);
    expect(notice?.warnings[0]).not.toMatch(/Temporal|AgentWorkflow|team_runtime/);
    expect(useHomeStore.getState().view).toEqual({ kind: 'employee', workflowId: 'w1' });
  });

  it('tells the owner in plain words when the same hire is still going through', async () => {
    sendRequest.mockResolvedValue({ success: false, error: 'busy' });
    renderPanel();
    const hire = await hireButton();
    await act(async () => {
      fireEvent.click(hire);
    });
    expect(pillToast).toHaveBeenCalledWith('They are still being set up. Give it a moment, then press Hire again.', {
      tone: 'error',
    });
    expect(useDraftStore.getState().hiring).toBe(false);
    expect(useHomeStore.getState().view).toEqual({ kind: 'hire' });
  });

  it('hands the draft to the composer for a change', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Change something' }));
    expect(useDraftStore.getState().refining).toBe(true);
  });

  it('writes a toggle and a choice back into the draft', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('switch', { name: 'Only reply 9 to 6' }));
    expect(useDraftStore.getState().uiState).toMatchObject({ rules: { hours: true } });
    expect(screen.getByRole('switch', { name: 'Only reply 9 to 6' })).toBeChecked();
    fireEvent.click(screen.getByRole('radio', { name: 'Weekly' }));
    expect(useDraftStore.getState().uiState).toMatchObject({ choices: { report: 'Weekly' } });
  });

  it('hires on the schedule the owner picked, and the routine follows it', async () => {
    sendRequest.mockResolvedValue(hired());
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Change' }));
    expect(screen.getByRole('button', { name: 'Done' })).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(screen.getByRole('radio', { name: 'On a schedule' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Weekdays' }));
    // The routine's When row says it.
    expect(screen.getByText('Every weekday at 09:00')).toBeInTheDocument();
    expect(screen.queryByText('When a message arrives')).not.toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Hire Maya' }));
    });
    const payload = sendRequest.mock.calls.find(([type]) => type === 'hire_employee')![1];
    expect(payload.trigger).toEqual({ kind: 'schedule', every: 'weekday', at: '09:00' });
    expect(payload.steps[0]).toEqual({ title: 'Every weekday at 09:00', detail: '', role: 'trigger' });
  });

  it('works the same from a reply in json-render’s own shape', async () => {
    resetDraftForTests();
    seedReadyDraft(replies.find((c) => c.name === 'json-render shape: on.press and checked')!.reply);
    sendRequest.mockResolvedValue(hired());
    renderPanel();
    expect(await screen.findByRole('switch', { name: 'Ask before sending' })).toBeChecked();
    fireEvent.click(screen.getByRole('switch', { name: 'Ask before sending' }));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Hire Maya' }));
    });
    const payload = sendRequest.mock.calls.find(([type]) => type === 'hire_employee')![1];
    expect(payload).toMatchObject({ name: 'Maya', rules: { ask_first: false }, sends_via: 'WhatsApp' });
  });

  it('opens the guided AI connect dialog from a setup that found no AI model', () => {
    useDraftStore.setState({ status: 'failed', failure: { code: 'no_ai_provider', text: 'Answer WhatsApp', refine: false } });
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Connect an AI model' }));
    expect(useShellDialogsStore.getState()).toMatchObject({
      credentialsOpen: true,
      credentialsOptions: { categoryId: 'ai', intent: 'connect' },
    });
  });

  it('shows how long the setup has taken, and Cancel hands the words back', () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] });
    resetDraftForTests();
    useDraftStore.setState({
      status: 'working',
      job: 'Answer WhatsApp',
      token: 't1',
      request: { text: 'Answer WhatsApp', refine: false },
    });
    sendRequest.mockResolvedValue({ success: true });
    renderPanel();
    expect(screen.getByText('Writing their setup…')).toBeInTheDocument();
    expect(screen.getByRole('timer')).toHaveTextContent('0:00');
    act(() => {
      vi.advanceTimersByTime(65_000);
    });
    expect(screen.getByRole('timer')).toHaveTextContent('1:05');
    expect(screen.getByText(/Some AI models take a few minutes/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(sendRequest).toHaveBeenCalledWith('cancel_employee_setup', { draft_token: 't1' });
    expect(useDraftStore.getState()).toMatchObject({ status: 'idle', input: 'Answer WhatsApp' });
  });

  it('shows the spec and its patch stream in Dev mode in development builds', async () => {
    vi.stubEnv('DEV', true);
    useAppStore.setState({ shellMode: 'dev' });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /Layout JSON/ }));
    expect(screen.getByRole('tab', { name: 'spec.json' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText(/"Their routine"/)).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'patches.jsonl' }));
    expect(screen.getByText(/\{"op":"add","path":"\/root","value":"a"\}/)).toBeInTheDocument();
  });

  it('keeps the spec inspector out of a release build', async () => {
    vi.stubEnv('DEV', false);
    renderPanel();
    await hireButton();
    expect(screen.queryByRole('button', { name: /Layout JSON/ })).not.toBeInTheDocument();
  });

  it('keeps technical JSON out of Normal mode even in development builds', async () => {
    vi.stubEnv('DEV', true);
    renderPanel();
    await hireButton();
    expect(screen.queryByRole('button', { name: /Layout JSON/ })).not.toBeInTheDocument();
  });
});
