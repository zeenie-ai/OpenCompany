/**
 * Server state for one Canvas node's board.
 *
 * Freshness is broadcast-driven: the `canvas_updated` case in
 * WebSocketContext invalidates the `['canvasBoard']` prefix and the query
 * refetches through the authorized `canvas_list` handler (identity-only
 * broadcast, ContextPanel posture).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  CANVAS_BOARD_QUERY_PREFIX,
  type CanvasItem,
  type CanvasListResponse,
  canvasBoardQueryKey,
} from '../lib/canvasBoard';
import { useWebSocketActions } from '../contexts/WebSocketContext';

export interface CanvasBoard {
  items: CanvasItem[];
  revision: number;
}

export function useCanvasBoardQuery(
  workflowId: string | null | undefined,
  nodeId: string | null | undefined,
) {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<CanvasBoard, Error>({
    queryKey: nodeId
      ? canvasBoardQueryKey(workflowId, nodeId)
      : [...CANVAS_BOARD_QUERY_PREFIX, 'idle'],
    enabled: isReady && !!workflowId && !!nodeId,
    queryFn: async () => {
      const response = await sendRequest<CanvasListResponse>('canvas_list', {
        workflow_id: workflowId,
        node_id: nodeId,
      });
      if (!response?.success) {
        throw new Error(response?.error || 'Failed to load canvas board');
      }
      return {
        items: response.items ?? [],
        revision: response.revision ?? 0,
      };
    },
  });
}

export function useCanvasRemove(
  workflowId: string | null | undefined,
  nodeId: string | null | undefined,
) {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (itemId: string) => {
      const response = await sendRequest<CanvasListResponse>('canvas_remove', {
        workflow_id: workflowId,
        node_id: nodeId,
        item_id: itemId,
      });
      if (!response?.success) {
        throw new Error(response?.error || 'Failed to remove item');
      }
      return response;
    },
    onSettled: () => {
      if (nodeId) {
        void queryClient.invalidateQueries({
          queryKey: canvasBoardQueryKey(workflowId, nodeId),
        });
      }
    },
  });
}

export function useCanvasClear(
  workflowId: string | null | undefined,
  nodeId: string | null | undefined,
) {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const response = await sendRequest<CanvasListResponse>('canvas_clear', {
        workflow_id: workflowId,
        node_id: nodeId,
      });
      if (!response?.success) {
        throw new Error(response?.error || 'Failed to clear the board');
      }
      return response;
    },
    onSettled: () => {
      if (nodeId) {
        void queryClient.invalidateQueries({
          queryKey: canvasBoardQueryKey(workflowId, nodeId),
        });
      }
    },
  });
}
