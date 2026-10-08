/**
 * The message box's extras against a fake server: Attach, a paste and a drop
 * add files the same way (CLAUDE.md rule 9: the drop has a pointer
 * alternative reaching the same state), files alone are a message and wait
 * for their uploads, slash commands fill the box, Web off goes with the next
 * message, suggestions fill an empty chat's box, and dictation puts its text
 * in the box.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();
const upload = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));
vi.mock('@/lib/workspaceUpload', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/workspaceUpload')>()),
  uploadToWorkspace: (file: File, workflowId: string) => upload(file, workflowId),
}));

import { resetChatRunStore } from '@/stores/chatRunStore';
import { ChatPane } from '../ChatPane';
import type { ChatHost } from '../host';
import { useAttachmentStore } from '../state/attachmentStore';
import { useComposerStore } from '../state/composerStore';
import '../markdown/ReplyMarkdown';

type Wire = Record<string, unknown>;
let server: { messages: Wire[]; context: Wire; dictation: boolean };

const ref = (name: string, mime = 'text/plain') => ({
  kind: mime.startsWith('image/') ? 'image' : 'file',
  path: `uploads/${name}`,
  workflow_id: 'w1',
  filename: name,
  mime_type: mime,
  size_bytes: 2048,
  url: `/api/workspace/w1/files/uploads/${name}`,
});

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

const box = () => screen.getByRole('textbox', { name: 'Message Maya' });

/** The box's files without what differs between adds (ids, thumbnails). */
function boxState() {
  return (useAttachmentStore.getState().boxes.w1 ?? []).map(({ name, size, mime, state, ref: stored, error }) => ({ name, size, mime, state, ref: stored, error }));
}

beforeEach(() => {
  // cmdk scrolls the highlighted command into view; jsdom has no layout.
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => undefined;
  resetChatRunStore();
  useComposerStore.setState({ drafts: {}, web: {} });
  useAttachmentStore.setState({ boxes: {} });
  upload.mockReset().mockImplementation(async (file: File) => ref(file.name, file.type || 'text/plain'));
  server = {
    messages: [],
    context: {
      success: true,
      commands: [
        { command: '/schedule', description: 'Check a day in the calendar', fill: 'What does tomorrow look like?', suggest: true },
        { command: '/summarize', description: 'Summarize this conversation', fill: 'Summarize what we’ve agreed so far.', suggest: false },
      ],
      capabilities: { attachments: true, web: true },
      limits: { max_attachments: 6 },
    },
    dictation: false,
  };
  sendRequest.mockReset().mockImplementation(async (kind: string, data: Wire) => {
    switch (kind) {
      case 'chat_subscribe':
        return { success: true, session_id: data.session_id, hub_epoch: 'e1', active_runs: [] };
      case 'get_chat_messages':
        return { success: true, messages: server.messages, thread: { active_leaf_id: null, revision: 1 }, active_runs: [] };
      case 'get_chat_context':
        return server.context;
      case 'dictation_status':
        return { success: true, available: server.dictation };
      case 'transcribe_audio':
        return { success: true, text: 'Send reminders for tomorrow' };
      case 'send_chat_message':
        return { success: true, message_id: 'm1', run_id: 'r1', delivery: 'now' };
      default:
        return { success: true };
    }
  });
});

