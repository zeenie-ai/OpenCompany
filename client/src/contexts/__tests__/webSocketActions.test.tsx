/**
 * Normal mode reads the WebSocket layer through two channels that must not
 * churn on broadcasts: the actions context (stable operations plus the
 * connection flags) and the workflow-control store mirror. The main context
 * value, by contrast, is rebuilt on every console / chat / terminal line.
 */

import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const sockets = vi.hoisted(() => [] as Array<{ onmessage: ((event: { data: string }) => void) | null }>);

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
    constructor() {
      sockets.push(this);
    }
    send() {}
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
import { useWorkflowControlStore } from '../../stores/workflowControlStore';

function broadcast(message: Record<string, unknown>): void {
  act(() => {
    sockets[0].onmessage?.({ data: JSON.stringify(message) });
  });
}

describe('WebSocket actions for Normal mode', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    sockets.length = 0;
    useWorkflowControlStore.setState({ statuses: {}, pending: {} });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('keeps the actions context stable while the main value churns', () => {
    const actions: WebSocketActions[] = [];
    const values: unknown[] = [];
    function Probe() {
      actions.push(useWebSocketActions());
      values.push(useWebSocket());
      return null;
    }
    const { unmount } = render(
      <WebSocketProvider>
        <Probe />
      </WebSocketProvider>,
    );
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(sockets).toHaveLength(1);

    const before = { actions: actions.at(-1), value: values.at(-1) };
    broadcast({ type: 'console_log', data: { node_id: 'n1', label: 'Console', data: 'hello' } });
    broadcast({ type: 'console_log', data: { node_id: 'n1', label: 'Console', data: 'again' } });

    expect(values.at(-1)).not.toBe(before.value);
    expect(actions.at(-1)).toBe(before.actions);
    unmount();
  });

  it('mirrors control status into the store, keeping the newest revision', () => {
    const { unmount } = render(
      <WebSocketProvider>
        <div />
      </WebSocketProvider>,
    );
    act(() => {
      vi.advanceTimersByTime(150);
    });
    broadcast({ type: 'workflow_control_status', data: { workflow_id: 'wf1', state: 'running', generation: 1, revision: 4 } });
    expect(useWorkflowControlStore.getState().statuses.wf1).toMatchObject({ state: 'running', revision: 4 });

    // A delayed older snapshot must not win.
    broadcast({ type: 'workflow_control_status', data: { workflow_id: 'wf1', state: 'starting', generation: 1, revision: 2 } });
    expect(useWorkflowControlStore.getState().statuses.wf1).toMatchObject({ state: 'running', revision: 4 });
    unmount();
  });
});
