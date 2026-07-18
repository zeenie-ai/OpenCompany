import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWithProviders as render } from '../../../test/providers';

const { wsMock } = vi.hoisted(() => ({ wsMock: { sendRequest: vi.fn() } }));
vi.mock('../../../contexts/WebSocketContext', () => ({ useWebSocket: () => wsMock }));

import ProcessManagerPanel from '../ProcessManagerPanel';

const process = {
  name: 'frontend', command: 'npm run dev -- --port 5173', pid: 4321,
  status: 'running', ports: [5173], started_at: new Date(Date.now() - 65_000).toISOString(),
  stdout_lines: 8, stderr_lines: 1, working_directory: 'D:/workspace',
};

describe('ProcessManagerPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    wsMock.sendRequest.mockImplementation((type: string) => {
      if (type === 'process_list') return Promise.resolve({ success: true, processes: [process], max_processes: 10 });
      if (type === 'process_get_output') return Promise.resolve({ success: true, lines: ['ready on 5173'] });
      return Promise.resolve({ success: true });
    });
  });

  it('renders live process identity, port, timing, and output details', async () => {
    render(<ProcessManagerPanel workflowId="workflow-1" />);
    expect(await screen.findByText('frontend')).toBeInTheDocument();
    expect(screen.getAllByText('5173').length).toBeGreaterThan(0);
    expect(screen.getByText(/1m [5-9]s/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect frontend' }));
    expect(await screen.findByText('ready on 5173')).toBeInTheDocument();
    expect(wsMock.sendRequest).toHaveBeenCalledWith('process_get_output', {
      workflow_id: 'workflow-1', name: 'frontend', stream: 'stdout', tail: 300,
    });
  });

  it('scopes lifecycle controls to the current workflow', async () => {
    render(<ProcessManagerPanel workflowId="workflow-1" />);
    await screen.findByText('frontend');
    fireEvent.click(screen.getByRole('button', { name: 'Stop frontend' }));
    await waitFor(() => expect(wsMock.sendRequest).toHaveBeenCalledWith('process_stop', {
      workflow_id: 'workflow-1', name: 'frontend',
    }));
  });
});
