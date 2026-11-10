import { createRef, type ComponentProps } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SurfaceControl } from '../../workspace/surface';

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

/** The browser, live, while the employee is using it. */
function browsing(props: Partial<ComponentProps<typeof BrowserWorkspace>> = {}) {
  render(<BrowserWorkspace workflowId="wf" nodes={nodes} {...props} />);
  const socket = MockSocket.instances.at(-1)!;
  act(() => { socket.open(); socket.message({ type: 'state', state: 'agent', controller: null }); });
  return socket;
}
/** The row of page controls. */
const row = () => screen.getByRole('button', { name: 'Back' }).parentElement!.parentElement!;
/** Every button and link the browser shows, by name, in order. */
const controls = () => Array.from(row().closest('[data-full-view]')!.querySelectorAll('button, a'), (control) => control.getAttribute('aria-label') ?? control.textContent);

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
    const page = screen.getByRole('group', { name: 'Live browser page' });
    expect(screen.getByRole('button', { name: 'Reload page' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Take over' }));
    expect(socket.sent.at(-1)).toEqual({ type: 'control_request' });
    // Asking is not having: nothing works until the server says so.
    expect(screen.getByRole('button', { name: 'Reload page' })).toBeDisabled();
    fireEvent.keyDown(page, { key: 'Enter', code: 'Enter', keyCode: 13 });
    expect(socket.sent.at(-1)).toEqual({ type: 'control_request' });
    act(() => socket.message({ type: 'state', state: 'user', controller: 'you' }));
    expect(screen.getByRole('button', { name: 'Reload page' })).toBeEnabled();
    fireEvent.keyDown(page, { key: 'Enter', code: 'Enter', keyCode: 13 });
    expect(socket.sent.at(-1)).toMatchObject({ type: 'key', action: 'down', key: 'Enter', key_code: 13 });
    unmount(); expect(socket.sent.at(-1)).toEqual({ type: 'control_release', outcome: 'control_lost' });
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
    // Still live: the picture is awaited and Take over is on offer.
    expect(screen.getByText('Waiting for the browser picture…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Take over' })).toBeEnabled();
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
  it('distinguishes an explicit challenge resume from losing browser focus', () => {
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.message({ type: 'state', state: 'awaiting_user', challenge_required: true, request: { reason: 'captcha', message: 'Finish the site verification.' } }); });
    // What the site asks for shows over the page, with Take over on offer.
    expect(screen.getByText('Finish the site verification.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Take over' })).toBeEnabled();
    act(() => socket.message({ type: 'state', state: 'user', controller: 'you', challenge_required: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Hand back' }));
    expect(socket.sent.at(-1)).toEqual({ type: 'control_release' });
    fireEvent(window, new Event('blur'));
    expect(socket.sent.at(-1)).toEqual({ type: 'control_release', outcome: 'control_lost' });
  });
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
    // Not live again: the page says so and Take over is not on offer.
    expect(screen.getByText('Disconnected')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
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
    expect(socket.sent.at(-1)).toEqual({ type: 'control_release', outcome: 'control_lost' });
    const count = socket.sent.length;
    fireEvent.keyDown(surface, { key: 'a', code: 'KeyA' });
    expect(socket.sent).toHaveLength(count);
  });
  it('answers the Workspace’s Take over no while the browser is stopped', async () => {
    const control = createRef<SurfaceControl>();
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} ref={control} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.message({ type: 'attached', running: false }); socket.message({ type: 'idle', reason: 'not running' }); });
    let claim!: Promise<(() => void) | null>;
    act(() => { claim = control.current!.claim(); });
    expect(socket.sent.at(-1)).toEqual({ type: 'control_request', force: true });
    // A stopped browser answers every request with `idle`.
    act(() => socket.message({ type: 'idle', reason: 'not running' }));
    await expect(claim).resolves.toBeNull();
  });
  it('gives back the control the Workspace’s Take over was granted', async () => {
    const control = createRef<SurfaceControl>();
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} ref={control} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.message({ type: 'state', state: 'agent' }); });
    let claim!: Promise<(() => void) | null>;
    act(() => { claim = control.current!.claim(); });
    expect(socket.sent.at(-1)).toEqual({ type: 'control_request', force: true });
    act(() => { socket.message({ type: 'control', granted: true }); socket.message({ type: 'state', state: 'user', controller: 'you' }); });
    const giveBack = await claim;
    expect(giveBack).toBeTypeOf('function');
    expect(screen.getByRole('button', { name: 'Back' })).toBeEnabled();
    act(() => giveBack!());
    expect(socket.sent.at(-1)).toEqual({ type: 'control_release' });
  });
  it('cancels a pending takeover when the document becomes hidden', () => {
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.message({ type: 'state', state: 'agent' }); });
    fireEvent.click(screen.getByRole('button', { name: 'Take over' }));
    expect(socket.sent.at(-1)).toEqual({ type: 'control_request' });
    expect(screen.getByRole('button', { name: 'Taking over…' })).toBeDisabled();
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    fireEvent(document, new Event('visibilitychange'));
    expect(socket.sent.slice(-2)).toEqual([{ type: 'visibility', visible: false }, { type: 'control_release', outcome: 'control_lost' }]);
    expect(screen.getByRole('button', { name: 'Take over' })).toBeEnabled();
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
  it('discards a pending picture during protected login and exposes authorized close for manual recovery', async () => {
    const { frame, loads, draw } = mockFrameRenderer(true);
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.message({ type: 'state', state: 'agent' }); socket.onmessage?.({ data: frame.buffer }); });
    await waitFor(() => expect(loads).toHaveLength(1));
    expect(screen.getByRole('button', { name: 'Take over' })).toBeInTheDocument();
    act(() => socket.message({ type: 'sensitive', enabled: true }));
    await act(async () => loads[0]());
    expect(draw).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Take over' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close browser for manual login' }));
    await waitFor(() => expect(actions.sendRequest).toHaveBeenCalledWith('browser_session_stop', { workflow_id: 'wf', node_id: 'browser-1' }, 60_000));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start browser' })).toBeInTheDocument());
  });
});

