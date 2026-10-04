/**
 * The shared chat against a fake server: it follows the session's runs
 * (subscribing while mounted), shows a message at once and the employee
 * working until the run ends, streams the answer and its steps, holds the
 * next message until then (Send is Stop, and Esc stops too), says why a run
 * failed, waits for Resume, and puts a message that did not go back in the
 * box, where it survives leaving the conversation.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));

import { resetChatRunStore, useChatRunStore } from '@/stores/chatRunStore';
import { ChatPane } from '../ChatPane';
import type { ChatHost } from '../host';
import { useComposerStore } from '../state/composerStore';
// Loaded up front so the turns' lazy markdown resolves from the module cache.
import '../markdown/ReplyMarkdown';

type Wire = Record<string, unknown>;
let server: { messages: Wire[]; send: Wire; activeRuns: Wire[] };
let client: QueryClient;

function row(id: string, role: 'user' | 'assistant', text: string, patch: Wire = {}): Wire {
  return { id, role, text, message: text, timestamp: '2026-10-03T09:00:00Z', run_key: 'g1', ...patch };
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

async function threadUpdated() {
  await act(async () => {
    await client.invalidateQueries({ queryKey: ['chatThread'] });
  });
}

function host(patch: Partial<ChatHost> = {}): ChatHost {
  return {
    kind: 'home',
    sessionId: 'w1',
    scope: 'all',
    persona: { name: 'Maya', colorRole: 'agent' },
    composer: 'send',
    notify: vi.fn(),
    ...patch,
  };
}

function renderPane(chat: ChatHost = host()) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = (next: ChatHost) => (
    <QueryClientProvider client={client}>
      <ChatPane host={next} />
    </QueryClientProvider>
  );
  const utils = render(view(chat));
  return { ...utils, rerender: (next: ChatHost) => utils.rerender(view(next)) };
}

function box() {
  return screen.getByRole('textbox', { name: 'Message Maya' });
}

function type(text: string) {
  fireEvent.change(box(), { target: { value: text } });
}

/** Write a message once the conversation has loaded (until then it cannot go). */
async function write(text: string) {
  type(text);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled());
}

beforeEach(() => {
  resetChatRunStore();
  useComposerStore.setState({ drafts: {} });
  server = { messages: [], send: { success: true, message_id: 'm1', run_id: 'r1', delivery: 'now' }, activeRuns: [] };
  sendRequest.mockReset().mockImplementation(async (kind: string, data: Wire) => {
    switch (kind) {
      case 'chat_subscribe':
        return { success: true, session_id: data.session_id, hub_epoch: 'e1', active_runs: server.activeRuns };
      case 'get_chat_messages':
        return { success: true, messages: server.messages, thread: { active_leaf_id: null, revision: 1 }, active_runs: server.activeRuns };
      case 'send_chat_message':
        if (server.send.success !== false) {
          server.messages = [...server.messages, row(String(server.send.message_id), 'user', String(data.message), {
            run_id: server.send.run_id,
            client_message_id: data.client_message_id,
          })];
        }
        return server.send;
      default:
        return { success: true };
    }
  });
});

