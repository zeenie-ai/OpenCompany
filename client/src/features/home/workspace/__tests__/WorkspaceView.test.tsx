/**
 * The Workspace dock's body (WorkspaceView) on its own: what Home's dock and
 * the editor's both show. Each host's test covers what that host adds
 * (features/home/__tests__/workspace.test.tsx for Home,
 * components/ui/__tests__/WorkspaceDock.test.tsx for the editor). This one
 * covers what the body does with what a host gives it: who works in the
 * Workspace (an employee, no one, or not known yet), the workflow and its
 * nodes, Dev mode's extras, the host's main action, Expand and Close, a
 * busy tab's dot, and where Take over's messages go. Take over itself is in
 * takeover.test.tsx beside this file.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, type ReactNode, type Ref } from 'react';

// The socket: the steps the status pill reads, and Take over's Stop and Resume.
const actions = vi.hoisted(() => ({
  isReady: true,
  sendRequest: vi.fn(),
  addEventListener: () => () => {},
  pauseWorkflow: vi.fn(),
  resumeWorkflow: vi.fn(),
}));

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => actions,
}));
// The body opens nothing in the editor; this keeps the app shell out of the test.
vi.mock('../../../../app/useShellActions', () => ({ enterDev: vi.fn() }));
// Home's toast. The body never uses it: the editor has none to show it in.
vi.mock('../../ui/pillToast', () => ({ pillToast: vi.fn() }));

interface ViewerProps {
  workflowId?: string | null;
  nodes: { node_id: string; label: string }[];
  visible?: boolean;
  agentName?: string;
  canvasNodeId?: string | null;
  notify?: (message: string, tone: 'success' | 'error') => void;
  dev?: boolean;
  action?: ReactNode;
  flushCanvas?: () => void;
  ref?: Ref<{ claim: () => unknown }>;
}

// The screen's Take over handle (components/workspace/surface.ts), and the
// way to give back what a claim took.
const screenControl = vi.hoisted(() => ({ claim: vi.fn(), giveBack: vi.fn() }));
// The Browser and phone views, showing what the body gave them. `data-take-over`
// says what it put in their `action` slot: its own button (`dock`), nothing at
// all (`none`), or nothing passed, so the view shows its own Take over (`own`).
// As the real views, they show the body's button only while the owner does not
// hold their screen: from a granted claim until it is given back or the view
// hides. Save the canvas stands for Browser AI tasks, and Screenshot to Canvas
// for the phone saying what it did.
const viewer = vi.hoisted(() => (testId: string) => async () => {
  const { useImperativeHandle, useState } = await import('react');
  return {
    default: function Viewer({ workflowId, nodes, visible, agentName, canvasNodeId, notify, dev = false, action, flushCanvas, ref }: ViewerProps) {
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
          data-agent={agentName ?? ''}
          data-canvas={canvasNodeId ?? ''}
        >
          {nodes.map((node) => (
            <span key={node.node_id}>{node.label}</span>
          ))}
          {flushCanvas && (
            <button type="button" onClick={flushCanvas}>
              Save the canvas
            </button>
          )}
          {notify && (
            <button type="button" onClick={() => notify('Screenshot added to Canvas', 'success')}>
              Screenshot to Canvas
            </button>
          )}
          {!held && action}
        </div>
      );
    },
  };
});
vi.mock('@/components/browser/BrowserWorkspace', viewer('browser-workspace'));
vi.mock('@/components/mobile/MobileWorkspace', viewer('mobile-workspace'));

import type { WorkspaceTab } from '@/components/workspace/WorkspaceTabs';
import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';
import { parseEmployee, type EmployeeSummary } from '../../data/schemas';
import { useHomeStore } from '../../state/homeStore';
import { pillToast } from '../../ui/pillToast';
import { WorkspaceView, type WorkspaceViewProps } from '../WorkspaceView';

const BROWSER_NODES = [{ node_id: 'w1:browser:1', label: 'Research browser' }];
const MOBILE_NODES = [{ node_id: 'w1:mobile:1', label: 'Front desk phone' }];
const BANNER = 'You’re in control · Maya is waiting';
const WAITING_IN_BROWSER = { browser_request: { node_id: 'w1:browser:1', reason: 'login', since: null } };

const control = (state: string, revision: number, workflowId = 'w1') => normalizeWorkflowControlStatus({ generation: 1, state, revision }, workflowId);
const STOPPED = { status: 'paused', control: control('paused', 5) };

/** Maya as her summary shows her: running at revision 4 unless told otherwise. */
function maya(patch: Record<string, unknown> = {}): EmployeeSummary {
  return parseEmployee({ workflow_id: 'w1', name: 'Maya', role: 'Receptionist', status: 'working', control: control('running', 4), ...patch })!;
}

