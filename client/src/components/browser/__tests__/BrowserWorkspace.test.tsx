import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { actions } = vi.hoisted(() => ({ actions: { isReady: true, sendRequest: vi.fn() } }));
vi.mock('@/contexts/WebSocketContext', () => ({ useWebSocketActions: () => actions }));
import BrowserWorkspace from '../BrowserWorkspace';

class MockSocket {
  static OPEN = 1;
  static instances: MockSocket[] = [];
  readyState = 0;
  binaryType = '';
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  sent: Record<string, unknown>[] = [];
  close = vi.fn(() => { this.readyState = 3; });
  constructor(public url: string) { MockSocket.instances.push(this); }
  send(message: string) { this.sent.push(JSON.parse(message)); }
  open() { this.readyState = 1; this.onopen?.(); }
  message(data: Record<string, unknown>) { this.onmessage?.({ data: JSON.stringify(data) }); }
}
const nodes = [{ node_id: 'browser-1', label: 'Research' }];

function mockFrameRenderer(deferred = false) {
  const loads: (() => void)[] = [];
  const draw = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage: draw } as unknown as CanvasRenderingContext2D);
  const revoke = vi.fn(); const create = vi.fn(() => 'blob:browser-frame');
  const NativeURL = URL;
  vi.stubGlobal('URL', class extends NativeURL { static createObjectURL = create; static revokeObjectURL = revoke; });
  vi.stubGlobal('Image', class {
    naturalWidth = 640; naturalHeight = 360;
    onload: (() => void) | null = null; onerror: (() => void) | null = null;
    set src(value: string) { if (!value) return; const load = () => this.onload?.(); if (deferred) loads.push(load); else queueMicrotask(load); }
  });
  const header = new TextEncoder().encode(JSON.stringify({ seq: 1, device_width: 1280, device_height: 720 }));
  const frame = new Uint8Array(4 + header.length + 1); frame.set([1, 1]);
  new DataView(frame.buffer).setUint16(2, header.length, false); frame.set(header, 4);
  return { draw, create, revoke, frame, loads };
}