describe('ChatPane', () => {
  it('follows the session while it is open and reads the thread for its scope', async () => {
    const { unmount } = renderPane(host({ scope: 'live' }));
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('chat_subscribe', { session_id: 'w1' }));
    expect(sendRequest).toHaveBeenCalledWith('get_chat_messages', { session_id: 'w1', limit: 200, all_generations: false });
    await waitFor(() => expect(useChatRunStore.getState().sessions.w1?.subscribed).toBe(true));
    unmount();
    expect(sendRequest).toHaveBeenCalledWith('chat_unsubscribe', { session_id: 'w1' });
  });

  it('shows a message at once and the employee working until the run ends', async () => {
    renderPane();
    await waitFor(() => expect(useChatRunStore.getState().sessions.w1?.subscribed).toBe(true));
    await write('  Any bookings today?  ');
    fireEvent.keyDown(box(), { key: 'Enter' });

    expect(box()).toHaveValue('');
    expect(await screen.findByText('Any bookings today?')).toBeInTheDocument();
    await waitFor(() =>
      expect(sendRequest).toHaveBeenCalledWith(
        'send_chat_message',
        expect.objectContaining({ message: 'Any bookings today?', session_id: 'w1', client_message_id: expect.any(String) }),
      ),
    );
    expect(await screen.findByText('Thinking')).toBeInTheDocument();
    // The next message waits for this answer: Send is Stop meanwhile.
    type('And tomorrow?');
    expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stop reply' })).toBeEnabled();

    runEvents(frame(1, 'started', { kind: 'message', user_message_id: 'm1' }));
    expect(screen.getByText('Thinking')).toBeInTheDocument();
    server.messages = [...server.messages, row('a_r1', 'assistant', 'Two, at **10** and at 3.', { run_id: 'r1' })];
    runEvents(frame(2, 'finished', { outcome: { type: 'success' }, result: { reply_message_id: 'a_r1' } }));
    await threadUpdated();

    expect(await screen.findByText('10', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.queryByText('Thinking')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled();
    expect(screen.getAllByText('Any bookings today?')).toHaveLength(1);
  });

  it('frees the box when a resent message names a run that already ended', async () => {
    // The first send went through but its answer never arrived: the draft
    // kept its id, and the server had already answered it.
    const finished = { run_id: 'r1', state: 'finished', outcome: { type: 'success' } };
    server.messages = [
      row('m1', 'user', 'Book Saturday', { client_message_id: 'c1', run_id: 'r1', run: finished }),
      row('a_r1', 'assistant', 'Saturday is booked.', { run_id: 'r1' }),
    ];
    useComposerStore.setState({ drafts: { w1: { text: 'Book Saturday', clientMessageId: 'c1' } } });
    const answer = sendRequest.getMockImplementation()!;
    sendRequest.mockImplementation(async (kind: string, data: Wire) => {
      if (kind === 'send_chat_message') return { success: true, message_id: 'm1', run_id: 'r1', delivery: 'now' };
      if (kind === 'get_chat_run') return { success: true, run: { ...finished, session_id: 'w1', seq: 4, hub_epoch: 'e1' } };
      return answer(kind, data);
    });
    renderPane();
    await waitFor(() => expect(useChatRunStore.getState().sessions.w1?.subscribed).toBe(true));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled());
    fireEvent.keyDown(box(), { key: 'Enter' });

    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('get_chat_run', { run_id: 'r1' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument());
    expect(screen.queryByText('Thinking')).not.toBeInTheDocument();
    expect(screen.getAllByText('Book Saturday')).toHaveLength(1);
  });

  it('reads no run again for a fresh message the thread still calls live', async () => {
    renderPane();
    await waitFor(() => expect(useChatRunStore.getState().sessions.w1?.subscribed).toBe(true));
    const answer = sendRequest.getMockImplementation()!;
    sendRequest.mockImplementation(async (kind: string, data: Wire) => {
      const reply = await answer(kind, data);
      if (kind === 'send_chat_message') {
        server.messages = server.messages.map((message) =>
          message.id === 'm1' ? { ...message, run: { run_id: 'r1', state: 'pending', outcome: null } } : message,
        );
      }
      return reply;
    });
    await write('Any bookings today?');
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(await screen.findByText('Thinking')).toBeInTheDocument();
    await threadUpdated();
    expect(sendRequest).not.toHaveBeenCalledWith('get_chat_run', expect.anything());
  });

  it('streams the answer with a caret, then shows the saved one in its place', async () => {
    server.messages = [row('m1', 'user', 'Any bookings today?', { run_id: 'r1' })];
    server.activeRuns = [{ run_id: 'r1', session_id: 'w1', state: 'running', seq: 1, hub_epoch: 'e1' }];
    renderPane();
    expect(await screen.findByText('Thinking')).toBeInTheDocument();
    runEvents(
      frame(2, 'text.started', { message_id: 'r1.0.1' }),
      frame(3, 'text.content', { message_id: 'r1.0.1', delta: 'Two bookings:\n\n' }),
      frame(4, 'text.content', { message_id: 'r1.0.1', delta: '- 10:00 **Priya**' }),
    );
    expect(await screen.findByText('Priya', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByText('Writing')).toBeInTheDocument();
    const streaming = document.querySelector('.chat-markdown[data-streaming]');
    expect(streaming).not.toBeNull();
    const answer = screen.getByText('Priya', { selector: 'strong' }).closest('[data-turn="assistant"]');

    server.messages = [...server.messages, row('a_r1', 'assistant', 'Two bookings:\n\n- 10:00 **Priya**', { run_id: 'r1' })];
    runEvents(
      frame(5, 'text.ended', { message_id: 'r1.0.1', final: true, reply_message_id: 'a_r1' }),
      frame(6, 'finished', { outcome: { type: 'success' }, result: { reply_message_id: 'a_r1' } }),
    );
    await threadUpdated();
    await waitFor(() => expect(document.querySelector('.chat-markdown[data-streaming]')).toBeNull());
    // One element from the first word to the saved reply.
    expect(screen.getByText('Priya', { selector: 'strong' }).closest('[data-turn="assistant"]')).toBe(answer);
    expect(screen.getAllByText('Priya', { selector: 'strong' })).toHaveLength(1);
  });

  it('shows the steps while they run and what they came to afterwards', async () => {
    server.messages = [row('m1', 'user', 'Check my calendar', { run_id: 'r1' })];
    server.activeRuns = [{ run_id: 'r1', session_id: 'w1', state: 'running', seq: 1, hub_epoch: 'e1' }];
    renderPane();
    await screen.findByText('Thinking');
    runEvents(frame(2, 'step.started', { step_id: 'c1', step_name: 'Checked Google Calendar' }));
    expect(screen.getByRole('button', { name: /Working…/ })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Checked Google Calendar')).toBeInTheDocument();
    runEvents(frame(3, 'step.finished', { step_id: 'c1', state: 'done', detail: '3 events on Saturday', duration_ms: 900 }));
    expect(screen.getByText('3 events on Saturday')).toBeInTheDocument();

    server.messages = [
      ...server.messages,
      row('a_r1', 'assistant', 'You have three.', {
        run_id: 'r1',
        run: { run_id: 'r1', state: 'finished', outcome: 'success', steps: [{ step_id: 'c1', name: 'Checked Google Calendar', state: 'done', detail: '3 events on Saturday' }], duration_ms: 4_000 },
      }),
    ];
    runEvents(frame(4, 'finished', { outcome: { type: 'success' }, result: { reply_message_id: 'a_r1' }, duration_ms: 4_000, step_count: 1 }));
    await threadUpdated();
    expect(await screen.findByText('You have three.')).toBeInTheDocument();
    // Still open above the answer; it says how long and how many.
    const done = screen.getByRole('button', { name: 'Worked for 4s · 1 step' });
    expect(done).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(done);
    expect(done).toHaveAttribute('aria-expanded', 'false');
  });

  it('keeps the steps of an answer read back closed until opened', async () => {
    const run = { run_id: 'r1', state: 'finished', outcome: 'success', steps: [{ step_id: 'c1', name: 'Searched the web', state: 'done' }], duration_ms: 2_000 };
    server.messages = [row('m1', 'user', 'Find a florist', { run_id: 'r1', run }), row('a_r1', 'assistant', 'Try Bloom.', { run_id: 'r1', run })];
    renderPane();
    const steps = await screen.findByRole('button', { name: 'Worked for 2s · 1 step' });
    expect(steps).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Searched the web')).not.toBeInTheDocument();
    fireEvent.click(steps);
    expect(screen.getByText('Searched the web')).toBeInTheDocument();
  });

  it('stops the answer from the Stop button or Esc', async () => {
    server.messages = [row('m1', 'user', 'Write me an essay', { run_id: 'r1' })];
    server.activeRuns = [{ run_id: 'r1', session_id: 'w1', state: 'running', seq: 1, hub_epoch: 'e1' }];
    const stopped: unknown[] = [];
    const answer = sendRequest.getMockImplementation()!;
    sendRequest.mockImplementation(async (kind: string, data: Wire) => {
      if (kind === 'stop_chat_run') {
        stopped.push(data);
        return { success: true, run_id: data.run_id, state: 'stopping' };
      }
      return answer(kind, data);
    });
    renderPane();
    await screen.findByText('Thinking');
    expect(screen.getByText('Esc')).toBeInTheDocument();

    fireEvent.keyDown(box(), { key: 'Escape' });
    await waitFor(() => expect(stopped).toEqual([{ run_id: 'r1' }]));
    expect(await screen.findByText('Stopping…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stopping' })).toBeDisabled();
    // Pressing it again changes nothing while it stops.
    fireEvent.keyDown(box(), { key: 'Escape' });
    expect(stopped).toHaveLength(1);

    runEvents(frame(2, 'custom', { name: 'opencompany.stopping', value: {} }), frame(3, 'finished', { outcome: { type: 'stopped' }, result: { no_reply: true } }));
    expect(await screen.findByText('You stopped this reply.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument();
  });

  it('says so when Stop did not reach the server', async () => {
    server.activeRuns = [{ run_id: 'r1', session_id: 'w1', state: 'running', seq: 1, hub_epoch: 'e1' }];
    server.messages = [row('m1', 'user', 'Hi', { run_id: 'r1' })];
    const answer = sendRequest.getMockImplementation()!;
    sendRequest.mockImplementation(async (kind: string, data: Wire) => {
      if (kind === 'stop_chat_run') throw new Error('socket closed');
      return answer(kind, data);
    });
    const chat = host();
    renderPane(chat);
    fireEvent.click(await screen.findByRole('button', { name: 'Stop reply' }));
    await waitFor(() => expect(chat.notify).toHaveBeenCalledWith('Couldn’t stop the reply. Try again.', 'error'));
  });

  it('offers the next questions the employee suggested, under its latest answer only', async () => {
    const followups = ['What about Sunday?', 'Move it to 3pm', 'What about Sunday?', '  '];
    server.messages = [
      row('m0', 'user', 'Old question'),
      row('a0', 'assistant', 'Old answer', { parts: { followups: ['Never shown'] } }),
      row('m1', 'user', 'Any bookings?', { run_id: 'r1' }),
      row('a_r1', 'assistant', 'Two today.', { run_id: 'r1', parts: { followups } }),
    ];
    renderPane();
    const group = await screen.findByRole('group', { name: 'Ask next' });
    expect(group).toHaveTextContent('What about Sunday?Move it to 3pm');
    expect(screen.queryByText('Never shown')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Move it to 3pm' }));
    await waitFor(() =>
      expect(sendRequest).toHaveBeenCalledWith('send_chat_message', expect.objectContaining({ message: 'Move it to 3pm', session_id: 'w1' })),
    );
  });

  it('says why a run failed, with what to do about it', async () => {
    server.messages = [row('m1', 'user', 'Book Priya in', { run_id: 'r1' })];
    server.activeRuns = [{ run_id: 'r1', session_id: 'w1', state: 'running', seq: 1, hub_epoch: 'e1' }];
    renderPane();
    expect(await screen.findByText('Thinking')).toBeInTheDocument();
    runEvents(frame(2, 'failed', { message: 'Calendar said no', code: 'run_failed', hint: 'Reconnect Google' }));
    expect(screen.getByText('Maya couldn’t answer.')).toBeInTheDocument();
    expect(screen.getByText('Calendar said no')).toBeInTheDocument();
    expect(screen.getByText('Reconnect Google')).toBeInTheDocument();
    expect(screen.queryByText('Thinking')).not.toBeInTheDocument();
  });

  it('shows how an earlier run ended after a reload', async () => {
    server.messages = [
      row('m1', 'user', 'Anyone there?', {
        run_id: 'r1',
        run: { run_id: 'r1', state: 'error', outcome: null, error: { message: 'x', code: 'not_delivered' } },
      }),
    ];
    renderPane();
    expect(await screen.findByText('Maya didn’t pick up this message.')).toBeInTheDocument();
  });

  it('waits for Resume when the message was queued', async () => {
    server.send = { success: true, message_id: 'm1', run_id: 'r1', delivery: 'queued' };
    renderPane(host({ composer: 'queue' }));
    await write('Call me back');
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(await screen.findByText('Waiting for you to resume Maya.')).toBeInTheDocument();
    expect(screen.queryByText('Thinking')).not.toBeInTheDocument();

    runEvents(frame(1, 'started', { kind: 'message' }));
    expect(await screen.findByText('Thinking')).toBeInTheDocument();
  });

  it('puts a message that did not go back in the box, and lets the host say why', async () => {
    server.send = { success: false, error: 'not_running' };
    const onSendRefused = vi.fn();
    renderPane(host({ onSendRefused }));
    await write('Hello?');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(onSendRefused).toHaveBeenCalledWith('not_running'));
    expect(box()).toHaveValue('Hello?');
    expect(screen.getByRole('log', { name: 'Conversation with Maya' })).not.toHaveTextContent('Hello?');
  });

  it('tells the owner a send failed when the host does not', async () => {
    sendRequest.mockImplementation(async (kind: string) => {
      if (kind === 'send_chat_message') throw new Error('socket closed');
      if (kind === 'chat_subscribe') return { success: true, hub_epoch: 'e1', active_runs: [] };
      return { success: true, messages: [] };
    });
    const chat = host();
    renderPane(chat);
    await write('Hello?');
    fireEvent.keyDown(box(), { key: 'Enter' });
    await waitFor(() => expect(chat.notify).toHaveBeenCalledWith('Your message didn’t send. Try again.', 'error'));
    // It may have reached the server: sending it again is the same message.
    expect(useComposerStore.getState().drafts.w1).toEqual({ text: 'Hello?', clientMessageId: expect.any(String) });
  });

  it('has no message box while nothing can read messages', async () => {
    renderPane(host({ composer: 'closed', notices: <p>Start Maya first.</p> }));
    expect(await screen.findByText('Start Maya first.')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('keeps an unsent message when the conversation is opened again', async () => {
    const { unmount } = renderPane();
    type('Half a thought');
    unmount();
    renderPane();
    expect(box()).toHaveValue('Half a thought');
  });
});