describe('adding files', () => {
  const file = () => new File(['a,b'], 'prices.txt', { type: 'text/plain' });

  async function viaPicker() {
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file()] } });
  }
  async function viaPaste() {
    fireEvent.paste(box(), { clipboardData: { files: [file()], types: ['Files'] } });
  }
  async function viaDrop() {
    const pane = screen.getByRole('region', { name: 'Chat with Maya' });
    fireEvent.dragEnter(pane, { dataTransfer: { files: [file()], types: ['Files'] } });
    expect(screen.getByText('Drop files for Maya')).toBeInTheDocument();
    fireEvent.drop(pane, { dataTransfer: { files: [file()], types: ['Files'] } });
    expect(screen.queryByText('Drop files for Maya')).not.toBeInTheDocument();
  }

  it.each([
    ['the picker', viaPicker],
    ['a paste', viaPaste],
    ['a drop', viaDrop],
  ])('adds the same file through %s', async (_how, add) => {
    renderPane();
    await screen.findByRole('button', { name: 'Attach files' });
    await add();
    await waitFor(() => expect(boxState()[0]?.state).toBe('ready'));
    expect(boxState()).toEqual([{ name: 'prices.txt', size: 3, mime: 'text/plain', state: 'ready', ref: ref('prices.txt'), error: null }]);
    expect(upload).toHaveBeenCalledWith(expect.any(File), 'w1');
    expect(screen.getByRole('button', { name: 'Remove prices.txt' })).toBeInTheDocument();
  });

  it('sends files alone, as paths, once they have uploaded', async () => {
    let finish: (value: unknown) => void = () => undefined;
    upload.mockImplementation(() => new Promise((resolve) => (finish = resolve)));
    renderPane();
    await screen.findByRole('button', { name: 'Attach files' });
    await act(async () => {
      fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [file()] } });
    });
    expect(screen.getByRole('status', { name: 'Uploading prices.txt' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    await act(async () => finish(ref('prices.txt')));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(requests('send_chat_message')).toHaveLength(1));
    expect(requests('send_chat_message')[0]).toMatchObject({ message: '', attachments: [{ path: 'uploads/prices.txt' }] });
    expect(boxState()).toEqual([]);
  });

  it('shows the files a message carried, and no bubble for files alone', async () => {
    server.messages = [
      { id: 'm1', role: 'user', text: '', message: '', timestamp: '2026-10-04T09:00:00Z', attachments: [ref('shelf.png', 'image/png'), ref('prices.txt')] },
    ];
    renderPane();
    const files = await screen.findByRole('list', { name: 'Attached files' });
    expect(within(files).getByRole('img', { name: 'shelf.png' })).toHaveAttribute('src', expect.stringContaining('/api/workspace/w1/files/uploads/shelf.png'));
    expect(within(files).getByText('prices.txt')).toBeInTheDocument();
    expect(document.querySelector('.chat-msg-user')).toBeNull();
  });
});

