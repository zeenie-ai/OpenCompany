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

  // Colour marks state or role (docs-internal/theme_system.md).
  it('marks a state with a dot beside neutral words, and keeps every button neutral', async () => {
    const failed = { ...process, name: 'worker', status: 'error', ports: [] };
    wsMock.sendRequest.mockResolvedValue({ success: true, processes: [process, failed], max_processes: 10 });
    const { container } = render(<ProcessManagerPanel workflowId="workflow-1" />);
    await screen.findByText('frontend');

    expect(screen.getByRole('heading', { name: 'Managed Processes' })).toHaveClass('font-display', 'text-md');

    // Running is the one dot that pulses; an error wants someone.
    const running = screen.getByText('running');
    expect(running).toHaveClass('text-fg-default');
    expect(running.querySelector('.home-pip')).toHaveClass('bg-status-working-dot');
    expect(running.querySelector('.home-pip')).toHaveAttribute('data-pip', 'on');
    const error = screen.getByText('error');
    expect(error).toHaveClass('text-fg-default');
    expect(error.querySelector('.home-pip')).toHaveClass('bg-status-attention-dot');
    expect(error.querySelector('.home-pip')).not.toHaveAttribute('data-pip');

    // The counts are ink, whatever they count.
    expect(screen.getByText('Errors').nextElementSibling).toHaveClass('text-fg-default');
    expect(container.querySelector('[class*="text-success"], [class*="text-destructive"]')).toBeNull();

    for (const name of ['Refresh', 'Inspect frontend', 'Stop frontend', 'Restart frontend']) {
      expect(screen.getByRole('button', { name })).toHaveAttribute('data-variant', 'quiet');
    }
    // A stopped or failed process has nothing to stop.
    expect(screen.queryByRole('button', { name: 'Stop worker' })).toBeNull();
  });

  it('opens a process in the editor’s modal, with its command and output on the code surface', async () => {
    render(<ProcessManagerPanel workflowId="workflow-1" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Inspect frontend' }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveClass('modal');
    expect(await screen.findByText('ready on 5173')).toHaveClass('font-mono', 'bg-[var(--code-bg)]');
    expect(screen.getByText('npm run dev -- --port 5173', { selector: 'pre' })).toBeInTheDocument();
  });
});
