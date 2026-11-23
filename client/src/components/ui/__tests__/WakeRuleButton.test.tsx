/**
 * "Wake me when", the same button in Home's header and the editor's toolbar:
 * not there for an employee no app event starts; for one that is, it opens
 * on the saved rule, Save sends the new one and waits for a change, and an
 * empty rule clears it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const socket = vi.hoisted(() => ({ sendRequest: vi.fn(), isReady: true }));

// Only the socket is replaced: the employee schema reads the control
// normalizer from the same module.
vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => socket,
}));

import { WakeRuleButton } from '../WakeRuleButton';

const RULE = 'a customer asks about an order';
let employee: Record<string, unknown>;

function renderButton() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <WakeRuleButton workflowId="wf_1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  employee = { workflow_id: 'wf_1', name: 'Maya', wake_rule: RULE };
  socket.sendRequest.mockReset().mockImplementation(async (type: string, data: Record<string, unknown>) => {
    if (type === 'set_employee_wake_rule') {
      employee = { ...employee, wake_rule: data.wake_rule };
      return { success: true, employee };
    }
    if (type === 'get_employee') return { success: true, employee };
    return { success: true, employees: [employee] };
  });
});

describe('WakeRuleButton', () => {
  it('is not there for an employee no app event starts', async () => {
    employee = { ...employee, wake_rule: null };

    renderButton();

    await waitFor(() => expect(socket.sendRequest).toHaveBeenCalledWith('get_employee', { workflow_id: 'wf_1' }));
    expect(screen.queryByRole('button', { name: /wake/i })).toBeNull();
  });

  it('opens on the saved rule and saves the new one', async () => {
    const user = userEvent.setup();
    renderButton();

    await user.click(await screen.findByRole('button', { name: /wake/i }));
    const field = screen.getByLabelText('Wake them when');
    expect(field).toHaveValue(RULE);
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();

    await user.clear(field);
    await user.type(field, '  an invoice   is overdue ');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(socket.sendRequest).toHaveBeenCalledWith('set_employee_wake_rule', {
        workflow_id: 'wf_1',
        wake_rule: 'an invoice is overdue',
      }),
    );
  });

  it('clears the rule when it is left empty', async () => {
    const user = userEvent.setup();
    renderButton();

    await user.click(await screen.findByRole('button', { name: /wake/i }));
    await user.clear(screen.getByLabelText('Wake them when'));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(socket.sendRequest).toHaveBeenCalledWith('set_employee_wake_rule', { workflow_id: 'wf_1', wake_rule: '' }),
    );
  });
});
