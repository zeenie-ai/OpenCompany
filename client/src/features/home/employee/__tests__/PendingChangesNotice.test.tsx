/**
 * The saved-changes notice above the message box: Apply, and Stop work and
 * apply while something waits, are neutral outlines (as Pause is), and Stop
 * work and apply still interrupts the current work.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const sendRequest = vi.fn();
vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest }),
}));
vi.mock('../../ui/pillToast', () => ({ pillToast: vi.fn() }));
import { WORKFLOW_CONTROL_REQUEST_TIMEOUT } from '@/contexts/WebSocketContext';
import { PendingChangesNotice } from '../PendingChangesNotice';
import { parseEmployee } from '../../data/schemas';

describe('saved changes notice', () => {
  it('offers Apply and Stop work and apply as neutral outlines', async () => {
    sendRequest.mockResolvedValue({ success: true });
    const employee = parseEmployee({ workflow_id: '7', name: 'Maya', pending_changes: true, pending_approvals: 1 })!;
    const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={cache}><PendingChangesNotice employee={employee} /></QueryClientProvider>);
    for (const name of ['Apply', 'Stop work and apply']) {
      const button = screen.getByRole('button', { name });
      expect(button).toHaveAttribute('data-variant', 'quiet');
      expect(button).toHaveClass('h-9', 'rounded-row', 'border-border-strong', 'px-4', 'font-semibold', 'text-fg-default');
      expect(button.className).not.toMatch(/action-/);
    }
    fireEvent.click(screen.getByRole('button', { name: 'Stop work and apply' }));
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith(
      'apply_employee_changes',
      { workflow_id: '7', idempotency_key: expect.any(String), stop_work: true },
      WORKFLOW_CONTROL_REQUEST_TIMEOUT,
    ));
  });
});