beforeEach(() => {
  MockSocket.instances = []; actions.isReady = true; actions.sendRequest.mockReset();
  actions.sendRequest.mockResolvedValue({ success: true });
  vi.stubGlobal('WebSocket', MockSocket);
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('BrowserWorkspace session lifecycle', () => {
  it('does not connect without a saved workflow or a browser node', () => {
    const { rerender } = render(<BrowserWorkspace nodes={nodes} />);
    expect(screen.getByText('Save this workflow to open its browser.')).toBeInTheDocument();
    rerender(<BrowserWorkspace workflowId="wf" nodes={[]} />);
    expect(MockSocket.instances).toHaveLength(0);
  });
  it('attaches immediately on open, watches idle sessions, and launches only on Start', async () => {
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    expect(socket.url).toMatch(/\/ws\/browser$/);
    act(() => { socket.open(); socket.message({ type: 'attached', running: false }); });
    expect(socket.sent[0]).toMatchObject({ type: 'attach', target: { kind: 'node', workflow_id: 'wf', node_id: 'browser-1' }, visible: true });
    expect(actions.sendRequest.mock.calls.some(([type]) => type === 'browser_session_open')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Start browser' }));
    await waitFor(() => expect(actions.sendRequest).toHaveBeenCalledWith('browser_session_open', { workflow_id: 'wf', node_id: 'browser-1' }, 60_000));
  });
  it('sends hidden visibility and closes the old socket when the selected node changes', () => {
    const { rerender, unmount } = render(<BrowserWorkspace workflowId="wf" nodes={[...nodes, { node_id: 'browser-2', label: 'Orders' }]} />);
    const old = MockSocket.instances[0]; act(() => old.open());
    rerender(<BrowserWorkspace workflowId="wf" nodes={[...nodes, { node_id: 'browser-2', label: 'Orders' }]} visible={false} />);
    expect(old.sent).toContainEqual({ type: 'visibility', visible: false });
    fireEvent.change(screen.getByRole('combobox', { name: 'Browser node' }), { target: { value: 'browser-2' } });
    expect(old.close).toHaveBeenCalledOnce();
    const current = MockSocket.instances[1]; act(() => current.open());
    expect(current.sent[0]).toMatchObject({ target: { node_id: 'browser-2' }, visible: false });
    unmount(); expect(current.close).toHaveBeenCalledOnce();
  });
  it('gates navigation and keyboard input on server-confirmed control and hands back on unmount', () => {
    const { unmount } = render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.message({ type: 'state', state: 'agent', controller: null }); });
    expect(screen.getByRole('textbox', { name: 'Browser address' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Take control' }));
    expect(socket.sent.at(-1)).toEqual({ type: 'control_request' });
    act(() => socket.message({ type: 'state', state: 'user', controller: 'you' }));
    expect(screen.getByRole('textbox', { name: 'Browser address' })).toBeEnabled();
    fireEvent.keyDown(screen.getByRole('group', { name: 'Live browser page' }), { key: 'Enter', code: 'Enter', keyCode: 13 });
    expect(socket.sent.at(-1)).toMatchObject({ type: 'key', action: 'down', key: 'Enter', key_code: 13 });
    unmount(); expect(socket.sent.at(-1)).toEqual({ type: 'control_release' });
  });
  it('acknowledges malformed binary frames so a bad frame cannot stall the stream', async () => {
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.onmessage?.({ data: new ArrayBuffer(2) }); });
    await waitFor(() => expect(socket.sent.at(-1)).toEqual({ type: 'ack' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Incomplete browser frame');
  });
  it('paints and acknowledges frames, revokes JPEG URLs, and skips decoding hidden frames', async () => {
    const { draw, create, revoke, frame } = mockFrameRenderer();
    const { rerender } = render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.onmessage?.({ data: frame.buffer }); });
    await waitFor(() => expect(draw).toHaveBeenCalledOnce());
    expect(socket.sent.at(-1)).toEqual({ type: 'ack', seq: 1 });
    expect(revoke).toHaveBeenCalledWith('blob:browser-frame');
    rerender(<BrowserWorkspace workflowId="wf" nodes={nodes} visible={false} />);
    act(() => socket.onmessage?.({ data: frame.buffer }));
    await waitFor(() => expect(socket.sent.filter((message) => message.type === 'ack')).toHaveLength(2));
    expect(create).toHaveBeenCalledOnce();
  });
  it('clears the picture and offers Reconnect after terminal screencast failure', async () => {
    const { draw, frame } = mockFrameRenderer();
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.onmessage?.({ data: frame.buffer }); });
    await waitFor(() => expect(draw).toHaveBeenCalledOnce());
    act(() => {
      socket.message({ type: 'error', code: 'screencast', retrying: false, message: 'Reconnect the live view.' });
      // A controller broadcast does not repair a failed picture.
      socket.message({ type: 'state', state: 'idle', controller: null });
    });
    expect(screen.getByLabelText('Browser screenshot')).not.toBeVisible();
    expect(screen.getByRole('alert')).toHaveTextContent('Reconnect the live view.');
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));
    expect(socket.close).toHaveBeenCalledOnce();
    expect(MockSocket.instances).toHaveLength(2);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
  it('recovers a retrying screencast error on a fresh frame without hiding navigation errors', async () => {
    const { draw, frame } = mockFrameRenderer();
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => {
      socket.open(); socket.message({ type: 'state', state: 'agent' });
      socket.message({ type: 'error', code: 'screencast', retrying: true, message: 'Retrying live view…' });
    });
    expect(screen.queryByRole('button', { name: 'Reconnect' })).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Employee is browsing');
    act(() => socket.onmessage?.({ data: frame.buffer }));
    await waitFor(() => expect(draw).toHaveBeenCalledOnce());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    act(() => {
      socket.message({ type: 'error', code: 'screencast', retrying: true, message: 'Retrying live view…' });
      socket.message({ type: 'error', code: 'navigation', message: 'This address is blocked.' });
      socket.onmessage?.({ data: frame.buffer });
    });
    await waitFor(() => expect(draw).toHaveBeenCalledTimes(2));
    expect(screen.getByRole('alert')).toHaveTextContent('This address is blocked.');
  });
});


