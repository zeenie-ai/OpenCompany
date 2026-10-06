import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();
vi.mock('@/contexts/WebSocketContext', () => ({ useWebSocketActions: () => ({ sendRequest }) }));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));
import { EmployeeAccess } from '../employee/EmployeeAccess';

function show(pendingOnly = false) {
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={cache}><EmployeeAccess workflowId="7" name="Maya" pendingOnly={pendingOnly} /></QueryClientProvider>);
}

beforeEach(() => sendRequest.mockReset());

describe('employee app access', () => {
  it('explains the app and action and lets the owner allow it', async () => {
    sendRequest.mockResolvedValue({ success: true, access: [{ id: 'permission', app: 'Calendar', action: 'manage appointments', approved: false, revoked: false }] });
    show(true);
    expect(await screen.findByText('Allow Maya’s team to use Calendar to manage appointments?')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Allow' }));
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('decide_employee_access', { access_request_id: 'permission', allow: true }));
    expect(screen.getByRole('button', { name: 'Not now' })).toBeInTheDocument();
  });

  it('lets an owner remove previously approved access in settings', async () => {
    sendRequest.mockResolvedValue({ success: true, access: [{ id: 'permission', app: 'Calendar', action: 'manage appointments', approved: true, revoked: false }] });
    show();
    await userEvent.click(await screen.findByRole('button', { name: 'Remove access' }));
    expect(sendRequest).toHaveBeenCalledWith('decide_employee_access', { access_request_id: 'permission', allow: false });
  });

  it('declines access using its permission identity without replacing socket correlation', async () => {
    sendRequest.mockResolvedValue({ success: true, access: [{ id: 'permission', app: 'Calendar', approved: false, revoked: false }] });
    show(true);
    await userEvent.click(await screen.findByRole('button', { name: 'Not now' }));
    expect(sendRequest).toHaveBeenCalledWith('decide_employee_access', { access_request_id: 'permission', allow: false });
    const [, payload] = sendRequest.mock.calls.find(([type]) => type === 'decide_employee_access')!;
    expect(payload).not.toHaveProperty('request_id');
  });

  it('hides rejected requests from the conversational permission prompt', async () => {
    sendRequest.mockResolvedValue({ success: true, access: [{ id: 'permission', app: 'Calendar', approved: false, revoked: true }] });
    show(true);
    await waitFor(() => expect(sendRequest).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: 'Allow' })).not.toBeInTheDocument();
  });

  it('lets an owner allow removed access again in Settings', async () => {
    sendRequest.mockResolvedValue({ success: true, access: [{ id: 'permission', app: 'Calendar', approved: false, revoked: true }] });
    show();
    expect(await screen.findByText('Maya’s team no longer has access to Calendar.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Allow again' }));
    expect(sendRequest).toHaveBeenCalledWith('decide_employee_access', { access_request_id: 'permission', allow: true });
    expect(screen.queryByRole('button', { name: 'Not now' })).not.toBeInTheDocument();
  });

  it('shows a specialist permission once with its scoped apps', async () => {
    sendRequest.mockResolvedValue({ success: true, access: [
      { id: 'team', app: 'Research help', action: 'research evidence', approved: false, revoked: false, scope_apps: ['Web search', 'Calendar'] },
      { id: 'tool', app: 'Web search', approved: true, revoked: false, parent_grant_id: 'team' },
    ] });
    show();
    expect(await screen.findByText('Includes Web search, Calendar for research evidence.')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Allow' })).toHaveLength(1);
    expect(screen.queryByText('Maya’s team can use Web search to help with their assigned work.')).not.toBeInTheDocument();
  });

  it('does not prompt for linked child grants even if they are pending', async () => {
    sendRequest.mockResolvedValue({ success: true, access: [{ id: 'tool', app: 'Calendar', approved: false, revoked: false, parent_grant_id: 'team' }] });
    show(true);
    await waitFor(() => expect(sendRequest).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: 'Allow' })).not.toBeInTheDocument();
  });
});
