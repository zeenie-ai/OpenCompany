/**
 * Turn on Talk is the card's one primary action while Talk is off (the
 * message box is closed then), so it is the invert button; it still asks
 * before turning Talk on.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest: vi.fn() }),
}));
import { TurnOnTalk } from '../TurnOnTalk';
import { parseEmployee } from '../../data/schemas';

describe('Turn on Talk', () => {
  it('is the invert button, and asks first', () => {
    const employee = parseEmployee({ workflow_id: '7', name: 'Maya', talk: { state: 'off', agent_node_id: null } })!;
    render(<QueryClientProvider client={new QueryClient()}><TurnOnTalk employee={employee} /></QueryClientProvider>);
    const button = screen.getByRole('button', { name: 'Turn on Talk' });
    expect(button).toHaveAttribute('data-variant', 'invert');
    expect(button).toHaveClass('h-9', 'rounded-row', 'px-4', 'font-semibold');
    expect(button.className).not.toMatch(/action-/);
    fireEvent.click(button);
    expect(screen.getByRole('alertdialog', { name: 'Turn on Talk for Maya?' })).toBeInTheDocument();
  });
});
