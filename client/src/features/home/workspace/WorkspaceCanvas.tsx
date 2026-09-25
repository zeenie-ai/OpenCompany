/**
 * The Workspace's Canvas tab: one employee's Canvas board, through the
 * renderer the editor's Canvas panel and dock use (CanvasPanel.tsx is the
 * template). The board refreshes on the `canvas_updated` broadcast, like
 * everywhere else it shows. Loaded lazily by WorkspaceDock.
 */

import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import CanvasContent from '@/components/parameterPanel/canvas/CanvasContent';
import { useCanvasBoardQuery, useCanvasRemove } from '@/hooks/useCanvasBoard';
import { pillToast } from '../ui/pillToast';

export default function WorkspaceCanvas({ workflowId, nodeId, name }: { workflowId: string; nodeId: string; name: string }) {
  const board = useCanvasBoardQuery(workflowId, nodeId);
  const remove = useCanvasRemove(workflowId, nodeId);

  if (board.isPending) return <Skeleton className="flex-1 rounded-card" />;
  if (board.isError) {
    return (
      <div className="m-auto flex flex-col items-center gap-3 p-6 text-center">
        <p className="m-0 text-sm text-fg-muted">Couldn’t load the canvas.</p>
        <Button variant="quiet" onClick={() => void board.refetch()} className="border-border-default text-fg-default">
          Try again
        </Button>
      </div>
    );
  }
  return (
    <CanvasContent
      items={board.data.items}
      workflowId={workflowId}
      emptyHint={`Nothing here yet. When ${name} finishes something you’ll want to see, it shows up here.`}
      onRemove={(itemId) => remove.mutate(itemId, { onError: () => pillToast('Couldn’t remove that. Try again.', { tone: 'error' }) })}
    />
  );
}
