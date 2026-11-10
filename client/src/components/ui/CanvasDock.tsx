/**
 * The editor's Workspace: the dock Home shows, for the open workflow. The
 * dock itself is shared: the shell (components/workspace/WorkspaceDockShell:
 * the sliding panel whose left edge drags) and what it shows
 * (features/home/workspace/WorkspaceView: the row of tabs with the
 * employee's status, the Browser, Canvas and Mobile views, the floating
 * status pill, Take over and Hand back). Every workflow is an employee, so
 * the open one's summary gives the status.
 *
 * This file is what the editor adds:
 * - its nodes come from the canvas as it is now (a Browser, Canvas or phone
 *   node shows before a save), with a picker when there are several Canvas
 *   nodes;
 * - the Canvas tab doubles as the click-to-preview surface for workspace
 *   files (canvasDockStore `ephemeral` mode, with Back to the board);
 * - it opens when content is pushed (canvas_updated -> notifyPushed) or an
 *   agent asks for help in its browser;
 * - Dev mode's extras in the Browser and phone views (WorkspaceView `dev`),
 *   and the note about a slow model at its foot.
 * The workflow's Start, Stop and Resume are in the toolbar, so the row has
 * no main action.
 */

import React, { useCallback, useMemo, useState, useSyncExternalStore } from 'react';
import { Undo2 } from 'lucide-react';
import type { Node } from 'reactflow';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useEmployeeDetailQuery, useEmployeeLifecycle } from '../../features/home/data/employees';
import { WorkspaceView } from '../../features/home/workspace/WorkspaceView';
import {
  useCanvasBoardQuery,
  useCanvasRemove,
} from '../../hooks/useCanvasBoard';
import { resolveNodeDescription } from '../../lib/nodeSpec';
import { queryClient } from '../../lib/queryClient';
import { useAppStore } from '../../store/useAppStore';
import { useCanvasDockStore } from '../../stores/canvasDockStore';
import CanvasContent from '../parameterPanel/canvas/CanvasContent';
import { workspaceNodes } from '../workspace/descriptors';
import { dockWidth, useDockPushes } from '../workspace/dockSize';
import type { Notify } from '../workspace/surface';
import { WorkspaceDockShell } from '../workspace/WorkspaceDockShell';
import { LlmWaitNote } from './LlmWaitNote';

interface CanvasDockProps {
  nodes: Node[];
  flushCanvas?: () => void;
  /** What the rest of the editor's row keeps while the dock pushes the
   *  canvas aside, as a CSS length: the panels on show and the canvas. */
  pageKeeps?: string;
}

const isCanvasNode = (node: Node): boolean =>
  resolveNodeDescription(node.type || '')?.uiHints?.isCanvasPanel === true;

const nodeLabel = (node: Node): string =>
  (node.data?.label as string | undefined) || node.type || node.id;

/** The editor says what happened in its toasts. */
const notify: Notify = (message, tone) => {
  if (tone === 'error') toast.error(message);
  else toast.success(message);
};

