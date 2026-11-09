/**
 * The Workspace dock: what it saves and how it reads that back, the orb
 * spike on opening, whose workspace it shows, its one row (the tabs, the
 * status dot, the main action: a round Stop or Resume, else a small
 * labelled one; Expand and Close), the Canvas tab (no request without a
 * Canvas, the board with one), Take over beside the screen it takes and
 * Hand back in the banner on every tab, the floating status pill, closing,
 * and the header button's dot.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement, ReactNode, Ref } from 'react';

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

interface SurfaceProps {
  workflowId: string;
  nodes: { node_id: string; label: string }[];
  visible: boolean;
  chrome?: string;
  action?: ReactNode;
  ref?: Ref<{ claim: () => unknown }>;
}

// The screen's Take over handle (components/workspace/surface.ts), and the
// way to give back what a claim took.
const screenControl = vi.hoisted(() => ({ claim: vi.fn(), giveBack: vi.fn() }));
// The Browser and phone views: their nodes, their look and their `action`
// slot, where the dock puts Take over. As the real views, they show it only
// while the owner does not hold their screen: from a granted claim until it
// is given back or the view hides.
const surface = vi.hoisted(() => (testId: string) => async () => {
  const { useImperativeHandle, useState } = await import('react');
  return {
    default: function Surface({ workflowId, nodes, visible, chrome, action, ref }: SurfaceProps) {
      const [held, setHeld] = useState(false);
      if (held && !visible) setHeld(false);
      useImperativeHandle(ref, () => ({
        claim: async () => {
          const giveBack = (await screenControl.claim()) as (() => void) | null;
          if (!giveBack) return null;
          setHeld(true);
          return () => {
            setHeld(false);
            giveBack();
          };
        },
      }));
      return (
        <div data-testid={testId} data-workflow={workflowId} data-visible={String(visible)} data-chrome={chrome}>
          {nodes.map((node) => (
            <span key={node.node_id}>{node.label}</span>
          ))}
          {!held && action}
        </div>
      );
    },
  };
});
vi.mock('@/components/browser/BrowserWorkspace', surface('browser-workspace'));
vi.mock('@/components/mobile/MobileWorkspace', surface('mobile-workspace'));

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
const BANNER = 'You’re in control · Maya is waiting';

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

/** The dot at the end of the dock's row, named by the status it shows. */
const statusDot = (name: string) => screen.getByRole('status', { name });
/** The pill floating at the dock's foot. */
const statusPill = () => screen.getByRole('complementary').querySelector('[aria-live="polite"]');
const frame = () => document.querySelector('[data-slot="workspace-takeover-frame"]');
/** The pill at the foot while the owner has the screen: the text and Hand back. */
const banner = () => screen.getByText(BANNER).closest<HTMLElement>('[data-slot="workspace-status"]')!;

function takeOverAnswers() {
  screenControl.claim.mockReset().mockResolvedValue(screenControl.giveBack);
  screenControl.giveBack.mockReset();
  actions.pauseWorkflow.mockReset().mockResolvedValue(normalizeWorkflowControlStatus({ generation: 1, state: 'paused', revision: 5 }, 'w1'));
  actions.resumeWorkflow.mockReset().mockResolvedValue(normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 6 }, 'w1'));
}