describe('The agent’s cursor', () => {
  /** A live picture of a 1280x720 page, while the employee browses. */
  async function livePicture() {
    const { draw, frame } = mockFrameRenderer();
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} agentName="Maya" />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.message({ type: 'state', state: 'agent', controller: null }); socket.onmessage?.({ data: frame.buffer }); });
    await waitFor(() => expect(draw).toHaveBeenCalledOnce());
    return socket;
  }

  it('shows where the employee acted, with their name, on the picture', async () => {
    const socket = await livePicture();
    expect(screen.queryByText('Maya')).toBeNull();
    act(() => socket.message({ type: 'agent_action', action: 'click', x: 640, y: 360 }));
    // The middle of the page is the middle of the picture.
    const cursor = screen.getByText('Maya').parentElement!;
    expect([cursor.style.left, cursor.style.top]).toEqual(['50%', '50%']);
  });

  it('rings the field the employee typed into', async () => {
    const socket = await livePicture();
    act(() => socket.message({ type: 'agent_action', action: 'type', box: [320, 180, 640, 180] }));
    const ring = screen.getByText('Maya').closest('[aria-hidden="true"]')!.querySelector<HTMLElement>('.rounded-sm')!;
    expect([ring.style.left, ring.style.top, ring.style.width, ring.style.height]).toEqual(['25%', '25%', '50%', '25%']);
  });

  it('hides while the owner has control, and goes with the picture', async () => {
    const socket = await livePicture();
    act(() => socket.message({ type: 'agent_action', action: 'hover', x: 100, y: 100 }));
    const layer = screen.getByText('Maya').closest('[aria-hidden="true"]')!;
    expect(layer).not.toHaveClass('invisible');
    act(() => socket.message({ type: 'state', state: 'user', controller: 'you' }));
    expect(layer).toHaveClass('invisible');
    act(() => socket.message({ type: 'sensitive', enabled: true }));
    expect(screen.queryByText('Maya')).toBeNull();
  });

  it('draws nothing before the first picture', () => {
    mockFrameRenderer();
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} agentName="Maya" />);
    const socket = MockSocket.instances[0];
    act(() => { socket.open(); socket.message({ type: 'agent_action', action: 'click', x: 1, y: 1 }); });
    expect(screen.queryByText('Maya')).toBeNull();
  });
});

