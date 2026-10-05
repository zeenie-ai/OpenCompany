import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const sendRequest = vi.fn();
vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest }),
}));
import { JobProgress } from '../JobProgress';
import { parseEmployee } from '../../data/schemas';

function show(state = 'delivery_needs_review') {
  const employee = parseEmployee({ workflow_id: '7', name: 'Maya', job_progress: {
    request_id: 'job', state, message: 'The send was interrupted. Check whether it arrived before trying again.',
  } })!;
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={cache}><JobProgress employee={employee} /></QueryClientProvider>);
}

beforeEach(() => sendRequest.mockReset());

describe('reviewed delivery progress', () => {
  it('requires an explicit outcome and never retries on mount', async () => {
    show();
    expect(screen.getByRole('button', { name: 'It arrived' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'It did not arrive; retry' })).toBeInTheDocument();
    expect(sendRequest).not.toHaveBeenCalled();
  });

  it('confirms arrival using only the saved job identity', async () => {
    sendRequest.mockResolvedValue({ success: true, message: 'Delivery confirmed.' });
    show();
    await userEvent.click(screen.getByRole('button', { name: 'It arrived' }));
    await screen.findByText('Delivery confirmed.');
    expect(sendRequest).toHaveBeenCalledWith('resolve_employee_delivery', {
      workflow_id: '7', job_id: 'job', decision: 'arrived', idempotency_key: expect.any(String),
    });
    expect(screen.queryByRole('button', { name: 'It arrived' })).not.toBeInTheDocument();
  });

  it('keeps the same retry identity after a response failure', async () => {
    sendRequest.mockRejectedValueOnce(new Error('Your connection was interrupted.'))
      .mockResolvedValueOnce({ success: true, message: 'Your decision is saved.' });
    show();
    await userEvent.click(screen.getByRole('button', { name: 'It did not arrive; retry' }));
    await screen.findByRole('alert');
    const first = sendRequest.mock.calls[0][1];
    await userEvent.click(screen.getByRole('button', { name: 'It did not arrive; retry' }));
    await waitFor(() => expect(sendRequest).toHaveBeenCalledTimes(2));
    expect(sendRequest.mock.calls[1][1]).toEqual(first);
  });

  it('explains ordinary progress without exposing delivery controls', () => {
    show('working');
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'It arrived' })).not.toBeInTheDocument();
    expect(sendRequest).not.toHaveBeenCalled();
  });
});
