/**
 * Locks where a palette drop places the new node.
 *
 * The position must come from React Flow's `screenToFlowPosition`, which
 * accounts for the pane's offset, pan and zoom. It used to be the pointer
 * minus the drop target's bounding rect, so a drop on a panned or zoomed
 * canvas landed somewhere else, and a drop onto an existing node measured
 * that node's rect.
 */

import { describe, it, expect, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import type { DragEvent } from 'react';
import type { Node } from 'reactflow';
import { useDragAndDrop } from '../useDragAndDrop';
import { snapToGrid } from '../../utils/workflow';
import { theme } from '../../styles/theme';
import * as nodeSpecs from '../../lib/nodeSpec';
import * as browserAgents from '../../services/browserAgentApi';
import { useAppStore } from '../../store/useAppStore';

const existing: Node = { id: 'wf:start:1', type: 'start', position: { x: 0, y: 0 }, data: { label: 'Start' } };

function dropEvent(clientX: number, clientY: number) {
  return {
    preventDefault: vi.fn(),
    clientX,
    clientY,
    // A rect that would put the node far away if it were still used.
    target: { getBoundingClientRect: () => ({ left: 5000, top: 5000 }) },
    dataTransfer: { getData: () => JSON.stringify({ type: 'httpRequest', data: { url: '' } }) },
  } as unknown as DragEvent;
}

describe('useDragAndDrop.onDrop', () => {
  it('uses the atomic creation recipe declared by NodeSpec', async () => {
    const saveWorkflow = vi.fn().mockResolvedValue(true);
    const updateWorkflow = vi.fn();
    const adoptSavedOperations = vi.fn();
    const state = { currentWorkflow: { id: 'wf' }, saveWorkflow, updateWorkflow, adoptSavedOperations };
    const stateSpy = vi.spyOn(useAppStore, 'getState').mockReturnValue(state as unknown as ReturnType<typeof useAppStore.getState>);
    const specSpy = vi.spyOn(nodeSpecs, 'getCachedNodeSpec').mockReturnValue({
      type: 'fixtureAgent', displayName: 'Fixture', icon: '', group: ['agent'], version: 1,
      uiHints: { createsBrowserAgent: true },
    });
    const createSpy = vi.spyOn(browserAgents, 'createBrowserAgent').mockResolvedValue({ node_ids: {}, operations: [] });
    try {
      const setNodes = vi.fn();
      const setEdges = vi.fn();
      const saveNodeParameters = vi.fn();
      const { result } = renderHook(() => useDragAndDrop({
        nodes: [existing], edges: [], setNodes, setEdges, saveNodeParameters, workflowId: 'wf',
        screenToFlowPosition: ({ x, y }) => ({ x, y }),
      }));
      const event = dropEvent(700, 450);
      event.dataTransfer.getData = () => JSON.stringify({ type: 'fixtureAgent' });
      await act(async () => { await result.current.onDrop(event); });

      expect(saveWorkflow).toHaveBeenCalledOnce();
      expect(createSpy).toHaveBeenCalledOnce();
      expect(createSpy.mock.calls[0][0].workflow_id).toBe('wf');
      expect(adoptSavedOperations).toHaveBeenCalledWith('wf', []);
      expect(setNodes).toHaveBeenCalledOnce();
      expect(setEdges).toHaveBeenCalledOnce();
      expect(saveNodeParameters).not.toHaveBeenCalled();
    } finally {
      createSpy.mockRestore();
      specSpy.mockRestore();
      stateSpy.mockRestore();
    }
  });

  it('places the node at the canvas point under the pointer, honouring pan and zoom', async () => {
    // Pane at (100, 50), zoomed 2x, panned so canvas (500, 300) sits at the pane's corner.
    const screenToFlowPosition = vi.fn(({ x, y }: { x: number; y: number }) => ({
      x: (x - 100) / 2 + 500,
      y: (y - 50) / 2 + 300,
    }));
    const setNodes = vi.fn();
    const { result } = renderHook(() => useDragAndDrop({
      nodes: [existing],
      setNodes,
      saveNodeParameters: vi.fn().mockResolvedValue(true),
      workflowId: 'wf',
      screenToFlowPosition,
    }));

    await act(async () => {
      await result.current.onDrop(dropEvent(700, 450));
    });

    expect(screenToFlowPosition).toHaveBeenCalledWith({ x: 700, y: 450 });
    const added = (setNodes.mock.calls[0][0] as (nodes: Node[]) => Node[])([]);
    expect(added).toHaveLength(1);
    expect(added[0].position).toEqual(snapToGrid({
      x: 800 - theme.constants.dragOffset.x,
      y: 500 - theme.constants.dragOffset.y,
    }));
  });
});
