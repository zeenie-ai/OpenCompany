import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const socket = vi.hoisted(() => {
  const listeners = new Map<string, (data: unknown) => void>();
  return {
    sendRequest: vi.fn(),
    isReady: true,
    listeners,
    addEventListener: (type: string, handler: (data: unknown) => void) => {
      listeners.set(type, handler);
      return () => listeners.delete(type);
    },
  };
});

vi.mock('@/contexts/WebSocketContext', () => ({
  useWebSocketActions: () => socket,
}));

import { invalidateUsage, useChatRunCostsQuery, useEmployeeUsageQuery, useWorkflowCostsQuery } from '../useUsageQueries';

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

  it('reads the costs again while this employee’s team works', async () => {
    socket.sendRequest.mockResolvedValue({ success: true, runs: { r_1: 0.42 } });
    const { result } = renderHook(() => useChatRunCostsQuery('wf'), { wrapper });
    await waitFor(() => expect(result.current.data).toEqual({ r_1: 0.42 }));
    socket.sendRequest.mockResolvedValue({ success: true, runs: { r_1: 1.5 } });

    act(() => socket.listeners.get('agent.instance.updated')?.({ workflow_id: 'wf' }));

    await waitFor(() => expect(result.current.data).toEqual({ r_1: 1.5 }));
  });

  it('leaves the costs alone while another employee’s team works', async () => {
    socket.sendRequest.mockResolvedValue({ success: true, runs: { r_1: 0.42 } });
    const { result } = renderHook(() => useChatRunCostsQuery('wf'), { wrapper });
    await waitFor(() => expect(result.current.data).toEqual({ r_1: 0.42 }));

    act(() => socket.listeners.get('agent.instance.updated')?.({ workflow_id: 'other' }));

    expect(socket.sendRequest).toHaveBeenCalledTimes(1);
  });
});

describe('useWorkflowCostsQuery', () => {
  it('reads this month’s cost and tokens by employee', async () => {
    socket.sendRequest.mockResolvedValue({
      success: true,
      since: '2026-11-01',
      total_cost: 3.5,
      workflows: [
        { workflow_id: 'wf_1', name: 'Mira', total_cost: 3, total_tokens: 120000 },
        { workflow_id: null, name: null, total_cost: 0.5, total_tokens: 0 },
      ],
    });

    const { result } = renderHook(() => useWorkflowCostsQuery(), { wrapper });

    await waitFor(() =>
      expect(result.current.data).toEqual({
        since: '2026-11-01',
        totalCost: 3.5,
        workflows: [
          { workflowId: 'wf_1', name: 'Mira', totalCost: 3, totalTokens: 120000 },
          { workflowId: null, name: null, totalCost: 0.5, totalTokens: 0 },
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

describe('useEmployeeUsageQuery', () => {
  it('reads the whole team’s tasks this month', async () => {
    socket.sendRequest.mockResolvedValue({ success: true, tasks_this_month: 12, since: '2026-11-01' });

    const { result } = renderHook(() => useEmployeeUsageQuery(), { wrapper });

    await waitFor(() => expect(result.current.data).toEqual({ tasksThisMonth: 12, since: '2026-11-01' }));
    expect(socket.sendRequest).toHaveBeenCalledWith('get_employee_usage', {});
  });

  it('reads one employee’s tasks this month', async () => {
    socket.sendRequest.mockResolvedValue({ success: true, tasks_this_month: 3, since: '2026-11-01' });

    const { result } = renderHook(() => useEmployeeUsageQuery('wf_1'), { wrapper });

    await waitFor(() => expect(result.current.data).toEqual({ tasksThisMonth: 3, since: '2026-11-01' }));
    expect(socket.sendRequest).toHaveBeenCalledWith('get_employee_usage', { workflow_id: 'wf_1' });
  });

  it('fails when the server could not count them', async () => {
    socket.sendRequest.mockResolvedValue({ success: false, error: 'not_found' });

    const { result } = renderHook(() => useEmployeeUsageQuery('wf_1'), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe('invalidateUsage', () => {
  it('reads the costs and the task counts again, once for a burst', async () => {
    vi.useFakeTimers();
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');

    invalidateUsage(client);
    invalidateUsage(client);
    await vi.advanceTimersByTimeAsync(300);

    expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([['workflowCosts'], ['employeeUsage']]);
    vi.useRealTimers();
  });
});