describe('browser frame and input lifecycle boundaries', () => {
  it('does not reset an unchanged canvas backing store', async () => {
    const { frame, draw } = mockFrameRenderer();
    const width = vi.spyOn(HTMLCanvasElement.prototype, 'width', 'set');
    const height = vi.spyOn(HTMLCanvasElement.prototype, 'height', 'set');
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.onmessage?.({ data: frame.buffer }); });
    await waitFor(() => expect(draw).toHaveBeenCalledOnce());
    act(() => socket.onmessage?.({ data: frame.buffer }));
    await waitFor(() => expect(draw).toHaveBeenCalledTimes(2));
    expect(width).toHaveBeenCalledTimes(1); expect(height).toHaveBeenCalledTimes(1);
  });
  it('does not paint after hiding during decode, but still acknowledges the frame', async () => {
    const { frame, loads, draw, revoke } = mockFrameRenderer(true);
    const { rerender } = render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.onmessage?.({ data: frame.buffer }); });
    await waitFor(() => expect(loads).toHaveLength(1));
    rerender(<BrowserWorkspace workflowId="wf" nodes={nodes} visible={false} />);
    await act(async () => loads[0]());
    expect(draw).not.toHaveBeenCalled(); expect(revoke).toHaveBeenCalled();
    expect(socket.sent).toContainEqual({ type: 'ack', seq: 1 });
  });
  it('does not restore live state when an old decode finishes after disconnect', async () => {
    const { frame, loads, draw, revoke } = mockFrameRenderer(true);
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.onmessage?.({ data: frame.buffer }); });
    await waitFor(() => expect(loads).toHaveLength(1));
    act(() => { socket.readyState = 3; socket.onclose?.({ code: 4004, reason: 'Session closed' }); });
    await act(async () => loads[0]());
    expect(draw).not.toHaveBeenCalled(); expect(revoke).toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent('Disconnected');
    expect(screen.getByLabelText('Browser screenshot')).not.toBeVisible();
    expect(screen.getByRole('button', { name: 'Reconnect' })).toBeInTheDocument();
  });
  it('cleans up an in-progress decode on disposal without painting', async () => {
    const { frame, loads, draw, revoke } = mockFrameRenderer(true);
    const { unmount } = render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.onmessage?.({ data: frame.buffer }); });
    await waitFor(() => expect(loads).toHaveLength(1));
    unmount(); await act(async () => loads[0]());
    expect(draw).not.toHaveBeenCalled(); expect(revoke).toHaveBeenCalled();
    expect(socket.close).toHaveBeenCalledOnce();
  });
  it.each(['blur', 'pointercancel'])('releases control on %s and stops further keys', (kind) => {
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.message({ type: 'state', state: 'user', controller: 'you' }); });
    const surface = screen.getByRole('group', { name: 'Live browser page' });
    fireEvent.keyDown(surface, { key: 'Shift', code: 'ShiftLeft' });
    if (kind === 'blur') fireEvent(window, new Event('blur'));
    else fireEvent.pointerCancel(surface);
    expect(socket.sent.at(-1)).toEqual({ type: 'control_release' });
    const count = socket.sent.length;
    fireEvent.keyDown(surface, { key: 'a', code: 'KeyA' });
    expect(socket.sent).toHaveLength(count);
  });
  it('cancels a pending takeover when the document becomes hidden', () => {
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.message({ type: 'state', state: 'agent' }); });
    fireEvent.click(screen.getByRole('button', { name: 'Take control' }));
    expect(socket.sent.at(-1)).toEqual({ type: 'control_request' });
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    fireEvent(document, new Event('visibilitychange'));
    expect(socket.sent.slice(-2)).toEqual([{ type: 'visibility', visible: false }, { type: 'control_release' }]);
    expect(screen.getByRole('button', { name: 'Take control' })).toBeEnabled();
  });
  it('bounds decoding when a sender exceeds its two-frame credit', async () => {
    const { frame, loads } = mockFrameRenderer(true);
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); for (let i = 0; i < 10; i++) socket.onmessage?.({ data: frame.buffer }); });
    await waitFor(() => expect(loads).toHaveLength(1));
    expect(socket.sent.filter((m) => m.type === 'ack')).toHaveLength(8);
    await act(async () => loads[0]());
    expect(loads).toHaveLength(2);
    await act(async () => loads[1]());
    expect(socket.sent.filter((m) => m.type === 'ack')).toHaveLength(10);
  });
});
