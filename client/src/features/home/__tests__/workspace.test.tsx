/**
 * The Workspace dock: what it saves and how it reads that back, the orb
 * spike on opening, whose workspace it shows, the header's pill, the
 * placeholder tabs, the Canvas tab (no request without a Canvas, the board
 * with one), closing, and the header pill's dot.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';

const actions = { isReady: true, sendRequest: vi.fn(), addEventListener: () => () => {} };

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => actions,
}));

vi.mock('../../../app/useShellActions', () => ({ enterDev: vi.fn() }));
vi.mock('@/components/browser/BrowserWorkspace', () => ({
  default: ({ workflowId, nodes, visible }: { workflowId: string; nodes: { node_id: string; label: string }[]; visible: boolean }) => (
    <div data-testid="browser-workspace" data-workflow={workflowId} data-visible={String(visible)}>
      {nodes.length ? nodes.map((node) => <span key={node.node_id}>{node.label}</span>) : 'No Browser node in this workflow'}
    </div>
  ),
}));

import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { ThemeProvider } from '@/contexts/ThemeContext';
import { enterDev } from '../../../app/useShellActions';
import { EMPLOYEES_QUERY_KEY } from '../data/employees';
import { parseEmployee, type EmployeeSummary } from '../data/schemas';
import { SPIKE, orbState } from '../orb/orb';
import { WORKSPACE_MIN_WIDTH, loadWorkspacePrefs, useHomeStore } from '../state/homeStore';
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

function renderWith(team: EmployeeSummary[], ui: ReactElement = <WorkspaceDock />) {
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

  it('shows the card’s pill when the employee is not working', () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ status: 'paused' })]);
    expect(screen.getByText('Paused')).toBeInTheDocument();
    expect(screen.queryByText('Live')).toBeNull();
  });

  it('follows the employee last opened, else the first', () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee(), employee({ workflow_id: 'w2', name: 'Leo' })]);
    expect(screen.getByText('Maya’s workspace')).toBeInTheDocument();
    act(() => useHomeStore.getState().showEmployee('w2'));
    expect(screen.getByText('Leo’s workspace')).toBeInTheDocument();
  });

  it('mounts the selected employee’s browser nodes and keeps the Android panel available', async () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ browser_nodes: [{ node_id: 'w1:browser:1', label: 'Research browser' }] })]);
    expect(screen.getByText('Research browser')).toBeInTheDocument();
    expect(screen.getByTestId('browser-workspace')).toHaveAttribute('data-workflow', 'w1');
    expect(screen.getByTestId('browser-workspace')).toHaveAttribute('data-visible', 'true');
    await userEvent.click(screen.getByRole('tab', { name: 'Android' }));
    expect(screen.getByText('The Android mirror isn’t available yet')).toBeInTheDocument();
    expect(useHomeStore.getState().workspaceTab).toBe('android');
    expect(screen.queryByTestId('browser-workspace')).toBeNull();
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
