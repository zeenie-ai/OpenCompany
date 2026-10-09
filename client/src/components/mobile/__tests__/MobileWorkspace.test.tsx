import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import MobileWorkspace from '../MobileWorkspace';
import { mobilePoint, mobileRequest } from '../api';

const { connectVideo, listeners, addEventListener, sendRequest } = vi.hoisted(() => {
  const listeners = new Map<string, Set<(data: unknown) => void>>();
  return {
    connectVideo: vi.fn(() => () => {}), listeners, sendRequest: vi.fn(),
    addEventListener: (type: string, handler: (data: unknown) => void) => {
      const handlers = listeners.get(type) ?? new Set();
      handlers.add(handler); listeners.set(type, handlers);
      return () => { handlers.delete(handler); };
    },
  };
});
vi.mock('../video', () => ({ connectMobileVideo: connectVideo }));
vi.mock('@/contexts/WebSocketContext', () => ({ useWebSocketActions: () => ({ addEventListener, sendRequest }) }));
const fetchMock = vi.fn();
const nodes = [{ node_id: 'flow:mobile_agent:1', label: 'Phone assistant' }];
const ready = { supported: true, adb: true, emulator: true, image: true, engine: true, video: true, acceleration: 'WHPX is installed and usable.' };
let snapshot: Record<string, unknown>;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const reset = (workflowId: string) => act(() => {
  for (const handler of listeners.get('workflow_runtime_reset') ?? []) handler({ workflow_id: workflowId });
});

