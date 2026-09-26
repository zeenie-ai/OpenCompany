import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Node } from 'reactflow';

const mocks = vi.hoisted(() => ({
  workflow: { id: 'wf1' },
  hints: {} as Record<string, { isCanvasPanel?: boolean; isBrowserPanel?: boolean }>,
  listeners: new Set<(event: { query: { queryKey: string[] } }) => void>(),
}));
vi.mock('../../../store/useAppStore', () => ({ useAppStore: (select: (s: unknown) => unknown) => select({ currentWorkflow: mocks.workflow }) }));
vi.mock('../../../lib/nodeSpec', () => ({ resolveNodeDescription: (type: string) => ({ uiHints: mocks.hints[type] }) }));
vi.mock('../../../lib/queryClient', () => ({ queryClient: { getQueryCache: () => ({ subscribe: (fn: (event: { query: { queryKey: string[] } }) => void) => {
  mocks.listeners.add(fn); return () => mocks.listeners.delete(fn);
} }) } }));
vi.mock('../../../hooks/useCanvasBoard', () => ({ useCanvasBoardQuery: () => ({ data: { items: [] } }), useCanvasRemove: () => ({ mutate: vi.fn() }) }));
vi.mock('../../parameterPanel/canvas/CanvasContent', () => ({ default: () => <div>Saved canvas board</div> }));
vi.mock('../../browser/BrowserWorkspace', () => ({ default: ({ workflowId, nodes }: { workflowId: string; nodes: { node_id: string; label: string }[] }) => (
  <div data-testid="browser-view" data-workflow={workflowId}>{nodes.map((node) => <span key={node.node_id}>{node.label}</span>)}</div>
) }));

import CanvasDock from '../CanvasDock';
import { useCanvasDockStore } from '../../../stores/canvasDockStore';

const nodes: Node[] = [
  { id: 'board1', type: 'boardPlugin', position: { x: 0, y: 0 }, data: { label: 'Board' } },
  { id: 'web1', type: 'webPlugin', position: { x: 1, y: 0 }, data: { label: 'Research browser' } },
];

beforeEach(() => {
  mocks.hints = { boardPlugin: { isCanvasPanel: true }, webPlugin: { isBrowserPanel: true } };
  useCanvasDockStore.setState({ open: true, widthPx: 460, mode: 'node', tab: 'board', selectedNodeId: null, ephemeralItem: null });
});

describe('Developer workspace', () => {
  it('shares Browser, Canvas and Android tabs and the workflow-bound viewer', () => {
    render(<CanvasDock nodes={nodes} />);
    expect(screen.getByText('Saved canvas board')).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Browser' }), { button: 0, ctrlKey: false });
    expect(screen.getByText('Research browser')).toBeInTheDocument();
    expect(screen.getByTestId('browser-view')).toHaveAttribute('data-workflow', 'wf1');
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Android' }), { button: 0, ctrlKey: false });
    expect(screen.getByText('The Android mirror isn’t available yet')).toBeInTheDocument();
    expect(screen.queryByTestId('browser-view')).toBeNull();
  });

  it('discovers a browser after schema hydration without a graph edit', () => {
    mocks.hints.webPlugin = {};
    useCanvasDockStore.setState({ tab: 'browser' });
    render(<CanvasDock nodes={nodes} />);
    expect(screen.queryByText('Research browser')).toBeNull();
    act(() => {
      mocks.hints.webPlugin = { isBrowserPanel: true };
      for (const fn of mocks.listeners) fn({ query: { queryKey: ['nodeSpec'] } });
    });
    expect(screen.getByText('Research browser')).toBeInTheDocument();
  });

  it('unmounts the live viewer when closed', () => {
    useCanvasDockStore.setState({ tab: 'browser' });
    render(<CanvasDock nodes={nodes} />);
    fireEvent.click(screen.getByRole('button', { name: 'Close workspace' }));
    expect(screen.queryByTestId('browser-view')).toBeNull();
  });
});