/** Leo, who works in another workflow. */
const leo = (): EmployeeSummary =>
  parseEmployee({ workflow_id: 'w2', name: 'Leo', role: 'Researcher', status: 'working', control: control('running', 9, 'w2') })!;

// What the host hears back from the body.
const host = { onToggleWide: vi.fn(), onClose: vi.fn(), notify: vi.fn() };

type HostProps = Partial<Omit<WorkspaceViewProps, 'employee' | 'tab' | 'onTabChange'>> & {
  /** Always given, since leaving it undefined says something: not known yet. */
  employee: WorkspaceViewProps['employee'];
  /** The tab on show first. The host then follows the body's tabs. */
  startTab?: WorkspaceTab;
};

/** A host as plain as can be: it keeps the tab, and the rest is the test's. */
function Host({ startTab = 'browser', ...props }: HostProps) {
  const [tab, setTab] = useState<WorkspaceTab>(startTab);
  return (
    <WorkspaceView
      workflowId="w1"
      browserNodes={BROWSER_NODES}
      mobileNodes={MOBILE_NODES}
      canvasNodeId="w1:canvas:1"
      board={<p>The board</p>}
      visible
      wide={false}
      {...host}
      {...props}
      tab={tab}
      onTabChange={setTab}
    />
  );
}

function renderView(props: HostProps) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (next: HostProps) => (
    <QueryClientProvider client={client}>
      <Host {...next} />
    </QueryClientProvider>
  );
  const view = render(ui(props));
  return { rerender: (next: HostProps) => view.rerender(ui(next)) };
}

const browser = () => screen.getByTestId('browser-workspace');
const phone = () => screen.getByTestId('mobile-workspace');
/** What the body put in a screen's Take over slot: `dock`, `none` or `own`. */
const takeOverOn = (view: HTMLElement) => view.getAttribute('data-take-over');
/** The one row, in order: the tabs, then what sits at its end, each by its name. */
const row = () =>
  Array.from(screen.getByRole('tablist', { name: 'Workspace views' }).parentElement!.children, (item) => item.getAttribute('aria-label') ?? item.textContent);
/** The pill floating at the foot. */
const pill = () => document.querySelector('[data-slot="workspace-status"]');
const frame = () => document.querySelector('[data-slot="workspace-takeover-frame"]');
// Radix tabs change on mouse down.
const showTab = (name: string | RegExp) => fireEvent.mouseDown(screen.getByRole('tab', { name }), { button: 0, ctrlKey: false });
const stepReads = () => actions.sendRequest.mock.calls.filter(([type]) => type === 'workspace_steps_list');

// What `workspace_steps_list` answers, oldest first.
let steps: { id: number; surface: string; text: string; at: string }[];

beforeEach(() => {
  steps = [];
  actions.sendRequest.mockReset().mockImplementation(async (type: string, data?: { workflow_id?: string }) =>
    type === 'workspace_steps_list' ? { success: true, workflow_id: data?.workflow_id, steps } : { success: true },
  );
  actions.pauseWorkflow.mockReset().mockResolvedValue(control('paused', 5));
  actions.resumeWorkflow.mockReset().mockResolvedValue(control('running', 6));
  screenControl.claim.mockReset().mockResolvedValue(screenControl.giveBack);
  screenControl.giveBack.mockReset();
  host.onToggleWide.mockReset();
  host.onClose.mockReset();
  host.notify.mockReset();
  vi.mocked(pillToast).mockClear();
  useHomeStore.setState({ takeovers: {}, takeoverBusy: {} });
  useNodeStatusStore.setState({ allStatuses: {} });
});

