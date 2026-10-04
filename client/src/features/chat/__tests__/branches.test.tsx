/**
 * Changing the conversation against a fake server: editing one of the
 * owner's messages in place (and ArrowUp to edit the last one), moving
 * between versions, trying the latest answer again (also from the note of a
 * run that gave none), rating an answer, copying, saying why a change was
 * refused, and hiding the suggested questions while the owner writes.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));

import { resetChatRunStore } from '@/stores/chatRunStore';
import { ChatPane } from '../ChatPane';
import { parseMessages } from '../data/schemas';
import type { ChatHost } from '../host';
import { useComposerStore } from '../state/composerStore';
import { branchRefusalText, feedbackThanks } from '../turns/runCopy';
import '../markdown/ReplyMarkdown';

type Wire = Record<string, unknown>;
let server: { messages: Wire[]; activeRuns: Wire[]; replies: Record<string, Wire> };

function row(id: string, role: 'user' | 'assistant', text: string, patch: Wire = {}): Wire {
  return { id, role, text, message: text, timestamp: '2026-10-04T09:00:00Z', run_key: 'g1', ...patch };
}

const versions = (index: number, ids: string[]) => ({ index, count: ids.length, ids });

function conversation(): Wire[] {
  return [
    row('m1', 'user', 'Book Saturday', { run_id: 'r1', editable: true, siblings: versions(1, ['m0', 'm1']) }),
    row('a1', 'assistant', 'Saturday is booked.', {
      run_id: 'r1',
      editable: true,
      parts: { followups: ['And Sunday?'] },
      run: { run_id: 'r1', state: 'finished', outcome: 'success' },
    }),
  ];
}

function host(patch: Partial<ChatHost> = {}): ChatHost {
  return { kind: 'home', sessionId: 'w1', scope: 'all', persona: { name: 'Maya', colorRole: 'agent' }, composer: 'send', notify: vi.fn(), ...patch };
}

function renderPane(chat: ChatHost = host()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ChatPane host={chat} />
    </QueryClientProvider>,
  );
  return chat;
}

function requests(kind: string): Wire[] {
  return sendRequest.mock.calls.filter(([name]) => name === kind).map(([, data]) => data as Wire);
}

beforeEach(() => {
  resetChatRunStore();
  useComposerStore.setState({ drafts: {} });
  server = { messages: conversation(), activeRuns: [], replies: {} };
  sendRequest.mockReset().mockImplementation(async (kind: string, data: Wire) => {
    if (server.replies[kind]) return server.replies[kind];
    switch (kind) {
      case 'chat_subscribe':
        return { success: true, session_id: data.session_id, hub_epoch: 'e1', active_runs: server.activeRuns };
      case 'get_chat_messages':
        return { success: true, messages: server.messages, thread: { active_leaf_id: 'a1', revision: 7 }, active_runs: server.activeRuns };
      case 'edit_chat_message':
        return { success: true, message_id: 'm2', run_id: 'r2', delivery: 'now' };
      case 'regenerate_chat_reply':
        return { success: true, message_id: 'm1', run_id: 'r3', delivery: 'now' };
      case 'set_chat_feedback':
        server.messages = server.messages.map((message) => (message.id === data.message_id ? { ...message, feedback: data.value } : message));
        return { success: true, message_id: data.message_id, value: data.value, reaches: ['next_turn'] };
      default:
        return { success: true };
    }
  });
});

describe('the message schema', () => {
  it('reads versions, whether it may change, and the rating', () => {
    const [one, alone, odd] = parseMessages([
      row('m1', 'user', 'a', { siblings: versions(1, ['m0', 'm1']), editable: true, feedback: 'down' }),
      row('m2', 'user', 'b', { siblings: versions(0, ['m2']), editable: 'yes', feedback: 'meh' }),
      row('m3', 'user', 'c', { siblings: { index: 3, count: 2, ids: ['x', 'y'] } }),
    ]);
    expect(one).toMatchObject({ siblings: versions(1, ['m0', 'm1']), editable: true, feedback: 'down' });
    // Alone, unreadable or missing: no versions; anything odd: unchanged and unrated.
    expect(alone).toMatchObject({ siblings: null, editable: false, feedback: null });
    expect(odd.siblings).toBeNull();
  });
});

describe('editing a message', () => {
  it('opens in place, sends the edit against the revision read, and closes', async () => {
    renderPane();
    await screen.findByText('Book Saturday');
    fireEvent.click(screen.getByRole('button', { name: 'Edit message' }));
    const editor = screen.getByRole('textbox', { name: 'Edit your message' });
    expect(editor).toHaveValue('Book Saturday');
    expect(screen.getByText('Editing starts a new branch of the conversation')).toBeInTheDocument();
    // Unchanged: Send waits.
    expect(screen.getByRole('button', { name: 'Send edit' })).toBeDisabled();
    fireEvent.change(editor, { target: { value: 'Book Sunday' } });
    fireEvent.keyDown(editor, { key: 'Enter' });
    await waitFor(() => expect(requests('edit_chat_message')).toHaveLength(1));
    expect(requests('edit_chat_message')[0]).toMatchObject({ session_id: 'w1', message_id: 'm1', message: 'Book Sunday', expected_revision: 7 });
    expect(typeof requests('edit_chat_message')[0].client_message_id).toBe('string');
    await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Edit your message' })).not.toBeInTheDocument());
  });

  it('cancels with Esc or Cancel, and Esc does not stop anything', async () => {
    renderPane();
    await screen.findByText('Book Saturday');
    fireEvent.click(screen.getByRole('button', { name: 'Edit message' }));
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Edit your message' }), { key: 'Escape' });
    expect(screen.queryByRole('textbox', { name: 'Edit your message' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit message' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('textbox', { name: 'Edit your message' })).not.toBeInTheDocument();
    expect(requests('stop_chat_run')).toHaveLength(0);
  });

  it('opens on the last message with ArrowUp in an empty box', async () => {
    renderPane();
    await screen.findByText('Book Saturday');
    const box = screen.getByRole('textbox', { name: 'Message Maya' });
    fireEvent.change(box, { target: { value: 'x' } });
    fireEvent.keyDown(box, { key: 'ArrowUp' });
    expect(screen.queryByRole('textbox', { name: 'Edit your message' })).not.toBeInTheDocument();
    fireEvent.change(box, { target: { value: '' } });
    fireEvent.keyDown(box, { key: 'ArrowUp' });
    expect(screen.getByRole('textbox', { name: 'Edit your message' })).toHaveValue('Book Saturday');
  });

  it('offers no edit where the server allows none', async () => {
    server.messages = [row('m1', 'user', 'Book Saturday', { run_id: 'r1', editable: false })];
    renderPane();
    await screen.findByText('Book Saturday');
    expect(screen.queryByRole('button', { name: 'Edit message' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy message' })).toBeInTheDocument();
  });

  it('says why an edit was refused and keeps it open', async () => {
    const chat = renderPane();
    server.replies.edit_chat_message = { success: false, error: 'cannot_rewind' };
    await screen.findByText('Book Saturday');
    fireEvent.click(screen.getByRole('button', { name: 'Edit message' }));
    const editor = screen.getByRole('textbox', { name: 'Edit your message' });
    fireEvent.change(editor, { target: { value: 'Book Sunday' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send edit' }));
    await waitFor(() => expect(chat.notify).toHaveBeenCalledWith(branchRefusalText('cannot_rewind', 'Maya'), 'error'));
    expect(screen.getByRole('textbox', { name: 'Edit your message' })).toHaveValue('Book Sunday');
  });
});

describe('versions and trying again', () => {
  it('moves to another version of a message', async () => {
    renderPane();
    await screen.findByText('Book Saturday');
    const steps = screen.getByRole('group', { name: 'Versions' });
    expect(within(steps).getByText('2 / 2')).toBeInTheDocument();
    expect(within(steps).getByRole('button', { name: 'Next version' })).toBeDisabled();
    fireEvent.click(within(steps).getByRole('button', { name: 'Previous version' }));
    await waitFor(() => expect(requests('switch_chat_branch')).toEqual([{ session_id: 'w1', expected_revision: 7, message_id: 'm0' }]));
  });

  it('tries the latest answer again', async () => {
    renderPane();
    await screen.findByText('Saturday is booked.');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(requests('regenerate_chat_reply')).toEqual([{ session_id: 'w1', expected_revision: 7, message_id: 'a1' }]));
  });

  it('tries again from the note of a run that gave no answer', async () => {
    server.messages = [
      row('m1', 'user', 'Book Saturday', {
        run_id: 'r1',
        editable: true,
        run: { run_id: 'r1', state: 'error', outcome: null, error: { message: 'boom', code: 'run_failed' } },
      }),
    ];
    renderPane();
    await screen.findByText('Book Saturday');
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(requests('regenerate_chat_reply')).toEqual([{ session_id: 'w1', expected_revision: 7, message_id: 'm1' }]));
  });
});

describe('rating and copying an answer', () => {
  it('rates, says where it goes, and takes it back', async () => {
    const chat = renderPane();
    await screen.findByText('Saturday is booked.');
    const good = screen.getByRole('button', { name: 'Good reply' });
    expect(good).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(good);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Good reply' })).toHaveAttribute('aria-pressed', 'true'));
    await waitFor(() => expect(chat.notify).toHaveBeenCalledWith(feedbackThanks('Maya'), 'success'));
    expect(requests('set_chat_feedback')[0]).toMatchObject({ message_id: 'a1', value: 'up' });
    // Pressed again: the rating is taken back, without a thank-you.
    fireEvent.click(screen.getByRole('button', { name: 'Good reply' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Good reply' })).toHaveAttribute('aria-pressed', 'false'));
    expect(requests('set_chat_feedback')[1]).toMatchObject({ message_id: 'a1', value: null });
    expect(chat.notify).toHaveBeenCalledTimes(1);
  });

  it('puts a rating back when it does not save', async () => {
    const chat = renderPane();
    server.replies.set_chat_feedback = { success: false, error: 'not_found' };
    await screen.findByText('Saturday is booked.');
    fireEvent.click(screen.getByRole('button', { name: 'Bad reply' }));
    await waitFor(() => expect(chat.notify).toHaveBeenCalledWith('Your rating didn’t save. Try again.', 'error'));
    expect(screen.getByRole('button', { name: 'Bad reply' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('copies a message and an answer', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderPane();
    await screen.findByText('Saturday is booked.');
    fireEvent.click(screen.getByRole('button', { name: 'Copy reply' }));
    expect(writeText).toHaveBeenLastCalledWith('Saturday is booked.');
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Copy message' }));
    expect(writeText).toHaveBeenLastCalledWith('Book Saturday');
  });

  it('hides the suggested questions while the owner writes', async () => {
    renderPane();
    expect(await screen.findByRole('button', { name: /And Sunday\?/ })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Maya' }), { target: { value: 'Something else' } });
    expect(screen.queryByRole('button', { name: /And Sunday\?/ })).not.toBeInTheDocument();
  });
});

describe('the copy', () => {
  it('names each refusal and where a rating goes', () => {
    expect(branchRefusalText('revision_conflict', 'Maya')).toBe('The conversation changed meanwhile. Try again.');
    expect(branchRefusalText('run_in_progress', 'Maya')).toContain('Maya is still answering');
    expect(branchRefusalText('older_generation', 'Maya')).toContain('before Maya restarted');
    expect(branchRefusalText('something', 'Maya')).toBe('That didn’t go through. Try again.');
    expect(feedbackThanks('Maya')).toBe('Thanks — Maya will see this next time.');
  });
});
