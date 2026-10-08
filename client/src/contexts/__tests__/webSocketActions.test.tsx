/**
 * Normal mode reads the WebSocket layer through two channels that must not
 * churn on broadcasts: the actions context (stable operations plus the
 * connection flags) and the workflow-control store mirror. The main context
 * value, by contrast, is rebuilt on every console / chat / terminal line.
 *
 * Also: requests go straight out again once the socket reconnects, a
 * `chat.updated` refreshes the conversation it names (Home's thread, and
 * Dev's chat pane when it is the open workflow's), and a refused chat send
 * takes its line back out of the pane.
 */

import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

interface FakeSocketShape {
  readyState: number;
  onopen: (() => unknown) | null;
  onmessage: ((event: { data: string }) => void) | null;
  onclose: ((event: { code: number; reason: string }) => void) | null;
  sent: string[];
}

const sockets = vi.hoisted(() => [] as FakeSocketShape[]);

vi.mock('partysocket/ws', () => {
  class FakeSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;
    readyState = 0;
    onopen: unknown = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: unknown = null;
    onerror: unknown = null;
    sent: string[] = [];
    constructor() {
      sockets.push(this as unknown as FakeSocketShape);
    }
    send(frame: string) {
      this.sent.push(frame);
    }
    close() {
      this.readyState = 3;
    }
    addEventListener() {}
    removeEventListener() {}
    reconnect() {}
  }
  return { default: FakeSocket };
});

vi.mock('../AuthContext', () => ({ useAuth: () => ({ isAuthenticated: true, isLoading: false }) }));

vi.mock('../../services/workflowApi', () => ({
  workflowApi: { getAllWorkflows: vi.fn(), getWorkflow: vi.fn(), saveWorkflow: vi.fn(), deleteWorkflow: vi.fn() },
}));

import { WebSocketProvider, useWebSocket, useWebSocketActions, type WebSocketActions } from '../WebSocketContext';
import { queryClient } from '../../lib/queryClient';
import { queryKeys } from '../../lib/queryConfig';
import { useAppStore, type WorkflowData } from '../../store/useAppStore';
import { useWorkflowControlStore } from '../../stores/workflowControlStore';
import { EMPLOYEES_QUERY_KEY } from '../../features/home/data/employeeCache';
import { useHomeStore } from '../../features/home/state/homeStore';
import { WORKFLOWS_QUERY_KEY } from '../../hooks/useWorkflowsQuery';

function broadcast(message: Record<string, unknown>): void {
  act(() => {
    sockets[0].onmessage?.({ data: JSON.stringify(message) });
  });
}

/** Mount the provider and let its delayed connect create the socket. */
function mount(Probe: () => null = () => null) {
  const view = render(
    <WebSocketProvider>
      <Probe />
    </WebSocketProvider>,
  );
  act(() => {
    vi.advanceTimersByTime(150);
  });
  return view;
}

async function open(socket: FakeSocketShape): Promise<void> {
  socket.readyState = 1;
  await act(async () => {
    await socket.onopen?.();
  });
}

function frames(socket: FakeSocketShape): Array<Record<string, any>> {
  return socket.sent.map((frame) => JSON.parse(frame));
}

