import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { renderWithProviders as render } from '../../../test/providers';
import ContextPanel from '../ContextPanel';
import MemoryToolPanel from '../MemoryToolPanel';

const sendRequest = vi.fn();
const addEventListener = vi.fn(() => vi.fn());

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ sendRequest, addEventListener }),
}));

beforeEach(() => {
  sendRequest.mockReset();
});

describe('ContextPanel', () => {
  it('keeps job instance history read-only', async () => {
    sendRequest.mockResolvedValue({ context: {
      conversations: [], generation: 2, agent_node_id: 'worker', agent_instance_id: 'instance-12345678',
      message_count: 1, messages: [{ role: 'user', content: 'Independent job instructions' }],
    } });
    render(<ContextPanel nodeId="ctx-1" workflowId="wf-1" />);
    await screen.findByText('Independent job instructions');
    expect(screen.getByRole('button', { name: /Clear/i })).toBeDisabled();
    expect(screen.getByText('Instance 12345678')).toBeInTheDocument();
  });

  it('loads the live conversation through get_agent_context', async () => {
    sendRequest.mockImplementation(async (operation: string) => {
      if (operation === 'get_agent_context') {
        return {
          context: {
            conversations: [
              {
                workflow_id: 'wf-1',
                generation: 2,
                agent_node_id: 'agent-1',
                message_count: 3,
                updated_at: '2026-08-18T00:00:00Z',
              },
            ],
            generation: 2,
            agent_node_id: 'agent-1',
            updated_at: '2026-08-18T00:00:00Z',
            message_count: 3,
            messages: [
              { role: 'user', content: 'hello agent' },
              {
                role: 'assistant',
                content: '',
                tool_calls: [
                  {
                    id: 'call-1',
                    name: 'memory',
                    args: { operation: 'remember', content: 'launch Tuesday' },
                  },
                ],
              },
              {
                role: 'tool',
                name: 'memory',
                tool_call_id: 'call-1',
                content: '{"operation": "remember", "memory": {"id": "m1"}}',
              },
            ],
          },
        };
      }
      return {};
    });

    render(<ContextPanel nodeId="ctx-1" workflowId="wf-1" />);

    expect(await screen.findByText('hello agent')).toBeInTheDocument();
    // The panel shows the live context only — no generation selector or
    // generation label anywhere.
    expect(screen.queryByText(/Generation/)).not.toBeInTheDocument();
    // Tool calls render as structured entries, not raw dumped text.
    expect(screen.getAllByText('memory').length).toBeGreaterThan(0);
    // The JSON-string tool result routes through the tree viewer: its keys
    // render as tree nodes rather than one messy serialized string.
    expect(
      screen.queryByText('{"operation": "remember", "memory": {"id": "m1"}}'),
    ).not.toBeInTheDocument();
    expect(sendRequest).toHaveBeenCalledWith(
      'get_agent_context',
      expect.objectContaining({
        workflow_id: 'wf-1',
        context_node_id: 'ctx-1',
      }),
    );
  });

  it('invokes backend clearing without synthesizing local Context', async () => {
    sendRequest.mockImplementation(async (operation: string) => {
      if (operation === 'get_agent_context') {
        return {
          context: {
            conversations: [
              {
                workflow_id: 'wf-1',
                generation: 2,
                agent_node_id: 'agent-1',
                message_count: 1,
                updated_at: null,
              },
            ],
            generation: 2,
            agent_node_id: 'agent-1',
            updated_at: null,
            message_count: 1,
            messages: [{ role: 'user', content: 'hello' }],
          },
        };
      }
      if (operation === 'clear_agent_context') return { success: true };
      return {};
    });
    const user = userEvent.setup();
    render(<ContextPanel nodeId="ctx-1" workflowId="wf-1" />);

    await screen.findByText('hello');
    await user.click(screen.getByRole('button', { name: /Clear/i }));

    await waitFor(() =>
      expect(sendRequest).toHaveBeenCalledWith('clear_agent_context', {
        workflow_id: 'wf-1',
        context_node_id: 'ctx-1',
        generation: 2,
        agent_node_id: 'agent-1',
      }),
    );
  });

  it('offers a raw view of the stored wire messages', async () => {
    sendRequest.mockImplementation(async (operation: string) => {
      if (operation === 'get_agent_context') {
        return {
          context: {
            conversations: [
              {
                workflow_id: 'wf-1',
                generation: 2,
                agent_node_id: 'agent-1',
                message_count: 1,
                updated_at: null,
              },
            ],
            generation: 2,
            agent_node_id: 'agent-1',
            updated_at: null,
            message_count: 1,
            messages: [{ role: 'user', content: 'plain prose message' }],
          },
        };
      }
      return {};
    });
    const user = userEvent.setup();
    render(<ContextPanel nodeId="ctx-1" workflowId="wf-1" />);

    await screen.findByText('plain prose message');
    // Formatted view renders role cards (the chat-token surface class).
    expect(document.querySelector('.bg-node-agent-soft')).not.toBeNull();

    await user.click(screen.getByRole('tab', { name: 'Raw' }));

    // The formatted cards are gone; the stored wire dicts render verbatim
    // in the themed JSON tree (the message content becomes a tree value).
    expect(document.querySelector('.bg-node-agent-soft')).toBeNull();
    const treeValues = Array.from(
      document.querySelectorAll('.w-rjv-value'),
    ).map((node) => node.textContent);
    expect(treeValues).toContain('plain prose message');
  });

  it('shows the empty state when nothing is stored', async () => {
    sendRequest.mockImplementation(async (operation: string) => {
      if (operation === 'get_agent_context') {
        return { context: { conversations: [], messages: [] } };
      }
      return {};
    });
    render(<ContextPanel nodeId="ctx-1" workflowId="wf-1" />);

    expect(
      await screen.findByText('No stored conversation yet.'),
    ).toBeInTheDocument();
  });
});

describe('MemoryToolPanel', () => {
  it('lists and remembers items through the backend Memory operations', async () => {
    sendRequest.mockImplementation(async (operation: string) => {
      if (operation === 'list_memory_items') {
        return { items: [], indexing_state: 'lexical_ready' };
      }
      if (operation === 'remember_memory') {
        return {
          item: {
            id: 'mem-1',
            version: 1,
            content: 'The launch is Tuesday',
          },
        };
      }
      if (operation === 'get_memory_item') {
        return {
          item: {
            id: 'mem-1',
            version: 1,
            content: 'The launch is Tuesday',
          },
        };
      }
      return {};
    });
    const user = userEvent.setup();
    render(
      <MemoryToolPanel
        nodeId="memory-1"
        workflowId="wf-1"
        parameters={{ reset_policy: 'preserve' }}
        onParameterChange={vi.fn()}
      />,
    );

    await screen.findByText('No Memory items match this query.');
    await user.type(
      screen.getByPlaceholderText('Durable fact, preference, decision, or note'),
      'The launch is Tuesday',
    );
    await user.click(screen.getByRole('button', { name: 'Remember' }));

    await waitFor(() =>
      expect(sendRequest).toHaveBeenCalledWith(
        'remember_memory',
        expect.objectContaining({
          workflow_id: 'wf-1',
          memory_node_id: 'memory-1',
          content: 'The launch is Tuesday',
        }),
      ),
    );
  });
});
