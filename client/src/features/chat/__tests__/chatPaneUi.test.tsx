/**
 * An interface the employee shows in its reply, against a fake server: it
 * streams in with the run, its buttons ask (sending only what they say) or
 * go back to the employee once the answer is done, what the owner sets goes
 * to the server after a pause or before anything the owner sends, and the
 * saved reply keeps it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));
// Everything shows at once, as under reduced motion.
vi.mock('@/lib/motion', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/motion')>()),
  motionSuppressed: () => true,
}));

import { resetChatRunStore, useChatRunStore } from '@/stores/chatRunStore';
import booking from '../__fixtures__/saturday-booking.spec.json';
import { ChatPane } from '../ChatPane';
import type { ChatHost } from '../host';
import { useComposerStore } from '../state/composerStore';
// Loaded up front so the lazy chunks resolve from the module cache.
import '../genui/ChatUi';
import '../markdown/ReplyMarkdown';

type Wire = Record<string, unknown>;
let server: { messages: Wire[]; activeRuns: Wire[] };
let client: QueryClient;

function row(id: string, role: 'user' | 'assistant', text: string, patch: Wire = {}): Wire {
  return { id, role, text, message: text, timestamp: '2026-10-04T09:00:00Z', run_key: 'g1', ...patch };
}

function frame(seq: number, suffix: string, data: Wire = {}, runId = 'r1') {
  return {
    specversion: '1.0',
    id: `${runId}:${seq}`,
    source: 'opencompany://services/chat',
    type: `com.opencompany.chat.run.${suffix}`,
    subject: runId,
    data: { workflow_id: 'w1', session_id: 'w1', run_id: runId, seq, hub_epoch: 'e1', ...data },
  };
}

function runEvents(...frames: unknown[]) {
  act(() => {
    const store = useChatRunStore.getState();
    for (const item of frames) store.receive(item);
    store.flush();
  });
}

/** The booking interface as the server streams it: a snapshot, then a patch per step. */
function uiFrames(firstSeq: number, spec: typeof booking = booking) {
  const patches: Wire[] = [
    { op: 'add', path: '/root', value: spec.root },
    { op: 'add', path: '/state', value: spec.state },
    ...Object.entries(spec.elements).map(([id, element]) => ({ op: 'add', path: `/elements/${id}`, value: element })),
  ];
  return [
    frame(firstSeq, 'activity.snapshot', { message_id: 'ui_1', activity_type: 'json_render', content: { root: '', state: {}, elements: {} }, replace: true }),
    ...patches.map((patch, index) => frame(firstSeq + 1 + index, 'activity.delta', { message_id: 'ui_1', activity_type: 'json_render', patch: [patch] })),
  ];
}

function host(patch: Partial<ChatHost> = {}): ChatHost {
  return { kind: 'home', sessionId: 'w1', scope: 'all', persona: { name: 'Maya', colorRole: 'agent' }, composer: 'send', notify: vi.fn(), ...patch };
}

function renderPane(chat: ChatHost = host()) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChatPane host={chat} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  resetChatRunStore();
  useComposerStore.setState({ drafts: {} });
  server = { messages: [row('m1', 'user', 'Book Priya on Saturday', { run_id: 'r1' })], activeRuns: [{ run_id: 'r1', session_id: 'w1', state: 'running', seq: 1, hub_epoch: 'e1' }] };
  sendRequest.mockReset().mockImplementation(async (kind: string, data: Wire) => {
    switch (kind) {
      case 'chat_subscribe':
        return { success: true, session_id: data.session_id, hub_epoch: 'e1', active_runs: server.activeRuns };
      case 'get_chat_messages':
        return { success: true, messages: server.messages, thread: { active_leaf_id: null, revision: 1 }, active_runs: server.activeRuns };
      case 'send_chat_message':
        return { success: true, message_id: 'm2', run_id: 'r2', delivery: 'now' };
      case 'chat_ui_state':
        return { success: true, part_id: data.part_id, state_revision: 1 };
      default:
        return { success: true };
    }
  });
});

async function finishRun(spec: typeof booking = booking) {
  server.activeRuns = [];
  server.messages = [
    ...server.messages,
    row('a_r1', 'assistant', 'Saturday has a clean gap with Ana.', {
      run_id: 'r1',
      parts: { ui: [{ part_id: 'ui_1', spec, state: spec.state, state_revision: 0, elements: 11 }] },
    }),
  ];
  runEvents(frame(16, 'finished', { outcome: { type: 'success' }, result: { reply_message_id: 'a_r1' } }));
  await act(async () => {
    await client.invalidateQueries({ queryKey: ['chatThread'] });
  });
  await screen.findByText('Saturday has a clean gap with Ana.');
}

