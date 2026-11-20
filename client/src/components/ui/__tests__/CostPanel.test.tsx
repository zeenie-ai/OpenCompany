/**
 * The cost panel, shared by Home's Billing and Dev's Settings: this month's
 * cost by employee, a dash when it can't be read, and two limits (the spend
 * limit of a reply and the monthly budget of an employee), each with a Save
 * that sends its one setting and nothing else.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const socket = vi.hoisted(() => ({ sendRequest: vi.fn(), isReady: true }));

vi.mock('@/contexts/WebSocketContext', () => ({
  useWebSocketActions: () => socket,
}));

import { monthDay } from '@/lib/format';
import { CostPanel } from '../CostPanel';

const COSTS = {
  success: true,
  since: '2026-11-01',
  total_cost: 12.5,
  workflows: [
    { workflow_id: 'wf_1', name: 'Mira', total_cost: 9.25 },
    { workflow_id: 'wf_2', name: 'Theo', total_cost: 3 },
    { workflow_id: null, name: null, total_cost: 0.25 },
  ],
};

/** What the server answers, by command. A test replaces an entry. */
let replies: Record<string, unknown>;

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CostPanel />
    </QueryClientProvider>,
  );
}

function limitBox(): Promise<HTMLElement> {
  return screen.findByLabelText('Spend limit per reply');
}

function saveButton(): HTMLElement {
  return screen.getByRole('button', { name: 'Save limit' });
}

function budgetBox(): Promise<HTMLElement> {
  return screen.findByLabelText('Monthly budget per employee');
}

function saveBudgetButton(): HTMLElement {
  return screen.getByRole('button', { name: 'Save budget' });
}

function savedSettings(): unknown[] {
  return socket.sendRequest.mock.calls.filter(([type]) => type === 'save_user_settings').map(([, data]) => data.settings);
}

beforeEach(() => {
  replies = {
    get_workflow_costs: COSTS,
    get_user_settings: { settings: { chat_run_spend_limit_usd: 2, employee_monthly_budget_usd: 20 } },
    save_user_settings: { settings: {} },
  };
  socket.sendRequest.mockReset().mockImplementation(async (type: string) => replies[type]);
});

describe('CostPanel', () => {
  it('shows what this month cost and when the month began', async () => {
    renderPanel();

    expect(await screen.findByText('$12.50')).toBeInTheDocument();
    expect(screen.getByText(`Since ${monthDay('2026-11-01')}`)).toBeInTheDocument();
    expect(socket.sendRequest).toHaveBeenCalledWith('get_workflow_costs', {});
  });

  it('lists what each employee cost, and Other for calls with no employee', async () => {
    renderPanel();

    const rows = within(await screen.findByRole('list', { name: 'Cost by employee' })).getAllByRole('listitem');

    expect(rows.map((row) => row.textContent)).toEqual(['Mira$9.25', 'Theo$3.00', 'Other$0.25']);
  });

  it('shows a dash and no rows when the cost can’t be read', async () => {
    replies.get_workflow_costs = { success: false, error: 'database locked' };

    renderPanel();

    expect(await screen.findByText('—')).toBeInTheDocument();
    expect(screen.getByText('This month’s cost can’t be read right now.')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Cost by employee' })).not.toBeInTheDocument();
  });

  it('shows the saved limit in the box', async () => {
    renderPanel();

    expect(await limitBox()).toHaveValue(2);
  });

  it('shows no limit when the settings hold none', async () => {
    replies.get_user_settings = { settings: {} };

    renderPanel();

    expect(await limitBox()).toHaveValue(0);
  });

  it('says so when the limits can’t be read', async () => {
    socket.sendRequest.mockImplementation(async (type: string) => {
      if (type === 'get_user_settings') throw new Error('socket closed');
      return replies[type];
    });

    renderPanel();

    expect(await screen.findByText('The limits can’t be read right now.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Spend limit per reply')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Monthly budget per employee')).not.toBeInTheDocument();
  });

  it('keeps Save limit off until the limit changes', async () => {
    renderPanel();
    const box = await limitBox();

    expect(saveButton()).toBeDisabled();
    fireEvent.change(box, { target: { value: '5' } });
    expect(saveButton()).toBeEnabled();
  });

  it('saves the limit and nothing else', async () => {
    renderPanel();
    fireEvent.change(await limitBox(), { target: { value: '2.5' } });

    fireEvent.click(saveButton());

    await waitFor(() => expect(savedSettings()).toEqual([{ chat_run_spend_limit_usd: 2.5 }]));
  });

  it('reads an empty box as no limit', async () => {
    renderPanel();
    fireEvent.change(await limitBox(), { target: { value: '' } });

    fireEvent.click(saveButton());

    await waitFor(() => expect(savedSettings()).toEqual([{ chat_run_spend_limit_usd: 0 }]));
  });

  it('refuses a limit below zero', async () => {
    renderPanel();

    fireEvent.change(await limitBox(), { target: { value: '-1' } });

    expect(screen.getByRole('alert')).toHaveTextContent('Enter 0 or more.');
    expect(saveButton()).toBeDisabled();
  });

  it('turns Save limit off again once the limit is saved', async () => {
    renderPanel();
    const box = await limitBox();
    fireEvent.change(box, { target: { value: '2.5' } });

    fireEvent.click(saveButton());

    await waitFor(() => expect(saveButton()).toBeDisabled());
    expect(box).toHaveValue(2.5);
  });

  it('says so when the limit didn’t save, and keeps what was typed', async () => {
    replies.save_user_settings = { success: false, error: 'database locked' };
    renderPanel();
    const box = await limitBox();
    fireEvent.change(box, { target: { value: '5' } });

    fireEvent.click(saveButton());

    expect(await screen.findByRole('alert')).toHaveTextContent('The limit didn’t save. Try again.');
    expect(box).toHaveValue(5);
    expect(saveButton()).toBeEnabled();
  });

  it('shows the saved monthly budget in its box', async () => {
    renderPanel();

    expect(await budgetBox()).toHaveValue(20);
  });

  it('shows no budget when the settings hold none', async () => {
    replies.get_user_settings = { settings: {} };

    renderPanel();

    expect(await budgetBox()).toHaveValue(0);
  });

  it('saves the budget and nothing else', async () => {
    renderPanel();
    fireEvent.change(await budgetBox(), { target: { value: '50' } });

    fireEvent.click(saveBudgetButton());

    await waitFor(() => expect(savedSettings()).toEqual([{ employee_monthly_budget_usd: 50 }]));
  });

  it('refuses a budget below zero', async () => {
    renderPanel();

    fireEvent.change(await budgetBox(), { target: { value: '-5' } });

    expect(screen.getByRole('alert')).toHaveTextContent('Enter 0 or more.');
    expect(saveBudgetButton()).toBeDisabled();
  });
});
