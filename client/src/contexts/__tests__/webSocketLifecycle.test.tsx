import { StrictMode, useLayoutEffect } from 'react';
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WS_HEARTBEAT, WS_RECONNECT } from '../../lib/connectionConfig';

const auth = vi.hoisted(() => ({ isAuthenticated: true, isLoading: false }));
vi.mock('../AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../../services/workflowApi', () => ({ workflowApi: {} }));

import { WebSocketProvider, useWebSocket, useWebSocketActions, type WebSocketActions } from '../WebSocketContext';
import { useAppStore } from '../../store/useAppStore';
import { useNodeStatusStore } from '../../stores/nodeStatusStore';

// Exercise the installed PartySocket implementation; only its network peer
// is simulated. Remote code 1000 and native error events really reconnect.
class NativeSocket extends EventTarget {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: NativeSocket[] = [];
  static autoSnapshots = true;
  readyState = NativeSocket.CONNECTING;
  sent: Array<Record<string, any>> = [];
  close = vi.fn((code = 1000, reason = '') => this.remoteClose(code, reason));
  constructor(public url: string) {
    super();
    NativeSocket.instances.push(this);
  }
  send(raw: string) {
    if (this.readyState !== NativeSocket.OPEN) throw new Error('Socket not open');
    const message = JSON.parse(raw);
    this.sent.push(message);
    if (NativeSocket.autoSnapshots && ['get_terminal_logs', 'get_chat_messages', 'get_console_logs'].includes(message.type)) {
      queueMicrotask(() => this.message({ request_id: message.request_id, success: true, logs: [], messages: [] }));
    }
  }
  open() {
    this.readyState = NativeSocket.OPEN;
    this.dispatchEvent(new Event('open'));
  }
  message(data: Record<string, unknown>) {
    this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(data) }));
  }
  remoteClose(code = 1006, reason = '') {
    this.readyState = NativeSocket.CLOSED;
    this.dispatchEvent(new CloseEvent('close', { code, reason }));
  }
}

let actions: WebSocketActions;
let context: ReturnType<typeof useWebSocket>;
function Probe() {
  const currentActions = useWebSocketActions();
  const currentContext = useWebSocket();
  useLayoutEffect(() => {
    actions = currentActions;
    context = currentContext;
  }, [currentActions, currentContext]);
  return null;
}
const tree = () => <WebSocketProvider><Probe /></WebSocketProvider>;
const advance = async (ms: number) => {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
};
const latest = () => NativeSocket.instances.at(-1)!;
const open = async () => { await act(async () => latest().open()); };
const mount = async () => {
  const view = render(tree());
  await advance(150);
  return view;
};

