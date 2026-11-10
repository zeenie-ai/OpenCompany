/**
 * The editor's Workspace dock (CanvasDock): the dock Home shows, for the open
 * workflow. The shell and what it shows are shared with Home, whose test
 * (features/home/__tests__/workspace.test.tsx) covers them in depth. This one
 * covers what the editor gives them and adds: its nodes from the canvas as
 * it is now, the open workflow's employee (the status dot, the status pill
 * and Take over), no main action in the row, Dev mode's extras in the views,
 * Expand, and the Canvas tab's own row.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement, ReactNode, Ref } from 'react';
import type { Node } from 'reactflow';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

interface Hints {
  isCanvasPanel?: boolean;
  isBrowserPanel?: boolean;
  workspace?: { kind: string };
}

const mocks = vi.hoisted(() => ({
  workflow: { id: 'wf1' } as { id: string } | null,
  hints: {} as Record<string, Hints>,
  listeners: new Set<(event: { query: { queryKey: string[] } }) => void>(),
}));

// The socket: what the dock asks the server (`sendRequest`), and the
// broadcast listeners, so a test can send the dock a frame (`emit`).
const ws = vi.hoisted(() => {
  const listeners = new Map<string, Set<(data: unknown) => void>>();
  return {
    listeners,
    actions: {
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
    },
  };
});

function emit(type: string, data: unknown) {
  act(() => ws.listeners.get(type)?.forEach((listener) => listener(data)));
}

interface ViewerProps {
  workflowId?: string | null;
  nodes: { node_id: string; label: string }[];
  visible?: boolean;
  dev?: boolean;
  action?: ReactNode;
  flushCanvas?: () => void;
  ref?: Ref<{ claim: () => unknown }>;
}

// The screen's Take over handle (components/workspace/surface.ts), and the
// way to give back what a claim took.
const screenControl = vi.hoisted(() => ({ claim: vi.fn(), giveBack: vi.fn() }));
// The Browser and phone views: their workflow and nodes, whether they are
// visible, Dev mode's extras, and their `action` slot. `data-take-over` says
// what the dock put there: its own button (`dock`), nothing at all (`none`),
// or nothing passed, so the view shows its own Take over (`own`). As the real
// views, they show the dock's button only while the owner does not hold
// their screen: from a granted claim until it is given back or the view hides.
const viewer = vi.hoisted(() => (testId: string) => async () => {
  const { useImperativeHandle, useState } = await import('react');
  return {
    default: function Viewer({ workflowId, nodes, visible, dev, action, flushCanvas, ref }: ViewerProps) {
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
        <div
          data-testid={testId}
          data-workflow={workflowId ?? ''}
          data-visible={String(visible)}
          data-dev={String(dev)}
          data-take-over={action === undefined ? 'own' : action === null ? 'none' : 'dock'}
        >
          {nodes.map((node) => (
            <span key={node.node_id}>{node.label}</span>
          ))}
          {flushCanvas && (
            <button type="button" onClick={flushCanvas}>
              Save the canvas
            </button>
          )}
          {!held && action}
        </div>
      );
    },
  };
});

vi.mock('../../../store/useAppStore', () => ({ useAppStore: (select: (s: unknown) => unknown) => select({ currentWorkflow: mocks.workflow }) }));
vi.mock('../../../lib/nodeSpec', () => ({ resolveNodeDescription: (type: string) => ({ uiHints: mocks.hints[type] }) }));
vi.mock('../../../lib/queryClient', () => ({ queryClient: { getQueryCache: () => ({ subscribe: (fn: (event: { query: { queryKey: string[] } }) => void) => {
  mocks.listeners.add(fn); return () => mocks.listeners.delete(fn);
} }) } }));
vi.mock('../../../hooks/useCanvasBoard', () => ({ useCanvasBoardQuery: () => ({ data: { items: [] } }), useCanvasRemove: () => ({ mutate: vi.fn() }) }));
// The board renderer: which node's board it shows, or the item it previews.
vi.mock('../../parameterPanel/canvas/CanvasContent', () => ({ default: ({ items, nodeId }: { items: { id: string }[]; nodeId?: string | null }) => (
  <div data-testid="board">{nodeId ? `Board of ${nodeId}` : `Preview of ${items[0]?.id}`}</div>
) }));
vi.mock('../../browser/BrowserWorkspace', viewer('browser-view'));
vi.mock('../../mobile/MobileWorkspace', viewer('mobile-view'));
// Keep the real control-status helpers: the employee's summary is parsed with them.
vi.mock('../../../contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../contexts/WebSocketContext')>()),
  useWebSocketActions: () => ws.actions,
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from 'sonner';
import CanvasDock from '../CanvasDock';
import { normalizeWorkflowControlStatus } from '../../../contexts/WebSocketContext';
import { employeeDetailKey, resetSeenEmployeeEvents } from '../../../features/home/data/employees';
import { useHomeStore } from '../../../features/home/state/homeStore';
import { workspaceStepsKey } from '../../../features/home/workspace/steps';
import type { CanvasItem } from '../../../lib/canvasBoard';
import { useCanvasDockStore } from '../../../stores/canvasDockStore';
import { useLlmWaitStore, type LlmWait } from '../../../stores/llmWaitStore';

const nodes: Node[] = [
  { id: 'board1', type: 'boardPlugin', position: { x: 0, y: 0 }, data: { label: 'Board' } },
  { id: 'web1', type: 'webPlugin', position: { x: 1, y: 0 }, data: { label: 'Research browser' } },
];

const RUNNING = { status: 'working', control: { generation: 1, state: 'running', revision: 4 } };
const STOPPED = { status: 'paused', control: { generation: 1, state: 'paused', revision: 5 } };
const NEVER_STARTED = { status: 'ready' };

// What `get_employee` answers for the open workflow, and `workspace_steps_list`
// for their steps (oldest first).
let employeeAnswer: (workflowId?: string) => unknown;
let steps: { id: number; surface: string; text: string; at: string }[];
/** The workflow's employee, Scout. */
const found = (patch: Record<string, unknown> = NEVER_STARTED) => (workflowId?: string) => ({
  success: true,
  employee: { workflow_id: workflowId, name: 'Scout', ...patch },
});
/** No summary: `not_found` where the workflow has no employee, else a read that failed. */
const refused = (error: string) => () => ({ success: false, error });

