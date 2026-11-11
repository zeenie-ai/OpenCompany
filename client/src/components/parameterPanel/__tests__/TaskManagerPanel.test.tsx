import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWithProviders as render } from '../../../test/providers';

const { wsMock, listeners } = vi.hoisted(() => ({
  listeners: new Map<string, (data: any) => void>(),
  wsMock: {
    sendRequest: vi.fn(),
    getTeamTaskTrace: vi.fn(),
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
  result: { summary: 'done' }, usage: { input_tokens: 70, output_tokens: 50, total_tokens: 120 },
  created_at: '2026-07-18 10:00:00', started_at: '2026-07-18 10:00:05',
  completed_at: '2026-07-18 10:01:10',
};

/** The server's answers, with the team's tasks as given. */
const serve = (tasks: unknown[]) => wsMock.sendRequest.mockImplementation((type: string) => {
  if (type === 'get_team_status') return Promise.resolve({ status: { team_id: 'team-1', execution_id: 'run-1', status: 'active', active_count: 2, max_concurrent_subagents: 3 } });
  if (type === 'get_team_tasks') return Promise.resolve({ tasks });
  return Promise.resolve({ success: true });
});

const renderPanel = () => render(<TaskManagerPanel nodeId="manager" workflowId="workflow-1" nodes={nodes} edges={edges} />);

/** Opens the task from its row; answers the modal. */
const openTask = async () => {
  await screen.findByText('Implement feature');
  fireEvent.click(screen.getByRole('button', { name: 'Inspect Implement feature' }));
  return screen.findByRole('dialog', { name: 'Task' });
};

beforeEach(() => {
  vi.clearAllMocks(); listeners.clear();
  wsMock.getTeamTaskTrace.mockReset();
  serve([task]);
});

describe('TaskManagerPanel', () => {
  it('derives team scope from the connected lead and renders the execution task table', async () => {
    renderPanel();
    expect(await screen.findByText('Implement feature')).toBeInTheDocument();
    expect(screen.getByText('2 / 3')).toBeInTheDocument();
    expect(screen.getByText('Coder')).toBeInTheDocument();
    expect(screen.getByText('Started')).toBeInTheDocument();
    expect(screen.getByText('Completed')).toBeInTheDocument();
    expect(screen.getByText('1m 5s')).toBeInTheDocument();
    expect(screen.getByText('70 in · 50 out')).toBeInTheDocument();
    expect(wsMock.sendRequest).toHaveBeenCalledWith('get_team_tasks', {
      workflow_id: 'workflow-1', team_lead_node_id: 'lead', include_history: true,
    });
  });

  it('is intrinsically scoped when opened directly on a team lead', async () => {
    render(<TaskManagerPanel nodeId="lead" workflowId="workflow-1" nodes={nodes} edges={edges} />);
    expect(await screen.findByText('Implement feature')).toBeInTheDocument();
    expect(wsMock.sendRequest).toHaveBeenCalledWith('get_team_tasks', {
      workflow_id: 'workflow-1', team_lead_node_id: 'lead', include_history: true,
    });
  });

  it('subscribes to team lifecycle events and refreshes scoped tasks', async () => {
    renderPanel();
    await screen.findByText('Implement feature');
    const callsBefore = wsMock.sendRequest.mock.calls.filter(([name]) => name === 'get_team_tasks').length;
    listeners.get('team.task.submitted')?.({ team_id: 'team-1' });
    await waitFor(() => expect(wsMock.sendRequest.mock.calls.filter(([name]) => name === 'get_team_tasks').length).toBeGreaterThan(callsBefore));
  });

  it('sends revision-scoped accept actions and never sends a browser-provided team id', async () => {
    renderPanel();
    const dialog = await openTask();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Accept' }));
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

describe('TaskManagerPanel in the theme', () => {
  it('marks a status with a dot beside neutral words', async () => {
    renderPanel();
    const row = (await screen.findByText('Implement feature')).closest('tr') as HTMLElement;
    const mark = within(row).getByText('submitted');
    expect(mark).toHaveClass('rounded-pill', 'border-border-default', 'text-fg-default');
    // Submitted work waits for the lead: the waiting dot, and no tint on the words.
    expect(mark.querySelector('.bg-status-waiting-dot')).not.toBeNull();
    expect(mark.className).not.toMatch(/success|warning|info|destructive|node-|action-|\/\d/);
  });

  it('gives a running task the one dot that pulses, and a done one a tick', async () => {
    serve([
      { ...task, id: 'a', title: 'Running one', status: 'running' },
      { ...task, id: 'b', title: 'Done one', status: 'accepted' },
      { ...task, id: 'c', title: 'Failed one', status: 'failed' },
    ]);
    renderPanel();
    const markOf = async (title: string, status: string) =>
      within((await screen.findByText(title)).closest('tr') as HTMLElement).getByText(status);
    const running = (await markOf('Running one', 'running')).querySelector('.home-pip');
    expect(running).toHaveClass('bg-status-working-dot');
    expect(running).toHaveAttribute('data-pip', 'on');
    const done = await markOf('Done one', 'accepted');
    expect(done.querySelector('svg')).toHaveClass('text-fg-default');
    expect(done.querySelector('.home-pip')).toBeNull();
    const failed = (await markOf('Failed one', 'failed')).querySelector('.home-pip');
    expect(failed).toHaveClass('bg-status-attention-dot');
    expect(failed).not.toHaveAttribute('data-pip');
  });

  it('keeps every button neutral but the one strong action', async () => {
    renderPanel();
    await screen.findByText('Implement feature');
    expect(screen.getByRole('button', { name: 'Finish team' })).toHaveAttribute('data-variant', 'invert');
    const refresh = screen.getByRole('button', { name: /Refresh/ });
    expect(refresh).toHaveAttribute('data-variant', 'quiet');
    expect(refresh).toHaveClass('border-border-default', 'text-fg-default');
    expect(screen.getByRole('button', { name: 'Inspect Implement feature' })).toHaveAttribute('data-variant', 'quiet');
  });

  it('counts each status on a chip that filters the table', async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('Implement feature');
    const chips = screen.getByRole('radiogroup', { name: 'Show tasks with status' });
    expect(within(chips).getByRole('radio', { name: 'submitted 1' })).toHaveAttribute('data-variant', 'chips');
    const queued = within(chips).getByRole('radio', { name: 'queued 0' });
    await user.click(queued);
    expect(queued).toBeChecked();
    expect(screen.queryByText('Implement feature')).not.toBeInTheDocument();
    expect(screen.getByText('No tasks match this view.')).toBeInTheDocument();
    // Picking it again shows every task.
    await user.click(queued);
    expect(screen.getByText('Implement feature')).toBeInTheDocument();
  });

  it('opens a task in the editor’s modal, with Accept as its one strong button', async () => {
    renderPanel();
    const dialog = await openTask();
    expect(dialog).toHaveClass('modal');
    expect(within(dialog).getByRole('heading', { name: 'Implement feature' })).toBeInTheDocument();
    expect(within(dialog).getByText('Coder · Revision 4 · Attempt 1 · 1m 5s')).toBeInTheDocument();
    expect(within(dialog).getByText('Build it')).toBeInTheDocument();
    expect(within(dialog).getByText(/"summary": "done"/)).toHaveClass('font-mono', 'bg-[var(--code-bg)]');
    // A field with nothing in it is left out.
    expect(within(dialog).queryByText('Acceptance criteria')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('Context')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('Attempt history')).not.toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Accept' })).toHaveAttribute('data-variant', 'invert');
    for (const name of ['Retry', 'Reassign']) {
      const button = within(dialog).getByRole('button', { name });
      expect(button).toHaveAttribute('data-variant', 'quiet');
      expect(button).toHaveClass('border-border-default', 'text-fg-default');
    }
  });

  it('shows a failed task’s error in the danger frame, and its attempts by teammate', async () => {
    serve([{
      ...task, status: 'failed', result: null, error: 'The model refused the request',
      acceptance_criteria: ['Tests pass', 'No lint errors'], context: { repo: 'app' },
      attempts: [{ id: 'attempt-1', attempt_number: 1, assignee_node_id: 'worker', status: 'failed', error: 'timed out' }],
    }]);
    renderPanel();
    const dialog = await openTask();
    expect(within(dialog).getByText('The model refused the request')).toHaveClass('border-danger-border', 'bg-danger-soft', 'text-destructive');
    expect(within(dialog).queryByText('Result')).not.toBeInTheDocument();
    expect(within(dialog).getByText('Tests pass').closest('ul')).toHaveClass('list-disc');
    expect(within(dialog).getByText(/"repo": "app"/)).toBeInTheDocument();
    // The teammate's name on the canvas, not its node id.
    expect(within(dialog).getByText('· Coder')).toBeInTheDocument();
    expect(within(dialog).getByText('timed out')).toHaveClass('text-destructive');
    // A failed task can only be tried again: no strong button.
    expect(within(dialog).queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Retry' })).toHaveAttribute('data-variant', 'quiet');
  });

  it('keeps an opened task current as the list refreshes', async () => {
    renderPanel();
    const dialog = await openTask();
    expect(within(dialog).getByRole('button', { name: 'Accept' })).toBeInTheDocument();
    serve([{ ...task, status: 'accepted', revision: 5 }]);
    listeners.get('team.task.accepted')?.({ team_id: 'team-1' });
    await waitFor(() => expect(within(dialog).queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument());
    expect(within(dialog).getByText('accepted')).toBeInTheDocument();
    expect(within(dialog).getByText('Coder · Revision 5 · Attempt 1 · 1m 5s')).toBeInTheDocument();
  });

  it('reads the Temporal trace when its tab opens', async () => {
    const user = userEvent.setup();
    wsMock.getTeamTaskTrace.mockResolvedValue({
      state: 'available', workflow_id: 'wf-child', run_id: 'run-child',
      events: [{ event_id: 1, event_type: 'ActivityTaskFailed', category: 'failure', name: 'execute_llm_step', summary: 'Attempt 2', failure: 'rate limited' }],
    });
    renderPanel();
    const dialog = await openTask();
    expect(wsMock.getTeamTaskTrace).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole('tab', { name: 'Temporal trace' }));
    expect(await within(dialog).findByText('execute_llm_step')).toBeInTheDocument();
    expect(wsMock.getTeamTaskTrace).toHaveBeenCalledWith({
      workflow_id: 'workflow-1', team_lead_node_id: 'lead', execution_id: 'run-1', task_id: 'task-1', limit: 50, detail: 'timeline',
    });
    expect(within(dialog).getByText('wf-child')).toHaveClass('font-mono');
    expect(within(dialog).getByText('rate limited')).toHaveClass('border-danger-border', 'bg-danger-soft', 'text-destructive');
  });

  it('says in plain words when a trace is no longer kept', async () => {
    const user = userEvent.setup();
    wsMock.getTeamTaskTrace.mockResolvedValue({ state: 'retention_expired', events: [] });
    renderPanel();
    const dialog = await openTask();
    await user.click(within(dialog).getByRole('tab', { name: 'Temporal trace' }));
    expect(await within(dialog).findByText('Retention expired')).toBeInTheDocument();
    expect(within(dialog).getByText(/Temporal history has expired/)).toBeInTheDocument();
  });
});