describe('WebSocket recovery and ownership', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('WebSocket', NativeSocket);
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
    vi.spyOn(console, 'log').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
    NativeSocket.instances = [];
    NativeSocket.autoSnapshots = true;
    useAppStore.setState({ currentWorkflow: null });
    auth.isAuthenticated = true;
    auth.isLoading = false;
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it.each(['canvas', 'employee'])('reconciles a stale %s Start after Hire without submitting another mutation', async (surface) => {
    await mount();
    await open();
    const running = { workflow_id: 'ravi', state: 'running', generation: 1, revision: 2,
      can_start: false, can_pause: true, can_reset: true };
    act(() => latest().message({ type: 'workflow_control_status', data: running }));
    let result!: Promise<unknown>;
    act(() => { result = surface === 'canvas'
      ? actions.startWorkflow('ravi', [], [], 'default', 0)
      : actions.startEmployee('ravi', 0); });
    const read = latest().sent.filter(item => item.type === 'get_workflow_control_status').at(-1)!;
    expect(read).toBeDefined();
    expect(latest().sent.some(item => ['start_workflow', 'start_employee'].includes(item.type))).toBe(false);
    await act(async () => { latest().message({ request_id: read.request_id, success: true, ...running }); });
    await expect(result).resolves.toMatchObject({ state: 'running', revision: 2 });
    expect(latest().sent.some(item => ['start_workflow', 'start_employee'].includes(item.type))).toBe(false);
  });

  it('does not rebase a stale Start onto a newer ready generation after Reset', async () => {
    await mount();
    await open();
    act(() => latest().message({ type: 'workflow_control_status', data: {
      workflow_id: 'ravi', state: 'running', generation: 1, revision: 2, can_start: false,
    } }));
    let result!: Promise<unknown>;
    act(() => { result = actions.startEmployee('ravi', 0).catch(error => error.message); });
    const read = latest().sent.filter(item => item.type === 'get_workflow_control_status').at(-1)!;
    const ready = { workflow_id: 'ravi', state: 'ready', generation: 1, revision: 4, can_start: true };
    await act(async () => { latest().message({ request_id: read.request_id, success: true, ...ready }); });
    const resync = latest().sent.filter(item => item.type === 'get_workflow_control_status').at(-1)!;
    await act(async () => { latest().message({ request_id: resync.request_id, success: true, ...ready }); });
    await expect(result).resolves.toBe('control_revision_conflict');
    expect(latest().sent.some(item => item.type === 'start_employee')).toBe(false);
    expect(context.workflowControlStatuses.ravi).toMatchObject({ state: 'ready', revision: 4 });
  });

  it('reconciles a Start conflict that arrives with the background hire running snapshot', async () => {
    await mount();
    await open();
    let result!: Promise<unknown>;
    act(() => { result = actions.startEmployee('ravi', 0); });
    const start = latest().sent.find(item => item.type === 'start_employee')!;
    expect(start.expected_revision).toBe(0);
    const running = { workflow_id: 'ravi', state: 'running', generation: 1, revision: 2, can_start: false };
    await act(async () => { latest().message({ request_id: start.request_id, success: false,
      error: 'control_revision_conflict', ...running }); });
    expect(context.workflowControlStatuses.ravi).toMatchObject({ state: 'running', revision: 2 });
    const resync = latest().sent.filter(item => item.type === 'get_workflow_control_status').at(-1)!;
    await act(async () => { latest().message({ request_id: resync.request_id, success: true, ...running }); });
    await expect(result).resolves.toMatchObject({ state: 'running', revision: 2 });
    expect(latest().sent.filter(item => item.type === 'start_employee')).toHaveLength(1);
  });

  it('does not clear phone UI when a failed reset resync finds an unchanged ready workflow', async () => {
    await mount();
    await open();
    const notify = vi.fn();
    const clear = vi.spyOn(useNodeStatusStore.getState(), 'clearWorkflow');
    actions.addEventListener('workflow_runtime_reset', notify);
    const status = { workflow_id: 'phone', state: 'ready', revision: 3, generation: 1,
      can_reset: true, workspace_epoch: 2, workspace_reset_request_id: 'previous-reset', workspace_resetting: false };
    act(() => latest().message({ type: 'workflow_control_status', data: status }));
    let result!: Promise<unknown>;
    act(() => { result = actions.resetWorkflow('phone', 3).catch(error => error.message); });
    const request = latest().sent.find(item => item.type === 'reset_workflow')!;
    expect(request.idempotency_key).toBeTruthy();
    await act(async () => {
      latest().message({ request_id: request.request_id, success: false, error: 'phone_cancel_failed', status });
    });
    const resync = latest().sent.filter(item => item.type === 'get_workflow_control_status').at(-1)!;
    expect(resync).toBeDefined();
    await act(async () => { latest().message({ request_id: resync.request_id, success: true, status }); });
    await expect(result).resolves.toBe('phone_cancel_failed');
    expect(notify).not.toHaveBeenCalled();
    expect(clear).not.toHaveBeenCalled();
  });

  it('uses the generation control protocol for chat Stop and reconciles a lost acknowledgement', async () => {
    await mount();
    await open();
    const running = { workflow_id: 'chat', state: 'running', revision: 3, generation: 1,
      root_execution_id: 'g1', execution_control_version: 1, in_flight_count: 2 };
    act(() => latest().message({ type: 'workflow_control_status', data: running }));
    let result!: Promise<unknown>;
    act(() => { result = actions.stopChatRun('chat', 3, 'r1'); });
    const request = latest().sent.find(item => item.type === 'stop_chat_run')!;
    expect(request).toMatchObject({ workflow_id: 'chat', run_id: 'r1', expected_revision: 3 });
    expect(request.idempotency_key).toBeTruthy();
    await act(async () => {
      latest().message({ request_id: request.request_id, success: false, error: 'workflow_control_transition_pending',
        status: { ...running, state: 'pausing', revision: 4 } });
    });
    const resync = latest().sent.filter(item => item.type === 'get_workflow_control_status').at(-1)!;
    expect(resync).toBeDefined();
    await act(async () => {
      latest().message({ request_id: resync.request_id, success: true,
        status: { ...running, state: 'paused', revision: 5, in_flight_count: 0 } });
    });
    await expect(result).resolves.toMatchObject({ state: 'paused', revision: 5 });
    expect(latest().sent.filter(item => item.type === 'stop_chat_run')).toHaveLength(1);
  });

  it.each([false, true])('confirms an exact phone reset ID only after cleanup ends (resetting=%s)', async (workspaceResetting) => {
    await mount();
    await open();
    const notify = vi.fn();
    const clear = vi.spyOn(useNodeStatusStore.getState(), 'clearWorkflow');
    actions.addEventListener('workflow_runtime_reset', notify);
    let result!: Promise<unknown>;
    act(() => { result = actions.resetWorkflow('phone', 0).catch(error => error.message); });
    const request = latest().sent.find(item => item.type === 'reset_workflow')!;
    await act(async () => {
      latest().message({ request_id: request.request_id, success: false, error: 'reset_outcome_unknown' });
    });
    const resync = latest().sent.filter(item => item.type === 'get_workflow_control_status').at(-1)!;
    const status = { workflow_id: 'phone', state: 'never_started', revision: 0, workspace_epoch: 1,
      workspace_reset_request_id: request.idempotency_key, workspace_resetting: workspaceResetting };
    await act(async () => { latest().message({ request_id: resync.request_id, success: true, status }); });
    if (workspaceResetting) {
      await expect(result).resolves.toBe('reset_outcome_unknown');
      expect(notify).not.toHaveBeenCalled();
      expect(clear).not.toHaveBeenCalled();
    } else {
      await expect(result).resolves.toMatchObject(status);
      expect(notify).toHaveBeenCalledWith({ workflow_id: 'phone' });
      expect(clear).toHaveBeenCalledWith('phone');
    }
  });

  it('recognizes completion of the original pending workspace reset when retrying it', async () => {
    await mount();
    await open();
    const notify = vi.fn();
    actions.addEventListener('workflow_runtime_reset', notify);
    const status = { workflow_id: 'phone', state: 'never_started', revision: 0,
      workspace_epoch: 1, workspace_reset_request_id: 'original-reset', workspace_resetting: true };
    act(() => latest().message({ type: 'workflow_control_status', data: status }));
    let result!: Promise<unknown>;
    act(() => { result = actions.resetWorkflow('phone', 0).catch(error => error.message); });
    const request = latest().sent.find(item => item.type === 'reset_workflow')!;
    expect(request.idempotency_key).not.toBe('original-reset');
    await act(async () => {
      latest().message({ request_id: request.request_id, success: false, error: 'reset_outcome_unknown' });
    });
    const resync = latest().sent.filter(item => item.type === 'get_workflow_control_status').at(-1)!;
    await act(async () => {
      latest().message({ request_id: resync.request_id, success: true, status: { ...status, workspace_resetting: false } });
    });
    await expect(result).resolves.toMatchObject({ workspace_reset_request_id: 'original-reset', workspace_resetting: false });
    expect(notify).toHaveBeenCalledWith({ workflow_id: 'phone' });
  });

  it.each([1006, 1000])('sends new requests after remote close %s and repeated reconnects', async (code) => {
    await mount();
    await open();
    for (let i = 0; i < 3; i++) {
      act(() => latest().remoteClose(code));
      expect(actions.isReady).toBe(false);
      await advance(WS_RECONNECT.MIN_DELAY_MS * 4);
      await open();
      expect(actions.isReady).toBe(true);
      const response = actions.sendRequest('test_read');
      const request = latest().sent.filter(m => m.type === 'test_read').at(-1)!;
      expect(request).toBeDefined();
      act(() => latest().message({ request_id: request.request_id, success: true }));
      await expect(response).resolves.toMatchObject({ success: true });
    }
    expect(NativeSocket.instances).toHaveLength(4);
  });

  it('preserves unsent work through PartySocket synthetic code 1000 on native error', async () => {
    await mount();
    const response = actions.sendRequest('queued_read');
    act(() => latest().dispatchEvent(new Event('error')));
    await advance(WS_RECONNECT.MIN_DELAY_MS * 4);
    await open();
    const request = latest().sent.find(m => m.type === 'queued_read')!;
    expect(request).toBeDefined();
    act(() => latest().message({ request_id: request.request_id, success: true }));
    await expect(response).resolves.toMatchObject({ success: true });
  });

  it('rejects in-flight mutations on disconnect without replaying them', async () => {
    await mount();
    await open();
    const result = actions.sendRequest('test_write').catch(error => error.message);
    act(() => latest().remoteClose());
    await expect(result).resolves.toContain('outcome may be unknown');
    await advance(WS_RECONNECT.MIN_DELAY_MS * 4);
    await open();
    expect(latest().sent.some(m => m.type === 'test_write')).toBe(false);
  });

  it('keeps the original deadline when queued work is sent', async () => {
    await mount();
    const result = actions.sendRequest('short_read', {}, 1000).catch(error => error.message);
    await advance(800);
    await open();
    expect(latest().sent.some(m => m.type === 'short_read')).toBe(true);
    await advance(201);
    await expect(result).resolves.toContain('Request timeout');
  });

  it('does not send work that expired while disconnected', async () => {
    await mount();
    const result = actions.sendRequest('expired_read', {}, 100).catch(error => error.message);
    await advance(101);
    await expect(result).resolves.toContain('Request timeout (queued)');
    await open();
    expect(latest().sent.some(m => m.type === 'expired_read')).toBe(false);
  });

  it('bounds the disconnected queue and cancels all remaining waits on unmount', async () => {
    const view = await mount();
    const waits = Array.from({ length: 201 }, () =>
      actions.sendRequest('queued_read', {}, -1).catch(error => error.message));
    await expect(waits[0]).resolves.toContain('backpressure');
    view.unmount();
    expect((await Promise.all(waits)).slice(1).every(reason => reason === 'Component unmounted')).toBe(true);
  });

  it('stops a connecting socket and rejects queued work on logout', async () => {
    const view = await mount();
    const old = latest();
    const result = actions.sendRequest('queued_read', {}, -1).catch(error => error.message);
    auth.isAuthenticated = false;
    view.rerender(tree());
    await expect(result).resolves.toBe('User logged out');
    expect(old.close).toHaveBeenCalledOnce();
    await advance(60_000);
    expect(NativeSocket.instances).toHaveLength(1);
    await expect(actions.sendRequest('after_logout')).rejects.toThrow('not authenticated');
    auth.isAuthenticated = true;
    view.rerender(tree());
    await advance(150);
    await open();
    act(() => old.remoteClose());
    expect(actions.isReady).toBe(true);
  });

  it.each(['connecting', 'open', 'retrying'])('disposes %s sockets on unmount', async state => {
    const view = await mount();
    if (state !== 'connecting') await open();
    if (state === 'retrying') act(() => latest().remoteClose());
    const result = actions.sendRequest('pending_read', {}, -1).catch(error => error.message);
    view.unmount();
    await expect(result).resolves.toBe('Component unmounted');
    await advance(60_000);
    expect(NativeSocket.instances).toHaveLength(1);
  });

  it('owns only one connection under Strict Mode', async () => {
    render(<StrictMode>{tree()}</StrictMode>);
    await advance(150);
    await open();
    expect(NativeSocket.instances).toHaveLength(1);
    expect(actions.isReady).toBe(true);
  });

  it('ignores late reconnect snapshots after switching workflows in the same tick', async () => {
    NativeSocket.autoSnapshots = false;
    useAppStore.setState({ currentWorkflow: { id: 'A' } as any });
    await mount();
    await open();
    const oldRequests = [...latest().sent];
    const oldCount = latest().sent.length;
    act(() => useAppStore.setState({ currentWorkflow: { id: 'B' } as any }));
    const newRequests = latest().sent.slice(oldCount);
    const respond = async (requests: Array<Record<string, any>>, label: string) => {
      await act(async () => {
        for (const request of requests) {
          if (request.type === 'get_console_logs') latest().message({
            request_id: request.request_id, success: true,
            logs: [{ node_id: 'n1', label, data: label, timestamp: 'now' }],
          });
        }
      });
    };
    // The chats read their own thread queries; the context fetches logs only.
    expect(newRequests.some((request) => request.type === 'get_chat_messages')).toBe(false);
    await respond(newRequests, 'B');
    expect(context.consoleLogs[0]?.label).toBe('B');
    await respond(oldRequests, 'A');
    expect(context.consoleLogs[0]?.label).toBe('B');
  });

  it('reconnects a silent open socket after a missed heartbeat', async () => {
    await mount();
    await open();
    await advance(WS_HEARTBEAT.INTERVAL_MS);
    expect(latest().sent.some(m => m.type === 'ping')).toBe(true);
    await advance(WS_HEARTBEAT.TIMEOUT_MS + 1);
    expect(actions.isReady).toBe(false);
    expect(NativeSocket.instances).toHaveLength(2);
    await open();
    expect(actions.isReady).toBe(true);
  });

  it('keeps a responsive connection and gives a resumed tab a fresh heartbeat window', async () => {
    await mount();
    await open();
    await advance(WS_HEARTBEAT.INTERVAL_MS);
    act(() => latest().message({ type: 'pong' }));
    await advance(WS_HEARTBEAT.TIMEOUT_MS + 1);
    expect(NativeSocket.instances).toHaveLength(1);
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    await advance(120_000);
    expect(NativeSocket.instances).toHaveLength(1);
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    await advance(WS_HEARTBEAT.TIMEOUT_MS - 1);
    act(() => latest().message({ type: 'pong' }));
    await advance(2);
    expect(actions.isReady).toBe(true);
    expect(NativeSocket.instances).toHaveLength(1);
  });
});
