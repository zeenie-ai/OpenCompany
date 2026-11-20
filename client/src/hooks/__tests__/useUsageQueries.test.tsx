import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const socket = vi.hoisted(() => ({ sendRequest: vi.fn(), isReady: true }));

vi.mock('@/contexts/WebSocketContext', () => ({
  useWebSocketActions: () => socket,
}));

import { useChatRunCostsQuery } from '../useUsageQueries';

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  socket.sendRequest.mockReset();
  socket.isReady = true;
});

describe('useChatRunCostsQuery', () => {
  it('reads the costs of one workflow by run', async () => {
    socket.sendRequest.mockResolvedValue({ success: true, runs: { r_1: 0.42, r_2: 0.004 } });

    const { result } = renderHook(() => useChatRunCostsQuery('wf'), { wrapper });

    await waitFor(() => expect(result.current.data).toEqual({ r_1: 0.42, r_2: 0.004 }));
    expect(socket.sendRequest).toHaveBeenCalledWith('get_chat_run_costs', { workflow_id: 'wf' });
  });

  it('has no costs when the reply carries none', async () => {
    socket.sendRequest.mockResolvedValue({ success: true });

    const { result } = renderHook(() => useChatRunCostsQuery('wf'), { wrapper });

    await waitFor(() => expect(result.current.data).toEqual({}));
  });

  it('fails when the server could not read them', async () => {
    socket.sendRequest.mockResolvedValue({ success: false, error: 'database down' });

    const { result } = renderHook(() => useChatRunCostsQuery('wf'), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it('asks nothing without a workflow', () => {
    renderHook(() => useChatRunCostsQuery(''), { wrapper });

    expect(socket.sendRequest).not.toHaveBeenCalled();
  });

  it('waits for the socket', () => {
    socket.isReady = false;

    renderHook(() => useChatRunCostsQuery('wf'), { wrapper });

    expect(socket.sendRequest).not.toHaveBeenCalled();
  });
});
