import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useState } from 'react';

const actions = { sendRequest: vi.fn(), isReady: true };
vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => actions,
}));

import { EmployeeWork } from '../EmployeeWork';
import { employeeDetailKey } from '../../data/employees';
import { parseEmployee, parseEmployeeDetail } from '../../data/schemas';
import { emptyRun } from '@/lib/agui/reduceRun';
import { resetChatRunStore, useChatRunStore } from '@/stores/chatRunStore';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';

let cache: QueryClient;
const now = new Date().toISOString();
const progress = {
  state: 'running', message: 'Research is gathering information for your request.',
  started_at: '2026-10-06T08:57:00.000Z', updated_at: '2026-10-06T08:58:30.000Z',
  steps: [
    { id: 'task-1', label: 'Gather information', member: 'Research', status: 'running' },
    { id: 'task-2', label: 'Check appointments', member: 'Appointments', status: 'done' },
    { id: 'task-3', label: 'Check the result', member: 'Maya', status: 'waiting' },
  ],
};

function show(extra: Record<string, unknown> = {}) {
  const employee = parseEmployee({ workflow_id: 'w1', name: 'Maya', status: 'working', has_team: true })!;
  const detail = parseEmployeeDetail({ ...employee, work_progress: progress, ...extra })!;
  cache.setQueryData(employeeDetailKey('w1'), detail);
  actions.sendRequest.mockResolvedValue({ success: true, employee: detail });
  return render(<QueryClientProvider client={cache}><EmployeeWork employee={employee} /></QueryClientProvider>);
}

beforeEach(() => {
  actions.isReady = true;
  actions.sendRequest.mockReset();
  resetChatRunStore();
  useNodeStatusStore.setState({ allStatuses: {} });
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  progress.started_at = new Date(Date.now() - 180_000).toISOString();
  progress.updated_at = new Date(Date.now() - 90_000).toISOString();
});

afterEach(() => { cache.clear(); vi.useRealTimers(); });