describe('The page and its row of controls', () => {
  it('works back, forward and reload only while the owner has control', () => {
    const socket = browsing();
    for (const name of ['Back', 'Forward', 'Reload page']) expect(screen.getByRole('button', { name })).toBeDisabled();
    act(() => socket.message({ type: 'state', state: 'user', controller: 'you' }));
    for (const [name, action] of [['Back', 'back'], ['Forward', 'forward'], ['Reload page', 'reload']]) {
      fireEvent.click(screen.getByRole('button', { name }));
      expect(socket.sent.at(-1)).toEqual({ type: 'navigate', action });
    }
  });

  it('shows the page’s domain, locked for https, with the whole address in its title', () => {
    const socket = browsing();
    act(() => socket.message({ type: 'page', url: 'https://example.com/book' }));
    const address = screen.getByText('example.com').parentElement!;
    expect(address).toHaveAttribute('title', 'https://example.com/book');
    expect(address.querySelector('.lucide-lock')).toBeInTheDocument();
    // Nothing to type into.
    expect(screen.queryByRole('textbox')).toBeNull();
    act(() => socket.message({ type: 'page', url: 'http://example.org/menu' }));
    expect(screen.getByText('example.org').parentElement!.querySelector('.lucide-lock')).toBeNull();
    // Any other address shows as it is.
    act(() => socket.message({ type: 'page', url: 'chrome://settings' }));
    expect(screen.getByText('chrome://settings')).toBeInTheDocument();
  });

  it('offers the page the browser shows in a new tab, for web pages only', () => {
    const socket = browsing();
    expect(screen.queryByRole('link', { name: 'Open in new tab' })).toBeNull();
    act(() => socket.message({ type: 'page', url: 'https://example.com/book' }));
    const link = screen.getByRole('link', { name: 'Open in new tab' });
    expect(link).toHaveAttribute('href', 'https://example.com/book');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    act(() => socket.message({ type: 'page', url: 'http://example.org/menu' }));
    expect(screen.getByRole('link', { name: 'Open in new tab' })).toHaveAttribute('href', 'http://example.org/menu');
    // Only http(s) pages get the link: a browser page, a script or a data address never does.
    for (const url of ['chrome://settings', 'javascript:alert(document.cookie)', 'data:text/html,<script>alert(1)</script>']) {
      act(() => socket.message({ type: 'page', url: 'https://example.com/book' }));
      expect(screen.getByRole('link', { name: 'Open in new tab' })).toBeInTheDocument();
      act(() => socket.message({ type: 'page', url }));
      expect(screen.queryByRole('link', { name: 'Open in new tab' })).toBeNull();
    }
  });

  it('puts the page-tab picker on a line of its own while the browser has several tabs', () => {
    const socket = browsing();
    act(() => socket.message({ type: 'tabs', tabs: [{ target_id: 't1', title: 'Bookings', active: true }] }));
    expect(screen.queryByRole('combobox', { name: 'Browser tab' })).toBeNull();
    act(() => {
      socket.message({ type: 'tabs', tabs: [{ target_id: 't1', title: 'Bookings', active: true }, { target_id: 't2', title: 'Calendar' }] });
      socket.message({ type: 'state', state: 'user', controller: 'you' });
    });
    const picker = screen.getByRole('combobox', { name: 'Browser tab' });
    expect(row()).not.toContainElement(picker);
    expect(row().nextElementSibling).toContainElement(picker);
    fireEvent.change(picker, { target: { value: 't2' } });
    expect(socket.sent.at(-1)).toEqual({ type: 'tab', action: 'activate', target_id: 't2' });
  });

  it('draws the buttons on the page neutral, and says to take over before answering a page dialog', () => {
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} />);
    const socket = MockSocket.instances[0];
    // Colour marks a state or a role, and these are neither.
    const neutral = (name: string) => {
      const button = screen.getByRole('button', { name });
      expect(button).toHaveAttribute('data-variant', 'quiet');
      expect(button).toHaveClass('border-border-default', 'text-fg-default');
    };
    act(() => { socket.open(); socket.message({ type: 'attached', running: false }); });
    neutral('Start browser');
    act(() => { socket.message({ type: 'state', state: 'agent', controller: null }); socket.message({ type: 'dialog', kind: 'confirm', message: 'Leave this page?' }); });
    neutral('OK'); neutral('Cancel');
    expect(screen.getByText('Take over to respond.')).toBeInTheDocument();
    act(() => socket.message({ type: 'sensitive', enabled: true }));
    neutral('Close browser for manual login');
    act(() => { socket.readyState = 3; socket.onclose?.({ code: 4004, reason: 'Session closed' }); });
    neutral('Reconnect');
  });
});