function renderDock(ui: ReactElement = <CanvasDock nodes={nodes} />) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return {
    client,
    rerender: (next: ReactElement) => view.rerender(<QueryClientProvider client={client}>{next}</QueryClientProvider>),
  };
}

// Radix tabs change on mouse down.
const showTab = (name: string) => fireEvent.mouseDown(screen.getByRole('tab', { name }), { button: 0, ctrlKey: false });
/** The dock, open or closed (closed, it is out of the accessibility tree). */
const dock = () => screen.getByRole('complementary', { hidden: true });
/** The dock's one row: the tabs, then what sits at its end. */
const row = () => screen.getByRole('tablist', { name: 'Workspace views' }).parentElement!;
/** The dot at the end of the row, named by the status it shows. */
const statusDot = (name: string) => screen.findByRole('status', { name });
/** The pill floating at the dock's foot. */
const pill = () => document.querySelector('[data-slot="workspace-status"]');
/** What the dock put in a view's Take over slot: `dock`, `none` or `own`. */
const takeOverIn = (view: 'browser-view' | 'mobile-view') => screen.getByTestId(view).getAttribute('data-take-over');

beforeEach(() => {
  localStorage.clear();
  mocks.workflow = { id: 'wf1' };
  mocks.hints = { boardPlugin: { isCanvasPanel: true }, webPlugin: { isBrowserPanel: true }, phonePlugin: { workspace: { kind: 'mobile' } } };
  mocks.listeners.clear();
  ws.listeners.clear();
  employeeAnswer = refused('not_found');
  steps = [];
  ws.actions.sendRequest.mockReset().mockImplementation(async (type: string, data?: { workflow_id?: string }) => {
    if (type === 'get_employee') return employeeAnswer(data?.workflow_id);
    if (type === 'workspace_steps_list') return { success: true, workflow_id: data?.workflow_id, steps };
    return { success: true };
  });
  ws.actions.pauseWorkflow.mockReset();
  ws.actions.resumeWorkflow.mockReset();
  screenControl.claim.mockReset();
  screenControl.giveBack.mockReset();
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
  resetSeenEmployeeEvents();
  useCanvasDockStore.setState({ open: true, widthPx: 460, mode: 'node', tab: 'board', selectedNodeId: null, ephemeralItem: null, focus: null });
  useHomeStore.setState({ takeovers: {}, takeoverBusy: {} });
  useLlmWaitStore.getState().resetSession();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Developer workspace', () => {
  it('shares Browser, Canvas and Mobile tabs and the workflow-bound viewer', () => {
    const phone: Node = { id: 'phone1', type: 'phonePlugin', position: { x: 2, y: 0 }, data: { label: 'Front desk phone' } };
    renderDock(<CanvasDock nodes={[...nodes, phone]} />);
    expect(within(screen.getByRole('tablist', { name: 'Workspace views' })).getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Browser', 'Canvas', 'Mobile']);
    expect(screen.getByText('Board of board1')).toBeInTheDocument();
    showTab('Browser');
    expect(screen.getByText('Research browser')).toBeInTheDocument();
    expect(screen.getByTestId('browser-view')).toHaveAttribute('data-workflow', 'wf1');
    expect(screen.getByTestId('browser-view')).toHaveAttribute('data-visible', 'true');
    showTab('Mobile');
    expect(screen.getByText('Front desk phone')).toBeInTheDocument();
    expect(screen.getByTestId('mobile-view')).toHaveAttribute('data-workflow', 'wf1');
    expect(screen.getByTestId('mobile-view')).toHaveAttribute('data-visible', 'true');
    expect(screen.queryByTestId('browser-view')).toBeNull();
  });

  it('discovers a browser after schema hydration without a graph edit', () => {
    mocks.hints.webPlugin = {};
    useCanvasDockStore.setState({ tab: 'browser' });
    renderDock();
    expect(screen.queryByText('Research browser')).toBeNull();
    act(() => {
      mocks.hints.webPlugin = { isBrowserPanel: true };
      for (const fn of mocks.listeners) fn({ query: { queryKey: ['nodeSpec'] } });
    });
    expect(screen.getByText('Research browser')).toBeInTheDocument();
  });

  it('is the dock Home shows: a labelled panel whose edge drags, with no title row', () => {
    renderDock();
    expect(screen.getByRole('complementary', { name: 'Workspace' })).toBeInTheDocument();
    expect(screen.getByRole('separator', { name: 'Resize workspace' })).toBeInTheDocument();
    // The one row is the tabs, with no title over them.
    expect(screen.queryByText('Workspace')).toBeNull();
  });

  it('mounts nothing and reads no summary before it first opens', async () => {
    employeeAnswer = found();
    useCanvasDockStore.setState({ open: false, tab: 'browser' });
    renderDock();
    expect(dock()).toHaveAttribute('aria-hidden', 'true');
    expect(dock()).toHaveAttribute('inert');
    expect(screen.queryByRole('tablist', { hidden: true })).toBeNull();
    expect(screen.queryByTestId('browser-view')).toBeNull();
    expect(ws.actions.sendRequest).not.toHaveBeenCalled();

    act(() => useCanvasDockStore.getState().toggle());
    expect(dock()).not.toHaveAttribute('aria-hidden', 'true');
    expect(screen.getByTestId('browser-view')).toHaveAttribute('data-visible', 'true');
    expect(await statusDot('Not started')).toBeInTheDocument();
    expect(ws.actions.sendRequest).toHaveBeenCalledWith('get_employee', { workflow_id: 'wf1' });
  });

  it('keeps its contents mounted when closed, and tells the live viewer it is not visible', () => {
    useCanvasDockStore.setState({ tab: 'browser' });
    renderDock();
    expect(dock()).not.toHaveAttribute('aria-hidden', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Close workspace' }));
    expect(useCanvasDockStore.getState().open).toBe(false);
    // Out of reach, not unmounted: the viewer stays and stops showing its screen.
    expect(dock()).toHaveAttribute('aria-hidden', 'true');
    expect(dock()).toHaveAttribute('inert');
    expect(screen.getByTestId('browser-view')).toHaveAttribute('data-visible', 'false');
    expect(screen.getByTestId('browser-view')).toHaveAttribute('data-workflow', 'wf1');
  });

  it('expands and restores from the round buttons at the end of the row', () => {
    renderDock();
    const expand = screen.getByRole('button', { name: 'Expand' });
    expect(expand).toHaveClass('size-7.5', 'rounded-full');
    expect(screen.getByRole('button', { name: 'Close workspace' })).toHaveClass('size-7.5', 'rounded-full');
    fireEvent.click(expand);
    expect(screen.queryByRole('button', { name: 'Expand' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Restore size' }));
    expect(screen.getByRole('button', { name: 'Expand' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Restore size' })).toBeNull();
  });

  it('ends Expand when its edge is dragged, and keeps the dragged width', () => {
    vi.stubGlobal('innerWidth', 1280);
    renderDock();
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    // A drag starts from the width on screen, Expand's 860px in this window
    // and not the 460px saved; the edge is the left one, so 100px to the
    // right narrows the dock by as much.
    vi.spyOn(dock(), 'getBoundingClientRect').mockReturnValue({ width: 860 } as DOMRect);
    fireEvent.mouseDown(screen.getByRole('separator', { name: 'Resize workspace' }), { clientX: 420 });
    fireEvent.mouseMove(document, { clientX: 520 });
    fireEvent.mouseUp(document);
    expect(useCanvasDockStore.getState().widthPx).toBe(760);
    expect(screen.getByRole('button', { name: 'Expand' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Restore size' })).toBeNull();
  });

  it('gives the Browser and phone views Dev mode’s extras', () => {
    const flushCanvas = vi.fn();
    useCanvasDockStore.setState({ tab: 'browser' });
    renderDock(<CanvasDock nodes={nodes} flushCanvas={flushCanvas} />);
    const browser = screen.getByTestId('browser-view');
    expect(browser).toHaveAttribute('data-dev', 'true');
    // Browser AI tasks saves the canvas through the editor before it adds to it.
    fireEvent.click(within(browser).getByRole('button', { name: 'Save the canvas' }));
    expect(flushCanvas).toHaveBeenCalledOnce();
    showTab('Mobile');
    expect(screen.getByTestId('mobile-view')).toHaveAttribute('data-dev', 'true');
  });
});

// Every workflow is an employee: the open one's summary gives the dock its
// status dot, its status pill and its Take over.
describe('The open workflow’s employee', () => {
  // A step's time as the pill words it. Its spaces are plain ones here, as
  // in the text the matcher compares it with (some locales put a narrow one
  // before AM).
  const clock = (at: string) => new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }).replace(/\s+/g, ' ');

  it.each([
    ['Not started', NEVER_STARTED],
    ['Stopped', STOPPED],
  ])('shows %s in the status dot and in the pill at the foot', async (label, patch) => {
    employeeAnswer = found(patch);
    renderDock();
    expect(await statusDot(label)).toHaveAttribute('title', label);
    expect(pill()).toHaveTextContent(new RegExp(`^${label}$`));
    expect(ws.actions.sendRequest).toHaveBeenCalledWith('get_employee', { workflow_id: 'wf1' });
  });

  it('shows a live employee’s latest step and its time in the pill', async () => {
    employeeAnswer = found(RUNNING);
    steps = [
      { id: 1, surface: 'browser', text: 'Opened example.com', at: '2026-10-09T10:00:00Z' },
      { id: 2, surface: 'canvas', text: 'Showed The plan on the Canvas', at: '2026-10-09T10:01:00Z' },
    ];
    renderDock();
    expect(await statusDot('Live')).toBeInTheDocument();
    await waitFor(() => expect(pill()).toHaveTextContent(`Showed The plan on the Canvas${clock(steps[1].at)}`));
    expect(ws.actions.sendRequest).toHaveBeenCalledWith('workspace_steps_list', { workflow_id: 'wf1' });
    expect(screen.queryByText('Opened example.com')).toBeNull();
  });

  it('says a live employee is waiting for work before their first step', async () => {
    employeeAnswer = found(RUNNING);
    const { client } = renderDock();
    expect(await statusDot('Live')).toBeInTheDocument();
    await waitFor(() => expect(client.getQueryState(workspaceStepsKey('wf1'))?.status).toBe('success'));
    expect(pill()).toHaveTextContent(/^Waiting for work$/);
  });

  it('shows neither where the workflow has no employee, and leaves the views their own Take over', async () => {
    employeeAnswer = refused('not_found');
    useCanvasDockStore.setState({ tab: 'browser' });
    renderDock();
    await waitFor(() => expect(takeOverIn('browser-view')).toBe('own'));
    expect(ws.actions.sendRequest).toHaveBeenCalledWith('get_employee', { workflow_id: 'wf1' });
    expect(screen.queryByRole('status')).toBeNull();
    expect(pill()).toBeNull();
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
    // No one's steps to read.
    expect(ws.actions.sendRequest).not.toHaveBeenCalledWith('workspace_steps_list', expect.anything());
    showTab('Mobile');
    expect(takeOverIn('mobile-view')).toBe('own');
  });

  it('shows neither, and no Take over at all, while the summary cannot be read', async () => {
    // Whether there is an employee stays unknown, so no screen is taken that
    // the dock would run Take over for once the read goes through.
    employeeAnswer = refused('internal_error');
    useCanvasDockStore.setState({ tab: 'browser' });
    const { client } = renderDock();
    await waitFor(() => expect(client.getQueryState(employeeDetailKey('wf1'))?.status).toBe('error'));
    expect(takeOverIn('browser-view')).toBe('none');
    expect(screen.queryByRole('status')).toBeNull();
    expect(pill()).toBeNull();
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
    showTab('Mobile');
    expect(takeOverIn('mobile-view')).toBe('none');
  });

  it('asks for no summary for a workflow not saved yet: the tabs and the views only', () => {
    mocks.workflow = null;
    useCanvasDockStore.setState({ tab: 'browser' });
    renderDock();
    expect(ws.actions.sendRequest).not.toHaveBeenCalled();
    expect(screen.getByRole('tablist', { name: 'Workspace views' })).toBeInTheDocument();
    expect(screen.queryByRole('status')).toBeNull();
    expect(pill()).toBeNull();
    expect(screen.getByTestId('browser-view')).toHaveAttribute('data-workflow', '');
    expect(takeOverIn('browser-view')).toBe('own');
  });

  it('offers no Take over at all while it does not know yet whether there is an employee', async () => {
    let answer!: (reply: unknown) => void;
    employeeAnswer = () => new Promise((resolve) => (answer = resolve));
    useCanvasDockStore.setState({ tab: 'browser' });
    renderDock();
    // Not the dock's, and not the view's own: the owner must not take a screen
    // the dock is about to run Take over for.
    expect(takeOverIn('browser-view')).toBe('none');
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
    expect(pill()).toBeNull();
    showTab('Mobile');
    expect(takeOverIn('mobile-view')).toBe('none');

    await act(async () => answer(found(RUNNING)('wf1')));
    await waitFor(() => expect(takeOverIn('mobile-view')).toBe('dock'));
    expect(within(screen.getByTestId('mobile-view')).getByRole('button', { name: 'Take over' })).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Live' })).toBeInTheDocument();
  });

  it.each([
    ['Live', RUNNING],
    ['Stopped', STOPPED],
    ['Not started', NEVER_STARTED],
  ])('has no main action in its row for an employee who is %s: the toolbar has the workflow’s', async (label, patch) => {
    employeeAnswer = found(patch);
    useCanvasDockStore.setState({ tab: 'browser' });
    renderDock();
    expect(await statusDot(label)).toBeInTheDocument();
    // After the tabs and the dot: Expand and Close, and nothing else.
    expect(within(row()).getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual(['Expand', 'Close workspace']);
    // On Home the row would hold Stop, Resume or Start.
    expect(screen.queryByRole('button', { name: /^(Stop|Resume|Start|Start again|Retry reset)/ })).toBeNull();
  });

  it('reads the summary again when the server says the employee changed', async () => {
    employeeAnswer = found(RUNNING);
    renderDock();
    expect(await statusDot('Live')).toBeInTheDocument();

    employeeAnswer = found(STOPPED);
    emit('employee_lifecycle', {
      specversion: '1.0',
      id: 'event-1',
      source: 'opencompany://services/employees',
      type: 'com.opencompany.employee.updated',
      subject: 'wf1',
      data: { workflow_id: 'wf1' },
    });
    expect(await statusDot('Stopped')).toBeInTheDocument();
    expect(screen.queryByRole('status', { name: 'Live' })).toBeNull();
  });

  it('runs Take over for the employee, on the screen it takes, and says what happened in a toast', async () => {
    employeeAnswer = found(RUNNING);
    ws.actions.pauseWorkflow.mockResolvedValue(normalizeWorkflowControlStatus({ generation: 1, state: 'paused', revision: 5 }, 'wf1'));
    ws.actions.resumeWorkflow.mockResolvedValue(normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 6 }, 'wf1'));
    useCanvasDockStore.setState({ tab: 'browser' });
    renderDock();
    const browser = screen.getByTestId('browser-view');
    const takeOver = () => within(browser).getByRole('button', { name: 'Take over' });
    await waitFor(() => expect(takeOverIn('browser-view')).toBe('dock'));

    // A screen that is refused: said as an error, and no one is stopped.
    screenControl.claim.mockResolvedValue(null);
    fireEvent.click(takeOver());
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Couldn’t take the screen. Try again.'));
    expect(ws.actions.pauseWorkflow).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();

    // Granted: the workflow's employee is stopped, and the owner is told.
    screenControl.claim.mockResolvedValue(screenControl.giveBack);
    await waitFor(() => expect(takeOver()).toBeEnabled());
    fireEvent.click(takeOver());
    expect(await screen.findByText('You’re in control · Scout is waiting')).toBeInTheDocument();
    expect(ws.actions.pauseWorkflow).toHaveBeenCalledWith('wf1', 4);
    expect(toast.success).toHaveBeenCalledWith('You have control — Scout will wait');
    expect(screen.getByRole('status', { name: 'Waiting' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();

    // Hand back gives the screen back and resumes them from the Stop's answer.
    fireEvent.click(screen.getByRole('button', { name: 'Hand back' }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Handed back to Scout'));
    expect(screenControl.giveBack).toHaveBeenCalledOnce();
    expect(ws.actions.resumeWorkflow).toHaveBeenCalledWith('wf1', 5);
    expect(screen.queryByText('You’re in control · Scout is waiting')).toBeNull();
  });
});

// What only the editor's Canvas tab has: a row over the board saying which
// board it is, or that it is a file's preview; and the slow-model note.
describe('The editor’s Canvas tab', () => {
  it('previews a workspace file, with Back to the board', () => {
    const file: CanvasItem = {
      id: 'ephemeral-1',
      kind: 'file',
      title: null,
      ref: { kind: 'file', path: 'reports/q3.pdf', filename: 'q3.pdf' },
      url: null,
      content: null,
      language: null,
      source: 'workflow',
      created_at: null,
    };
    useCanvasDockStore.setState({ open: false, tab: 'browser' });
    renderDock();
    act(() => useCanvasDockStore.getState().showEphemeral(file));
    // The dock opens on the Canvas tab, on the file.
    expect(screen.getByRole('tab', { name: 'Canvas' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Preview')).toBeInTheDocument();
    expect(screen.getByText('q3.pdf')).toBeInTheDocument();
    expect(screen.getByTestId('board')).toHaveTextContent('Preview of ephemeral-1');

    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.queryByText('Preview')).toBeNull();
    expect(screen.getByTestId('board')).toHaveTextContent('Board of board1');
  });

  it('names the board it shows: the node’s label, a picker for several, a note for none', () => {
    const { rerender } = renderDock();
    expect(screen.getByText('Board')).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).toBeNull();

    const second: Node = { id: 'board2', type: 'boardPlugin', position: { x: 3, y: 0 }, data: { label: 'Reports' } };
    rerender(<CanvasDock nodes={[...nodes, second]} />);
    expect(screen.getByRole('combobox')).toBeInTheDocument();
    expect(screen.getByTestId('board')).toHaveTextContent('Board of board1');
    act(() => useCanvasDockStore.getState().selectNode('board2'));
    expect(screen.getByTestId('board')).toHaveTextContent('Board of board2');

    // The chosen node is gone, and no other Canvas node is left.
    rerender(<CanvasDock nodes={[nodes[1]]} />);
    expect(screen.getByText(/^No Canvas node in this workflow yet/)).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByTestId('board')).toBeNull();
  });

  it('notes a slow model at its foot while it is open', () => {
    const wait: LlmWait = {
      execution_key: 'run-1',
      request_id: 'request-1',
      seq: 1,
      state: 'waiting',
      stage: 'sdk_request',
      elapsed_ms: 6000,
      runtime_behavior_version: 1,
      scope: 'generation',
      execution_id: 'run-1',
      root_execution_id: 'run-1',
    };
    useLlmWaitStore.getState().apply('wf1', 'agent1', wait);
    renderDock();
    expect(screen.getByText('Agent: Waiting for the model response')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close workspace' }));
    expect(screen.queryByText('Agent: Waiting for the model response')).toBeNull();
  });
});
