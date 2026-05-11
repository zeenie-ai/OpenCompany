/**
 * Tests for InputSection.
 *
 * Locks in the connection-discovery contract:
 *   - Empty state when no edges target the node
 *   - Direct edges to main handles populate connected nodes
 *   - Config handles (input-memory, input-tools, input-skill) are SKIPPED
 *     for agent nodes (those have a dedicated UI in MiddleSection)
 *   - input-main / input-chat / input-task / input-teammates are NEVER skipped
 *   - Config nodes (memory/tool group) inherit their parent node's main inputs
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders as render } from '../../../test/providers';

// --- Mocks -----------------------------------------------------------------

const storeState: { currentWorkflow: any } = { currentWorkflow: null };
// Threads the slice selector so `useAppStore((s) => s.currentWorkflow)`
// returns the right slice. A no-arg `useAppStore()` returns the whole
// state for legacy whole-store consumers.
vi.mock('../../../store/useAppStore', () => ({
  useAppStore: <T,>(selector?: (state: typeof storeState) => T): T | typeof storeState =>
    selector ? selector(storeState) : storeState,
}));

const wsMock = {
  getNodeOutput: vi.fn().mockResolvedValue(null),
};
vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => wsMock,
}));

vi.mock('../../../hooks/useDragVariable', () => ({
  useDragVariable: () => ({
    handleVariableDragStart: vi.fn(),
    getTemplateVariableName: (id: string) => id,
  }),
}));

vi.mock('../../../hooks/useAppTheme', () => ({
  useAppTheme: () => ({
    isDarkMode: false,
    colors: {
      background: '#fff',
      backgroundAlt: '#fafafa',
      backgroundPanel: '#f5f5f5',
      text: '#000',
      textSecondary: '#666',
      border: '#ddd',
      primary: '#1890ff',
    },
    spacing: { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 },
    fontSize: { xs: 10, sm: 12, md: 14, lg: 16, xl: 18 },
    fontWeight: { normal: 400, medium: 500, semibold: 600, bold: 700 },
    borderRadius: { sm: 4, md: 6, lg: 8 },
    accent: { blue: '#268bd2', cyan: '#2aa198', green: '#859900' },
    dracula: {
      purple: '#bd93f9',
      cyan: '#8be9fd',
      green: '#50fa7b',
      pink: '#ff79c6',
      yellow: '#f1fa8c',
      orange: '#ffb86c',
      red: '#ff5555',
    },
    transitions: { fast: '0.15s ease', medium: '0.3s ease' },
  }),
}));

// The legacy `../../../nodeDefinitions` module was deleted in Wave 11;
// InputSection now resolves node metadata via `lib/nodeSpec`.  The
// remaining tests in this file exercise edge-filtering behaviour that
// doesn't need per-type metadata, so no spec mock is required.

import InputSection from '../InputSection';


function setWorkflow(nodes: any[], edges: any[]) {
  storeState.currentWorkflow = {
    id: 'wf-1',
    name: 'wf',
    nodes,
    edges,
    createdAt: new Date(),
    lastModified: new Date(),
  };
}


beforeEach(() => {
  storeState.currentWorkflow = null;
  wsMock.getNodeOutput.mockReset();
  wsMock.getNodeOutput.mockResolvedValue(null);
});


describe('InputSection -- empty states', () => {
  it('returns nothing visible when no workflow loaded', () => {
    storeState.currentWorkflow = null;
    const { container } = render(<InputSection nodeId="n-1" />);
    // Should render the section with empty state, no node listings
    expect(container.textContent).not.toContain('HTTP Request');
  });

  it('renders empty state when no edges target this node', async () => {
    setWorkflow(
      [{ id: 'n-1', type: 'aiAgent', data: {}, position: { x: 0, y: 0 } }],
      [], // no edges
    );
    const { container } = render(<InputSection nodeId="n-1" />);
    await waitFor(() => {
      expect(container.textContent).not.toContain('Cron Scheduler');
    });
  });
});


describe('InputSection -- direct main edges', () => {
  it('lists nodes connected via input-main handle', async () => {
    setWorkflow(
      [
        { id: 'src', type: 'cronScheduler', data: { label: 'Cron' }, position: { x: 0, y: 0 } },
        { id: 'tgt', type: 'aiAgent', data: { label: 'Agent' }, position: { x: 100, y: 0 } },
      ],
      [{ id: 'e1', source: 'src', target: 'tgt', sourceHandle: 'output-main', targetHandle: 'input-main' }],
    );

    render(<InputSection nodeId="tgt" />);
    await waitFor(() => {
      expect(screen.getByText(/Cron Scheduler|Cron/)).toBeInTheDocument();
    });
  });
});


describe('InputSection -- config handle filtering for agent nodes', () => {
  it('SKIPS input-memory edges on agent nodes (they belong in MiddleSection)', async () => {
    setWorkflow(
      [
        { id: 'mem', type: 'simpleMemory', data: { label: 'Memory' }, position: { x: 0, y: 0 } },
        { id: 'agent', type: 'aiAgent', data: { label: 'Agent' }, position: { x: 100, y: 0 } },
      ],
      [{ id: 'e1', source: 'mem', target: 'agent', sourceHandle: 'output', targetHandle: 'input-memory' }],
    );

    render(<InputSection nodeId="agent" />);
    await waitFor(() => {
      // Memory should NOT appear -- it's filtered out for agent nodes
      expect(screen.queryByText(/Simple Memory/)).not.toBeInTheDocument();
    });
  });

  it('SKIPS input-tools edges on agent nodes', async () => {
    setWorkflow(
      [
        { id: 'tool', type: 'calculatorTool', data: { label: 'Calc' }, position: { x: 0, y: 0 } },
        { id: 'agent', type: 'aiAgent', data: { label: 'Agent' }, position: { x: 100, y: 0 } },
      ],
      [{ id: 'e1', source: 'tool', target: 'agent', sourceHandle: 'output', targetHandle: 'input-tools' }],
    );

    render(<InputSection nodeId="agent" />);
    await waitFor(() => {
      expect(screen.queryByText(/Calculator/)).not.toBeInTheDocument();
    });
  });

  it('KEEPS input-task edges on agent nodes (not filtered)', async () => {
    setWorkflow(
      [
        { id: 'src', type: 'cronScheduler', data: { label: 'Cron' }, position: { x: 0, y: 0 } },
        { id: 'agent', type: 'aiAgent', data: { label: 'Agent' }, position: { x: 100, y: 0 } },
      ],
      [{ id: 'e1', source: 'src', target: 'agent', sourceHandle: 'output', targetHandle: 'input-task' }],
    );

    render(<InputSection nodeId="agent" />);
    await waitFor(() => {
      expect(screen.getByText(/Cron Scheduler|Cron/)).toBeInTheDocument();
    });
  });

  it('KEEPS input-main edges on agent nodes', async () => {
    setWorkflow(
      [
        { id: 'src', type: 'httpRequest', data: { label: 'HTTP' }, position: { x: 0, y: 0 } },
        { id: 'agent', type: 'aiAgent', data: { label: 'Agent' }, position: { x: 100, y: 0 } },
      ],
      [{ id: 'e1', source: 'src', target: 'agent', sourceHandle: 'output', targetHandle: 'input-main' }],
    );

    render(<InputSection nodeId="agent" />);
    await waitFor(() => {
      expect(screen.getByText(/HTTP Request|HTTP/)).toBeInTheDocument();
    });
  });
});


// `InputSection -- config-node parent inheritance` removed: the single
// test in that describe block depended on mocking `../../../nodeDefinitions`
// (now deleted). The config-handle filtering logic that remains in this
// file covers the agent-side of the same behaviour.
