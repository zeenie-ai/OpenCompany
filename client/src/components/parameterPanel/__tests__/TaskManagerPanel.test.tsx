import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWithProviders as render } from '../../../test/providers';

const { wsMock, listeners } = vi.hoisted(() => ({
  listeners: new Map<string, (data: any) => void>(),
  wsMock: {
    sendRequest: vi.fn(),
    addEventListener: vi.fn((name: string, handler: (data: any) => void) => {
      listeners.set(name, handler);
      return vi.fn();
    }),
  },
}));

vi.mock('../../../contexts/WebSocketContext', () => ({ useWebSocket: () => wsMock }));

import TaskManagerPanel from '../TaskManagerPanel';

const nodes: any[] = [
  { id: 'manager', type: 'taskManager', data: { label: 'Tasks' }, position: { x: 0, y: 0 } },
  { id: 'lead', type: 'orchestrator_agent', data: { label: 'Lead' }, position: { x: 1, y: 1 } },
  { id: 'worker', type: 'coding_agent', data: { label: 'Coder' }, position: { x: 2, y: 2 } },
];
const edges: any[] = [
  { id: 'tool', source: 'manager', target: 'lead', targetHandle: 'input-tools' },
  { id: 'mate', source: 'worker', target: 'lead', targetHandle: 'input-teammates' },
];
const task = {
  id: 'task-1', title: 'Implement feature', mission: 'Build it', status: 'submitted',
  assigned_to: 'worker', assignee_label: 'Coder', revision: 4, current_attempt: 1,
  result: { summary: 'done' }, usage: { total_tokens: 120 },
};

describe('TaskManagerPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks(); listeners.clear();
    wsMock.sendRequest.mockImplementation((type: string) => {
      if (type === 'get_team_status') return Promise.resolve({ status: { team_id: 'team-1', execution_id: 'run-1', status: 'active', active_count: 2, max_concurrent_subagents: 3 } });
      if (type === 'get_team_tasks') return Promise.resolve({ tasks: [task] });
      return Promise.resolve({ success: true });
    });
  });

  it('derives team scope from the connected lead and renders the execution task table', async () => {
    render(<TaskManagerPanel nodeId="manager" workflowId="workflow-1" nodes={nodes} edges={edges} />);
    expect(await screen.findByText('Implement feature')).toBeInTheDocument();
    expect(screen.getByText('2 / 3')).toBeInTheDocument();
    expect(screen.getByText('Coder')).toBeInTheDocument();
    expect(wsMock.sendRequest).toHaveBeenCalledWith('get_team_tasks', {
      workflow_id: 'workflow-1', team_lead_node_id: 'lead',
    });
  });

  it('is intrinsically scoped when opened directly on a team lead', async () => {
    render(<TaskManagerPanel nodeId="lead" workflowId="workflow-1" nodes={nodes} edges={edges} />);
    expect(await screen.findByText('Implement feature')).toBeInTheDocument();
    expect(wsMock.sendRequest).toHaveBeenCalledWith('get_team_tasks', {
      workflow_id: 'workflow-1', team_lead_node_id: 'lead',
    });
  });

  it('subscribes to team lifecycle events and refreshes scoped tasks', async () => {
    render(<TaskManagerPanel nodeId="manager" workflowId="workflow-1" nodes={nodes} edges={edges} />);
    await screen.findByText('Implement feature');
    const callsBefore = wsMock.sendRequest.mock.calls.filter(([name]) => name === 'get_team_tasks').length;
    listeners.get('team.task.submitted')?.({ team_id: 'team-1' });
    await waitFor(() => expect(wsMock.sendRequest.mock.calls.filter(([name]) => name === 'get_team_tasks').length).toBeGreaterThan(callsBefore));
  });

  it('sends revision-scoped accept actions and never sends a browser-provided team id', async () => {
    render(<TaskManagerPanel nodeId="manager" workflowId="workflow-1" nodes={nodes} edges={edges} />);
    await screen.findByText('Implement feature');
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Implement feature' }));
    fireEvent.click(await screen.findByRole('button', { name: 'accept' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(wsMock.sendRequest).toHaveBeenCalledWith('manage_team_task', expect.objectContaining({
      operation: 'accept', workflow_id: 'workflow-1', team_lead_node_id: 'lead',
      execution_id: 'run-1', task_id: 'task-1', revision: 4,
    })));
    const mutation = wsMock.sendRequest.mock.calls.find(([name]) => name === 'manage_team_task')?.[1];
    expect(mutation).not.toHaveProperty('team_id');
  });

  it('shows a connection instruction when no lead owns the tool', () => {
    render(<TaskManagerPanel nodeId="manager" workflowId="workflow-1" nodes={nodes} edges={[]} />);
    expect(screen.getByText(/Connect Task Manager to a team lead/)).toBeInTheDocument();
  });
});
