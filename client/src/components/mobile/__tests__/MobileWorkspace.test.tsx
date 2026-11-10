import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createRef } from 'react';
import type { SurfaceControl } from '../../workspace/surface';
import MobileWorkspace from '../MobileWorkspace';
import { mobilePoint, mobileRequest } from '../api';

const { connectVideo, listeners, addEventListener, sendRequest } = vi.hoisted(() => {
  const listeners = new Map<string, Set<(data: unknown) => void>>();
  return {
    connectVideo: vi.fn((_options: { onError: (message: string) => void }) => () => {}), listeners, sendRequest: vi.fn(),
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
/** The JSON bodies the view POSTed to an endpoint. */
const posted = (endpoint: string) => fetchMock.mock.calls
  .filter(([url, options]) => url.endsWith(endpoint) && options.method === 'POST')
  .map(([, options]) => JSON.parse(options.body));
/** A neutral button: quiet with a border. Colour marks a state or a role. */
const expectNeutral = (button: HTMLElement) => {
  expect(button).toHaveAttribute('data-variant', 'quiet');
  expect(button).toHaveClass('border-border-default', 'text-fg-default');
};
// What `dev` adds, by the names the tests find it by.
const devButtons = ['Phone full view', 'Run task', 'Stop phone', 'Refresh mobile status'];
const devSections = ['Android activity', 'Phone AI task', 'Phone controls', 'Completed mobile setup'];
/** A running phone whose setup finished and whose AI is at work: each of Dev mode's extras has something to show. */
const phoneAtWork = () => {
  const now = Date.now() / 1000;
  return { ...snapshot, running: true, setup: 'ready', setup_progress: { message: 'Phone is ready', started_at: now - 120, finished_at: now - 30, events: [] },
    active: { run_id: 'run', phase: 'Using phone', current_goal: 'Open Settings', steps: 2, max_steps: 40 } };
};

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

  it('shows the phone, its buttons, Take over and Help & diagnostics, and none of Dev mode’s extras', async () => {
    snapshot = phoneAtWork();
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    const bar = await screen.findByRole('toolbar', { name: 'Phone buttons' });
    expect(screen.getByLabelText('Phone screen, view only')).toBeInTheDocument();
    expect(within(bar).getByRole('button', { name: 'Take over' })).toBeInTheDocument();
    expect(await screen.findByText('Help & diagnostics')).toBeVisible();
    expect(screen.getByText('WHPX is installed and usable.')).toBeInTheDocument();
    // No toolbar above the phone says what it is doing.
    expect(screen.queryAllByRole('status')).toHaveLength(0);
    // Dev mode's extras stay out, so there is no Stop phone here.
    for (const name of devButtons) expect(screen.queryByRole('button', { name, hidden: true })).toBeNull();
    for (const name of devSections) expect(screen.queryByRole('group', { name, hidden: true })).toBeNull();
    expect(screen.queryByText(/Engine step/)).toBeNull();
    expect(screen.queryByText('Open Settings')).toBeNull();
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
    await userEvent.click(await screen.findByRole('button', { name: 'Take over' }));
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

  it('takes the phone with its own Take over and gives it back with Hand back, where no host runs Take over', async () => {
    snapshot.running = true;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/takeover')) {
        snapshot = { ...snapshot, controller: 'viewer:server-hash', control_state: 'human', epoch: 7 };
        return json({ owner: 'viewer:server-hash', epoch: 7 });
      }
      if (url.endsWith('/release')) {
        snapshot = { ...snapshot, controller: null, control_state: 'idle' };
        return json({ success: true });
      }
      return json(url.endsWith('/doctor') ? ready : snapshot);
    });
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    const bar = await screen.findByRole('toolbar', { name: 'Phone buttons' });
    // A neutral pill at the end of the phone's buttons, as tall as they are.
    const takeOver = within(bar).getByRole('button', { name: 'Take over' });
    expect(bar.lastElementChild).toContainElement(takeOver);
    expect(bar.lastElementChild).toHaveClass('*:h-7.5');
    expectNeutral(takeOver);
    expect(takeOver).toHaveClass('rounded-pill');
    expect(screen.queryByRole('button', { name: 'Hand back' })).toBeNull();

    await userEvent.click(takeOver);
    const handBack = await within(bar).findByRole('button', { name: 'Hand back' });
    expect(bar.lastElementChild).toContainElement(handBack);
    expectNeutral(handBack);
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
    // Take over asked for the phone's lease, and the phone is the owner's to use.
    expect(posted('/takeover')).toEqual([{ viewer_id: expect.any(String) }]);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Home' })).toBeEnabled());
    expect(screen.getByLabelText('Phone screen: tap or drag to interact')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Text to type on phone' })).toBeInTheDocument();
    // Hand back is the one way to give the phone back.
    for (const name of ['Use phone', 'Let AI continue', 'Finish using phone']) expect(screen.queryByRole('button', { name, hidden: true })).toBeNull();

    await userEvent.click(handBack);
    await within(bar).findByRole('button', { name: 'Take over' });
    // Hand back gave up that lease and let a task waiting for the owner go on.
    expect(posted('/release')).toEqual([{ viewer_id: posted('/takeover')[0].viewer_id, epoch: 7, resume: true }]);
    expect(posted('/resume')).toEqual([]);
    expect(screen.queryByRole('button', { name: 'Hand back' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Home' })).toBeDisabled();
    expect(screen.queryByRole('textbox', { name: 'Text to type on phone' })).toBeNull();
  });

  it('puts a host’s Take over at the end of the phone’s buttons, as tall as they are, in place of its own', async () => {
    snapshot.running = true;
    const hostTakeOver = vi.fn();
    render(<MobileWorkspace workflowId="flow" nodes={nodes} action={<button type="button" onClick={hostTakeOver}>Take over</button>} />);
    const bar = await screen.findByRole('toolbar', { name: 'Phone buttons' });
    const takeOver = within(bar).getByRole('button', { name: 'Take over' });
    expect(bar.lastElementChild).toContainElement(takeOver);
    expect(bar.lastElementChild).toHaveClass('*:h-7.5');
    // The one Take over is the host's: the phone asks for no lease itself.
    expect(screen.getAllByRole('button', { name: 'Take over' })).toHaveLength(1);
    await userEvent.click(takeOver);
    expect(hostTakeOver).toHaveBeenCalledOnce();
    expect(posted('/takeover')).toEqual([]);
  });

  it('shows no Take over where the host passes none', async () => {
    snapshot.running = true;
    render(<MobileWorkspace workflowId="flow" nodes={nodes} action={null} />);
    const bar = await screen.findByRole('toolbar', { name: 'Phone buttons' });
    // The phone's buttons end with Rotate: neither a host's Take over nor the phone's own.
    expect(bar.lastElementChild).toBe(screen.getByRole('button', { name: 'Rotate' }));
    for (const name of ['Take over', 'Hand back']) expect(screen.queryByRole('button', { name })).toBeNull();
  });

  it('types on the phone once the Workspace’s Take over has it, and drops the host’s action meanwhile', async () => {
    snapshot.running = true;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/takeover')) {
        snapshot = { ...snapshot, controller: 'viewer:server-hash', control_state: 'human', epoch: 7 };
        return json({ owner: 'viewer:server-hash', epoch: 7 });
      }
      if (url.endsWith('/input')) return json({ success: true, result: true });
      return json(url.endsWith('/doctor') ? ready : snapshot);
    });
    const control = createRef<SurfaceControl>();
    render(<MobileWorkspace workflowId="flow" nodes={nodes} ref={control} action={<button type="button">Take over</button>} />);
    const bar = await screen.findByRole('toolbar', { name: 'Phone buttons' });
    expect(within(bar).getByRole('button', { name: 'Take over' })).toBeInTheDocument();
    await act(async () => { await control.current!.claim(); });
    // The next status read shows this view holding the phone.
    const field = await screen.findByRole('textbox', { name: 'Text to type on phone' }, { timeout: 3000 });
    expect(screen.getByRole('button', { name: 'Home' })).toBeEnabled();
    // Hand back is the host's, so the phone's buttons end without one; Dev mode's Install APK stays out.
    for (const name of ['Take over', 'Hand back']) expect(screen.queryByRole('button', { name })).toBeNull();
    expect(bar.lastElementChild).toBe(screen.getByRole('button', { name: 'Rotate' }));
    expect(screen.queryByLabelText('Install APK')).toBeNull();
    await userEvent.type(field, 'hello');
    const type = screen.getByRole('button', { name: 'Type' });
    expectNeutral(type);
    await userEvent.click(type);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/input'))).toBe(true));
    const request = fetchMock.mock.calls.find(([url]) => url.endsWith('/input'))!;
    expect(JSON.parse(request[1].body)).toMatchObject({ epoch: 7, operation: 'text', parameters: { text: 'hello' } });
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
    await userEvent.click(screen.getByRole('button', { name: 'Take over' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Home' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: 'Home' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/input'))).toBe(true));
    const request = fetchMock.mock.calls.find(([url]) => url.endsWith('/input'))!;
    expect(JSON.parse(request[1].body)).toMatchObject({ epoch: 7, operation: 'key', parameters: { key: 'home' } });
    // Hiding the view gives up that grant, and resumes no task as Hand back would.
    view.rerender(<MobileWorkspace workflowId="flow" nodes={nodes} visible={false} />);
    await waitFor(() => expect(posted('/release')).toHaveLength(1));
    expect(posted('/release')[0]).toMatchObject({ epoch: 7, resume: false });
  });

  it('gives the phone back from the Workspace’s Take over after its view went away', async () => {
    snapshot.running = true;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/takeover')) {
        snapshot = { ...snapshot, controller: 'viewer:server-hash', control_state: 'human', epoch: 7 };
        return json({ owner: 'viewer:server-hash', epoch: 7 });
      }
      return json(url.endsWith('/doctor') ? ready : snapshot);
    });
    const control = createRef<SurfaceControl>();
    const view = render(<MobileWorkspace workflowId="flow" nodes={nodes} ref={control} />);
    await screen.findByRole('toolbar', { name: 'Phone buttons' });
    let giveBack: (() => void) | null = null;
    await act(async () => { giveBack = await control.current!.claim(); });
    expect(giveBack).toBeTypeOf('function');
    // The dock moved to another tab: the view lets the phone go, and the task it took it from still waits.
    view.unmount();
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, options]) => url.endsWith('/release') && JSON.parse(options.body).resume === false)).toBe(true));
    giveBack!();
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/resume'))).toBe(true));
  });

  it('offers Start phone on the screen where the phone is ready to turn on, and starts it', async () => {
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    const start = await screen.findByRole('button', { name: 'Start phone' });
    expect(screen.getByText(/Your phone is ready to turn on/).parentElement).toContainElement(start);
    expectNeutral(start);
    // The status beside it is silent until the phone starts.
    expect(within(start.parentElement!).getByRole('status')).toBeEmptyDOMElement();
    await waitFor(() => expect(start).toBeEnabled());
    await userEvent.click(start);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, options]) => url.endsWith('/start') && options.method === 'POST')).toBe(true));
  });

  it('says the phone is starting while it boots', async () => {
    snapshot.starting = true;
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    const starting = await screen.findByText('Starting phone…');
    expect(starting).toHaveAttribute('role', 'status');
    const start = screen.getByRole('button', { name: 'Start phone' });
    expect(start).toBeDisabled();
    expect(start.parentElement).toContainElement(starting);
    expect(screen.queryByText(/ready to turn on/)).toBeNull();
  });

  it('retains a startup failure across status refreshes without claiming the phone is ready', async () => {
    snapshot.start_error = 'This phone is already open, but could not be safely reconnected.';
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('already open');
    expect(screen.queryByText(/Your phone is ready to turn on/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Start phone' })).toBeEnabled();
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
    expect(screen.getByRole('toolbar', { name: 'Phone buttons' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('offers a neutral Reconnect when the live view fails, and connects again', async () => {
    snapshot.running = true;
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await waitFor(() => expect(connectVideo).toHaveBeenCalledOnce());
    act(() => connectVideo.mock.calls[0][0].onError('Live video stopped.'));
    expect(screen.getByRole('alert')).toHaveTextContent('Live video stopped.');
    const reconnect = screen.getByRole('button', { name: 'Reconnect' });
    expectNeutral(reconnect);
    await userEvent.click(reconnect);
    await waitFor(() => expect(connectVideo).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reconnect' })).toBeNull();
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
    expect(fetchMock.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true);
    expect(screen.getByText('Help & diagnostics')).toBeInTheDocument();
    expect(screen.getByText('WHPX is installed and usable.')).toBeInTheDocument();
  });

  it('offers setup, with Set up phone as its card’s one primary action', async () => {
    fetchMock.mockImplementation(async (url: string) => json(url.endsWith('/doctor') ? { supported: true } : snapshot));
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    expect(await screen.findByRole('region', { name: 'Mobile setup' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Set up phone' })).toHaveAttribute('data-variant', 'invert');
    expect(screen.queryByRole('button', { name: 'Start phone' })).toBeNull();
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
    const setup = await screen.findByRole('region', { name: 'Mobile setup' });
    expect(within(setup).getByRole('status')).toHaveTextContent('Preparing Android phone');
    expect(screen.getByText('Downloading Android image')).toBeInTheDocument();
    expect(screen.getByText(/Elapsed: 1m/)).toBeInTheDocument();
    expect(screen.getByText(/Last update: \d+s/)).toBeInTheDocument();
    expect(screen.queryByText(/— Step 0$/)).toBeNull();
    expect(screen.getByText(/Step 24$/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Set up phone' })).toBeDisabled();
  });

  it('keeps interrupted setup visible after reopening and offers an explicit retry', async () => {
    snapshot = { ...snapshot, setup: 'interrupted', setup_error: 'Setup stopped when the host restarted.' };
    render(<MobileWorkspace workflowId="flow" nodes={nodes} />);
    await screen.findByText('Setup stopped when the host restarted.');
    const retry = screen.getByRole('button', { name: 'Retry setup' });
    expect(retry).toHaveAttribute('data-variant', 'invert');
    expect(retry).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox', { name: 'Accept Android SDK license terms' }));
    expect(retry).toBeEnabled();
  });
});

// Dev mode adds Full view, the AI's activity, Ask AI to use the phone and Phone controls to the same view.
describe('Mobile Workspace: Dev mode’s extras', () => {
  it('adds Full view, the AI’s activity, Ask AI to use the phone and Phone controls', async () => {
    sessionStorage.setItem('mobile-task:flow:flow:mobile_agent:1', 'submission');
    snapshot = phoneAtWork();
    fetchMock.mockImplementation(async (url: string) => {
      if (url.includes('/tasks/')) return json({ status: 'running' });
      return json(url.endsWith('/doctor') ? ready : snapshot);
    });
    render(<MobileWorkspace workflowId="flow" nodes={nodes} dev />);
    const bar = await screen.findByRole('toolbar', { name: 'Phone buttons' });
    // The view itself stays: the phone, its buttons ending in Take over, and Help & diagnostics.
    expect(bar.lastElementChild).toContainElement(within(bar).getByRole('button', { name: 'Take over' }));
    expect(await screen.findByText('Help & diagnostics')).toBeVisible();
    for (const name of devButtons) expect(screen.getByRole('button', { name })).toBeInTheDocument();
    for (const name of devSections) expect(screen.getByRole('group', { name })).toBeInTheDocument();
    // Full view sits among the phone's buttons.
    expect(within(bar).getByRole('button', { name: 'Phone full view' })).toBeInTheDocument();
    // The AI's activity is open.
    const activity = screen.getByRole('group', { name: 'Android activity' });
    expect(within(activity).getByText('Open Settings')).toBeVisible();
    expect(within(activity).getByText(/Engine step 2 \/ 40/)).toBeVisible();
    // Ask AI to use the phone: Run task is its one primary action, Cancel task a neutral one.
    const ask = screen.getByRole('group', { name: 'Phone AI task' });
    expect(within(ask).getByRole('textbox', { name: 'Ask AI to use the phone' })).toBeInTheDocument();
    expect(within(ask).getByRole('button', { name: 'Run task' })).toHaveAttribute('data-variant', 'invert');
    expectNeutral(within(ask).getByRole('button', { name: 'Cancel task' }));
    // Phone controls: Stop phone while it runs, refresh, and how setup went.
    const controls = screen.getByRole('group', { name: 'Phone controls' });
    expectNeutral(within(controls).getByRole('button', { name: 'Stop phone' }));
    expect(within(controls).getByRole('button', { name: 'Refresh mobile status' })).toBeInTheDocument();
    expect(within(controls).getByRole('group', { name: 'Completed mobile setup' })).toBeInTheDocument();
  });

  it('shows internal phone progress when invoked by an AI agent tool call', async () => {
    snapshot.running = true;
    snapshot.active = { run_id: 'tool-run', phase: 'Waiting for model', steps: 7, max_steps: 40 };
    render(<MobileWorkspace workflowId="flow" nodes={nodes} dev />);
    const activity = await screen.findByRole('group', { name: 'Android activity' });
    expect(within(activity).getByText(/AI activity · Waiting for model/)).toBeVisible();
    expect(within(activity).getByText(/Engine step 7 \/ 40/)).toBeVisible();
  });

  it('explains how to start and disables AI tasks while the phone is off', async () => {
    render(<MobileWorkspace workflowId="flow" nodes={nodes} dev />);
    await screen.findByRole('button', { name: 'Start phone' });
    expect(screen.getByText('Your phone is ready to turn on. Click Start phone to use it.')).toBeInTheDocument();
    await userEvent.click(screen.getByText('Ask AI to use the phone', { selector: 'summary' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Ask AI to use the phone' }), 'Open Settings');
    expect(screen.getByRole('button', { name: 'Run task' })).toBeDisabled();
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
    render(<MobileWorkspace workflowId="home-flow" nodes={nodes} dev />);
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

  it('keeps the phone connected and preserves a task draft when secondary panels collapse', async () => {
    snapshot.running = true;
    render(<MobileWorkspace workflowId="flow" nodes={nodes} dev />);
    await screen.findByRole('toolbar', { name: 'Phone buttons' });
    await waitFor(() => expect(connectVideo).toHaveBeenCalledOnce());
    const canvas = screen.getByLabelText('Phone screen, view only').querySelector('canvas');
    const ask = screen.getByText('Ask AI to use the phone', { selector: 'summary' });
    const draft = () => screen.getByRole('textbox', { name: 'Ask AI to use the phone' });
    expect(draft()).not.toBeVisible();
    await userEvent.click(ask);
    await userEvent.type(draft(), 'Open Settings');
    await userEvent.click(ask);
    expect(draft()).not.toBeVisible();
    const controls = screen.getByText('Phone controls', { selector: 'summary' });
    await userEvent.click(controls);
    expect(screen.getByRole('button', { name: 'Stop phone' })).toBeVisible();
    await userEvent.click(controls);
    expect(screen.getByRole('button', { name: 'Stop phone' })).not.toBeVisible();
    await userEvent.click(ask);
    expect(draft()).toBeVisible();
    expect(draft()).toHaveValue('Open Settings');
    expect(screen.getByLabelText('Phone screen, view only').querySelector('canvas')).toBe(canvas);
    expect(connectVideo).toHaveBeenCalledOnce();
  });

  it('stops the phone from Phone controls, which offers Stop phone only while it runs', async () => {
    snapshot.running = true;
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/stop')) {
        snapshot = { ...snapshot, running: false };
        return json({ success: true });
      }
      return json(url.endsWith('/doctor') ? ready : snapshot);
    });
    render(<MobileWorkspace workflowId="flow" nodes={nodes} dev />);
    await screen.findByRole('toolbar', { name: 'Phone buttons' });
    const controls = screen.getByRole('group', { name: 'Phone controls' });
    await userEvent.click(within(controls).getByText('Phone controls'));
    await userEvent.click(within(controls).getByRole('button', { name: 'Stop phone' }));
    await waitFor(() => expect(posted('/stop')).toEqual([{}]));
    // The phone is off: Start phone is on the screen, and Phone controls offers no Stop phone.
    expect(await screen.findByRole('button', { name: 'Start phone' })).toBeInTheDocument();
    expect(screen.queryByRole('toolbar', { name: 'Phone buttons' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Stop phone' })).toBeNull();
    expect(within(controls).getByRole('button', { name: 'Refresh mobile status' })).toBeVisible();
  });

  it('installs an app file from Phone controls while the owner holds the phone, and says so meanwhile', async () => {
    snapshot.running = true;
    let finishInstall!: (response: Response) => void;
    fetchMock.mockImplementation((url: string) => {
      if (url.endsWith('/takeover')) {
        snapshot = { ...snapshot, controller: 'viewer:server-hash', control_state: 'human', epoch: 7 };
        return Promise.resolve(json({ owner: 'viewer:server-hash', epoch: 7 }));
      }
      if (url.includes('/apk?')) return new Promise<Response>((resolve) => { finishInstall = resolve; });
      return Promise.resolve(json(url.endsWith('/doctor') ? ready : snapshot));
    });
    render(<MobileWorkspace workflowId="flow" nodes={nodes} dev />);
    await screen.findByRole('toolbar', { name: 'Phone buttons' });
    const controls = screen.getByRole('group', { name: 'Phone controls' });
    await userEvent.click(within(controls).getByText('Phone controls'));
    expect(within(controls).queryByLabelText('Install APK')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Take over' }));
    const file = await within(controls).findByLabelText('Install APK');
    expect(file).toBeVisible();
    await waitFor(() => expect(file).toBeEnabled());
    // Anything but an app file is refused before it is sent.
    fireEvent.change(file, { target: { files: [new File(['bad'], 'not-an-app.txt')] } });
    expect(screen.getByText('Choose an APK file no larger than 256 MB.')).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => url.includes('/apk?'))).toBe(false);
    const app = new File(['app'], 'notes.apk');
    fireEvent.change(file, { target: { files: [app] } });
    // Phone controls says what is in flight.
    expect(await within(controls).findByRole('status')).toHaveTextContent('installing APK…');
    const [url, options] = fetchMock.mock.calls.find(([url]) => url.includes('/apk?'))!;
    expect(url.split('?')[0]).toBe('/api/mobile/flow/flow%3Amobile_agent%3A1/apk');
    expect(Object.fromEntries(new URLSearchParams(url.split('?')[1]))).toMatchObject({ viewer_id: posted('/takeover')[0].viewer_id, epoch: '7', filename: 'notes.apk' });
    expect(options).toMatchObject({ method: 'POST', headers: { 'Content-Type': 'application/vnd.android.package-archive' } });
    expect(options.body).toBe(app);
    await act(async () => { finishInstall(json({ success: true })); });
    await waitFor(() => expect(within(controls).queryByRole('status')).toBeNull());
  });

  it('retains completed setup activity without install controls', async () => {
    const now = Date.now() / 1000;
    snapshot = { ...snapshot, setup: 'ready', setup_progress: { message: 'Phone is ready', started_at: now - 120, finished_at: now - 30, updated_at: now - 30, events: [{ at: now - 30, message: 'Installation finished' }] } };
    render(<MobileWorkspace workflowId="flow" nodes={nodes} dev />);
    // Phone controls keeps how setup went, folded away.
    const finished = await screen.findByRole('group', { name: 'Completed mobile setup' });
    expect(screen.getByRole('group', { name: 'Phone controls' })).toContainElement(finished);
    await userEvent.click(screen.getByText('Phone controls', { selector: 'summary' }));
    await userEvent.click(within(finished).getByText('Setup finished · View details'));
    expect(within(finished).getByText('Setup complete')).toBeVisible();
    expect(within(finished).getByText('Elapsed: 1m 30s')).toBeVisible();
    expect(within(finished).getByText(/Installation finished/)).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Mobile setup' })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: 'Accept Android SDK license terms' })).toBeNull();
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
    render(<MobileWorkspace workflowId="flow" nodes={[...nodes, { node_id: 'flow:android:2', label: 'Second phone' }]} dev />);
    await screen.findByText('Old phone task');
    await screen.findByText('Working on your request');
    await userEvent.click(screen.getByRole('button', { name: 'Take over' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Home' })).toBeEnabled());
    snapshot = { ...snapshot, active: null, last_task: null };
    reset('flow');
    // The phone shows again, in a new session.
    await screen.findByRole('toolbar', { name: 'Phone buttons' });
    expect(screen.queryByText('Old phone task')).toBeNull();
    expect(screen.queryByText('Working on your request')).toBeNull();
    // The view no longer holds the phone and offers Take over again; the server's lease was not given away.
    expect(screen.getByRole('button', { name: 'Home' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Take over' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Hand back' })).toBeNull();
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
    render(<MobileWorkspace workflowId="flow" nodes={nodes} dev />);
    await waitFor(() => expect(oldTaskSignal).toBeDefined());
    reset('flow');
    await screen.findByRole('toolbar', { name: 'Phone buttons' });
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
    render(<MobileWorkspace workflowId="flow" nodes={nodes} dev />);
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
    render(<MobileWorkspace workflowId="flow" nodes={nodes} dev />);
    await screen.findByRole('toolbar', { name: 'Phone buttons' });
    await userEvent.click(screen.getByText('Ask AI to use the phone', { selector: 'summary' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Ask AI to use the phone' }), 'Old request');
    await userEvent.click(screen.getByRole('button', { name: 'Run task' }));
    await waitFor(() => expect(resolveSubmission).toBeDefined());
    reset('flow');
    await screen.findByRole('toolbar', { name: 'Phone buttons' });
    await act(async () => { resolveSubmission(json({ status: 'running', invocation_id: 'old-run' })); });
    expect(sessionStorage.getItem('mobile-task:flow:flow:mobile_agent:1')).toBeNull();
    // No task shows, whatever its state.
    expect(within(screen.getByRole('group', { name: 'Phone AI task' })).queryByRole('status')).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => url.includes('/tasks/'))).toBe(false);
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
