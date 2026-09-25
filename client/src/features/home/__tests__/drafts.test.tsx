/**
 * Drafts on the employee card: Send and Discard take the draft away at once
 * and bring it back if the server refuses, an edit must fit the channel,
 * each click carries its own decision key, and a new draft says so.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));

vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { applyApprovalLifecycle, approvalsKey, type Approval } from '../approvals/data';
import { EMPLOYEES_QUERY_KEY } from '../data/employees';
import { DraftsSection } from '../employee/DraftsSection';
import { pillToast } from '../ui/pillToast';

const draft: Approval = {
  approval_id: 'a1',
  workflow_id: 'w1',
  node_id: 'n',
  status: 'pending',
  channel: 'WhatsApp',
  channel_label: 'WhatsApp',
  recipient: '447700900123',
  recipient_label: 'Priya',
  body: 'Yes! Saturday at 10 works.',
  context_excerpt: 'Can I book Saturday?',
  created_at: null,
  revision: 0,
  max_length: 40,
};

function renderSection(paused = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let decided = false;
  sendRequest.mockImplementation(async (type: string) => {
    if (type === 'decide_approval') {
      decided = true;
      return { success: true };
    }
    // Like the server: a decided draft is no longer pending.
    return { success: true, approvals: decided ? [] : [draft] };
  });
  render(
    <QueryClientProvider client={client}>
      <DraftsSection workflowId="w1" employeeName="Maya" paused={paused} />
    </QueryClientProvider>,
  );
  return client;
}

beforeEach(() => {
  sendRequest.mockReset();
  vi.mocked(pillToast).mockClear();
});

describe('DraftsSection', () => {
  it('shows the draft as it will go out', async () => {
    renderSection();
    expect(await screen.findByText('Yes! Saturday at 10 works.')).toBeInTheDocument();
    expect(screen.getByText('to Priya')).toBeInTheDocument();
    expect(screen.getByText(/Can I book Saturday/)).toBeInTheDocument();
  });

  it('sends with a fresh decision key and takes the draft away at once', async () => {
    const client = renderSection();
    fireEvent.click(await screen.findByRole('button', { name: 'Send' }));
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('decide_approval', expect.objectContaining({ approval_id: 'a1', decision: 'send' })));
    const call = sendRequest.mock.calls.find(([type]) => type === 'decide_approval')!;
    expect(call[1].decision_key).toEqual(expect.any(String));
    expect(call[1]).not.toHaveProperty('text');
    expect(client.getQueryData<Approval[]>(approvalsKey('w1'))?.length ?? 0).toBe(0);
  });

  it('puts the draft back when the server refuses', async () => {
    const client = renderSection();
    await screen.findByRole('button', { name: 'Discard' });
    sendRequest.mockImplementation(async (type: string) =>
      type === 'decide_approval' ? { success: false, error: 'expired' } : { success: true, approvals: [draft] },
    );
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    });
    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('That draft expired before it was sent.', { tone: 'error' }));
    expect(client.getQueryData<Approval[]>(approvalsKey('w1'))?.[0]?.approval_id).toBe('a1');
  });

  it('sends an edit only when it fits', async () => {
    renderSection();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    const box = screen.getByRole('textbox', { name: 'Edit the draft' });
    fireEvent.change(box, { target: { value: 'x'.repeat(41) } });
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
    fireEvent.change(box, { target: { value: '  Saturday at 11 instead?  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() =>
      expect(sendRequest).toHaveBeenCalledWith('decide_approval', expect.objectContaining({ text: 'Saturday at 11 instead?' })),
    );
  });

  it('says a paused employee sends on resume', async () => {
    renderSection(true);
    expect(await screen.findByText('Sends when you resume Maya.')).toBeInTheDocument();
  });
});

describe('applyApprovalLifecycle', () => {
  it('refetches the list and announces a new draft by the employee name', () => {
    const client = new QueryClient();
    client.setQueryData(EMPLOYEES_QUERY_KEY, [{ workflow_id: 'w1', name: 'Maya' }]);
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    applyApprovalLifecycle(client, { specversion: '1.0', type: 'com.opencompany.approval.requested', data: { workflow_id: 'w1', approval_id: 'a1' } });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: approvalsKey('w1') });
    expect(pillToast).toHaveBeenCalledWith('Maya has a draft for you to check', { tone: 'info' });
    applyApprovalLifecycle(client, { specversion: '1.0', type: 'com.opencompany.approval.decided', data: { workflow_id: 'w1' } });
    expect(pillToast).toHaveBeenCalledTimes(1);
  });
});
