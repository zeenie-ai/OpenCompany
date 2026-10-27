/**
 * Home's attention line (header/AttentionLine): nothing while no item is
 * open; one warning line while work keeps retrying; gone after the
 * `attention_lifecycle` broadcast and the refetch it starts.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();
const listeners = new Map<string, Set<(data: unknown) => void>>();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({
    sendRequest,
    isReady: true,
    addEventListener: (type: string, listener: (data: unknown) => void) => {
      const set = listeners.get(type) ?? new Set();
      set.add(listener);
      listeners.set(type, set);
      return () => set.delete(listener);
    },
  }),
}));

import { AttentionLine } from '../header/AttentionLine';

let items: unknown[] = [];

function renderLine() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AttentionLine />
    </QueryClientProvider>,
  );
}

function emit(type: string, data: unknown) {
  act(() => {
    for (const listener of listeners.get(type) ?? []) listener(data);
  });
}

const listCalls = () => sendRequest.mock.calls.filter(([type]) => type === 'list_attention_items');

beforeEach(() => {
  listeners.clear();
  items = [];
  sendRequest.mockReset().mockImplementation(async (type: string) =>
    type === 'list_attention_items' ? { success: true, items } : { success: true },
  );
});

describe('the attention line on Home', () => {
  it('shows nothing while nothing is open', async () => {
    renderLine();
    await waitFor(() => expect(listCalls()).toHaveLength(1));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows one line while work keeps retrying, and clears after the broadcast', async () => {
    items = [
      { id: 'stuck_retry:wf:a', kind: 'stuck_retry', count: 3 },
      { id: 'stuck_retry:wf:b', kind: 'stuck_retry', count: 1 },
    ];
    renderLine();
    expect(await screen.findByRole('alert')).toHaveTextContent('2 tasks keep retrying and may be stuck.');

    items = [];
    emit('attention_lifecycle', { specversion: '1.0', type: 'com.opencompany.attention.resolved', data: { kind: 'stuck_retry' } });

    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    expect(listCalls()).toHaveLength(2);
  });
});
