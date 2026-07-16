/**
 * Tests for OutputSection -- specifically the combineResults logic.
 *
 * Locks in:
 *   - executionResults are passed through to the output panel
 *   - nodeStatus from WebSocket is folded in (newest first) when not already present
 *   - Duplicate detection compares outputs via JSON.stringify
 *   - visible=false renders nothing
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/providers';

// Scaling-v2: OutputSection now wraps the new shadcn OutputPanel at
// `components/output/OutputPanel`, which uses TanStack Query + ThemeContext.
// renderWithProviders handles both -- and we still mock the panel itself so
// the test stays focused on combineResults logic.
const render = renderWithProviders;

// --- Mock the new OutputPanel (path changed in scaling-v2) ----------------

const capturedResults: any[][] = [];
vi.mock('../../output/OutputPanel', () => ({
  default: ({ results, onClear }: any) => {
    capturedResults.push(results);
    return (
      <div data-testid="node-output-panel">
        <span data-testid="result-count">{results.length}</span>
        {results.map((r: any, i: number) => (
          <div key={i} data-testid={`result-${i}`}>
            {r.success ? 'success' : 'error'}:{JSON.stringify(r.outputs)}
          </div>
        ))}
        <button data-testid="clear-btn" onClick={onClear}>clear</button>
      </div>
    );
  },
}));

vi.mock('../../../hooks/useAppTheme', () => ({
  useAppTheme: () => ({ colors: { backgroundPanel: '#fff' } }),
}));

const wsState: { nodeStatuses: Record<string, any> } = { nodeStatuses: {} };
vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => wsState,
}));

import OutputSection from '../OutputSection';
import userEvent from '@testing-library/user-event';


function setNodeStatus(map: Record<string, any>) {
  wsState.nodeStatuses = map;
}


beforeEach(() => {
  capturedResults.length = 0;
  setNodeStatus({});
});


describe('OutputSection', () => {
  it('returns null when visible=false', () => {
    const { container } = render(
      <OutputSection
        selectedNode={{ id: 'n-1', type: 'http' } as any}
        executionResults={[]}
        onClearResults={vi.fn()}
        visible={false}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('passes executionResults through when no WS status present', () => {
    const results = [
      {
        success: true,
        nodeId: 'n-1',
        nodeType: 'http',
        nodeName: 'HTTP',
        timestamp: 't1',
        executionTime: 10,
        outputs: { ok: true },
        nodeData: [[{ json: { ok: true } }]],
      },
    ];

    render(
      <OutputSection
        selectedNode={{ id: 'n-1', type: 'http' } as any}
        executionResults={results as any}
        onClearResults={vi.fn()}
      />,
    );

    expect(screen.getByTestId('result-count').textContent).toBe('1');
    expect(screen.getByTestId('result-0').textContent).toContain('success');
  });

  it('folds in nodeStatus from WebSocket as newest result when not duplicate', () => {
    setNodeStatus({
      'n-1': { status: 'success', data: { foo: 'bar' } },
    });

    render(
      <OutputSection
        selectedNode={{ id: 'n-1', type: 'http' } as any}
        executionResults={[]}
        onClearResults={vi.fn()}
      />,
    );

    expect(screen.getByTestId('result-count').textContent).toBe('1');
    expect(screen.getByTestId('result-0').textContent).toContain('success');
    expect(screen.getByTestId('result-0').textContent).toContain('"foo":"bar"');
  });

  it('puts the WebSocket result at the front of an existing results array', () => {
    setNodeStatus({
      'n-1': { status: 'success', data: { from: 'ws' } },
    });

    const local = [
      {
        success: true,
        nodeId: 'n-1',
        nodeType: 'http',
        nodeName: 'HTTP',
        timestamp: 't1',
        executionTime: 10,
        outputs: { from: 'local' },
        nodeData: [[{ json: { from: 'local' } }]],
      },
    ];

    render(
      <OutputSection
        selectedNode={{ id: 'n-1', type: 'http' } as any}
        executionResults={local as any}
        onClearResults={vi.fn()}
      />,
    );

    expect(screen.getByTestId('result-count').textContent).toBe('2');
    expect(screen.getByTestId('result-0').textContent).toContain('"from":"ws"');
    expect(screen.getByTestId('result-1').textContent).toContain('"from":"local"');
  });

  it('does NOT duplicate when WS result outputs match existing executionResults entry', () => {
    setNodeStatus({
      'n-1': { status: 'success', data: { same: 'output' } },
    });

    const existing = [
      {
        success: true,
        nodeId: 'n-1',
        nodeType: 'http',
        nodeName: 'HTTP',
        timestamp: 't1',
        executionTime: 10,
        outputs: { same: 'output' },
        nodeData: [[{ json: { same: 'output' } }]],
      },
    ];

    render(
      <OutputSection
        selectedNode={{ id: 'n-1', type: 'http' } as any}
        executionResults={existing as any}
        onClearResults={vi.fn()}
      />,
    );

    // No duplicate -- still 1 result
    expect(screen.getByTestId('result-count').textContent).toBe('1');
  });

  it('marks WS result as error when nodeStatus.status === "error"', () => {
    setNodeStatus({
      'n-1': { status: 'error', data: { error: 'boom' } },
    });

    render(
      <OutputSection
        selectedNode={{ id: 'n-1', type: 'http' } as any}
        executionResults={[]}
        onClearResults={vi.fn()}
      />,
    );

    expect(screen.getByTestId('result-0').textContent).toContain('error');
  });

  it('ignores WS status that is not success or error (e.g. running)', () => {
    setNodeStatus({
      'n-1': { status: 'running', data: { progress: 50 } },
    });

    render(
      <OutputSection
        selectedNode={{ id: 'n-1', type: 'http' } as any}
        executionResults={[]}
        onClearResults={vi.fn()}
      />,
    );

    expect(screen.getByTestId('result-count').textContent).toBe('0');
  });

  it('dedups by executionId when both sides carry one (correlation-id pattern)', () => {
    setNodeStatus({
      'n-1': {
        status: 'success',
        data: { ok: true, execution_id: 'exec-abc' },
      },
    });

    const existing = [
      {
        success: true,
        nodeId: 'n-1',
        nodeType: 'http',
        nodeName: 'HTTP',
        timestamp: 't1',
        executionTime: 10,
        outputs: { ok: true },
        nodeData: [[{ json: { ok: true } }]],
        executionId: 'exec-abc',
      },
    ];

    render(
      <OutputSection
        selectedNode={{ id: 'n-1', type: 'http' } as any}
        executionResults={existing as any}
        onClearResults={vi.fn()}
      />,
    );

    expect(screen.getByTestId('result-count').textContent).toBe('1');
  });

  it('keeps two distinct results when execution_ids differ even if outputs match', () => {
    setNodeStatus({
      'n-1': {
        status: 'success',
        data: { ok: true, execution_id: 'exec-second' },
      },
    });

    const existing = [
      {
        success: true,
        nodeId: 'n-1',
        nodeType: 'http',
        nodeName: 'HTTP',
        timestamp: 't1',
        executionTime: 10,
        outputs: { ok: true },
        nodeData: [[{ json: { ok: true } }]],
        executionId: 'exec-first',
      },
    ];

    render(
      <OutputSection
        selectedNode={{ id: 'n-1', type: 'http' } as any}
        executionResults={existing as any}
        onClearResults={vi.fn()}
      />,
    );

    // Two distinct runs with identical payloads -- correlation-id keeps
    // them separate where the old JSON-stringify dedup would collapse.
    expect(screen.getByTestId('result-count').textContent).toBe('2');
  });

  it('invokes onClearResults when the clear button is pressed', async () => {
    const onClear = vi.fn();
    render(
      <OutputSection
        selectedNode={{ id: 'n-1', type: 'http' } as any}
        executionResults={[]}
        onClearResults={onClear}
      />,
    );
    await userEvent.click(screen.getByTestId('clear-btn'));
    expect(onClear).toHaveBeenCalled();
  });
});
