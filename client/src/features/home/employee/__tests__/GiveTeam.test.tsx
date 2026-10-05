import { act, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';
const sendRequest = vi.fn();
vi.mock('@/contexts/WebSocketContext', () => ({ useWebSocketActions: () => ({ sendRequest }) }));
import { GiveTeam } from '../GiveTeam';

beforeEach(() => sendRequest.mockReset());

function show(available = true) {
  render(<QueryClientProvider client={new QueryClient()}><GiveTeam workflowId="81" available={available} /></QueryClientProvider>);
}

it('shows an ordinary-language review before asking the server to apply a team', async () => {
  sendRequest.mockResolvedValueOnce({ success: true, review_id: 'review-1', explanation: 'Maya will ask helpers and check their work.', team: [{ responsibility: 'Checks your calendar' }] });
  show();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Give them a team' })));
  expect(sendRequest).toHaveBeenCalledWith('plan_employee_team', { workflow_id: '81' });
  expect(screen.getByText('Checks your calendar')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Not now' })).toBeInTheDocument();
  expect(sendRequest).toHaveBeenCalledTimes(1);
  sendRequest.mockResolvedValueOnce({ success: true, activation_state: 'waiting' });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Give them a team' })));
  expect(sendRequest).toHaveBeenLastCalledWith('give_employee_team', { review_id: 'review-1' });
  expect(screen.getByRole('status')).toHaveTextContent('finish their current work');
});

it('does not show conversion when its independent rollout is unavailable', () => {
  show(false);
  expect(screen.queryByRole('button', { name: 'Give them a team' })).not.toBeInTheDocument();
});

it('Not now closes the review without changing the employee', async () => {
  sendRequest.mockResolvedValueOnce({ success: true, review_id: 'review-1', explanation: 'Checks work before replying', team: [] });
  show();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Give them a team' })));
  fireEvent.click(screen.getByRole('button', { name: 'Not now' }));
  expect(sendRequest).toHaveBeenCalledTimes(1);
});
