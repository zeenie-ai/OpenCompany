import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const socket = vi.hoisted(() => ({ sendRequest: vi.fn(), isReady: true }));

vi.mock('@/contexts/WebSocketContext', () => ({
  useWebSocketActions: () => socket,
}));

import { useChatRunCostsQuery, useWorkflowCostsQuery } from '../useUsageQueries';

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

describe('useWorkflowCostsQuery', () => {
  it('reads this month’s cost by employee', async () => {
    socket.sendRequest.mockResolvedValue({
      success: true,
      since: '2026-11-01',
      total_cost: 3.5,
      workflows: [
        { workflow_id: 'wf_1', name: 'Mira', total_cost: 3 },
        { workflow_id: null, name: null, total_cost: 0.5 },
      ],
    });

    const { result } = renderHook(() => useWorkflowCostsQuery(), { wrapper });

    await waitFor(() =>
      expect(result.current.data).toEqual({
        since: '2026-11-01',
        totalCost: 3.5,
        workflows: [
          { workflowId: 'wf_1', name: 'Mira', totalCost: 3 },
          { workflowId: null, name: null, totalCost: 0.5 },
        ],
      }),
    );
    expect(socket.sendRequest).toHaveBeenCalledWith('get_workflow_costs', {});
  });

  it('fails when the server could not read them', async () => {
    socket.sendRequest.mockResolvedValue({ success: false, error: 'database down' });

    const { result } = renderHook(() => useWorkflowCostsQuery(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it('fails on a reply that carries no costs', async () => {
    socket.sendRequest.mockResolvedValue({ success: true });

    const { result } = renderHook(() => useWorkflowCostsQuery(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it('waits for the socket', () => {
    socket.isReady = false;

    renderHook(() => useWorkflowCostsQuery(), { wrapper });

    expect(socket.sendRequest).not.toHaveBeenCalled();
  });
});
