/**
 * The Workspace dock: what it saves and how it reads that back, the orb
 * spike on opening, whose workspace it shows, the header's pill and main
 * action (Stop lives here), the tabs, the Canvas tab (no request without a
 * Canvas, the board with one), the footer's timeline of steps and Take
 * over, closing, and the header pill's dot.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';

// Broadcast listeners, so a test can send the dock a frame (`emit`).
const listeners = new Map<string, Set<(data: unknown) => void>>();
const actions = {
  isReady: true,
  sendRequest: vi.fn(),
  addEventListener: (type: string, listener: (data: unknown) => void) => {
    const forType = listeners.get(type) ?? new Set();
    forType.add(listener);
    listeners.set(type, forType);
    return () => {
      forType.delete(listener);
    };
  },
  pauseWorkflow: vi.fn(),
  resumeWorkflow: vi.fn(),
  resetWorkflow: vi.fn(),
  getWorkflowControlStatus: vi.fn(),
  startEmployee: vi.fn(),
};

function emit(type: string, data: unknown) {
  act(() => listeners.get(type)?.forEach((listener) => listener(data)));
}

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => actions,
}));

vi.mock('../../../app/useShellActions', () => ({ enterDev: vi.fn() }));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));
// The browser's Take over handle (components/workspace/surface.ts), and the
// way to give back what a claim took.
const screenControl = vi.hoisted(() => ({ claim: vi.fn(), giveBack: vi.fn() }));
vi.mock('@/components/browser/BrowserWorkspace', async () => {
  const { useImperativeHandle } = await import('react');
  return {
    default: function Browser({ workflowId, nodes, visible, ref }: { workflowId: string; nodes: { node_id: string; label: string }[]; visible: boolean; ref?: import('react').Ref<{ claim: typeof screenControl.claim }> }) {
      useImperativeHandle(ref, () => ({ claim: screenControl.claim }));
      return (
        <div data-testid="browser-workspace" data-workflow={workflowId} data-visible={String(visible)}>
          {nodes.length ? nodes.map((node) => <span key={node.node_id}>{node.label}</span>) : 'No Browser node in this workflow'}
        </div>
      );
    },
  };
});

import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { ThemeProvider } from '@/contexts/ThemeContext';
import { enterDev } from '../../../app/useShellActions';
import { EMPLOYEES_QUERY_KEY } from '../data/employees';
import { parseEmployee, type EmployeeSummary } from '../data/schemas';
import { SPIKE, orbState } from '../orb/orb';
import { WORKSPACE_MIN_WIDTH, loadWorkspacePrefs, useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';
import { WorkspaceButton } from '../workspace/WorkspaceButton';
import { WorkspaceDock } from '../workspace/WorkspaceDock';
// Loaded up front so the dock's lazy import resolves from the module cache:
// importing the board's viewers cold can outlast findBy's wait on a busy machine.
import '../workspace/WorkspaceCanvas';

const PREFS_KEY = 'home_workspace_v1';
const DEFAULTS = { open: false, widthPx: 460, tab: 'board' };

function employee(patch: Record<string, unknown> = {}): EmployeeSummary {
  const id = typeof patch.workflow_id === 'string' ? patch.workflow_id : 'w1';
  const state = patch.status === 'paused' ? 'paused' : 'running';
  return parseEmployee({
    workflow_id: id,
    name: 'Maya',
    role: 'Receptionist',
    status: 'working',
    task: { label: 'Now', text: 'Replying to Priya' },
    canvas_node_id: `${id}:canvas:1`,
    control: normalizeWorkflowControlStatus({ generation: 1, state }, id),
    ...patch,
  })!;
}

function renderWith(team: EmployeeSummary[], ui: ReactElement = <WorkspaceDock onConnect={vi.fn()} />) {
  const sendOtherRequest = actions.sendRequest.getMockImplementation();
  actions.sendRequest.mockImplementation(async (type: string, data?: Record<string, unknown>) => {
    if (type === 'list_employees') return { success: true, employees: team };
    if (type === 'get_employee') {
      const selected = team.find((member) => member.workflow_id === data?.workflow_id);
      return selected ? { success: true, employee: selected } : { success: false, error: 'not_found' };
    }
    return sendOtherRequest?.(type, data);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(EMPLOYEES_QUERY_KEY, team);
  render(
    <ThemeProvider>
      <QueryClientProvider client={client}>{ui}</QueryClientProvider>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  listeners.clear();
  actions.sendRequest.mockReset();
  actions.sendRequest.mockResolvedValue({ success: true, items: [], revision: 0 });
  vi.mocked(enterDev).mockClear();
  useHomeStore.setState({
    workspaceOpen: false,
    workspaceWidth: 460,
    workspaceTab: 'board',
    workspaceWide: false,
    workspaceFor: null,
  });
});

describe('Workspace preferences', () => {
  it('start closed on the Canvas tab at 460px', () => {
    expect(loadWorkspacePrefs()).toEqual(DEFAULTS);
  });

  it('fall back field by field, and wholly on unreadable JSON', () => {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ open: true, widthPx: 'wide', tab: 'desk' }));
    expect(loadWorkspacePrefs()).toEqual({ ...DEFAULTS, open: true });
    localStorage.setItem(PREFS_KEY, '{not json');
    expect(loadWorkspacePrefs()).toEqual(DEFAULTS);
  });

  it('save what they restore, and keep a drag inside the window', () => {
    const store = useHomeStore.getState();
    store.openWorkspace();
    store.setWorkspaceTab('android');
    store.toggleWorkspaceWide();
    store.setWorkspaceWidth(99_999);
    expect(useHomeStore.getState()).toMatchObject({ workspaceWidth: window.innerWidth - 420, workspaceWide: false });
    store.setWorkspaceWidth(10);
    expect(useHomeStore.getState().workspaceWidth).toBe(WORKSPACE_MIN_WIDTH);
    expect(loadWorkspacePrefs()).toEqual({ open: true, widthPx: WORKSPACE_MIN_WIDTH, tab: 'android' });
  });

  it('spike the orb only when the dock opens', () => {
    orbState.spike = 0;
    useHomeStore.getState().openWorkspace();
    expect(orbState.spike).toBe(SPIKE.workspace);
    orbState.spike = 0;
    useHomeStore.getState().openWorkspace('w2');
    expect(orbState.spike).toBe(0);
    expect(useHomeStore.getState().workspaceFor).toBe('w2');
  });
});

describe('WorkspaceDock', () => {
  it('mounts nothing before it first opens, and closes out of reach', () => {
    useHomeStore.setState({ workspaceTab: 'browser' });
    renderWith([employee()]);
    // A hidden element has no accessible name, so it is found by role alone.
    const dock = screen.getByRole('complementary', { hidden: true });
    expect(dock).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByText('Maya’s workspace')).toBeNull();

    act(() => useHomeStore.getState().openWorkspace());
    expect(dock).not.toHaveAttribute('aria-hidden', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Close workspace' }));
    expect(useHomeStore.getState().workspaceOpen).toBe(false);
    expect(dock).toHaveAttribute('aria-hidden', 'true');
    expect(dock).toHaveAttribute('inert');
  });

  it('shows who it is for, what they are doing, and Live while they work', () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee()]);
    expect(screen.getByText('Maya’s workspace')).toBeInTheDocument();
    expect(screen.getByText('Replying to Priya')).toBeInTheDocument();
    expect(screen.getByText('Live')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    expect(useHomeStore.getState().workspaceWide).toBe(true);
    expect(screen.getByRole('button', { name: 'Restore size' })).toBeInTheDocument();
  });

  it('shows their status and Resume when the employee is not working', () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ status: 'paused' })]);
    expect(screen.getByText('Stopped')).toBeInTheDocument();
    expect(screen.queryByText('Live')).toBeNull();
    expect(screen.getByRole('button', { name: 'Resume' })).toBeEnabled();
  });

  it('pauses the employee from the header', () => {
    actions.pauseWorkflow.mockReturnValue(new Promise(() => {}));
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ control: normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 4 }, 'w1') })]);
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(actions.pauseWorkflow).toHaveBeenCalledWith('w1', 4);
    expect(screen.getByRole('button', { name: 'Stopping…' })).toBeDisabled();
  });

  it('does not say stopped while the stop is still draining', async () => {
    // The server answers a Stop within 10 s even while work drains (1b.15).
    vi.mocked(pillToast).mockClear();
    actions.pauseWorkflow.mockResolvedValue(normalizeWorkflowControlStatus({ generation: 1, state: 'pausing', revision: 5 }, 'w1'));
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ control: normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 4 }, 'w1') })]);
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    await waitFor(() => expect(actions.pauseWorkflow).toHaveBeenCalledWith('w1', 4));
    await act(async () => {});
    expect(pillToast).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Stopping…' })).toBeDisabled();
  });

  it('offers Retry reset in the header while a Reset waits in resetting', async () => {
    const resetting = normalizeWorkflowControlStatus({ generation: 1, state: 'resetting', revision: 7, root_execution_id: 'r1' }, 'w1');
    actions.getWorkflowControlStatus.mockReset().mockResolvedValue(resetting);
    actions.resetWorkflow.mockReset().mockReturnValue(new Promise(() => {}));
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ status: 'ready', control: resetting })]);
    // The quiet button Stop uses: no intent colour on Home.
    expect(screen.getByRole('button', { name: 'Retry reset' })).toHaveAttribute('data-variant', 'quiet');
    fireEvent.click(screen.getByRole('button', { name: 'Retry reset' }));
    await waitFor(() => expect(actions.resetWorkflow).toHaveBeenCalledWith('w1', 7));
    expect(actions.getWorkflowControlStatus).toHaveBeenCalledWith('w1');
    expect(screen.getByRole('button', { name: 'Resetting…' })).toBeDisabled();
  });

  it('follows the employee last opened, else the first', () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee(), employee({ workflow_id: 'w2', name: 'Leo' })]);
    expect(screen.getByText('Maya’s workspace')).toBeInTheDocument();
    act(() => useHomeStore.getState().showEmployee('w2'));
    expect(screen.getByText('Leo’s workspace')).toBeInTheDocument();
  });

  it('mounts the selected employee’s browser nodes and preserves the Mobile tab preference', async () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ browser_nodes: [{ node_id: 'w1:browser:1', label: 'Research browser' }] })]);
    expect(screen.getByText('Research browser')).toBeInTheDocument();
    expect(screen.getByTestId('browser-workspace')).toHaveAttribute('data-workflow', 'w1');
    expect(screen.getByTestId('browser-workspace')).toHaveAttribute('data-visible', 'true');
    await userEvent.click(screen.getByRole('tab', { name: 'Mobile' }));
    expect(screen.getByText('Add a Mobile Agent or Android tool to this workflow to use its phone here.')).toBeInTheDocument();
    expect(useHomeStore.getState().workspaceTab).toBe('android');
    expect(screen.queryByTestId('browser-workspace')).toBeNull();
  });

  it('takes over the screen and stops the employee, and hands both back', async () => {
    screenControl.claim.mockReset().mockResolvedValue(screenControl.giveBack);
    screenControl.giveBack.mockReset();
    actions.pauseWorkflow.mockReset().mockResolvedValue(normalizeWorkflowControlStatus({ generation: 1, state: 'paused', revision: 5 }, 'w1'));
    actions.resumeWorkflow.mockReset().mockResolvedValue(normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 6 }, 'w1'));
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser', takeovers: {} });
    renderWith([employee({ control: normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 4 }, 'w1') })]);
    await userEvent.click(screen.getByRole('button', { name: 'Take over' }));
    // The screen first, so the employee never acts on it again, then Stop.
    expect(screenControl.claim).toHaveBeenCalledOnce();
    expect(actions.pauseWorkflow).toHaveBeenCalledWith('w1', 4);
    expect(screenControl.claim.mock.invocationCallOrder[0]).toBeLessThan(actions.pauseWorkflow.mock.invocationCallOrder[0]);
    expect(await screen.findByText('You’re in control · Maya is waiting')).toBeInTheDocument();
    expect(useHomeStore.getState().takeovers).toEqual({ w1: { stopped: true, giveBack: screenControl.giveBack } });

    await userEvent.click(screen.getByRole('button', { name: 'Hand back' }));
    expect(screenControl.giveBack).toHaveBeenCalledOnce();
    expect(actions.resumeWorkflow).toHaveBeenCalledOnce();
    expect(screen.queryByText('You’re in control · Maya is waiting')).toBeNull();
    expect(useHomeStore.getState().takeovers).toEqual({});
  });

  it('hands back the screen that was taken, whichever tab the dock shows', async () => {
    screenControl.claim.mockReset().mockResolvedValue(screenControl.giveBack);
    screenControl.giveBack.mockReset();
    actions.pauseWorkflow.mockReset().mockResolvedValue(normalizeWorkflowControlStatus({ generation: 1, state: 'paused', revision: 5 }, 'w1'));
    actions.resumeWorkflow.mockReset().mockResolvedValue(normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 6 }, 'w1'));
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser', takeovers: {} });
    renderWith([employee({ control: normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 4 }, 'w1') })]);
    await userEvent.click(screen.getByRole('button', { name: 'Take over' }));
    expect(await screen.findByText('You’re in control · Maya is waiting')).toBeInTheDocument();
    // The taken screen's view goes with its tab.
    await userEvent.click(screen.getByRole('tab', { name: 'Canvas' }));
    expect(screen.queryByTestId('browser-workspace')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Hand back' }));
    expect(screenControl.giveBack).toHaveBeenCalledOnce();
    expect(actions.resumeWorkflow).toHaveBeenCalledWith('w1', 4);
  });

  it('keeps each employee’s Take over while the dock shows another', async () => {
    const giveBackMaya = vi.fn();
    const giveBackLeo = vi.fn();
    screenControl.claim.mockReset().mockResolvedValueOnce(giveBackMaya).mockResolvedValueOnce(giveBackLeo);
    actions.pauseWorkflow.mockReset().mockResolvedValue(normalizeWorkflowControlStatus({ generation: 1, state: 'paused', revision: 5 }, 'w1'));
    actions.resumeWorkflow.mockReset().mockResolvedValue(normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 6 }, 'w1'));
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser', takeovers: {} });
    renderWith([
      employee({ control: normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 4 }, 'w1') }),
      employee({ workflow_id: 'w2', name: 'Leo', control: normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 9 }, 'w2') }),
    ]);
    await userEvent.click(screen.getByRole('button', { name: 'Take over' }));
    expect(await screen.findByText('You’re in control · Maya is waiting')).toBeInTheDocument();
    act(() => useHomeStore.getState().showEmployee('w2'));
    await userEvent.click(await screen.findByRole('button', { name: 'Take over' }));
    expect(await screen.findByText('You’re in control · Leo is waiting')).toBeInTheDocument();

    act(() => useHomeStore.getState().showEmployee('w1'));
    await userEvent.click(await screen.findByRole('button', { name: 'Hand back' }));
    expect(giveBackMaya).toHaveBeenCalledOnce();
    expect(giveBackLeo).not.toHaveBeenCalled();
    expect(actions.resumeWorkflow).toHaveBeenCalledWith('w1', 4);
    expect(useHomeStore.getState().takeovers).toEqual({ w2: { stopped: true, giveBack: giveBackLeo } });
  });

  it('leaves a stopped employee stopped, and stops no one when the screen is refused', async () => {
    screenControl.claim.mockReset().mockResolvedValue(screenControl.giveBack);
    actions.pauseWorkflow.mockReset();
    actions.resumeWorkflow.mockReset();
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser', takeovers: {} });
    renderWith([employee({ status: 'paused' })]);
    await userEvent.click(screen.getByRole('button', { name: 'Take over' }));
    expect(await screen.findByText('You’re in control · Maya is waiting')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Hand back' }));
    expect(actions.pauseWorkflow).not.toHaveBeenCalled();
    expect(actions.resumeWorkflow).not.toHaveBeenCalled();

    screenControl.claim.mockResolvedValue(null);
    await userEvent.click(screen.getByRole('button', { name: 'Take over' }));
    expect(actions.pauseWorkflow).not.toHaveBeenCalled();
    expect(screen.queryByText('You’re in control · Maya is waiting')).toBeNull();
  });

  it('offers no Take over on the Canvas tab', () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'board', takeovers: {} });
    renderWith([employee({ canvas_node_id: null })]);
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
  });

  it('hides the live viewer when the workspace closes without changing its employee', () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee()]);
    act(() => useHomeStore.getState().closeWorkspace());
    expect(screen.getByTestId('browser-workspace')).toHaveAttribute('data-visible', 'false');
    expect(screen.getByTestId('browser-workspace')).toHaveAttribute('data-workflow', 'w1');
  });

  it('asks for no board when the employee has no Canvas', () => {
    useHomeStore.setState({ workspaceOpen: true });
    renderWith([employee({ canvas_node_id: null })]);
    expect(screen.getByText('Maya has no Canvas')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open workflow' }));
    expect(enterDev).toHaveBeenCalledWith({ workflowId: 'w1' });
    expect(actions.sendRequest).not.toHaveBeenCalledWith('canvas_list', expect.anything());
  });

  it('shows the board of the employee’s Canvas node', async () => {
    useHomeStore.setState({ workspaceOpen: true });
    renderWith([employee()]);
    expect(await screen.findByText(/^Nothing here yet\. When Maya finishes something/)).toBeInTheDocument();
    expect(actions.sendRequest).toHaveBeenCalledWith('canvas_list', { workflow_id: 'w1', node_id: 'w1:canvas:1' });
  });

  it('says so when no one is on the team', () => {
    useHomeStore.setState({ workspaceOpen: true });
    renderWith([]);
    expect(screen.getByText('No one on your team yet')).toBeInTheDocument();
  });

  it('opens on the document a reply names, on its employee’s Canvas tab', async () => {
    const note = (id: string, content: string, version = 1) => ({
      id,
      kind: 'note',
      title: null,
      ref: null,
      url: null,
      content,
      language: null,
      source: 'agent',
      created_at: null,
      version,
    });
    actions.sendRequest.mockImplementation(async (type: string) =>
      type === 'canvas_list'
        ? { success: true, items: [note('n1', 'The plan', 2), note('n2', 'Later notes')], revision: 3 }
        : { success: true },
    );
    useHomeStore.setState({ workspaceTab: 'browser' });
    orbState.spike = 0;
    renderWith([employee(), employee({ workflow_id: 'w2', name: 'Ravi' })]);
    act(() => useHomeStore.getState().openCanvasItem({ workflowId: 'w2', canvasNodeId: 'w2:canvas:1', itemId: 'n1', version: 2 }));

    expect(useHomeStore.getState()).toMatchObject({ workspaceOpen: true, workspaceFor: 'w2', workspaceTab: 'board' });
    expect(orbState.spike).toBe(SPIKE.workspace);
    expect(loadWorkspacePrefs()).toMatchObject({ open: true, tab: 'board' });
    expect(await screen.findByText('The plan')).toBeInTheDocument();
    // The first of two: the Library strip shows it chosen, and Latest goes back to the newest.
    expect(screen.getByRole('button', { name: 'Library, 2 items' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Latest' })).toBeInTheDocument();
    expect(actions.sendRequest).toHaveBeenCalledWith('canvas_list', { workflow_id: 'w2', node_id: 'w2:canvas:1' });
  });
});

describe('Workspace timeline', () => {
  const STEPS = [
    { id: 1, surface: 'browser', text: 'Opened example.com', at: '2026-10-09T10:00:00Z' },
    { id: 2, surface: 'canvas', text: 'Showed The plan on the Canvas', at: '2026-10-09T10:01:00Z' },
    { id: 3, surface: 'mobile', text: 'Tapped Send', at: '2026-10-09T10:02:00Z' },
  ];
  const clock = (at: string) => new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  let steps: typeof STEPS;

  function answerSteps() {
    actions.sendRequest.mockImplementation(async (type: string, data?: Record<string, unknown>) =>
      type === 'workspace_steps_list'
        ? { success: true, workflow_id: data?.workflow_id, steps }
        : { success: true, items: [], revision: 0 },
    );
  }

  const stepCalls = () => actions.sendRequest.mock.calls.filter(([type]) => type === 'workspace_steps_list');

  /** The line under the segments: "n/N" and the step's words and time. */
  async function shownLine(count: string) {
    return (await screen.findByText(count)).parentElement;
  }

  beforeEach(() => {
    steps = STEPS;
    answerSteps();
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser', takeovers: {} });
  });

  it('lists what the employee did, coloured by surface, and follows the newest step', async () => {
    renderWith([employee()]);
    expect(await shownLine('3/3')).toHaveTextContent(`Tapped Send · ${clock(STEPS[2].at)}`);
    expect(stepCalls()).toEqual([['workspace_steps_list', { workflow_id: 'w1' }]]);
    const segments = within(screen.getByRole('list', { name: 'Steps' })).getAllByRole('listitem');
    expect(segments.map((segment) => segment.getAttribute('aria-label'))).toEqual(
      STEPS.map((step) => `${step.text}, ${clock(step.at)}`),
    );
    // Done steps in their surface's soft shade, the step shown in its ink.
    expect(segments[0]).toHaveClass('bg-action-save-border');
    expect(segments[1]).toHaveClass('bg-action-tools-border');
    expect(segments[2]).toHaveClass('bg-action-run-ink');
    expect(segments[2]).toHaveAttribute('aria-current', 'step');
    expect(screen.queryByRole('button', { name: 'Jump to live' })).toBeNull();
  });

  it('shows an older step on its surface’s tab, and Jump to live goes back to the newest', async () => {
    renderWith([employee()]);
    await shownLine('3/3');
    const segments = within(screen.getByRole('list', { name: 'Steps' })).getAllByRole('listitem');

    await userEvent.click(segments[1]);
    expect(await shownLine('2/3')).toHaveTextContent('Showed The plan on the Canvas');
    expect(useHomeStore.getState().workspaceTab).toBe('board');
    expect(segments[1]).toHaveAttribute('aria-current', 'step');
    expect(segments[2]).toHaveClass('bg-border-default');

    await userEvent.click(segments[0]);
    expect(await shownLine('1/3')).toHaveTextContent('Opened example.com');
    expect(useHomeStore.getState().workspaceTab).toBe('browser');

    await userEvent.click(screen.getByRole('button', { name: 'Jump to live' }));
    expect(await shownLine('3/3')).toHaveTextContent('Tapped Send');
    expect(screen.queryByRole('button', { name: 'Jump to live' })).toBeNull();
  });

  it('reads the steps again when this employee takes one, and only then', async () => {
    steps = STEPS.slice(0, 2);
    renderWith([employee()]);
    await shownLine('2/2');

    steps = STEPS;
    // Another employee's step first: had it read again, there would be one
    // read more by the time this employee's step shows.
    emit('workspace_step', { type: 'com.opencompany.workspace.step', subject: 'w2', data: { workflow_id: 'w2', surface: 'browser', step_id: 9 } });
    emit('workspace_step', { type: 'com.opencompany.workspace.step', subject: 'w1', data: { workflow_id: 'w1', surface: 'mobile', step_id: 3 } });
    expect(await shownLine('3/3')).toHaveTextContent('Tapped Send');
    expect(stepCalls()).toHaveLength(2);
  });

  it('says it is waiting before the first step', async () => {
    steps = [];
    renderWith([employee()]);
    expect(await screen.findByText('Waiting for work')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Steps' })).toBeNull();
  });
});

describe('WorkspaceButton', () => {
  it('opens and closes the dock, with a live dot while it is closed and someone works', () => {
    renderWith([employee()], <WorkspaceButton />);
    const button = screen.getByRole('button', { name: 'Workspace' });
    expect(button).toHaveAttribute('aria-pressed', 'false');
    expect(button.querySelector('.opencompany-pip-pulse')).not.toBeNull();

    fireEvent.click(button);
    expect(useHomeStore.getState().workspaceOpen).toBe(true);
    expect(button).toHaveAttribute('aria-pressed', 'true');
    expect(button.querySelector('.opencompany-pip-pulse')).toBeNull();

    fireEvent.click(button);
    expect(useHomeStore.getState().workspaceOpen).toBe(false);
  });

  it('shows no dot when no one is working', () => {
    renderWith([employee({ status: 'paused' })], <WorkspaceButton />);
    expect(screen.getByRole('button', { name: 'Workspace' }).querySelector('.opencompany-pip-pulse')).toBeNull();
  });
});
