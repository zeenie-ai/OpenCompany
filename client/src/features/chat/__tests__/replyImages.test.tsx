/**
 * A chart the employee names in a reply by its workspace path loads from the
 * chat's workflow's workspace; in the chat with no workflow open there is no
 * workspace, so the path waits behind the image button as written.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
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

import { resetChatRunStore } from '@/stores/chatRunStore';
import { ChatPane } from '../ChatPane';
import type { ChatHost } from '../host';
import { useComposerStore } from '../state/composerStore';
import '../markdown/ReplyMarkdown';

type Wire = Record<string, unknown>;

const row = (id: string, role: 'user' | 'assistant', text: string): Wire => ({
  id,
  role,
  text,
  message: text,
  timestamp: '2026-11-11T09:00:00Z',
  run_key: 'g1',
});

function renderChat(sessionId: string) {
  const host: ChatHost = {
    kind: 'home',
    sessionId,
    scope: 'all',
    persona: { name: 'Maya', colorRole: 'agent' },
    composer: 'send',
    notify: vi.fn(),
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChatPane host={host} />
    </QueryClientProvider>,
  );
}

describe('images in a reply', () => {
  beforeEach(() => {
    resetChatRunStore();
    useComposerStore.setState({ drafts: {} });
    sendRequest.mockReset().mockImplementation(async (kind: string, data: Wire) => {
      switch (kind) {
        case 'chat_subscribe':
          return { success: true, session_id: data.session_id, hub_epoch: 'e1', active_runs: [] };
        case 'get_chat_messages':
          return {
            success: true,
            messages: [
              row('m1', 'user', 'Chart the revenue'),
              row('a1', 'assistant', 'Here it is.\n\n![Revenue by quarter](charts/revenue.png)'),
            ],
            thread: { active_leaf_id: null, revision: 1 },
            active_runs: [],
          };
        default:
          return { success: true };
      }
    });
  });

  it('loads a chart named by its workspace path from the chat’s workflow', async () => {
    renderChat('w1');

    const image = await screen.findByRole('img', { name: 'Revenue by quarter' });
    expect(image).toHaveAttribute('src', '/api/workspace/w1/files/charts/revenue.png');
  });

  it('leaves the path as written in the chat with no workflow open', async () => {
    renderChat('default');

    expect(await screen.findByRole('button', { name: /^Show image from / })).toBeInTheDocument();
    expect(screen.queryByRole('img', { name: 'Revenue by quarter' })).toBeNull();
  });
});
