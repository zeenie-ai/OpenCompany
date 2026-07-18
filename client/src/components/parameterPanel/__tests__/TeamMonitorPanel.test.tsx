import { screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWithProviders as render } from '../../../test/providers';

const wsMock = vi.hoisted(() => ({ sendRequest: vi.fn(), addEventListener: vi.fn(() => vi.fn()) }));
vi.mock('../../../contexts/WebSocketContext', () => ({ useWebSocket: () => wsMock }));

import TeamMonitorPanel from '../TeamMonitorPanel';

const nodes: any[] = [
  { id: 'monitor', type: 'teamMonitor', data: { label: 'Monitor' }, position: { x: 0, y: 0 } },
  { id: 'lead', type: 'orchestrator_agent', data: { label: 'Lead' }, position: { x: 1, y: 0 } },
  { id: 'coder', type: 'coding_agent', data: { label: 'Coder' }, position: { x: 2, y: 0 } },
];
const edges: any[] = [
  { id: 'monitor-edge', source: 'lead', target: 'monitor' },
  { id: 'team-edge', source: 'coder', target: 'lead', targetHandle: 'input-teammates' },
];

describe('TeamMonitorPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    wsMock.sendRequest.mockImplementation((type: string) => {
      if (type === 'get_team_status') return Promise.resolve({ status: { status: 'active', members: [{ agent_node_id: 'coder', agent_type: 'coding_agent', label: 'Coder', status: 'working' }] } });
      return Promise.resolve({ tasks: [{ id: 'task-1', title: 'Build API', status: 'running', assigned_to: 'coder' }] });
    });
  });

  it('renders connected teammates and current execution tasks in one middle panel', async () => {
    render(<TeamMonitorPanel nodeId="monitor" workflowId="workflow-1" nodes={nodes} edges={edges} />);
    expect(await screen.findByText('Build API')).toBeInTheDocument();
    expect(screen.getByText('Coder')).toBeInTheDocument();
    expect(screen.getByText('working')).toBeInTheDocument();
    expect(wsMock.sendRequest).toHaveBeenCalledWith('get_team_status', { workflow_id: 'workflow-1', team_lead_node_id: 'lead' });
  });

  it('shows graph teammates even before a persisted execution exists', async () => {
    wsMock.sendRequest.mockResolvedValueOnce({ status: {} }).mockResolvedValueOnce({ tasks: [] });
    render(<TeamMonitorPanel nodeId="monitor" workflowId="workflow-1" nodes={nodes} edges={edges} />);
    expect(await screen.findByText('Coder')).toBeInTheDocument();
    expect(screen.getByText('connected')).toBeInTheDocument();
  });

  it('requires a team-lead connection', async () => {
    render(<TeamMonitorPanel nodeId="monitor" workflowId="workflow-1" nodes={nodes} edges={[]} />);
    expect(screen.getByText(/Connect an Orchestrator Agent or AI Employee/)).toBeInTheDocument();
    await waitFor(() => expect(wsMock.sendRequest).not.toHaveBeenCalled());
  });
});