describe('Who works in the Workspace', () => {
  it('gives an employee a status dot, a status pill and the dock’s Take over on each screen', async () => {
    steps = [{ id: 1, surface: 'browser', text: 'Opened example.com', at: '2026-10-09T10:00:00Z' }];
    renderView({ employee: maya() });
    expect(screen.getByRole('status', { name: 'Live' })).toHaveAttribute('title', 'Live');
    // The pill at the foot reads their steps, as soon as it shows.
    expect(stepReads()).toEqual([['workspace_steps_list', { workflow_id: 'w1' }]]);
    await waitFor(() => expect(pill()).toHaveTextContent('Opened example.com'));
    expect(stepReads()).toHaveLength(1);

    expect(takeOverOn(browser())).toBe('dock');
    expect(within(browser()).getByRole('button', { name: 'Take over' })).toBeEnabled();
    showTab('Mobile');
    expect(takeOverOn(phone())).toBe('dock');
    expect(within(phone()).getByRole('button', { name: 'Take over' })).toBeEnabled();
    // The Canvas has no screen to take.
    showTab('Canvas');
    expect(screen.getByText('The board')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
  });

  it('shows no status for no one, and leaves each screen its own Take over', () => {
    // The editor's workflow not saved yet: no workflow, and no one working in it.
    renderView({ employee: null, workflowId: null });
    expect(screen.queryByRole('status')).toBeNull();
    expect(pill()).toBeNull();
    expect(row()).toEqual(['Workspace views', 'Expand', 'Close workspace']);
    // Nothing passed: the screen shows its own Take over, which stops no one.
    expect(takeOverOn(browser())).toBe('own');
    expect(browser()).toHaveAttribute('data-workflow', '');
    expect(browser()).toHaveAttribute('data-agent', '');
    showTab('Mobile');
    expect(takeOverOn(phone())).toBe('own');
    // No one's steps to read.
    expect(actions.sendRequest).not.toHaveBeenCalled();
  });

  it('shows no Take over at all while it is not known who works in it', () => {
    const { rerender } = renderView({ employee: undefined });
    // Neither the dock's nor the screen's own: the owner never takes a screen
    // the dock is about to run Take over for.
    expect(takeOverOn(browser())).toBe('none');
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
    expect(pill()).toBeNull();
    showTab('Mobile');
    expect(takeOverOn(phone())).toBe('none');

    // Known now, and there is no one: the screen keeps its own.
    rerender({ employee: null });
    expect(takeOverOn(phone())).toBe('own');
    expect(pill()).toBeNull();
    // Known now, and there is an employee: the dock runs Take over.
    rerender({ employee: maya() });
    expect(takeOverOn(phone())).toBe('dock');
    expect(within(phone()).getByRole('button', { name: 'Take over' })).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Live' })).toBeInTheDocument();
    expect(pill()).toHaveTextContent(/^Waiting for work$/);
  });

  it('offers Take over only on the screen the owner took, while they have it', () => {
    useHomeStore.setState({ takeovers: { w1: { screen: 'android', giveBack: vi.fn() } } });
    renderView({ employee: maya(STOPPED) });
    // They hold Maya's phone: her browser offers nothing, not even its own.
    expect(takeOverOn(browser())).toBe('none');
    // Whichever tab shows, the body is framed and the pill is the banner with Hand back.
    expect(screen.getByRole('status', { name: 'Waiting' })).toBeInTheDocument();
    expect(frame()).not.toBeNull();
    expect(pill()).toHaveTextContent(BANNER);
    expect(screen.getByRole('button', { name: 'Hand back' })).toBeEnabled();
    // The phone let go of them (this view never held it), so they can take it again.
    showTab('Mobile');
    expect(takeOverOn(phone())).toBe('dock');
    expect(within(phone()).getByRole('button', { name: 'Take over' })).toBeEnabled();
  });
});

describe('What the host gives the Workspace', () => {
  it('hands each screen its workflow, its nodes and whether it is on show', () => {
    const { rerender } = renderView({ employee: maya() });
    expect(browser()).toHaveAttribute('data-workflow', 'w1');
    expect(browser()).toHaveAttribute('data-visible', 'true');
    expect(within(browser()).getByText('Research browser')).toBeInTheDocument();
    // The employee's name goes on the agent's cursor.
    expect(browser()).toHaveAttribute('data-agent', 'Maya');
    // The dock closed: the live view stops.
    rerender({ employee: maya(), visible: false });
    expect(browser()).toHaveAttribute('data-visible', 'false');

    rerender({ employee: maya() });
    showTab('Mobile');
    expect(phone()).toHaveAttribute('data-workflow', 'w1');
    expect(phone()).toHaveAttribute('data-visible', 'true');
    expect(within(phone()).getByText('Front desk phone')).toBeInTheDocument();
    expect(within(phone()).queryByText('Research browser')).toBeNull();
    // A phone screenshot goes to this board.
    expect(phone()).toHaveAttribute('data-canvas', 'w1:canvas:1');
    // Only the tab on show has its screen mounted.
    expect(screen.queryByTestId('browser-workspace')).toBeNull();
  });

  it('starts a screen afresh for another workflow', async () => {
    const { rerender } = renderView({ employee: maya() });
    fireEvent.click(within(browser()).getByRole('button', { name: 'Take over' }));
    expect(await screen.findByText(BANNER)).toBeInTheDocument();
    // The owner holds Maya's browser, so it offers nothing more.
    expect(within(browser()).queryByRole('button', { name: 'Take over' })).toBeNull();
    // The dock moves to Leo. His browser is another screen, which no one holds.
    rerender({ employee: leo(), workflowId: 'w2' });
    expect(browser()).toHaveAttribute('data-workflow', 'w2');
    expect(browser()).toHaveAttribute('data-agent', 'Leo');
    expect(within(browser()).getByRole('button', { name: 'Take over' })).toBeEnabled();
    expect(screen.queryByText(BANNER)).toBeNull();
    expect(frame()).toBeNull();
    // Maya's is still the owner's, for when the dock shows her again.
    expect(Object.keys(useHomeStore.getState().takeovers)).toEqual(['w1']);
  });

  it('leaves Dev mode’s extras out unless the host asks for them', () => {
    renderView({ employee: maya() });
    expect(browser()).toHaveAttribute('data-dev', 'false');
    expect(screen.queryByRole('button', { name: 'Save the canvas' })).toBeNull();
    showTab('Mobile');
    expect(phone()).toHaveAttribute('data-dev', 'false');
  });

  it('gives the screens Dev mode’s extras, and the browser the canvas save', () => {
    const flushCanvas = vi.fn();
    renderView({ employee: maya(), dev: true, flushCanvas });
    expect(browser()).toHaveAttribute('data-dev', 'true');
    // Browser AI tasks saves the canvas through the host before it adds to it.
    fireEvent.click(within(browser()).getByRole('button', { name: 'Save the canvas' }));
    expect(flushCanvas).toHaveBeenCalledOnce();
    showTab('Mobile');
    expect(phone()).toHaveAttribute('data-dev', 'true');
    // The phone adds nothing to the canvas, so it gets no save.
    expect(screen.queryByRole('button', { name: 'Save the canvas' })).toBeNull();
  });

  it('puts the host’s main action between the status dot and Expand', () => {
    const { rerender } = renderView({ employee: maya(), action: <button type="button">Stop</button> });
    expect(row()).toEqual(['Workspace views', 'Live', 'Stop', 'Expand', 'Close workspace']);
    // Outside the tablist, so the arrow keys move along the tabs only.
    expect(screen.getByRole('tablist', { name: 'Workspace views' })).not.toContainElement(screen.getByRole('button', { name: 'Stop' }));
    // A host with no action (the editor: its toolbar has the workflow's).
    rerender({ employee: maya() });
    expect(row()).toEqual(['Workspace views', 'Live', 'Expand', 'Close workspace']);
  });

  it('asks the host to expand, restore and close, and keeps no size of its own', () => {
    const { rerender } = renderView({ employee: maya() });
    const expand = screen.getByRole('button', { name: 'Expand' });
    expect(expand).toHaveAttribute('title', 'Expand');
    fireEvent.click(expand);
    expect(host.onToggleWide).toHaveBeenCalledOnce();
    // The host decides: until it says wide, the button still offers Expand.
    expect(screen.getByRole('button', { name: 'Expand' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Restore size' })).toBeNull();

    rerender({ employee: maya(), wide: true });
    expect(screen.queryByRole('button', { name: 'Expand' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Restore size' }));
    expect(host.onToggleWide).toHaveBeenCalledTimes(2);

    expect(host.onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Close workspace' }));
    expect(host.onClose).toHaveBeenCalledOnce();
    expect(host.onToggleWide).toHaveBeenCalledTimes(2);
  });
});

describe('A busy tab', () => {
  it('dots the Browser tab while the employee waits for the owner there, unless it is the tab on show', () => {
    const { rerender } = renderView({ employee: maya(WAITING_IN_BROWSER), startTab: 'board' });
    const busy = screen.getByRole('tab', { name: 'Browser (busy)' });
    expect(busy.querySelector('[data-slot="workspace-tab-dot"]')).toBeInTheDocument();
    // The phone has nothing going on.
    expect(screen.getByRole('tab', { name: 'Mobile' })).toBeInTheDocument();
    // On show, the tab needs no dot: the request is on the page.
    showTab('Browser (busy)');
    expect(screen.getByRole('tab', { name: 'Browser' })).toHaveAttribute('aria-selected', 'true');
    showTab('Canvas');
    expect(screen.getByRole('tab', { name: 'Browser (busy)' })).toBeInTheDocument();
    // The owner answered: no dot.
    rerender({ employee: maya() });
    expect(screen.getByRole('tab', { name: 'Browser' })).toBeInTheDocument();
  });

  it('dots a tab while one of its nodes runs in this workflow', () => {
    renderView({ employee: maya(), startTab: 'board' });
    expect(screen.getByRole('tab', { name: 'Browser' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Mobile' })).toBeInTheDocument();
    // The same node ids in another workflow say nothing about this one.
    act(() => useNodeStatusStore.getState().setStatus('w2', 'w1:mobile:1', { status: 'executing' }));
    expect(screen.getByRole('tab', { name: 'Mobile' })).toBeInTheDocument();

    act(() => useNodeStatusStore.getState().setStatus('w1', 'w1:mobile:1', { status: 'executing' }));
    expect(screen.getByRole('tab', { name: 'Mobile (busy)' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Browser' })).toBeInTheDocument();
    act(() => useNodeStatusStore.getState().setStatus('w1', 'w1:browser:1', { status: 'executing' }));
    expect(screen.getByRole('tab', { name: 'Browser (busy)' })).toBeInTheDocument();
    act(() => useNodeStatusStore.getState().setStatus('w1', 'w1:mobile:1', { status: 'success' }));
    expect(screen.getByRole('tab', { name: 'Mobile' })).toBeInTheDocument();
  });
});

describe('What happened', () => {
  it('says what Take over and Hand back did through the host’s notify, each with its tone', async () => {
    renderView({ employee: maya() });
    const takeOver = () => within(browser()).getByRole('button', { name: 'Take over' });

    // A screen that refuses: said as an error, and no one is stopped.
    screenControl.claim.mockResolvedValue(null);
    fireEvent.click(takeOver());
    await waitFor(() => expect(host.notify).toHaveBeenCalledWith('Couldn’t take the screen. Try again.', 'error'));
    expect(actions.pauseWorkflow).not.toHaveBeenCalled();

    // Granted: the owner is told they are in control.
    screenControl.claim.mockResolvedValue(screenControl.giveBack);
    await waitFor(() => expect(takeOver()).toBeEnabled());
    fireEvent.click(takeOver());
    expect(await screen.findByText(BANNER)).toBeInTheDocument();
    expect(host.notify).toHaveBeenLastCalledWith('You have control — Maya will wait', 'success');

    fireEvent.click(screen.getByRole('button', { name: 'Hand back' }));
    await waitFor(() => expect(host.notify).toHaveBeenLastCalledWith('Handed back to Maya', 'success'));
    // Those three, in that order, and nothing else.
    expect(host.notify.mock.calls).toEqual([
      ['Couldn’t take the screen. Try again.', 'error'],
      ['You have control — Maya will wait', 'success'],
      ['Handed back to Maya', 'success'],
    ]);
    // Nothing went to Home's toast: the host says it its own way.
    expect(pillToast).not.toHaveBeenCalled();
  });

  it('says a Stop that failed as an error', async () => {
    actions.pauseWorkflow.mockRejectedValue(new Error('control_revision_conflict'));
    renderView({ employee: maya() });
    fireEvent.click(within(browser()).getByRole('button', { name: 'Take over' }));
    await waitFor(() => expect(host.notify).toHaveBeenLastCalledWith('That changed a moment ago. Try again.', 'error'));
    expect(host.notify.mock.calls).toEqual([
      ['You have control — Maya will wait', 'success'],
      ['That changed a moment ago. Try again.', 'error'],
    ]);
    // Not stopped: the screen went back, and the owner is no longer in control.
    expect(screenControl.giveBack).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.queryByText(BANNER)).toBeNull());
    expect(frame()).toBeNull();
    expect(pillToast).not.toHaveBeenCalled();
  });

  it('gives the phone the same notify, for what it does on its own', () => {
    renderView({ employee: maya(), startTab: 'android' });
    fireEvent.click(within(phone()).getByRole('button', { name: 'Screenshot to Canvas' }));
    expect(host.notify.mock.calls).toEqual([['Screenshot added to Canvas', 'success']]);
  });
});
