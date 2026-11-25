import { StrictMode, useLayoutEffect } from 'react';
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WS_HEARTBEAT, WS_RECONNECT } from '../../lib/connectionConfig';

const auth = vi.hoisted(() => ({ isAuthenticated: true, isLoading: false }));
vi.mock('../AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../../services/workflowApi', () => ({ workflowApi: {} }));

import { WebSocketProvider, WorkflowControlMutationSupersededError, useWebSocket, useWebSocketActions, type WebSocketActions } from '../WebSocketContext';
import { useAppStore } from '../../store/useAppStore';
import { useNodeStatusStore } from '../../stores/nodeStatusStore';
import { useWorkflowControlStore } from '../../stores/workflowControlStore';
import { useLlmWaitStore } from '../../stores/llmWaitStore';

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
    useLlmWaitStore.getState().resetSession();
    auth.isAuthenticated = true;
    auth.isLoading = false;
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('projects actual diagnostic frames without replacing terminal execution status', async () => {
    await mount(); await open();
    useNodeStatusStore.getState().setStatus('diagnostic', 'agent', { status: 'success', data: { phase: 'completed' } });
    const wait = { execution_key: 'e:agent', request_id: 'a:1', seq: 1, state: 'waiting', stage: 'sdk_request',
      elapsed_ms: 5000, runtime_behavior_version: 1, execution_id: 'e', root_execution_id: 'root', scope: 'workspace', workspace_epoch: 1 };
    act(() => latest().message({ type: 'node_status', workflow_id: 'diagnostic', node_id: 'agent', data: { llm_wait: wait } }));
    expect(useNodeStatusStore.getState().allStatuses.diagnostic.agent.status).toBe('success');
    expect(Object.values(useLlmWaitStore.getState().byWorkflow.diagnostic)[0].state).toBe('waiting');
    act(() => latest().message({ type: 'node_status', workflow_id: 'diagnostic', node_id: 'agent', data: { llm_wait: { ...wait, state: 'cleared', seq: 2 } } }));
    act(() => latest().message({ type: 'node_status', workflow_id: 'diagnostic', node_id: 'agent', data: { llm_wait: { ...wait, seq: 999 } } }));
    expect(Object.values(useLlmWaitStore.getState().byWorkflow.diagnostic)[0].state).toBe('cleared');
    act(() => latest().message({ type: 'node_status', workflow_id: 'diagnostic', node_id: 'agent', data: { llm_wait: null } }));
    expect(useNodeStatusStore.getState().allStatuses.diagnostic.agent.status).toBe('success');
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

  it.each([
    { root: 'g1', revision: 7, state: 'running', expected: 'retry' },
    { root: 'g1', revision: 9, state: 'running', expected: 'conflict' }, // a newer Resume
    { root: 'g1', revision: 8, state: 'pausing', expected: 'pausing' },
    { root: 'g2', revision: 7, state: 'running', expected: 'conflict' },
    { root: 'g2', revision: 9, state: 'paused', expected: 'conflict' },
  ])('recovers a stale Stop when resync sees $root at $state revision $revision', async ({ root, revision, state, expected }) => {
    await mount();
    await open();
    // Home can have a summary before the socket has any control snapshot.
    const running = { workflow_id: 'browser', state: 'running', generation: 1,
      root_execution_id: 'g1', revision: 7, execution_control_version: 1 };
    let result!: Promise<unknown>;
    act(() => { result = actions.pauseWorkflow('browser', 3, 'g1').catch(error => error.message); });
    const pauses = () => latest().sent.filter(item => item.type === 'pause_workflow');
    const first = pauses()[0];
    expect(first).toMatchObject({ expected_revision: 3, expected_root_execution_id: 'g1' });
    await act(async () => latest().message({ request_id: first.request_id, success: false,
      error: 'control_revision_conflict', ...running }));
    const read = latest().sent.filter(item => item.type === 'get_workflow_control_status').at(-1)!;
    await act(async () => latest().message({ request_id: read.request_id, success: true,
      ...running, root_execution_id: root, generation: root === 'g1' ? 1 : 2, revision, state }));
    if (expected === 'retry') {
      const retry = pauses()[1];
      expect(retry).toMatchObject({ expected_revision: 7, expected_root_execution_id: 'g1' });
      expect(retry.idempotency_key).not.toBe(first.idempotency_key);
      await act(async () => latest().message({ request_id: retry.request_id, success: true,
        ...running, state: 'paused', revision: 9, pause_started_revision: 8 }));
      await expect(result).resolves.toMatchObject({ state: 'paused', revision: 9, pause_started_revision: 8 });
      expect(context.workflowControlStatuses.browser).not.toHaveProperty('pause_started_revision');
      expect(pauses()).toHaveLength(2);
    } else {
      if (expected === 'pausing') await expect(result).resolves.toMatchObject({ state: 'pausing', revision: 8 });
      else await expect(result).resolves.toBe('control_revision_conflict');
      expect(pauses()).toHaveLength(1);
    }
  });

  it.each([
    { receipt: undefined, replyRevision: 5, broadcastRevision: 5, owns: false }, // legacy or joined
    { receipt: 4, replyRevision: 5, broadcastRevision: 5, owns: true },
    { receipt: 4, replyRevision: 5, broadcastRevision: 9, owns: false }, // stale direct reply
    { receipt: 8, replyRevision: 9, broadcastRevision: 9, owns: false }, // later pause
  ])('keeps pause receipt $receipt scoped to direct reply $replyRevision at revision $broadcastRevision', async ({
    receipt, replyRevision, broadcastRevision, owns,
  }) => {
    await mount();
    await open();
    const paused = { workflow_id: 'browser', generation: 1, root_execution_id: 'g1', state: 'paused' };
    let result!: ReturnType<typeof actions.pauseWorkflow>;
    act(() => { result = actions.pauseWorkflow('browser', 3, 'g1'); });
    const request = latest().sent.find(item => item.type === 'pause_workflow')!;
    act(() => latest().message({ type: 'workflow_control_status', data: { ...paused, revision: broadcastRevision } }));
    await act(async () => latest().message({ request_id: request.request_id, success: true,
      ...paused, revision: replyRevision, pause_started_revision: receipt }));
    if (owns) await expect(result).resolves.toHaveProperty('pause_started_revision', 4);
    else await expect(result).resolves.not.toHaveProperty('pause_started_revision');
    expect(context.workflowControlStatuses.browser).not.toHaveProperty('pause_started_revision');
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

  it.each(['pause_workflow', 'stop_chat_run'])('lets Reset supersede a stuck %s using the admitted generation revision', async (stopType) => {
    await mount();
    await open();
    const clear = vi.spyOn(useNodeStatusStore.getState(), 'clearWorkflow');
    const notify = vi.fn();
    actions.addEventListener('workflow_runtime_reset', notify);
    const running = { workflow_id: 'browser', state: 'running', revision: 3, generation: 1,
      root_execution_id: 'g1', can_pause: true, can_reset: true, in_flight_count: 1 };
    act(() => latest().message({ type: 'workflow_control_status', data: running }));
    let stop!: Promise<unknown>;
    act(() => { stop = (stopType === 'pause_workflow'
      ? actions.pauseWorkflow('browser', 3)
      : actions.stopChatRun('browser', 3, 'r1')).catch(error => error); });
    const stopRequest = latest().sent.find(item => item.type === stopType)!;
    act(() => latest().message({ type: 'workflow_control_status', data: { ...running, state: 'pausing', revision: 4 } }));
    let reset!: Promise<unknown>;
    await act(async () => { reset = actions.resetWorkflow('browser', 3); });
    const resetRequest = latest().sent.find(item => item.type === 'reset_workflow')!;
    expect(resetRequest).toMatchObject({ expected_revision: 4, expected_root_execution_id: 'g1' });
    await expect(stop).resolves.toBeInstanceOf(WorkflowControlMutationSupersededError);
    expect(context.workflowControlPending.browser).toEqual({ action: 'reset', state: 'resetting' });
    expect(useWorkflowControlStore.getState().pending.browser).toEqual({ action: 'reset', state: 'resetting' });
    expect(clear).not.toHaveBeenCalled();
    expect(notify).not.toHaveBeenCalled();
    const resetting = { ...running, state: 'resetting', revision: 6, can_pause: false };
    await act(async () => {
      latest().message({ type: 'workflow_control_status', data: resetting });
      latest().message({ request_id: stopRequest.request_id, success: true,
        ...running, state: 'paused', revision: 5 });
    });
    expect(context.workflowControlStatuses.browser).toMatchObject({ state: 'resetting', revision: 6 });
    expect(context.workflowControlPending.browser?.action).toBe('reset');
    expect(latest().sent.some(item => item.type === 'get_workflow_control_status')).toBe(false);
    await expect(actions.resumeWorkflow('browser', 6)).rejects.toThrow('already in progress');
    await expect(actions.resetWorkflow('browser', 6)).rejects.toThrow('already in progress');
    await act(async () => latest().message({ request_id: resetRequest.request_id, success: true,
      ...running, state: 'ready', revision: 7, can_start: true, can_reset: false, in_flight_count: 0 }));
    await expect(reset).resolves.toMatchObject({ state: 'ready', revision: 7 });
    expect(context.workflowControlPending.browser).toBeUndefined();
    expect(clear).toHaveBeenCalledWith('browser');
    expect(notify).toHaveBeenCalledWith({ workflow_id: 'browser' });
    act(() => latest().message({ type: 'workflow_control_status', data: { ...running, state: 'paused', revision: 5 } }));
    expect(context.workflowControlStatuses.browser).toMatchObject({ state: 'ready', revision: 7 });
  });

  it('ignores an interrupted Stop status resync once Reset takes ownership', async () => {
    await mount();
    await open();
    const running = { workflow_id: 'browser', state: 'running', revision: 3, generation: 1, root_execution_id: 'g1', can_reset: true };
    act(() => latest().message({ type: 'workflow_control_status', data: running }));
    let stop!: Promise<unknown>;
    act(() => { stop = actions.pauseWorkflow('browser', 3).catch(error => error); });
    const stopRequest = latest().sent.find(item => item.type === 'pause_workflow')!;
    const pausing = { ...running, state: 'pausing', revision: 4 };
    await act(async () => latest().message({ request_id: stopRequest.request_id, success: false,
      error: 'workflow_control_transition_pending', status: pausing }));
    const read = latest().sent.find(item => item.type === 'get_workflow_control_status')!;
    let reset!: Promise<unknown>;
    await act(async () => { reset = actions.resetWorkflow('browser', 3); });
    await expect(stop).resolves.toBeInstanceOf(WorkflowControlMutationSupersededError);
    const resetRequest = latest().sent.find(item => item.type === 'reset_workflow')!;
    await act(async () => latest().message({ request_id: read.request_id, success: true,
      ...running, state: 'paused', revision: 5 }));
    expect(context.workflowControlStatuses.browser).toMatchObject({ state: 'pausing', revision: 4 });
    expect(context.workflowControlPending.browser?.action).toBe('reset');
    await act(async () => latest().message({ request_id: resetRequest.request_id, success: true,
      ...running, state: 'ready', revision: 7 }));
    await expect(reset).resolves.toMatchObject({ state: 'ready' });
  });

  it('does not apply pending Stop intent to a newer generation when Reset is pressed', async () => {
    await mount();
    await open();
    const running = { workflow_id: 'browser', state: 'running', revision: 3, generation: 1, root_execution_id: 'g1', can_reset: true };
    act(() => latest().message({ type: 'workflow_control_status', data: running }));
    let stop!: Promise<unknown>;
    act(() => { stop = actions.pauseWorkflow('browser', 3); });
    const stopRequest = latest().sent.find(item => item.type === 'pause_workflow')!;
    act(() => latest().message({ type: 'workflow_control_status', data: {
      ...running, revision: 8, generation: 2, root_execution_id: 'g2',
    } }));
    await expect(actions.resetWorkflow('browser', 3)).rejects.toThrow('control_generation_conflict');
    expect(latest().sent.some(item => item.type === 'reset_workflow')).toBe(false);
    await act(async () => latest().message({ request_id: stopRequest.request_id, success: true,
      ...running, state: 'paused', revision: 5 }));
    await stop;
    expect(context.workflowControlStatuses.browser).toMatchObject({ generation: 2, revision: 8 });
  });

  it.each(['pause_workflow', 'stop_chat_run', 'resume_workflow'])('recognizes a Reset from another tab superseding %s without another status request', async (type) => {
    await mount();
    await open();
    const clear = vi.spyOn(useNodeStatusStore.getState(), 'clearWorkflow');
    const status = { workflow_id: 'browser', state: type === 'resume_workflow' ? 'paused' : 'running',
      revision: 3, generation: 1, root_execution_id: 'g1', can_reset: true };
    act(() => latest().message({ type: 'workflow_control_status', data: status }));
    let result!: Promise<unknown>;
    act(() => {
      result = (type === 'resume_workflow'
        ? actions.resumeWorkflow('browser', 3)
        : type === 'stop_chat_run'
          ? actions.stopChatRun('browser', 3, 'r1')
          : actions.pauseWorkflow('browser', 3)).catch(error => error);
    });
    const request = latest().sent.find(item => item.type === type)!;
    await act(async () => latest().message({ request_id: request.request_id, success: true, superseded: true,
      ...status, state: 'ready', revision: 7, can_start: true, can_reset: false }));
    await expect(result).resolves.toBeInstanceOf(WorkflowControlMutationSupersededError);
    expect(context.workflowControlStatuses.browser).toMatchObject({ state: 'ready', revision: 7 });
    expect(context.workflowControlPending.browser).toBeUndefined();
    expect(latest().sent.some(item => item.type === 'get_workflow_control_status')).toBe(false);
    expect(clear).not.toHaveBeenCalled();
  });

  it('does not retry a Reset conflict or clear runtime state in a newer generation', async () => {
    await mount();
    await open();
    const clear = vi.spyOn(useNodeStatusStore.getState(), 'clearWorkflow');
    const running = { workflow_id: 'browser', state: 'running', revision: 3, generation: 1, root_execution_id: 'g1', can_reset: true };
    act(() => latest().message({ type: 'workflow_control_status', data: running }));
    let reset!: Promise<unknown>;
    act(() => { reset = actions.resetWorkflow('browser', 3).catch(error => error.message); });
    const resetRequest = latest().sent.find(item => item.type === 'reset_workflow')!;
    const nextGeneration = { ...running, generation: 2, root_execution_id: 'g2', revision: 8 };
    await act(async () => latest().message({ request_id: resetRequest.request_id, success: false,
      error: 'control_revision_conflict', status: nextGeneration }));
    const read = latest().sent.find(item => item.type === 'get_workflow_control_status')!;
    await act(async () => latest().message({ request_id: read.request_id, success: true, status: nextGeneration }));
    await expect(reset).resolves.toBe('control_revision_conflict');
    expect(latest().sent.filter(item => item.type === 'reset_workflow')).toHaveLength(1);
    expect(clear).not.toHaveBeenCalled();
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
