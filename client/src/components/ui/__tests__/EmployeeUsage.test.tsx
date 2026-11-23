/**
 * One employee's usage this month, shown by the Usage button in both modes
 * and by an employee's row in the cost panel: the cost panel's own cost and
 * tokens, the employee's finished tasks, a zero for an employee with nothing
 * booked, and a dash for a figure that can't be read.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const socket = vi.hoisted(() => ({ sendRequest: vi.fn(), isReady: true }));

vi.mock('@/contexts/WebSocketContext', () => ({
  useWebSocketActions: () => socket,
}));

import { monthDay } from '@/lib/format';
import { EmployeeUsage } from '../EmployeeUsage';

const COSTS = {
  success: true,
  since: '2026-11-01',
  total_cost: 3.25,
  workflows: [{ workflow_id: 'wf_1', name: 'Mira', total_cost: 3.25, total_tokens: 52000 }],
};

let replies: Record<string, unknown>;

async function renderUsage(workflowId: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <EmployeeUsage workflowId={workflowId} />
    </QueryClientProvider>,
  );
  return within(await screen.findByLabelText('Usage this month'));
}

/** The figure under a label: "Cost", "Tokens" or "Tasks done". */
function figure(usage: ReturnType<typeof within>, label: string): HTMLElement {
  return usage.getByText(label).nextElementSibling as HTMLElement;
}

beforeEach(() => {
  replies = {
    get_workflow_costs: COSTS,
    get_employee_usage: { success: true, tasks_this_month: 5, since: '2026-11-01' },
  };
  socket.sendRequest.mockReset().mockImplementation(async (type: string) => replies[type]);
});

describe('EmployeeUsage', () => {
  it('shows the employee’s cost, tokens and finished tasks this month', async () => {
    const usage = await renderUsage('wf_1');

    expect(await usage.findByText('$3.25')).toBeInTheDocument();
    expect(figure(usage, 'Tokens')).toHaveTextContent((52000).toLocaleString());
    expect(await usage.findByText('5')).toBeInTheDocument();
    expect(screen.getByText(`Since ${monthDay('2026-11-01')}. The cost is an estimate.`)).toBeInTheDocument();
    expect(socket.sendRequest).toHaveBeenCalledWith('get_employee_usage', { workflow_id: 'wf_1' });
  });

  it('shows zero for an employee with nothing booked this month', async () => {
    replies.get_employee_usage = { success: true, tasks_this_month: 0, since: '2026-11-01' };

    const usage = await renderUsage('wf_2');

    expect(await usage.findByText('$0.00')).toBeInTheDocument();
    expect(figure(usage, 'Tokens')).toHaveTextContent(/^0$/);
    await waitFor(() => expect(figure(usage, 'Tasks done')).toHaveTextContent(/^0$/));
  });

  it('shows a dash for a figure that can’t be read', async () => {
    replies.get_employee_usage = { success: false, error: 'not_found' };

    const usage = await renderUsage('wf_1');

    expect(await usage.findByText('$3.25')).toBeInTheDocument();
    expect(await usage.findByText('—')).toBeInTheDocument();
    expect(figure(usage, 'Tasks done')).toHaveTextContent('—');
  });
});