const CanvasDock: React.FC<CanvasDockProps> = ({ nodes, flushCanvas, pageKeeps }) => {
  const open = useCanvasDockStore((s) => s.open);
  const tab = useCanvasDockStore((s) => s.tab);
  const setTab = useCanvasDockStore((s) => s.setTab);
  const [wide, setWide] = useState(false);
  const widthPx = useCanvasDockStore((s) => s.widthPx);
  const mode = useCanvasDockStore((s) => s.mode);
  const selectedNodeId = useCanvasDockStore((s) => s.selectedNodeId);
  const ephemeralItem = useCanvasDockStore((s) => s.ephemeralItem);
  const followMode = useCanvasDockStore((s) => s.followMode);
  const setFollowMode = useCanvasDockStore((s) => s.setFollowMode);
  const setWidth = useCanvasDockStore((s) => s.setWidth);
  const close = useCanvasDockStore((s) => s.close);
  const selectNode = useCanvasDockStore((s) => s.selectNode);
  const backToNode = useCanvasDockStore((s) => s.backToNode);
  const focus = useCanvasDockStore((s) => s.focus);

  const workflowId = useAppStore((s) => s.currentWorkflow?.id);
  const pushes = useDockPushes();

  // Every workflow is an employee: the open one's summary is the dock's
  // status, pill and Take over. Read while the dock is open, and again when
  // the server says they changed (Home's own listener is not mounted here).
  useEmployeeLifecycle();
  const summary = useEmployeeDetailQuery(open && workflowId ? workflowId : null);
  // None for a workflow not saved yet (no id, or the server knows none);
  // otherwise not known until the server answers, also while a read fails.
  const employee = workflowId ? summary.data : null;

  // Schema hydration can finish after the graph mounts. Subscribe to hints
  // so newly available Browser/Canvas definitions appear without a graph edit.
  const hintsVersion = useSyncExternalStore(
    (notifyChange) => queryClient.getQueryCache().subscribe((event) => {
      if (event.query.queryKey[0] === 'nodeSpec') notifyChange();
    }),
    () => nodes.map((node) => {
      const hints = resolveNodeDescription(node.type || '')?.uiHints;
      return `${node.id}:${!!hints?.isCanvasPanel}:${!!hints?.isBrowserPanel}:${hints?.workspace?.kind ?? ''}`;
    }).join('|'),
  );
  // eslint-disable-next-line react-hooks/exhaustive-deps -- hintsVersion tracks external schema hydration.
  const canvasNodes = useMemo(() => nodes.filter(isCanvasNode), [nodes, hintsVersion]);
  const browserNodes = useMemo(() => nodes
    .filter((node) => resolveNodeDescription(node.type || '')?.uiHints?.isBrowserPanel === true)
    .map((node) => ({ node_id: node.id, label: nodeLabel(node) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- hintsVersion tracks external schema hydration.
    [nodes, hintsVersion]);
  // The same list while its nodes and their names stay the same, not one rebuilt each render.
  const phoneNodes = workspaceNodes(nodes).filter((node) => node.kind === 'mobile');
  const mobileKey = phoneNodes.map((node) => `${node.node_id}\n${node.label}`).join('\n');
  // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed by the ids and labels.
  const mobileNodes = useMemo(() => phoneNodes, [mobileKey]);

  // A stale selection (workflow switch, node deleted) falls back to the
  // first Canvas node rather than a dead board.
  const effectiveNodeId = useMemo(() => {
    if (selectedNodeId && canvasNodes.some((n) => n.id === selectedNodeId)) {
      return selectedNodeId;
    }
    return canvasNodes[0]?.id ?? null;
  }, [canvasNodes, selectedNodeId]);

  const showBoard = open && tab === 'board' && mode === 'node';
  const board = useCanvasBoardQuery(
    showBoard ? workflowId : null,
    showBoard ? effectiveNodeId : null,
  );
  const removeItem = useCanvasRemove(workflowId, effectiveNodeId);

  // A drag sets the width, and with it ends Expand.
  const onWidth = useCallback((px: number) => {
    setWide(false);
    setWidth(px);
  }, [setWidth]);

  return (
    <WorkspaceDockShell open={open} width={dockWidth(widthPx, wide, pushes ? pageKeeps : undefined)} onWidth={onWidth}>
      <WorkspaceView
        workflowId={workflowId}
        employee={employee}
        browserNodes={browserNodes}
        mobileNodes={mobileNodes}
        canvasNodeId={effectiveNodeId}
        tab={tab}
        onTabChange={setTab}
        visible={open}
        wide={wide}
        onToggleWide={() => setWide((value) => !value)}
        onClose={close}
        notify={notify}
        dev
        flushCanvas={flushCanvas}
        board={<>
          {/* The editor's: which board this is, or that it is a file's preview. */}
          <div className="mb-2 flex shrink-0 items-center gap-2">
            {mode === 'ephemeral' ? (
              <>
                <Badge variant="secondary" className="shrink-0">Preview</Badge>
                <span className="min-w-0 flex-1 truncate text-xs text-fg-muted">
                  {ephemeralItem?.title || ephemeralItem?.ref?.filename || ''}
                </span>
                {canvasNodes.length > 0 && (
                  <Button
                    variant="quiet"
                    size="sm"
                    onClick={backToNode}
                    className="shrink-0 border-border-default text-fg-default"
                  >
                    <Undo2 className="h-3.5 w-3.5" /> Back
                  </Button>
                )}
              </>
            ) : canvasNodes.length > 1 ? (
              <div className="min-w-0 flex-1">
                <Select
                  value={effectiveNodeId ?? undefined}
                  onValueChange={selectNode}
                >
                  <SelectTrigger className="h-7 text-xs">
                    <SelectValue placeholder="Select canvas node" />
                  </SelectTrigger>
                  <SelectContent>
                    {canvasNodes.map((node) => (
                      <SelectItem key={node.id} value={node.id}>
                        {nodeLabel(node)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : (
              <span className="min-w-0 flex-1 truncate text-xs text-fg-muted">
                {canvasNodes[0] ? nodeLabel(canvasNodes[0]) : ''}
              </span>
            )}
          </div>

          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            {mode === 'ephemeral' && ephemeralItem ? (
              <CanvasContent items={[ephemeralItem]} workflowId={workflowId} />
            ) : !effectiveNodeId ? (
              <div className="flex flex-1 items-center justify-center p-4 text-center text-sm text-fg-muted">
                No Canvas node in this workflow yet — add one from the palette,
                or open a file&apos;s preview and choose &quot;Open in side
                panel&quot;.
              </div>
            ) : (
              <CanvasContent
                items={board.data?.items ?? []}
                workflowId={workflowId}
                nodeId={effectiveNodeId}
                focus={focus?.nodeId === effectiveNodeId ? focus : null}
                followLatestDefault={followMode}
                onFollowLatestChange={setFollowMode}
                onRemove={(itemId) =>
                  removeItem.mutate(itemId, {
                    onError: (error) => toast.error(error.message),
                  })
                }
                emptyHint="Nothing here yet — agents push content with the canvas tool, or wire a node into the Canvas input."
              />
            )}
          </div>
        </>}
      />
      {open && <LlmWaitNote workflowId={workflowId} className="shrink-0 border-t border-border-default px-3 py-2" />}
    </WorkspaceDockShell>
  );
};

export default CanvasDock;