beforeEach(() => {
  sessionStorage.clear();
  connectVideo.mockClear();
  listeners.clear();
  snapshot = { running: false, control_state: 'idle', controller: null, epoch: 0, geometry: { width: 1080, height: 1920, rotation: 0 } };
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset().mockImplementation(async (url: string) => json(url.endsWith('/doctor') ? ready : snapshot));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('Mobile Workspace', () => {
  it('frames the phone by its screen and names it', async () => {
    snapshot = { ...snapshot, running: true, device: 'Pixel 7 · local emulator · up to 30 fps' };
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    expect(await screen.findByText('Pixel 7 · local emulator · up to 30 fps')).toBeInTheDocument();
    const screenArea = screen.getByLabelText('Phone screen, view only');
    expect(screenArea.parentElement?.style.aspectRatio).toBe(String(1080 / 1920));
    // The buttons wait until the owner uses the phone; without a Canvas there is no screenshot.
    for (const name of ['Back', 'Home', 'Recent apps', 'Rotate']) expect(screen.getByRole('button', { name })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Screenshot to Canvas' })).toBeNull();
  });

  it('presses Recent apps and rotates the phone on its side and back', async () => {
    snapshot.running = true;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/takeover')) {
        snapshot = { ...snapshot, controller: 'viewer:server-hash', control_state: 'human', epoch: 3 };
        return json({ owner: 'viewer:server-hash', epoch: 3 });
      }
      if (url.endsWith('/input')) return json({ success: true, result: true });
      return json(url.endsWith('/doctor') ? ready : snapshot);
    });
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Use phone' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Recent apps' })).toBeEnabled());
    const inputs = () => fetchMock.mock.calls.filter(([url]) => url.endsWith('/input')).map(([, options]) => JSON.parse(options.body));
    await userEvent.click(screen.getByRole('button', { name: 'Recent apps' }));
    await waitFor(() => expect(inputs()).toHaveLength(1));
    expect(inputs()[0]).toMatchObject({ operation: 'key', parameters: { key: 'recent' } });
    await userEvent.click(screen.getByRole('button', { name: 'Rotate' }));
    await waitFor(() => expect(inputs()).toHaveLength(2));
    expect(inputs()[1]).toMatchObject({ operation: 'rotate', parameters: { orientation: 'left' } });
  });

  it('puts a screenshot on the Canvas', async () => {
    snapshot.running = true;
    const notify = vi.fn();
    sendRequest.mockReset().mockResolvedValue({ success: true });
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/screenshot')) return json({ success: true, ref: { path: 'media/phone-1.png' } });
      return json(url.endsWith('/doctor') ? ready : snapshot);
    });
    render(<MobileWorkspace workflowId="flow" nodes={nodes} canvasNodeId="flow:canvas:1" notify={notify} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Screenshot to Canvas' }));
    await waitFor(() => expect(notify).toHaveBeenCalledWith('Screenshot added to Canvas', 'success'));
    expect(sendRequest).toHaveBeenCalledWith('canvas_add', { workflow_id: 'flow', node_id: 'flow:canvas:1', path: 'media/phone-1.png' });
  });

  it('clears the old run, task recovery IDs and lease after a matching workflow reset', async () => {
    sessionStorage.setItem('mobile-task:flow:flow:mobile_agent:1', 'old-submission');
    sessionStorage.setItem('mobile-task:flow:flow:android:2', 'other-phone-submission');
    sessionStorage.setItem('mobile-task:other:other:mobile_agent:1', 'unrelated-submission');
    snapshot = { ...snapshot, running: true, active: { run_id: 'old-run', phase: 'Using phone', current_goal: 'Old phone task' } };
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/takeover')) {
        snapshot = { ...snapshot, controller: 'viewer:server-hash', control_state: 'human', epoch: 7 };
        return json({ owner: 'viewer:server-hash', epoch: 7 });
      }
      if (url.includes('/tasks/')) return json({ status: 'running' });
      return json(url.endsWith('/doctor') ? ready : snapshot);
    });
    render(<MobileWorkspace workflowId="flow" nodes={[...nodes, { node_id: 'flow:android:2', label: 'Second phone' }]} />);
    await screen.findByText('Old phone task');
    await screen.findByText('Working on your request');
    await userEvent.click(screen.getByRole('button', { name: 'Use phone' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Home' })).toBeEnabled());
    snapshot = { ...snapshot, active: null, last_task: null };
    reset('flow');
    await screen.findByText('Ready');
    expect(screen.queryByText('Old phone task')).toBeNull();
    expect(screen.queryByText('Working on your request')).toBeNull();
    expect(screen.getByRole('button', { name: 'Home' })).toBeDisabled();
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/release'))).toBe(false);
    expect(sessionStorage.getItem('mobile-task:flow:flow:mobile_agent:1')).toBeNull();
    expect(sessionStorage.getItem('mobile-task:flow:flow:android:2')).toBeNull();
    expect(sessionStorage.getItem('mobile-task:other:other:mobile_agent:1')).toBe('unrelated-submission');
    await userEvent.click(screen.getByText('Ask AI to use the phone', { selector: 'summary' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Ask AI to use the phone' }), 'New task');
    expect(screen.getByRole('button', { name: 'Run task' })).toBeEnabled();
  });

  it('aborts old status and task polls and ignores their delayed responses after reset', async () => {
    sessionStorage.setItem('mobile-task:flow:flow:mobile_agent:1', 'old-submission');
    let resolveStatus!: (response: Response) => void;
    let resolveTask!: (response: Response) => void;
    let oldStatusSignal: AbortSignal | undefined;
    let oldTaskSignal: AbortSignal | undefined;
    let statusCalls = 0;
    snapshot.running = true;
    fetchMock.mockImplementation((url: string, options: RequestInit) => {
      if (url.endsWith('/status') && statusCalls++ === 0) {
        oldStatusSignal = options.signal as AbortSignal;
        return new Promise<Response>((resolve) => { resolveStatus = resolve; });
      }
      if (url.includes('/tasks/old-submission')) {
        oldTaskSignal = options.signal as AbortSignal;
        return new Promise<Response>((resolve) => { resolveTask = resolve; });
      }
      return Promise.resolve(json(url.endsWith('/doctor') ? ready : snapshot));
    });
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await waitFor(() => expect(oldTaskSignal).toBeDefined());
    reset('flow');
    await screen.findByText('Ready');
    expect(oldStatusSignal?.aborted).toBe(true);
    expect(oldTaskSignal?.aborted).toBe(true);
    await act(async () => {
      resolveStatus(json({ ...snapshot, active: { run_id: 'stale-run', phase: 'Using phone', current_goal: 'Stale phone activity' } }));
      resolveTask(json({ status: 'completed', result: 'Stale task result' }));
    });
    expect(screen.queryByText('Stale phone activity')).toBeNull();
    expect(screen.queryByText('Stale task result')).toBeNull();
    expect(sessionStorage.getItem('mobile-task:flow:flow:mobile_agent:1')).toBeNull();
  });

  it('ignores resets for another workflow without replacing the phone session', async () => {
    sessionStorage.setItem('mobile-task:flow:flow:mobile_agent:1', 'existing-submission');
    snapshot = { ...snapshot, running: true, active: { run_id: 'run', phase: 'Using phone', current_goal: 'Keep this task' } };
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText('Keep this task');
    await waitFor(() => expect(connectVideo).toHaveBeenCalledOnce());
    const canvas = screen.getByLabelText('Phone screen, view only').querySelector('canvas');
    reset('other');
    expect(screen.getByText('Keep this task')).toBeInTheDocument();
    expect(screen.getByLabelText('Phone screen, view only').querySelector('canvas')).toBe(canvas);
    expect(sessionStorage.getItem('mobile-task:flow:flow:mobile_agent:1')).toBe('existing-submission');
    expect(connectVideo).toHaveBeenCalledOnce();
  });

  it('does not restore a pre-reset submission when its HTTP response arrives late', async () => {
    snapshot.running = true;
    let resolveSubmission!: (response: Response) => void;
    fetchMock.mockImplementation((url: string, options: RequestInit) => {
      if (url.endsWith('/tasks') && options.method === 'POST') {
        return new Promise<Response>((resolve) => { resolveSubmission = resolve; });
      }
      return Promise.resolve(json(url.endsWith('/doctor') ? ready : snapshot));
    });
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText('Ready');
    await userEvent.click(screen.getByText('Ask AI to use the phone', { selector: 'summary' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Ask AI to use the phone' }), 'Old request');
    await userEvent.click(screen.getByRole('button', { name: 'Run task' }));
    await waitFor(() => expect(resolveSubmission).toBeDefined());
    reset('flow');
    await screen.findByText('Ready');
    await act(async () => { resolveSubmission(json({ status: 'running', invocation_id: 'old-run' })); });
    expect(sessionStorage.getItem('mobile-task:flow:flow:mobile_agent:1')).toBeNull();
    expect(screen.queryByText('Working on your request')).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => url.includes('/tasks/'))).toBe(false);
  });

  it('shows internal phone progress when invoked by an AI agent tool call', async () => {
    snapshot.running = true;
    snapshot.active = { run_id: 'tool-run', phase: 'Waiting for model', steps: 7, max_steps: 40 };
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    expect(await screen.findByText(/Waiting for model.*Engine step 7 \/ 40/)).toBeVisible();
  });
  it('retains a startup failure across status refreshes without claiming the phone is ready', async () => {
    snapshot.start_error = 'This phone is already open, but could not be safely reconnected.';
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('already open');
    expect(screen.queryByText(/Your phone is ready to turn on/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Start phone' })).toBeEnabled();
  });
  it('keeps the phone connected and preserves a task draft when secondary panels collapse', async () => {
    snapshot.running = true;
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText('Ready');
    await waitFor(() => expect(connectVideo).toHaveBeenCalledOnce());
    const canvas = screen.getByLabelText('Phone screen, view only').querySelector('canvas');
    const summary = screen.getByText('Ask AI to use the phone', { selector: 'summary' });
    expect(screen.getByRole('textbox', { name: 'Ask AI to use the phone' })).not.toBeVisible();
    await userEvent.click(summary);
    await userEvent.type(screen.getByRole('textbox', { name: 'Ask AI to use the phone' }), 'Open Settings');
    await userEvent.click(summary);
    await userEvent.click(screen.getByRole('button', { name: 'Phone controls' }));
    expect(screen.getByRole('button', { name: 'Stop phone' })).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Phone controls' }));
    await userEvent.click(summary);
    expect(screen.getByRole('textbox', { name: 'Ask AI to use the phone' })).toHaveValue('Open Settings');
    expect(screen.getByLabelText('Phone screen, view only').querySelector('canvas')).toBe(canvas);
    expect(connectVideo).toHaveBeenCalledOnce();
  });
  it('clears a failed status check when the phone reconnects', async () => {
    let polls = 0;
    snapshot.running = true;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/status') && polls++ === 0) throw new Error('Start the device before using it');
      return json(url.endsWith('/doctor') ? ready : snapshot);
    });
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText(/Retrying automatically/);
    await waitFor(() => expect(screen.queryByText(/Retrying automatically/)).toBeNull(), { timeout: 4000 });
    expect(screen.getByText('Ready')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('explains how to start and disables AI tasks while the phone is off', async () => {
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText('Phone is off');
    await userEvent.click(screen.getByText('Ask AI to use the phone', { selector: 'summary' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Ask AI to use the phone' }), 'Open Settings');
    expect(screen.getByRole('button', { name: 'Run task' })).toBeDisabled();
  });
  it('requires a saved workflow and an explicitly discovered Mobile node', () => {
    const view = render(<MobileWorkspace nodes={nodes} />);
    expect(screen.getByText(/Save this workflow/)).toBeInTheDocument();
    view.rerender(<MobileWorkspace workflowId="flow" nodes={[]} />);
    expect(screen.getByText(/Add a Mobile Agent/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('only reads status on open; never starts or installs automatically', async () => {
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByRole('button', { name: 'Start phone' });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await userEvent.click(screen.getByRole('button', { name: 'Phone controls' }));
    expect(fetchMock.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true);
    expect(screen.getByText('Help & diagnostics')).toBeInTheDocument();
    expect(screen.getByText('WHPX is installed and usable.')).toBeInTheDocument();
  });

  it('requires explicit license acceptance before setup', async () => {
    fetchMock.mockImplementation(async (url: string) => json(url.endsWith('/doctor') ? { supported: true } : snapshot));
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    const setup = await screen.findByRole('button', { name: 'Set up phone' });
    expect(setup).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox', { name: 'Accept Android SDK license terms' }));
    await userEvent.click(setup);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, options]) => url.endsWith('/setup') && JSON.parse(options.body).licenses_accepted === true)).toBe(true));
  });

  it('shows active setup and bounded activity even when all tools are ready', async () => {
    const now = Date.now() / 1000;
    snapshot = { ...snapshot, setup: 'installing_device', setup_progress: {
      message: 'Downloading Android image', started_at: now - 90, updated_at: now - 10,
      events: Array.from({ length: 25 }, (_, index) => ({ at: now - 25 + index, message: `Step ${index}` })),
    } };
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText('Setting up');
    expect(screen.getByRole('region', { name: 'Mobile setup' })).toBeInTheDocument();
    expect(screen.getByText('Downloading Android image')).toBeInTheDocument();
    expect(screen.getByText(/Elapsed: 1m/)).toBeInTheDocument();
    expect(screen.getByText(/Last update: \d+s/)).toBeInTheDocument();
    expect(screen.queryByText(/— Step 0$/)).toBeNull();
    expect(screen.getByText(/Step 24$/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Set up phone' })).toBeDisabled();
  });

  it('retains completed setup activity without install controls', async () => {
    const now = Date.now() / 1000;
    snapshot = { ...snapshot, setup: 'ready', setup_progress: { message: 'Phone is ready', started_at: now - 120, finished_at: now - 30, updated_at: now - 30, events: [{ at: now - 30, message: 'Installation finished' }] } };
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText('Setup complete');
    expect(screen.getByText('Elapsed: 1m 30s')).toBeInTheDocument();
    expect(screen.getByText(/Installation finished/)).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'Accept Android SDK license terms' })).toBeNull();
  });

  it('keeps interrupted setup visible after reopening and offers an explicit retry', async () => {
    snapshot = { ...snapshot, setup: 'interrupted', setup_error: 'Setup stopped when the host restarted.' };
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText('Setup stopped when the host restarted.');
    const retry = screen.getByRole('button', { name: 'Retry setup' });
    expect(retry).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox', { name: 'Accept Android SDK license terms' }));
    expect(retry).toBeEnabled();
  });

  it('submits only to the displayed workflow and retries a lost response with the same id', async () => {
    snapshot.running = true;
    let attempts = 0;
    fetchMock.mockImplementation(async (url: string, options: RequestInit) => {
      if (url.endsWith('/tasks') && options.method === 'POST') {
        attempts++;
        if (attempts === 1) throw new Error('Connection dropped');
        return json({ status: 'queued', invocation_id: 'inv-1' });
      }
      if (url.includes('/tasks/')) return json({ status: 'completed', result: 'Done' });
      return json(url.endsWith('/doctor') ? ready : snapshot);
    });
    render(<MobileWorkspace workflowId="home-flow" nodes={nodes} />);
    await userEvent.click(screen.getByText('Ask AI to use the phone', { selector: 'summary' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Ask AI to use the phone' }), 'Open settings');
    await userEvent.click(screen.getByRole('button', { name: 'Run task' }));
    await screen.findByText('Connection dropped');
    await userEvent.click(screen.getByRole('button', { name: 'Run task' }));
    await screen.findByText('Done', { selector: 'pre' });
    const requests = fetchMock.mock.calls.filter(([url, options]) => url.endsWith('/tasks') && options.method === 'POST');
    expect(requests).toHaveLength(2);
    expect(requests[0][0]).toBe('/api/mobile/home-flow/flow%3Amobile_agent%3A1/tasks');
    expect(JSON.parse(requests[0][1].body)).toEqual(JSON.parse(requests[1][1].body));
    expect(JSON.parse(requests[0][1].body)).toMatchObject({ prompt: 'Open settings' });
  });

  it('unlocks input only after an opaque lease grant matches status and sends epoch', async () => {
    snapshot.running = true;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/takeover')) {
        snapshot = { ...snapshot, controller: 'viewer:server-hash', control_state: 'human', epoch: 7 };
        return json({ owner: 'viewer:server-hash', epoch: 7 });
      }
      return json(url.endsWith('/doctor') ? ready : snapshot);
    });
    const view = render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    expect(await screen.findByRole('button', { name: 'Home' })).toBeDisabled();
    await userEvent.click(await screen.findByRole('button', { name: 'Use phone' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Home' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: 'Home' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/input'))).toBe(true));
    const request = fetchMock.mock.calls.find(([url]) => url.endsWith('/input'))!;
    expect(JSON.parse(request[1].body)).toMatchObject({ epoch: 7, operation: 'key', parameters: { key: 'home' } });
    await userEvent.click(screen.getByRole('button', { name: 'Phone controls' }));
    expect(screen.getByLabelText('Install APK')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Install APK'), { target: { files: [new File(['bad'], 'not-an-app.txt')] } });
    expect(screen.getByText('Choose an APK file no larger than 256 MB.')).toBeInTheDocument();
    view.rerender(<MobileWorkspace workflowId="flow" nodes={nodes} visible={false} />);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, options]) => url.endsWith('/release') && JSON.parse(options.body).epoch === 7)).toBe(true));
  });
});

describe('mobile transport boundaries', () => {
  it('maps aspect-fit coordinates and refuses letterbox input', () => {
    const geometry = { width: 100, height: 200 };
    expect(mobilePoint(100, 100, 200, 200, geometry)).toEqual({ x: 50, y: 100 });
    expect(mobilePoint(5, 100, 200, 200, geometry)).toBeNull();
    expect(mobilePoint(0, 0, 0, 0, geometry)).toBeNull();
  });
  it('surfaces structured device errors', async () => {
    fetchMock.mockResolvedValue(json({ detail: { code: 'stale_lease', message: 'Device control changed.' } }, 409));
    await expect(mobileRequest('/api/mobile/test')).rejects.toThrow('Device control changed.');
  });
});
