import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWithProviders as render } from '../../../test/providers';

const { appState, wsMock } = vi.hoisted(() => ({
  appState: { currentWorkflow: { id: 'workflow-1' } as any },
  wsMock: {
    isReady: true,
    sendRequest: vi.fn(),
  },
}));

vi.mock('../../../store/useAppStore', () => ({
  useAppStore: <T,>(selector: (state: typeof appState) => T): T => selector(appState),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => wsMock,
}));

import TodoEditor from '../TodoEditor';


describe('TodoEditor node isolation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    wsMock.sendRequest.mockImplementation((type: string, payload: any) => {
      if (type !== 'get_todos') return Promise.resolve({ success: true });
      return Promise.resolve({
        todos: [{
          content: payload.node_id === 'todo-a' ? 'node A task' : 'node B task',
          status: 'pending',
        }],
      });
    });
  });

  it('drops an active node-A draft when the panel switches to node B', async () => {
    const view = render(<TodoEditor nodeId="todo-a" />);
    const inputA = await screen.findByDisplayValue('node A task');
    fireEvent.focus(inputA);
    fireEvent.change(inputA, { target: { value: 'unsaved A draft' } });
    expect(screen.getByDisplayValue('unsaved A draft')).toBeInTheDocument();

    view.rerender(<TodoEditor nodeId="todo-b" />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('node B task')).toBeInTheDocument();
      expect(screen.queryByDisplayValue('unsaved A draft')).not.toBeInTheDocument();
    });
  });
});