describe('employee live work', () => {
  it('reads detailed progress even when the employee already exists in the team list', async () => {
    show();
    await waitFor(() => expect(actions.sendRequest).toHaveBeenCalledWith('get_employee', { workflow_id: 'w1' }));
    const panel = screen.getByRole('region', { name: 'Employee work progress' });
    expect(panel).toHaveTextContent('Live work');
    expect(panel).toHaveTextContent('Research is gathering information');
    expect(panel).toHaveTextContent('Research · In progress');
    expect(panel).toHaveTextContent('Appointments · Finished');
    expect(panel).toHaveTextContent('1 of 3 steps finished');
    expect(panel).toHaveTextContent(/Elapsed 3m \d+s/);
    expect(panel).toHaveTextContent(/Last activity 1m \d+s ago/);
    expect(panel).toHaveTextContent('This is taking longer');
  });

  it('shows an approval wait without labelling it as a slow task or retrying delivery', () => {
    show({ work_progress: { ...progress, state: 'waiting', message: 'Their result is waiting for you to approve.' },
      job_progress: { request_id: 'job-1', state: 'waiting_for_approval', message: 'Their result is ready for you to check before sending.' } });
    expect(screen.getByRole('region', { name: 'Employee work progress' })).toHaveTextContent('waiting for you to approve');
    expect(screen.queryByText(/This is taking longer/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument();
    expect(actions.sendRequest.mock.calls.every(([type]) => type === 'get_employee')).toBe(true);
  });

  it('shows stale activity as last known when disconnected and preserves it through reconnect', async () => {
    actions.isReady = false;
    const view = show();
    expect(screen.getByText('Last known activity')).toBeInTheDocument();
    expect(screen.getByText(/Connection lost/)).toBeInTheDocument();
    expect(screen.queryByText(/This is taking longer/)).not.toBeInTheDocument();
    expect(actions.sendRequest).not.toHaveBeenCalled();
    actions.isReady = true;
    view.rerender(<QueryClientProvider client={cache}><EmployeeWork employee={parseEmployee({ workflow_id: 'w1', name: 'Maya' })!} /></QueryClientProvider>);
    await waitFor(() => expect(actions.sendRequest).toHaveBeenCalledTimes(1));
    expect(screen.getByText('Live work')).toBeInTheDocument();
    expect(screen.queryByText(/Connection lost/)).not.toBeInTheDocument();
  });

  it('retains the last update and offers refresh when progress cannot be loaded', async () => {
    show();
    await waitFor(() => expect(actions.sendRequest).toHaveBeenCalledTimes(1));
    actions.sendRequest.mockRejectedValue(new Error('internal execution error'));
    await act(async () => { await cache.invalidateQueries({ queryKey: employeeDetailKey('w1'), exact: true }); });
    expect(await screen.findByText(/Couldn’t refresh progress/)).toBeInTheDocument();
    // A neutral outline: a refresh borrows no action colour.
    const refresh = screen.getByRole('button', { name: 'Refresh progress' });
    expect(refresh).toHaveClass('border-border-default', 'text-fg-default');
    expect(refresh.className).not.toMatch(/action-/);
    expect(screen.queryByText('internal execution error')).not.toBeInTheDocument();
    expect(screen.getByText('Gather information')).toBeInTheDocument();
  });

  it('says progress is unavailable when there is none to show and it cannot be read', async () => {
    show({ work_progress: null });
    await waitFor(() => expect(actions.sendRequest).toHaveBeenCalledTimes(1));
    actions.sendRequest.mockRejectedValue(new Error('internal execution error'));
    await act(async () => { await cache.invalidateQueries({ queryKey: employeeDetailKey('w1'), exact: true }); });
    expect(await screen.findByText('Work progress is unavailable.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Refresh progress' }).className).not.toMatch(/action-/);
  });

  it('leaves a reply under way to the chat, and shows their own work again once it ends', () => {
    const run = { ...emptyRun('r1', 'w1'), state: 'running' as const, startedAt: now,
      steps: [{ stepId: 'search', name: 'Searching the web', state: 'running' as const }] };
    useChatRunStore.getState().upsertRun(run);
    show();
    expect(screen.queryByRole('region', { name: 'Employee work progress' })).not.toBeInTheDocument();
    expect(screen.queryByText('Searching the web')).not.toBeInTheDocument();
    act(() => useChatRunStore.getState().upsertRun({ ...run, seq: 2, state: 'finished' }));
    expect(screen.getByRole('region', { name: 'Employee work progress' })).toHaveTextContent('Research is gathering information');
  });

  it('keeps employee progress isolated when switching conversations', async () => {
    const a = parseEmployee({ workflow_id: 'w1', name: 'Maya' })!;
    const b = parseEmployee({ workflow_id: 'w2', name: 'Arjun' })!;
    cache.setQueryData(employeeDetailKey('w1'), parseEmployeeDetail({ ...a, work_progress: progress }));
    actions.sendRequest.mockImplementation(async (_type, data) => ({ success: true, employee: { ...(data.workflow_id === 'w1' ? a : b), work_progress: data.workflow_id === 'w1' ? progress : null } }));
    function Switch() {
      const [employee, setEmployee] = useState(a);
      return <><button onClick={() => setEmployee(b)}>Switch employee</button><EmployeeWork key={employee.workflow_id} employee={employee} /></>;
    }
    render(<QueryClientProvider client={cache}><Switch /></QueryClientProvider>);
    expect(screen.getByText('Gather information')).toBeInTheDocument();
    act(() => screen.getByRole('button', { name: 'Switch employee' }).click());
    await waitFor(() => expect(actions.sendRequest).toHaveBeenCalledWith('get_employee', { workflow_id: 'w2' }));
    expect(screen.queryByText('Gather information')).not.toBeInTheDocument();
  });

  it('shows no earlier finished job while a new reply is under way', () => {
    useChatRunStore.getState().upsertRun({ ...emptyRun('new-reply', 'w1'), state: 'running', startedAt: now,
      steps: [{ stepId: 'new-search', name: 'Searching for today’s news', state: 'running' }] });
    show({ work_progress: { ...progress, state: 'done', message: 'The work is finished.' } });
    expect(screen.queryByRole('region', { name: 'Employee work progress' })).not.toBeInTheDocument();
    expect(screen.queryByText('The work is finished.')).not.toBeInTheDocument();
    expect(screen.queryByText('Preparing your reply.')).not.toBeInTheDocument();
    expect(screen.queryByText('Searching for today’s news')).not.toBeInTheDocument();
  });

  it('offers refresh for an unavailable snapshot without leaking backend errors', () => {
    show({ work_progress: { state: 'unavailable', message: 'Work progress could not be loaded. Try refreshing.' } });
    expect(screen.getByRole('button', { name: 'Refresh progress' })).toHaveClass('border-border-default', 'text-fg-default');
    expect(screen.queryByText(/This is taking longer/)).not.toBeInTheDocument();
  });
});