describe('WebSocket actions for Normal mode', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    sockets.length = 0;
    useWorkflowControlStore.setState({ statuses: {}, pending: {} });
    useAppStore.setState({ currentWorkflow: null });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('removes a remotely deleted employee while Home is unmounted', () => {
    const id = 'remote-deleted-employee';
    const workflow: WorkflowData = {
      id, name: 'Maya', slug: 'maya', nodes: [], edges: [],
      createdAt: new Date(0), lastModified: new Date(0),
    };
    useAppStore.setState({ currentWorkflow: workflow, hasUnsavedChanges: true, shellMode: 'dev' });
    useHomeStore.getState().showEmployee(id);
    queryClient.setQueryData(WORKFLOWS_QUERY_KEY, [workflow]);
    queryClient.setQueryData(EMPLOYEES_QUERY_KEY, [{ workflow_id: id }]);
    const { unmount } = mount();
    broadcast({ type: 'workflow_lifecycle', data: { specversion: '1.0', type: 'com.opencompany.workflow.deleted', subject: id } });
    expect(useAppStore.getState().currentWorkflow).toBeNull();
    expect(useAppStore.getState().hasUnsavedChanges).toBe(false);
    expect(useHomeStore.getState().view).toEqual({ kind: 'hire' });
    expect(queryClient.getQueryData(EMPLOYEES_QUERY_KEY)).toEqual([]);
    expect(queryClient.getQueryData(WORKFLOWS_QUERY_KEY)).toEqual([]);
    unmount();
    queryClient.clear();
  });

  it('keeps the actions context stable while the main value churns', () => {
    const actions: WebSocketActions[] = [];
    const values: unknown[] = [];
    function Probe() {
      actions.push(useWebSocketActions());
      values.push(useWebSocket());
      return null;
    }
    const { unmount } = mount(Probe);
    expect(sockets).toHaveLength(1);

    const before = { actions: actions.at(-1), value: values.at(-1) };
    broadcast({ type: 'console_log', data: { node_id: 'n1', label: 'Console', data: 'hello' } });
    broadcast({ type: 'console_log', data: { node_id: 'n1', label: 'Console', data: 'again' } });

    expect(values.at(-1)).not.toBe(before.value);
    expect(actions.at(-1)).toBe(before.actions);
    unmount();
  });

  it('mirrors control status into the store, keeping the newest revision', () => {
    const { unmount } = mount();
    broadcast({ type: 'workflow_control_status', data: { workflow_id: 'wf1', state: 'running', generation: 1, revision: 4 } });
    expect(useWorkflowControlStore.getState().statuses.wf1).toMatchObject({ state: 'running', revision: 4 });

    // A delayed older snapshot must not win.
    broadcast({ type: 'workflow_control_status', data: { workflow_id: 'wf1', state: 'starting', generation: 1, revision: 2 } });
    expect(useWorkflowControlStore.getState().statuses.wf1).toMatchObject({ state: 'running', revision: 4 });
    unmount();
  });

  it('says while it reconnects, and sends straight away again once the socket is back', async () => {
    let actions!: WebSocketActions;
    function Probe() {
      actions = useWebSocketActions();
      return null;
    }
    const { unmount } = mount(Probe);
    const socket = sockets[0];
    await open(socket);
    expect(actions.reconnecting).toBe(false);
    act(() => {
      socket.readyState = 3;
      socket.onclose?.({ code: 1006, reason: 'server restarted' });
    });
    // The sign-in gate covers the app while this is true.
    expect(actions.reconnecting).toBe(true);
    // PartySocket reopens the same instance.
    await open(socket);
    expect(actions.reconnecting).toBe(false);

    const request = actions.sendRequest('list_employees', {});
    expect(frames(socket).map((frame) => frame.type)).toContain('list_employees');
    expect(sockets).toHaveLength(1);
    unmount();
    // Unmounting disposes the connection, which fails what is still in flight.
    await expect(request).rejects.toThrow('Component unmounted');
  });

  it('refreshes only the thread a chat.updated names', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    useAppStore.setState({ currentWorkflow: { id: 'wf1' } as WorkflowData });
    function Probe() {
      useWebSocket();
      return null;
    }
    const { unmount } = mount(Probe);
    const socket = sockets[0];
    await open(socket);
    socket.sent.length = 0;

    const updated = (sessionId: string) => ({
      type: 'chat.updated',
      data: {
        specversion: '1.0',
        id: `e-${sessionId}`,
        source: 'opencompany://services/chat',
        type: 'com.opencompany.chat.updated',
        data: { workflow_id: sessionId, session_id: sessionId, role: 'assistant' },
      },
    });

    broadcast(updated('wf2'));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.chatThread.bySession('wf2').queryKey });
    broadcast(updated('wf1'));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.chatThread.bySession('wf1').queryKey });
    await act(async () => {});
    // The chats read their threads through their own queries; the context
    // keeps no copy of its own to reload.
    expect(frames(socket).filter((frame) => frame.type === 'get_chat_messages')).toHaveLength(0);
    unmount();
  });
});