describe('an interface in a reply', () => {
  it('streams in with the run and stays once the reply is saved', async () => {
    renderPane();
    await screen.findByText('Thinking');
    runEvents(...uiFrames(2));
    expect(await screen.findByRole('radiogroup', { name: 'Saturday 28 Sep' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hold this slot for 15 min' })).toBeInTheDocument();
    // The Callout waits for its condition.
    expect(screen.queryByText(/Highlights take about 3 hours/)).not.toBeInTheDocument();

    const picker = screen.getByRole('radiogroup', { name: 'Saturday 28 Sep' });
    await finishRun();
    // The same element: the saved reply took over without redrawing it.
    expect(screen.getByRole('radiogroup', { name: 'Saturday 28 Sep' })).toBe(picker);
  });

  it('sends what the owner sets after a pause, and shows what depends on it', async () => {
    renderPane();
    await screen.findByText('Thinking');
    runEvents(...uiFrames(2));
    fireEvent.click(await screen.findByRole('radio', { name: 'Highlights' }));
    expect(await screen.findByText(/Highlights take about 3 hours/)).toBeInTheDocument();
    await waitFor(() =>
      expect(sendRequest).toHaveBeenCalledWith('chat_ui_state', {
        session_id: 'w1',
        part_id: 'ui_1',
        changes: [{ path: '/service', value: 'Highlights' }],
      }),
    );
  });

  it('puts an ask that is not what its button says into the box', async () => {
    renderPane();
    await screen.findByText('Thinking');
    runEvents(...uiFrames(2));
    fireEvent.click(await screen.findByRole('button', { name: 'Show Sunday instead' }));
    expect(screen.getByRole('textbox', { name: 'Message Maya' })).toHaveValue('What does Sunday look like?');
    expect(sendRequest).not.toHaveBeenCalledWith('send_chat_message', expect.anything());
  });

  it('sends what the owner set before an ask that goes as written', async () => {
    // This Sunday button asks exactly what it says, so a press sends it at once.
    const asking = structuredClone(booking);
    asking.elements.sun.on.press.params.text = asking.elements.sun.props.label;
    renderPane();
    await screen.findByText('Thinking');
    runEvents(...uiFrames(2, asking));
    await finishRun(asking);
    fireEvent.click(screen.getByRole('radio', { name: 'Highlights' }));
    fireEvent.click(screen.getByRole('button', { name: 'Show Sunday instead' }));
    await waitFor(() =>
      expect(sendRequest).toHaveBeenCalledWith('send_chat_message', expect.objectContaining({ message: 'Show Sunday instead' })),
    );
    // The change did not wait for the pause: it went before the message.
    const kinds = sendRequest.mock.calls.map(([kind]) => kind);
    expect(kinds).toContain('chat_ui_state');
    expect(kinds.indexOf('chat_ui_state')).toBeLessThan(kinds.indexOf('send_chat_message'));
  });

  it('sends what the owner set before a message they type', async () => {
    renderPane();
    await screen.findByText('Thinking');
    runEvents(...uiFrames(2));
    await finishRun();
    fireEvent.click(screen.getByRole('radio', { name: 'Highlights' }));
    const box = screen.getByRole('textbox', { name: 'Message Maya' });
    fireEvent.change(box, { target: { value: 'Book that one' } });
    const send = within(box.closest('.chat-composer') as HTMLElement).getByRole('button', { name: 'Send' });
    await waitFor(() => expect(send).toBeEnabled());
    fireEvent.click(send);
    await waitFor(() =>
      expect(sendRequest).toHaveBeenCalledWith('send_chat_message', expect.objectContaining({ message: 'Book that one' })),
    );
    const kinds = sendRequest.mock.calls.map(([kind]) => kind);
    expect(kinds).toContain('chat_ui_state');
    expect(kinds.indexOf('chat_ui_state')).toBeLessThan(kinds.indexOf('send_chat_message'));
  });

  it('sends a press back to the employee once the answer is done', async () => {
    const chat = host();
    renderPane(chat);
    await screen.findByText('Thinking');
    runEvents(...uiFrames(2));
    fireEvent.click(await screen.findByRole('button', { name: 'Hold this slot for 15 min' }));
    // Still answering: the press waits.
    expect(chat.notify).toHaveBeenCalledWith('Maya is still answering. Try that again once they finish.', 'info');

    await finishRun();
    fireEvent.click(screen.getByRole('button', { name: 'Hold this slot for 15 min' }));
    // The owner's side reads as the press.
    expect(await screen.findByText('Pressed')).toHaveClass('sr-only');
    await waitFor(() =>
      expect(sendRequest).toHaveBeenCalledWith(
        'send_chat_message',
        expect.objectContaining({
          message: 'Hold this slot for 15 min',
          ui_event: { part_id: 'ui_1', element_id: 'hold', action: 'holdSlot', params: { slot: 's2' } },
        }),
      ),
    );
  });
});
