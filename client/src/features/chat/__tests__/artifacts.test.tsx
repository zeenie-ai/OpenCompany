/**
 * A document the employee writes on its Canvas, in the reply that wrote it:
 * the card arrives with the run, names the latest version the reply or the
 * run knows, and Open hands the host the document to show.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));
vi.mock('@/lib/motion', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/motion')>()),
  motionSuppressed: () => true,
}));

import { resetChatRunStore, useChatRunStore } from '@/stores/chatRunStore';
import { ChatPane } from '../ChatPane';
import { liveArtifacts, mergeArtifacts, savedArtifacts, type ArtifactRef } from '../data/parts';
import type { ChatHost } from '../host';
import { ArtifactCard } from '../turns/ArtifactCard';
import { useComposerStore } from '../state/composerStore';
import '../markdown/ReplyMarkdown';

type Wire = Record<string, unknown>;

const wire = (version: number, itemId = 'item_1', title = 'Weekly report'): Wire => ({
  workflow_id: 'w1',
  canvas_node_id: 'w1:canvas:1',
  item_id: itemId,
  version,
  title,
  format: 'markdown',
});

const ref = (version: number, itemId = 'item_1', title = 'Weekly report'): ArtifactRef => ({
  workflowId: 'w1',
  canvasNodeId: 'w1:canvas:1',
  itemId,
  version,
  title,
  format: 'markdown',
});

describe('reading documents off a reply', () => {
  it('keeps each document once and drops what does not name one', () => {
    const parts = {
      artifacts: [wire(1), wire(2), { item_id: 'x' }, { ...wire(1, 'item_2', ' '), format: 'code', version: 0 }, 'nope'],
    };
    expect(savedArtifacts(parts)).toEqual([ref(1), { ...ref(1, 'item_2', 'Document'), format: 'code' }]);
    expect(savedArtifacts(null)).toEqual([]);
    expect(savedArtifacts({ artifacts: 'nope' })).toEqual([]);
  });

  it('reads the run’s artifact activities only', () => {
    expect(
      liveArtifacts([
        { messageId: 'artifact_item_1', activityType: 'artifact', content: wire(2), patches: 0 },
        { messageId: 'ui_1', activityType: 'json_render', content: {}, patches: 3 },
      ]),
    ).toEqual([ref(2)]);
  });

  it('shows each saved document at its newest version, then the run’s new ones', () => {
    expect(mergeArtifacts([ref(1), ref(3, 'item_2')], [ref(2), ref(1, 'item_2'), ref(1, 'item_3')])).toEqual([
      ref(2),
      ref(3, 'item_2'),
      ref(1, 'item_3'),
    ]);
  });
});

describe('the document card', () => {
  it('opens the document at its version', () => {
    const onOpen = vi.fn();
    render(<ArtifactCard artifact={ref(2)} onOpen={onOpen} />);
    expect(screen.getByText('Document · version 2')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open Weekly report' }));
    expect(onOpen).toHaveBeenCalledWith(ref(2));
  });

  it('cannot open without a host that shows documents', () => {
    render(<ArtifactCard artifact={{ ...ref(1), format: 'code' }} />);
    expect(screen.getByRole('button', { name: 'Open Weekly report' })).toBeDisabled();
    expect(screen.getByText('Code')).toBeInTheDocument();
  });

  it('draws its tile neutral, in no action’s colour', () => {
    render(<ArtifactCard artifact={ref(1)} onOpen={vi.fn()} />);
    const tile = screen.getByRole('button', { name: 'Open Weekly report' }).querySelector('[aria-hidden]');
    expect(tile).toHaveClass('border-border-default', 'bg-bg-active', 'text-fg-muted');
    expect(tile?.className).not.toMatch(/action-tools|node-agent/);
  });
});

describe('a document in a streaming reply', () => {
  let server: { messages: Wire[]; activeRuns: Wire[] };
  let client: QueryClient;

  const row = (id: string, role: 'user' | 'assistant', text: string, patch: Wire = {}): Wire => ({
    id,
    role,
    text,
    message: text,
    timestamp: '2026-10-04T09:00:00Z',
    run_key: 'g1',
    ...patch,
  });

  const frame = (seq: number, suffix: string, data: Wire = {}) => ({
    specversion: '1.0',
    id: `r1:${seq}`,
    source: 'opencompany://services/chat',
    type: `com.opencompany.chat.run.${suffix}`,
    subject: 'r1',
    data: { workflow_id: 'w1', session_id: 'w1', run_id: 'r1', seq, hub_epoch: 'e1', ...data },
  });

  const runEvents = (...frames: unknown[]) =>
    act(() => {
      const store = useChatRunStore.getState();
      for (const item of frames) store.receive(item);
      store.flush();
    });

  beforeEach(() => {
    resetChatRunStore();
    useComposerStore.setState({ drafts: {} });
    server = {
      messages: [row('m1', 'user', 'Write up the week', { run_id: 'r1' })],
      activeRuns: [{ run_id: 'r1', session_id: 'w1', state: 'running', seq: 1, hub_epoch: 'e1' }],
    };
    sendRequest.mockReset().mockImplementation(async (kind: string, data: Wire) => {
      switch (kind) {
        case 'chat_subscribe':
          return { success: true, session_id: data.session_id, hub_epoch: 'e1', active_runs: server.activeRuns };
        case 'get_chat_messages':
          return { success: true, messages: server.messages, thread: { active_leaf_id: null, revision: 1 }, active_runs: server.activeRuns };
        default:
          return { success: true };
      }
    });
  });

  it('arrives with the run, takes its newer version and opens through the host', async () => {
    const openArtifact = vi.fn();
    const host: ChatHost = {
      kind: 'home',
      sessionId: 'w1',
      scope: 'all',
      persona: { name: 'Maya', colorRole: 'agent' },
      composer: 'send',
      notify: vi.fn(),
      openArtifact,
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ChatPane host={host} />
      </QueryClientProvider>,
    );
    await screen.findByText('Write up the week');

    runEvents(
      frame(1, 'started', { kind: 'message', user_message_id: 'm1', reply_message_id: 'a_r1', started_at: '2026-10-04T09:00:01Z' }),
      frame(2, 'activity.snapshot', { message_id: 'artifact_item_1', activity_type: 'artifact', content: wire(1), replace: true }),
    );
    expect(await screen.findByRole('button', { name: 'Open Weekly report' })).toBeInTheDocument();
    expect(screen.getByText('Document')).toBeInTheDocument();

    runEvents(frame(3, 'activity.snapshot', { message_id: 'artifact_item_1', activity_type: 'artifact', content: wire(2), replace: true }));
    expect(screen.getByText('Document · version 2')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open Weekly report' }));
    expect(openArtifact).toHaveBeenCalledWith(ref(2));

    // The saved reply keeps the card.
    server.activeRuns = [];
    server.messages = [...server.messages, row('a_r1', 'assistant', 'The report is on your Canvas.', { run_id: 'r1', parts: { artifacts: [wire(2)] } })];
    runEvents(frame(4, 'finished', { outcome: { type: 'success' }, result: { reply_message_id: 'a_r1' } }));
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['chatThread'] });
    });
    await screen.findByText('The report is on your Canvas.');
    expect(screen.getAllByRole('button', { name: 'Open Weekly report' })).toHaveLength(1);
    expect(screen.getByText('Document · version 2')).toBeInTheDocument();
  });
});
