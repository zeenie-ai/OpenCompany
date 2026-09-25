/**
 * The employee card's main button: Connect goes to the provider's connect
 * dialog, Pause sends the summary's revision, and the button says
 * "Pausing…" until the summary shows the pause (never flashing "Pause"
 * again in between). Watch live opens the Workspace on the employee.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const actions = {
  isReady: true,
  sendRequest: vi.fn(),
  addEventListener: () => () => {},
  pauseWorkflow: vi.fn(),
  resumeWorkflow: vi.fn(),
  startEmployee: vi.fn(),
};

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => actions,
}));

vi.mock('../../../app/useShellActions', () => ({ enterDev: vi.fn() }));

import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { enterDev } from '../../../app/useShellActions';
import { EMPLOYEES_QUERY_KEY } from '../data/employees';
import { parseEmployee, type EmployeeSummary } from '../data/schemas';
import { ThemeProvider } from '@/contexts/ThemeContext';
import { EmployeeView } from '../employee/EmployeeView';
import { useHomeStore } from '../state/homeStore';

function summary(patch: Record<string, unknown>, control: Record<string, unknown>): EmployeeSummary {
  return parseEmployee({
    workflow_id: 'w1',
    name: 'Maya',
    role: 'Receptionist',
    control: normalizeWorkflowControlStatus({ generation: 1, ...control }, 'w1'),
    ...patch,
  })!;
}

function renderCard(employee: EmployeeSummary, onConnect = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(EMPLOYEES_QUERY_KEY, [employee]);
  render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <EmployeeView workflowId="w1" onConnect={onConnect} />
      </QueryClientProvider>
    </ThemeProvider>,
  );
  return { client, onConnect };
}

beforeEach(() => {
  actions.pauseWorkflow.mockReset();
  actions.resumeWorkflow.mockReset();
  actions.startEmployee.mockReset();
  vi.mocked(enterDev).mockClear();
});

describe('EmployeeView', () => {
  it('connects the missing app through its provider', () => {
    const whatsapp = { app_id: 'whatsapp', provider_id: 'whatsapp', name: 'WhatsApp', connected: false, supported: true };
    const { onConnect } = renderCard(
      summary({ status: 'ready', apps: [whatsapp], missing_apps: [whatsapp] }, { state: 'never_started' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Connect WhatsApp' }));
    expect(onConnect).toHaveBeenCalledWith('whatsapp');
  });

  it('pauses with the summary’s revision and waits for the summary to catch up', async () => {
    let done!: (status: unknown) => void;
    actions.pauseWorkflow.mockReturnValue(new Promise((resolve) => (done = resolve)));
    const { client } = renderCard(summary({ status: 'working' }, { state: 'running', revision: 7 }));

    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    expect(actions.pauseWorkflow).toHaveBeenCalledWith('w1', 7);
    expect(screen.getByRole('button', { name: 'Pausing…' })).toBeDisabled();

    await act(async () => done(normalizeWorkflowControlStatus({ state: 'paused', revision: 8, generation: 1 }, 'w1')));
    // The summary still says working: keep the in-flight label.
    expect(screen.getByRole('button', { name: 'Pausing…' })).toBeInTheDocument();

    await act(async () => {
      client.setQueryData(EMPLOYEES_QUERY_KEY, [summary({ status: 'paused' }, { state: 'paused', revision: 8 })]);
    });
    expect(await screen.findByRole('button', { name: 'Resume' })).toBeEnabled();
  });

  it('opens the Workspace on this employee', () => {
    useHomeStore.setState({ workspaceOpen: false, workspaceFor: null });
    renderCard(summary({ status: 'working' }, { state: 'running' }));
    fireEvent.click(screen.getByRole('button', { name: 'Watch live' }));
    expect(useHomeStore.getState()).toMatchObject({ workspaceOpen: true, workspaceFor: 'w1' });
  });

  it('opens the workflow in the editor', () => {
    renderCard(summary({ status: 'working' }, { state: 'running' }));
    fireEvent.click(screen.getByRole('button', { name: 'Open workflow' }));
    expect(enterDev).toHaveBeenCalledWith({ workflowId: 'w1' });
  });

  it('says so when the employee is gone', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    client.setQueryData(EMPLOYEES_QUERY_KEY, []);
    actions.sendRequest.mockResolvedValue({ success: false, error: 'not_found' });
    render(
      <QueryClientProvider client={client}>
        <EmployeeView workflowId="gone" onConnect={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('This employee is no longer on the team.')).toBeInTheDocument();
  });
});
