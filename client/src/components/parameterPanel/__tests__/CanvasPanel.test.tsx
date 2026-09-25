import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWithProviders as render } from '../../../test/providers';
import type { CanvasItem } from '../../../lib/canvasBoard';

const { wsMock } = vi.hoisted(() => ({
  wsMock: { sendRequest: vi.fn(), isReady: true },
}));
vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => wsMock,
  useWebSocketActions: () => wsMock,
}));

import CanvasPanel from '../CanvasPanel';

const noteItem = (id: string, content: string): CanvasItem => ({
  id,
  kind: 'note',
  title: null,
  ref: null,
  url: null,
  content,
  language: null,
  source: 'agent',
  created_at: '2026-08-18T10:00:00+00:00',
});

const board = (items: CanvasItem[]) => ({
  success: true,
  items,
  revision: items.length,
});

describe('CanvasPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('lists the board through the authorized canvas_list handler', async () => {
    wsMock.sendRequest.mockResolvedValue(board([noteItem('a', 'hello board')]));
    render(<CanvasPanel nodeId="canvas-1" workflowId="wf-1" />);

    expect(await screen.findByText('hello board')).toBeInTheDocument();
    expect(wsMock.sendRequest).toHaveBeenCalledWith('canvas_list', {
      workflow_id: 'wf-1',
      node_id: 'canvas-1',
    });
  });

  it('sends canvas_remove for the active item', async () => {
    wsMock.sendRequest.mockImplementation(async (type: string) =>
      type === 'canvas_list'
        ? board([noteItem('a', 'keep'), noteItem('b', 'drop')])
        : { success: true, revision: 3 },
    );
    render(<CanvasPanel nodeId="canvas-1" workflowId="wf-1" />);
    await screen.findByText('drop');

    fireEvent.click(screen.getByRole('button', { name: 'Remove item' }));
    await waitFor(() =>
      expect(wsMock.sendRequest).toHaveBeenCalledWith('canvas_remove', {
        workflow_id: 'wf-1',
        node_id: 'canvas-1',
        item_id: 'b',
      }),
    );
  });

  it('sends canvas_clear from the header action', async () => {
    wsMock.sendRequest.mockImplementation(async (type: string) =>
      type === 'canvas_list'
        ? board([noteItem('a', 'something')])
        : { success: true, revision: 2 },
    );
    render(<CanvasPanel nodeId="canvas-1" workflowId="wf-1" />);
    await screen.findByText('something');

    fireEvent.click(screen.getByRole('button', { name: /Clear/ }));
    await waitFor(() =>
      expect(wsMock.sendRequest).toHaveBeenCalledWith('canvas_clear', {
        workflow_id: 'wf-1',
        node_id: 'canvas-1',
      }),
    );
  });

  it('never calls the backend without a workflow, and says why', async () => {
    render(<CanvasPanel nodeId="canvas-1" />);
    expect(
      await screen.findByText(/Save this workflow to start collecting/),
    ).toBeInTheDocument();
    expect(wsMock.sendRequest).not.toHaveBeenCalled();
  });

  it('surfaces a denied listing instead of an empty board', async () => {
    wsMock.sendRequest.mockResolvedValue({
      success: false,
      error: 'Canvas node does not belong to the requested workflow',
    });
    render(<CanvasPanel nodeId="canvas-1" workflowId="wf-1" />);
    expect(
      await screen.findByText(/does not belong to the requested workflow/),
    ).toBeInTheDocument();
  });
});