describe('Take over', () => {
  it('takes over and hands back with the browser’s own button where the host leaves the action out', () => {
    const socket = browsing();
    const takeOver = screen.getByRole('button', { name: 'Take over' });
    // A neutral pill at the end of the row.
    expect(takeOver).toHaveAttribute('data-variant', 'quiet');
    expect(row().lastElementChild).toBe(takeOver);
    fireEvent.click(takeOver);
    expect(socket.sent.at(-1)).toEqual({ type: 'control_request' });
    expect(screen.getByRole('button', { name: 'Taking over…' })).toBeDisabled();
    act(() => { socket.message({ type: 'control', granted: true }); socket.message({ type: 'state', state: 'user', controller: 'you' }); });
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
    const handBack = screen.getByRole('button', { name: 'Hand back' });
    expect(handBack).toBeEnabled();
    expect(handBack).toHaveAttribute('data-variant', 'quiet');
    expect(row().lastElementChild).toBe(handBack);
    fireEvent.click(handBack);
    expect(socket.sent.at(-1)).toEqual({ type: 'control_release' });
    act(() => socket.message({ type: 'state', state: 'agent', controller: null }));
    expect(screen.getByRole('button', { name: 'Take over' })).toBeEnabled();
  });

  it('takes the browser from another viewer by force', () => {
    const socket = browsing();
    act(() => socket.message({ type: 'state', state: 'user', controller: 'other' }));
    fireEvent.click(screen.getByRole('button', { name: 'Take over' }));
    expect(socket.sent.at(-1)).toEqual({ type: 'control_request', force: true });
  });

  it('offers Take over again when the server refuses', () => {
    const socket = browsing();
    fireEvent.click(screen.getByRole('button', { name: 'Take over' }));
    act(() => socket.message({ type: 'control', granted: false, reason: 'viewer_unavailable' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Control could not be granted. Try again.');
    expect(screen.getByRole('button', { name: 'Take over' })).toBeEnabled();
  });

  it('ends the row of page controls with the host’s action, while the owner does not have control', () => {
    const onTakeOver = vi.fn();
    const socket = browsing({ action: <button type="button" onClick={onTakeOver}>Take over</button> });
    const takeOver = screen.getByRole('button', { name: 'Take over' });
    expect(row()).toHaveClass('h-13', 'border-b', 'border-border-default');
    expect(row().lastElementChild).toBe(takeOver);
    // It is the host's button, which runs the takeover itself: the browser asks for nothing.
    fireEvent.click(takeOver);
    expect(onTakeOver).toHaveBeenCalledOnce();
    expect(socket.sent.some((message) => message.type === 'control_request')).toBe(false);
    act(() => socket.message({ type: 'state', state: 'user', controller: 'you' }));
    // The host holds Hand back too: the browser shows neither.
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Hand back' })).toBeNull();
    // The window lost focus, which hands control back: the action is offered again.
    act(() => { window.dispatchEvent(new Event('blur')); });
    expect(socket.sent.at(-1)).toEqual({ type: 'control_release', outcome: 'control_lost' });
    expect(screen.getByRole('button', { name: 'Take over' })).toBeInTheDocument();
  });

  it('shows no Take over and no Hand back where the host passes a null action', () => {
    const socket = browsing({ action: null });
    expect(controls()).toEqual(['Back', 'Forward', 'Reload page']);
    act(() => socket.message({ type: 'state', state: 'user', controller: 'you' }));
    expect(controls()).toEqual(['Back', 'Forward', 'Reload page']);
  });

  it.each(['its own Take over', 'the host’s action'])('offers %s only while the browser could grant control', (whose) => {
    const action = whose === 'the host’s action' ? <button type="button">Take over</button> : undefined;
    render(<BrowserWorkspace workflowId="wf" nodes={nodes} action={action} />);
    const socket = MockSocket.instances.at(-1)!;
    // Connecting, then stopped: the server would answer no.
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
    act(() => { socket.open(); socket.message({ type: 'attached', running: false }); socket.message({ type: 'idle', reason: 'not running' }); });
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
    act(() => socket.message({ type: 'state', state: 'agent', controller: null }));
    expect(screen.getByRole('button', { name: 'Take over' })).toBeInTheDocument();
    // A protected login refuses every claim.
    act(() => socket.message({ type: 'sensitive', enabled: true }));
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
  });
});

describe('Dev mode’s extras', () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  // Browser AI tasks asks the server for this browser's agents: none here.
  beforeEach(() => {
    fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ agents: [] }) }));
    vi.stubGlobal('fetch', fetchMock);
  });
  /** Dev mode's browser while the employee is using it, once Browser AI tasks has loaded. */
  async function devBrowsing() {
    const socket = browsing({ dev: true });
    await screen.findByRole('button', { name: 'Add Browser AI Agent' });
    return socket;
  }

  it('leaves out the address box, Full view and Browser AI tasks unless the host asks for them', () => {
    const socket = browsing();
    act(() => socket.message({ type: 'page', url: 'https://example.com/book' }));
    // All the browser shows is its row of page controls, with no Full view in it.
    expect(controls()).toEqual(['Back', 'Forward', 'Reload page', 'Open in new tab', 'Take over']);
    expect(screen.queryByRole('region', { name: 'Browser AI tasks' })).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    // It has no status text of its own.
    expect(screen.queryByRole('status')).toBeNull();
    // With control too, the address stays the page's domain: nothing to type into.
    act(() => socket.message({ type: 'state', state: 'user', controller: 'you' }));
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.getByText('example.com')).toBeInTheDocument();
  });

  it('turns the address into a box to go to a page from, only while the owner has control', async () => {
    const socket = await devBrowsing();
    act(() => socket.message({ type: 'page', url: 'https://example.com/book' }));
    // Without control it is the same read-only domain.
    expect(screen.queryByRole('textbox', { name: 'Browser address' })).toBeNull();
    expect(screen.getByText('example.com').parentElement).toHaveAttribute('title', 'https://example.com/book');
    // Asking for control is not having it.
    fireEvent.click(screen.getByRole('button', { name: 'Take over' }));
    expect(screen.queryByRole('textbox', { name: 'Browser address' })).toBeNull();
    act(() => socket.message({ type: 'state', state: 'user', controller: 'you' }));
    const address = screen.getByRole('textbox', { name: 'Browser address' });
    expect(address).toBeEnabled();
    expect(address).toHaveValue('https://example.com/book');
    fireEvent.change(address, { target: { value: 'https://example.org/menu' } });
    fireEvent.submit(address.closest('form')!);
    expect(socket.sent.at(-1)).toEqual({ type: 'navigate', action: 'goto', url: 'https://example.org/menu' });
    // Control gone, the domain is back.
    act(() => socket.message({ type: 'state', state: 'agent', controller: null }));
    expect(screen.queryByRole('textbox', { name: 'Browser address' })).toBeNull();
    expect(screen.getByText('example.com')).toBeInTheDocument();
  });

  it('adds Full view to the row of page controls, before Take over', async () => {
    const socket = await devBrowsing();
    act(() => socket.message({ type: 'page', url: 'https://example.com/book' }));
    const fullView = screen.getByRole('button', { name: 'Browser full view' });
    expect(fullView).toHaveAttribute('aria-pressed', 'false');
    expect(fullView).toHaveAttribute('data-variant', 'quiet');
    expect(row()).toContainElement(fullView);
    // The same browser otherwise: the same controls, and no status text of its own.
    expect(controls()).toEqual(['Back', 'Forward', 'Reload page', 'Open in new tab', 'Browser full view', 'Take over']);
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('adds Browser AI tasks under the page, for the browser shown', async () => {
    await devBrowsing();
    const tasks = screen.getByRole('region', { name: 'Browser AI tasks' });
    // Right under the browser's own box.
    expect(tasks.previousElementSibling).toContainElement(screen.getByRole('group', { name: 'Live browser page' }));
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/browser/tasks/discovery?workflow_id=wf&browser_node_id=browser-1'), expect.anything());
  });
});