describe('commands, Web and suggestions', () => {
  it('fills the box from a slash command', async () => {
    renderPane();
    await screen.findByPlaceholderText(/Type \/ for commands/);
    fireEvent.change(box(), { target: { value: '/' } });
    const list = await screen.findByRole('listbox', { name: 'Commands' });
    expect(within(list).getAllByRole('option').map((option) => option.textContent)).toEqual([
      '/scheduleCheck a day in the calendar',
      '/summarizeSummarize this conversation',
    ]);
    fireEvent.keyDown(box(), { key: 'ArrowDown' });
    // The box controls the list and names the highlighted command.
    expect(box()).toHaveAttribute('aria-controls', list.id);
    expect(box()).toHaveAttribute('aria-activedescendant', within(list).getAllByRole('option')[1].id);
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(box()).toHaveValue('Summarize what we’ve agreed so far.');
    expect(box()).not.toHaveAttribute('aria-activedescendant');
    expect(screen.queryByRole('listbox', { name: 'Commands' })).not.toBeInTheDocument();
    expect(requests('send_chat_message')).toHaveLength(0);
  });

  it('closes the list with Esc, and shows none for what matches nothing', async () => {
    renderPane();
    await screen.findByPlaceholderText(/Type \/ for commands/);
    fireEvent.change(box(), { target: { value: '/sch' } });
    await screen.findByRole('listbox', { name: 'Commands' });
    fireEvent.keyDown(box(), { key: 'Escape' });
    expect(screen.queryByRole('listbox', { name: 'Commands' })).not.toBeInTheDocument();
    fireEvent.change(box(), { target: { value: '/nothing' } });
    expect(screen.queryByRole('listbox', { name: 'Commands' })).not.toBeInTheDocument();
  });

  it('sends with Web off until it is turned back on', async () => {
    renderPane();
    const web = await screen.findByRole('button', { name: 'Web' });
    expect(web).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(web);
    fireEvent.change(box(), { target: { value: 'What did we sell?' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(requests('send_chat_message')).toHaveLength(1));
    expect(requests('send_chat_message')[0]).toMatchObject({ options: { web: false } });
  });

  it('suggests what to ask in an empty chat', async () => {
    renderPane();
    fireEvent.click(await screen.findByRole('button', { name: /Check a day in the calendar/ }));
    expect(box()).toHaveValue('What does tomorrow look like?');
  });

  it('says hello in place of the suggestions, filling the box', async () => {
    renderPane(host({ greetings: ['Hi! What can you do?', 'Walk me through your routine'] }));
    fireEvent.click(await screen.findByRole('button', { name: 'Hi! What can you do?' }));
    expect(box()).toHaveValue('Hi! What can you do?');
    expect(screen.queryByRole('button', { name: /Check a day in the calendar/ })).not.toBeInTheDocument();
  });

  it('shows a box that takes nothing yet while the host waits, and no greetings', async () => {
    renderPane(host({ composer: 'wait', placeholder: 'Maya is starting…', greetings: ['Hi! What can you do?'] }));
    expect(await screen.findByPlaceholderText('Maya is starting…')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    await waitFor(() => expect(requests('get_chat_context')).toHaveLength(1));
    await act(async () => {});
    expect(screen.queryByRole('button', { name: 'Attach files' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Hi! What can you do?' })).not.toBeInTheDocument();
  });

  it('suggests nothing in the editor’s chat, a console for trying chat triggers', async () => {
    renderPane(host({ kind: 'dev', compact: true }));
    // Attach shows once the chat's context has arrived, commands included.
    await screen.findByRole('button', { name: 'Attach files' });
    expect(screen.queryByRole('button', { name: /Check a day in the calendar/ })).not.toBeInTheDocument();
  });

  it('offers nothing of this where the chat allows none', async () => {
    server.context = { success: true, commands: [], capabilities: { attachments: false, web: false }, limits: { max_attachments: 6 } };
    renderPane();
    await screen.findByPlaceholderText(/^Message Maya…$/);
    expect(screen.queryByRole('button', { name: 'Attach files' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Web' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Dictate' })).not.toBeInTheDocument();
  });
});

describe('dictation', () => {
  it('records, turns it into text and puts it in the box', async () => {
    server.dictation = true;
    const stop = vi.fn();
    class FakeRecorder {
      static isTypeSupported = () => true;
      mimeType = 'audio/webm';
      state = 'inactive';
      ondataavailable: ((event: { data: Blob }) => void) | null = null;
      onstop: (() => void) | null = null;
      start() {
        this.state = 'recording';
      }
      stop() {
        this.state = 'inactive';
        stop();
        this.ondataavailable?.({ data: new Blob(['audio'], { type: 'audio/webm' }) });
        this.onstop?.();
      }
    }
    const track = { stop: vi.fn() };
    vi.stubGlobal('MediaRecorder', FakeRecorder);
    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [track] }) },
      configurable: true,
    });
    vi.stubGlobal('AudioContext', undefined);
    upload.mockImplementation(async (file: File) => ref(file.name, 'audio/webm'));

    useComposerStore.getState().setText('w1', 'Please');
    renderPane();
    fireEvent.click(await screen.findByRole('button', { name: 'Dictate' }));
    expect(await screen.findByLabelText('Recording time')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Use dictation' }));
    await waitFor(() => expect(box()).toHaveValue('Please Send reminders for tomorrow'));
    expect(stop).toHaveBeenCalled();
    expect(track.stop).toHaveBeenCalled();
    expect(requests('transcribe_audio')[0]).toMatchObject({ session_id: 'w1', path: expect.stringMatching(/^uploads\/dictation-\d+\.webm$/) });
    vi.unstubAllGlobals();
  });
});
