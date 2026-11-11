/**
 * Parameter-panel host for the Canvas node (uiHints.isCanvasPanel).
 *
 * Gallery-pattern full-height panel: the shared head (NodePanel) over the
 * shared CanvasContent. Server state rides useCanvasBoardQuery; freshness is
 * broadcast-driven (`canvas_updated` -> ['canvasBoard'] invalidation in
 * WebSocketContext).
 */

import React from 'react';
import { Eraser, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  useCanvasBoardQuery,
  useCanvasClear,
  useCanvasRemove,
} from '../../hooks/useCanvasBoard';
import CanvasContent from './canvas/CanvasContent';
import { NodePanel } from './NodePanel';

interface Props {
  nodeId: string;
  workflowId?: string | null;
}

const CanvasPanel: React.FC<Props> = ({ nodeId, workflowId }) => {
  const board = useCanvasBoardQuery(workflowId, nodeId);
  const removeItem = useCanvasRemove(workflowId, nodeId);
  const clearBoard = useCanvasClear(workflowId, nodeId);

  const items = board.data?.items ?? [];

  return (
    <NodePanel
      title="Canvas"
      description="What agents and workflow runs put here. Items are references: removing one never deletes the file behind it."
      actions={(
        <>
          {items.length > 0 && (
            <span
              title={`${items.length} ${items.length === 1 ? 'item' : 'items'}`}
              className="rounded-md border border-border-default px-1.75 py-0.5 font-mono text-2xs font-medium text-fg-muted"
            >
              {items.length}
            </span>
          )}
          <Button
            type="button"
            variant="quiet"
            className="border-border-default text-fg-default"
            disabled={items.length === 0 || clearBoard.isPending}
            onClick={() =>
              clearBoard.mutate(undefined, {
                onError: (error) => toast.error(error.message),
              })
            }
          >
            <Eraser /> Clear
          </Button>
        </>
      )}
    >
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-4">
        {!workflowId ? (
          <p className="py-8 text-center text-sm text-fg-muted">
            Save this workflow to start collecting Canvas content.
          </p>
        ) : board.isLoading ? (
          <div className="flex flex-1 items-center justify-center">
            <RefreshCw aria-hidden className="size-4 animate-spin text-fg-muted" />
          </div>
        ) : board.isError ? (
          <p className="py-8 text-center text-sm text-fg-muted">
            {board.error.message}
          </p>
        ) : (
          <CanvasContent
            items={items}
            workflowId={workflowId}
            nodeId={nodeId}
            onRemove={(itemId) =>
              removeItem.mutate(itemId, {
                onError: (error) => toast.error(error.message),
              })
            }
          />
        )}
      </div>
    </NodePanel>
  );
};

export default CanvasPanel;