const running = (revision: number, id = 'w1') => normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision }, id);

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
    takeovers: {},
    takeoverBusy: {},
    workspaceResizing: false,
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
    expect(screen.queryByRole('tablist', { hidden: true })).toBeNull();

    act(() => useHomeStore.getState().openWorkspace());
    expect(dock).not.toHaveAttribute('aria-hidden', 'true');
    expect(screen.getByRole('tablist', { name: 'Workspace views' })).toBeInTheDocument();
    // Lying over the page below 1100px, it casts a modal's shadow.
    expect(dock).toHaveClass('max-[1100px]:shadow-modal');
    fireEvent.click(screen.getByRole('button', { name: 'Close workspace' }));
    expect(useHomeStore.getState().workspaceOpen).toBe(false);
    expect(dock).toHaveAttribute('aria-hidden', 'true');
    expect(dock).toHaveAttribute('inert');
  });

  it('has no title, avatar or task line: the page’s header says who they are', () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee()]);
    expect(screen.queryByText('Maya’s workspace')).toBeNull();
    expect(screen.queryByText('Replying to Priya')).toBeNull();
  });

  it('says while its edge is dragged, for what sits beside it', () => {
    useHomeStore.setState({ workspaceOpen: true });
    renderWith([employee()]);
    const handle = screen.getByRole('separator', { name: 'Resize workspace' });
    // Neutral, never a role colour (the editor's handles keep their purple).
    expect(handle).toHaveClass('hover:bg-border-strong');
    expect(handle.className).not.toMatch(/node-agent/);
    fireEvent.mouseDown(handle, { clientX: 900 });
    expect(useHomeStore.getState().workspaceResizing).toBe(true);
    expect(handle).toHaveClass('bg-border-strong');
    fireEvent.mouseUp(document);
    expect(useHomeStore.getState().workspaceResizing).toBe(false);
  });

  it('expands and closes from round buttons at the end of the row', () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee()]);
    const expand = screen.getByRole('button', { name: 'Expand' });
    expect(expand).toHaveClass('size-7.5', 'rounded-full');
    expect(screen.getByRole('button', { name: 'Close workspace' })).toHaveClass('size-7.5', 'rounded-full');
    fireEvent.click(expand);
    expect(useHomeStore.getState().workspaceWide).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Restore size' }));
    expect(useHomeStore.getState().workspaceWide).toBe(false);
  });

  it.each([
    ['Live', {}],
    ['Stopped', { status: 'paused' }],
    ['Needs you', { browser_request: { node_id: 'w1:browser:1', reason: 'login', since: null } }],
    ['Needs you', { pending_approvals: 2 }],
  ])('shows %s in the status dot', (label, patch) => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee(patch)]);
    expect(statusDot(label)).toHaveAttribute('title', label);
  });

  it('shows Waiting in the status dot while the owner has the screen', () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser', takeovers: { w1: { screen: 'browser', giveBack: vi.fn() } } });
    renderWith([employee({ status: 'paused' })]);
    expect(statusDot('Waiting')).toHaveAttribute('title', 'Waiting');
    expect(screen.queryByRole('status', { name: 'Stopped' })).toBeNull();
  });

  it('offers a round green Resume when the employee is stopped', () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ status: 'paused' })]);
    const resume = screen.getByRole('button', { name: 'Resume' });
    expect(resume).toBeEnabled();
    expect(resume).toHaveAttribute('title', 'Resume');
    // Green, as starting them is, and round, with no words on it.
    expect(resume).toHaveClass('bg-action-run-soft', 'size-7.5', 'rounded-full');
    expect(resume).toHaveTextContent('');
  });

  it('pauses the employee from a round, quiet Stop', () => {
    actions.pauseWorkflow.mockReturnValue(new Promise(() => {}));
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ control: running(4) })]);
    const stop = screen.getByRole('button', { name: 'Stop' });
    expect(stop).toHaveAttribute('data-variant', 'quiet');
    expect(stop).toHaveClass('size-7.5', 'rounded-full');
    expect(stop).toHaveTextContent('');
    fireEvent.click(stop);
    expect(actions.pauseWorkflow).toHaveBeenCalledWith('w1', 4);
    expect(screen.getByRole('button', { name: 'Stopping…' })).toBeDisabled();
  });

  it('does not say stopped while the stop is still draining', async () => {
    // The server answers a Stop within 10 s even while work drains (1b.15).
    vi.mocked(pillToast).mockClear();
    actions.pauseWorkflow.mockResolvedValue(normalizeWorkflowControlStatus({ generation: 1, state: 'pausing', revision: 5 }, 'w1'));
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ control: running(4) })]);
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    await waitFor(() => expect(actions.pauseWorkflow).toHaveBeenCalledWith('w1', 4));
    await act(async () => {});
    expect(pillToast).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Stopping…' })).toBeDisabled();
  });

  it('offers a small labelled Start for an employee never started', () => {
    actions.startEmployee.mockReset().mockReturnValue(new Promise(() => {}));
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ status: 'ready', control: normalizeWorkflowControlStatus({ generation: 0, state: 'never_started' }, 'w1') })]);
    const start = screen.getByRole('button', { name: 'Start' });
    expect(start).toHaveTextContent('Start');
    expect(start).toHaveClass('h-7.5', 'bg-action-run-soft');
    fireEvent.click(start);
    expect(actions.startEmployee).toHaveBeenCalledWith('w1', 0);
  });

  it('offers a small labelled Retry reset while a Reset waits in resetting', async () => {
    const resetting = normalizeWorkflowControlStatus({ generation: 1, state: 'resetting', revision: 7, root_execution_id: 'r1' }, 'w1');
    actions.getWorkflowControlStatus.mockReset().mockResolvedValue(resetting);
    actions.resetWorkflow.mockReset().mockReturnValue(new Promise(() => {}));
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ status: 'ready', control: resetting })]);
    const retry = screen.getByRole('button', { name: 'Retry reset' });
    // Labelled and neutral: no intent colour on Home.
    expect(retry).toHaveTextContent('Retry reset');
    expect(retry).toHaveAttribute('data-variant', 'quiet');
    expect(retry).toHaveClass('h-7.5');
    fireEvent.click(retry);
    await waitFor(() => expect(actions.resetWorkflow).toHaveBeenCalledWith('w1', 7));
    expect(actions.getWorkflowControlStatus).toHaveBeenCalledWith('w1');
    expect(screen.getByRole('button', { name: 'Resetting…' })).toBeDisabled();
  });

  it('follows the employee last opened, else the first', () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee(), employee({ workflow_id: 'w2', name: 'Leo' })]);
    expect(screen.getByTestId('browser-workspace')).toHaveAttribute('data-workflow', 'w1');
    act(() => useHomeStore.getState().showEmployee('w2'));
    expect(screen.getByTestId('browser-workspace')).toHaveAttribute('data-workflow', 'w2');
  });

  it('mounts the selected employee’s browser and phone nodes in Home’s look, and keeps the tab chosen', async () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([
      employee({
        browser_nodes: [{ node_id: 'w1:browser:1', label: 'Research browser' }],
        workspace_nodes: [
          { kind: 'mobile', node_id: 'w1:mobile:1', label: 'Front desk phone' },
          { kind: 'browser', node_id: 'w1:browser:1', label: 'Research browser' },
        ],
      }),
    ]);
    const browser = screen.getByTestId('browser-workspace');
    expect(within(browser).getByText('Research browser')).toBeInTheDocument();
    expect(browser).toHaveAttribute('data-workflow', 'w1');
    expect(browser).toHaveAttribute('data-visible', 'true');
    expect(browser).toHaveAttribute('data-chrome', 'home');
    await userEvent.click(screen.getByRole('tab', { name: 'Mobile' }));
    const phone = screen.getByTestId('mobile-workspace');
    expect(phone).toHaveTextContent('Front desk phone');
    expect(phone).toHaveAttribute('data-chrome', 'home');
    expect(useHomeStore.getState().workspaceTab).toBe('android');
    expect(screen.queryByTestId('browser-workspace')).toBeNull();
  });

  it('puts Take over with the screen it takes: the browser’s row and the phone’s buttons', async () => {
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee()]);
    expect(within(screen.getByTestId('browser-workspace')).getByRole('button', { name: 'Take over' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Take over' })).toHaveLength(1);

    await userEvent.click(screen.getByRole('tab', { name: 'Mobile' }));
    expect(within(screen.getByTestId('mobile-workspace')).getByRole('button', { name: 'Take over' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Take over' })).toHaveLength(1);

    await userEvent.click(screen.getByRole('tab', { name: 'Canvas' }));
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
  });

  it('takes over the screen and stops the employee, and hands both back', async () => {
    takeOverAnswers();
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ control: running(4) })]);
    expect(frame()).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Take over' }));
    // The screen first, so the employee never acts on it again, then Stop.
    expect(screenControl.claim).toHaveBeenCalledOnce();
    expect(actions.pauseWorkflow).toHaveBeenCalledWith('w1', 4);
    expect(screenControl.claim.mock.invocationCallOrder[0]).toBeLessThan(actions.pauseWorkflow.mock.invocationCallOrder[0]);
    expect(await screen.findByText(BANNER)).toBeInTheDocument();
    expect(useHomeStore.getState().takeovers).toMatchObject({ w1: { screen: 'browser', stop: expect.any(Promise) } });
    // Take over left the screen the owner now holds: the keyboard is on Hand back.
    expect(within(banner()).getByRole('button', { name: 'Hand back' })).toHaveFocus();
    // The banner is the pill at the dock's foot, in the room each body keeps free: it
    // covers nothing under the toolbar, such as the employee's request.
    expect(banner().parentElement).toHaveClass('absolute', 'bottom-3.5');
    expect(statusPill()).toHaveTextContent(new RegExp(`^${BANNER}$`));
    expect(banner()).toHaveClass('border-action-config-border', 'text-action-config-ink');
    // The body under the row is framed, and Take over is gone while the owner has the screen.
    expect(frame()).toHaveClass('border-action-config-border', 'top-(--h-home-header)');
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();

    await userEvent.click(within(banner()).getByRole('button', { name: 'Hand back' }));
    expect(screenControl.giveBack).toHaveBeenCalledOnce();
    expect(actions.resumeWorkflow).toHaveBeenCalledOnce();
    expect(actions.resumeWorkflow.mock.calls[0][0]).toBe('w1');
    expect(screen.queryByText(BANNER)).toBeNull();
    expect(frame()).toBeNull();
    expect(useHomeStore.getState().takeovers).toEqual({});
    // Hand back went with the keyboard on it: the keyboard is on the tab on show.
    expect(screen.getByRole('tab', { name: 'Browser' })).toHaveFocus();
  });

  it('leaves the keyboard alone when the owner went on elsewhere while the screen was being taken', async () => {
    takeOverAnswers();
    let grant!: (giveBack: () => void) => void;
    screenControl.claim.mockReset().mockReturnValue(new Promise((resolve) => (grant = resolve)));
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ control: running(4) })], (
      <>
        <input aria-label="Message Maya" />
        <WorkspaceDock onConnect={vi.fn()} />
      </>
    ));
    await userEvent.click(screen.getByRole('button', { name: 'Take over' }));
    await userEvent.click(screen.getByRole('textbox', { name: 'Message Maya' }));
    await act(async () => grant(screenControl.giveBack));
    expect(await screen.findByText(BANNER)).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Message Maya' })).toHaveFocus();
  });

  it('offers Take over again on the screen that let go of the owner, and stops no one twice', async () => {
    takeOverAnswers();
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ control: running(4) })]);
    await userEvent.click(screen.getByRole('button', { name: 'Take over' }));
    expect(await screen.findByText(BANNER)).toBeInTheDocument();
    // The phone was not taken: it offers nothing while the owner has Maya.
    await userEvent.click(screen.getByRole('tab', { name: 'Mobile' }));
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
    // The browser let go when its view hid, and offers Take over again.
    await userEvent.click(screen.getByRole('tab', { name: 'Browser' }));
    await userEvent.click(within(screen.getByTestId('browser-workspace')).getByRole('button', { name: 'Take over' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull());
    expect(screenControl.claim).toHaveBeenCalledTimes(2);
    expect(actions.pauseWorkflow).toHaveBeenCalledOnce();
    expect(screen.getByText(BANNER)).toBeInTheDocument();
    expect(within(banner()).getByRole('button', { name: 'Hand back' })).toHaveFocus();

    await userEvent.click(within(banner()).getByRole('button', { name: 'Hand back' }));
    expect(screenControl.giveBack).toHaveBeenCalledOnce();
    expect(actions.resumeWorkflow).toHaveBeenCalledOnce();
    expect(useHomeStore.getState().takeovers).toEqual({});
  });

  it('offers the one Hand back in the banner, on all three tabs, after a phone Take over', async () => {
    takeOverAnswers();
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'android' });
    renderWith([employee({ control: running(4) })]);
    await userEvent.click(within(screen.getByTestId('mobile-workspace')).getByRole('button', { name: 'Take over' }));
    expect(await screen.findByText(BANNER)).toBeInTheDocument();
    for (const tab of ['Mobile', 'Browser', 'Canvas']) {
      await userEvent.click(screen.getByRole('tab', { name: tab }));
      expect(screen.getAllByRole('button', { name: 'Hand back' })).toHaveLength(1);
      expect(within(banner()).getByRole('button', { name: 'Hand back' })).toHaveAttribute('data-variant', 'invert');
      expect(frame()).not.toBeNull();
      expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
    }
    // The taken screen's view went with its tab; Hand back still gives it back.
    await userEvent.click(within(banner()).getByRole('button', { name: 'Hand back' }));
    expect(screenControl.giveBack).toHaveBeenCalledOnce();
    expect(actions.resumeWorkflow.mock.calls[0][0]).toBe('w1');
  });

  it('keeps each employee’s Take over while the dock shows another', async () => {
    const giveBackMaya = vi.fn();
    const giveBackLeo = vi.fn();
    screenControl.claim.mockReset().mockResolvedValueOnce(giveBackMaya).mockResolvedValueOnce(giveBackLeo);
    actions.pauseWorkflow.mockReset().mockResolvedValue(normalizeWorkflowControlStatus({ generation: 1, state: 'paused', revision: 5 }, 'w1'));
    actions.resumeWorkflow.mockReset().mockResolvedValue(normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 6 }, 'w1'));
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ control: running(4) }), employee({ workflow_id: 'w2', name: 'Leo', control: running(9, 'w2') })]);
    await userEvent.click(screen.getByRole('button', { name: 'Take over' }));
    expect(await screen.findByText(BANNER)).toBeInTheDocument();
    act(() => useHomeStore.getState().showEmployee('w2'));
    await userEvent.click(await screen.findByRole('button', { name: 'Take over' }));
    expect(await screen.findByText('You’re in control · Leo is waiting')).toBeInTheDocument();

    act(() => useHomeStore.getState().showEmployee('w1'));
    await userEvent.click(await screen.findByRole('button', { name: 'Hand back' }));
    expect(giveBackMaya).toHaveBeenCalledOnce();
    expect(giveBackLeo).not.toHaveBeenCalled();
    expect(actions.resumeWorkflow).toHaveBeenCalledOnce();
    expect(actions.resumeWorkflow.mock.calls[0][0]).toBe('w1');
    expect(Object.keys(useHomeStore.getState().takeovers)).toEqual(['w2']);
  });

  it('leaves a stopped employee stopped, and stops no one when the screen is refused', async () => {
    screenControl.claim.mockReset().mockResolvedValue(screenControl.giveBack);
    actions.pauseWorkflow.mockReset();
    actions.resumeWorkflow.mockReset();
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
    renderWith([employee({ status: 'paused' })]);
    await userEvent.click(screen.getByRole('button', { name: 'Take over' }));
    expect(await screen.findByText(BANNER)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Hand back' }));
    expect(actions.pauseWorkflow).not.toHaveBeenCalled();
    expect(actions.resumeWorkflow).not.toHaveBeenCalled();

    screenControl.claim.mockResolvedValue(null);
    await userEvent.click(await screen.findByRole('button', { name: 'Take over' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Take over' })).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Take over' })).toHaveFocus();
    expect(actions.pauseWorkflow).not.toHaveBeenCalled();
    expect(screen.queryByText(BANNER)).toBeNull();
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

  it('says so when no one is on the team, on a plain row with Expand and Close', () => {
    useHomeStore.setState({ workspaceOpen: true });
    renderWith([]);
    expect(screen.getByText('No one on your team yet')).toBeInTheDocument();
    expect(screen.getByText('Workspace')).toBeInTheDocument();
    expect(screen.queryByRole('tablist')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.getByRole('button', { name: 'Expand' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close workspace' }));
    expect(useHomeStore.getState().workspaceOpen).toBe(false);
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

describe('The Workspace status pill', () => {
  const STEPS = [
    { id: 1, surface: 'browser', text: 'Opened example.com', at: '2026-10-09T10:00:00Z' },
    { id: 2, surface: 'canvas', text: 'Showed The plan on the Canvas', at: '2026-10-09T10:01:00Z' },
    { id: 3, surface: 'mobile', text: 'Tapped Send', at: '2026-10-09T10:02:00Z' },
  ];
  const clock = (at: string) => new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  let steps: typeof STEPS;

  const stepCalls = () => actions.sendRequest.mock.calls.filter(([type]) => type === 'workspace_steps_list');

  beforeEach(() => {
    steps = STEPS;
    actions.sendRequest.mockImplementation(async (type: string, data?: Record<string, unknown>) =>
      type === 'workspace_steps_list'
        ? { success: true, workflow_id: data?.workflow_id, steps }
        : { success: true, items: [], revision: 0 },
    );
    useHomeStore.setState({ workspaceOpen: true, workspaceTab: 'browser' });
  });

  it('shows the latest step and its time while the employee is live, and no list of steps', async () => {
    renderWith([employee()]);
    await waitFor(() => expect(statusPill()).toHaveTextContent(`Tapped Send${clock(STEPS[2].at)}`));
    expect(statusPill()?.querySelector('.bg-destructive')).not.toBeNull();
    expect(stepCalls()).toEqual([['workspace_steps_list', { workflow_id: 'w1' }]]);
    expect(screen.queryByRole('list', { name: 'Steps' })).toBeNull();
    expect(screen.queryByText('Opened example.com')).toBeNull();
    expect(screen.queryByText(/3\/3/)).toBeNull();
  });

  it('reads the steps again when this employee takes one, and only then', async () => {
    steps = STEPS.slice(0, 2);
    renderWith([employee()]);
    await waitFor(() => expect(statusPill()).toHaveTextContent('Showed The plan on the Canvas'));

    steps = STEPS;
    // Another employee's step first: had it read again, there would be one
    // read more by the time this employee's step shows.
    emit('workspace_step', { type: 'com.opencompany.workspace.step', subject: 'w2', data: { workflow_id: 'w2', surface: 'browser', step_id: 9 } });
    emit('workspace_step', { type: 'com.opencompany.workspace.step', subject: 'w1', data: { workflow_id: 'w1', surface: 'mobile', step_id: 3 } });
    await waitFor(() => expect(statusPill()).toHaveTextContent('Tapped Send'));
    expect(stepCalls()).toHaveLength(2);
  });

  it('says it is waiting for work before the first step, with no time', async () => {
    steps = [];
    renderWith([employee()]);
    await waitFor(() => expect(stepCalls()).toHaveLength(1));
    await act(async () => {});
    expect(statusPill()).toHaveTextContent(/^Waiting for work$/);
  });

  it('shows their status, not a step, while they are not live', async () => {
    renderWith([employee({ status: 'paused' })]);
    await waitFor(() => expect(stepCalls()).toHaveLength(1));
    await act(async () => {});
    expect(statusPill()).toHaveTextContent(/^Stopped$/);
  });

  it('is the banner while the owner has the screen', async () => {
    useHomeStore.setState({ takeovers: { w1: { screen: 'browser', giveBack: vi.fn() } } });
    renderWith([employee()]);
    await waitFor(() => expect(stepCalls()).toHaveLength(1));
    await act(async () => {});
    expect(statusPill()).toHaveTextContent(new RegExp(`^${BANNER}$`));
    expect(within(banner()).getByRole('button', { name: 'Hand back' })).toHaveAttribute('data-variant', 'invert');
  });
});

describe('WorkspaceButton', () => {
  const dot = () => screen.getByRole('button', { name: 'Workspace' }).querySelector('.bg-destructive');

  it('opens and closes the dock, pressed while it is open', () => {
    renderWith([employee()], <WorkspaceButton />);
    const button = screen.getByRole('button', { name: 'Workspace' });
    expect(button).toHaveAttribute('aria-pressed', 'false');
    expect(button).toHaveAttribute('data-variant', 'quiet');

    fireEvent.click(button);
    expect(useHomeStore.getState().workspaceOpen).toBe(true);
    expect(button).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(button);
    expect(useHomeStore.getState().workspaceOpen).toBe(false);
  });

  it('blinks red only while the dock is closed and its employee is live', () => {
    renderWith([employee()], <WorkspaceButton />);
    expect(dot()).toHaveClass('opencompany-pip-pulse');
    act(() => useHomeStore.getState().openWorkspace());
    expect(dot()).toBeNull();
  });

  it('follows the employee the dock would show, not whoever works', () => {
    renderWith([employee({ status: 'paused' }), employee({ workflow_id: 'w2', name: 'Leo' })], <WorkspaceButton />);
    // The first on the team, who is stopped.
    expect(dot()).toBeNull();
    act(() => useHomeStore.setState({ workspaceFor: 'w2' }));
    expect(dot()).not.toBeNull();
  });

  it('shows no dot while the employee needs the owner, or the owner has their screen', () => {
    renderWith([employee({ pending_approvals: 1 }), employee({ workflow_id: 'w2', name: 'Leo' })], <WorkspaceButton />);
    expect(dot()).toBeNull();
    act(() => useHomeStore.setState({ workspaceFor: 'w2', takeovers: { w2: { screen: 'browser', giveBack: vi.fn() } } }));
    expect(dot()).toBeNull();
    act(() => useHomeStore.setState({ takeovers: {} }));
    expect(dot()).not.toBeNull();
  });
});
