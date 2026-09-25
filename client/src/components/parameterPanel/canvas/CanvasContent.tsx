/**
 * The one shared Canvas board renderer — hosted by both the parameter-panel
 * CanvasPanel and the docked CanvasDock.
 *
 * The item list IS the carousel: one active item, prev/next in the footer,
 * `pinnedId: null` meaning "follow newest" (the chat stick-to-bottom rule —
 * a pushed item surfaces automatically unless the user deliberately
 * navigated back). Keyboard arrows work on the focused content region only;
 * no document-level listeners, which would fight React Flow node nudging.
 *
 * Only the ACTIVE item mounts — no N live videos/iframes.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  Download,
  File as FileIcon,
  X,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';

import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { buildApiUrl } from '../../../config/api';
import { useWebSocketActions } from '../../../contexts/WebSocketContext';
import type { CanvasItem } from '../../../lib/canvasBoard';
import type {
  ListWorkspaceFilesResponse,
  WorkspaceFileRef,
} from '../../../types/workspaceFiles';
import { formatBytes } from '../gallery/fileIcons';
import {
  itemLabel,
  parentDirOf,
  prismLanguageFor,
  resolveRenderKind,
} from './canvasKinds';
import TextFileView from './TextFileView';
import { ExternalSiteView, PdfView, WorkspaceHtmlView } from './WebView';

const FOLLOW_POLL_MS = 5_000;

interface Props {
  items: CanvasItem[];
  workflowId?: string | null;
  onRemove?: (itemId: string) => void;
  emptyHint?: React.ReactNode;
  /** Initial state of the follow-latest toggle (dock persists it). */
  followLatestDefault?: boolean;
  onFollowLatestChange?: (value: boolean) => void;
}

const HonestFallback: React.FC<{ failed: boolean; hasUrl: boolean }> = ({
  failed,
  hasUrl,
}) => (
  <div className="flex flex-col items-center gap-2 py-10 text-center">
    <FileIcon className="h-10 w-10 text-fg-muted" />
    <p className="flex items-center gap-2 text-xs text-fg-muted">
      {failed && <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />}
      {failed
        ? 'Could not load this file. It may have been removed, or the session expired.'
        : !hasUrl
          ? 'Save and run this workflow to give its files a stable address.'
          : 'No inline preview for this file type.'}
    </p>
  </div>
);

const BinaryView: React.FC<{ refFile: WorkspaceFileRef }> = ({ refFile }) => {
  const href = refFile.url ? buildApiUrl(refFile.url) : null;
  return (
    <div className="flex flex-col items-center gap-3 py-10 text-center">
      <FileIcon className="h-10 w-10 text-fg-muted" />
      <div className="text-xs text-fg-muted">
        <p className="break-all font-mono text-fg-default">{refFile.path}</p>
        <p>
          {refFile.mime_type || 'unknown'} · {formatBytes(refFile.size_bytes ?? 0)}
        </p>
      </div>
      {href && (
        <Button asChild variant="outline" size="sm">
          <a href={href} download={refFile.filename} target="_blank" rel="noreferrer">
            <Download className="h-4 w-4" /> Download
          </a>
        </Button>
      )}
    </div>
  );
};

const CanvasContent: React.FC<Props> = ({
  items,
  workflowId,
  onRemove,
  emptyHint,
  followLatestDefault = false,
  onFollowLatestChange,
}) => {
  const { sendRequest, isReady } = useWebSocketActions();
  const [pinnedId, setPinnedId] = useState<string | null>(null);
  const [followLatest, setFollowLatest] = useState(followLatestDefault);
  const [mediaFailed, setMediaFailed] = useState(false);

  const activeIndex = useMemo(() => {
    if (items.length === 0) return -1;
    if (pinnedId) {
      const index = items.findIndex((item) => item.id === pinnedId);
      if (index >= 0) return index;
    }
    return items.length - 1;
  }, [items, pinnedId]);

  const active = activeIndex >= 0 ? items[activeIndex] : null;
  useEffect(() => setMediaFailed(false), [active?.id]);

  const goTo = useCallback(
    (index: number) => {
      if (items.length === 0) return;
      const clamped = Math.max(0, Math.min(items.length - 1, index));
      // Landing on the newest item resumes follow-newest.
      setPinnedId(clamped === items.length - 1 ? null : items[clamped].id);
    },
    [items],
  );

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        goTo(activeIndex - 1);
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        goTo(activeIndex + 1);
      } else if (event.key === 'Home') {
        event.preventDefault();
        goTo(0);
      } else if (event.key === 'End') {
        event.preventDefault();
        setPinnedId(null);
      }
    },
    [activeIndex, goTo],
  );

  const verdict = active ? resolveRenderKind(active) : null;

  // Follow-latest: while the active item is a workspace image, poll its
  // folder listing (gallery cadence) and render the newest image there —
  // how a browser-automation run becomes a live view with zero backend.
  const followDir = active?.ref ? parentDirOf(active.ref.path) : '';
  const followEnabled =
    followLatest && verdict === 'media-image' && !!workflowId && isReady;
  const followQuery = useQuery<ListWorkspaceFilesResponse>({
    queryKey: ['canvasFollowLatest', workflowId ?? '', followDir],
    enabled: followEnabled,
    refetchInterval: FOLLOW_POLL_MS,
    queryFn: () =>
      sendRequest<ListWorkspaceFilesResponse>('list_workspace_files', {
        workflow_id: workflowId,
        path: followDir,
        limit: 500,
      }),
  });

  const followRef = useMemo<WorkspaceFileRef | null>(() => {
    if (!followEnabled || !followQuery.data?.success) return null;
    const images = (followQuery.data.entries ?? []).filter(
      (entry) => !entry.is_dir && entry.preview === 'image' && entry.ref,
    );
    if (images.length === 0) return null;
    images.sort((a, b) =>
      String(b.modified_at ?? '').localeCompare(String(a.modified_at ?? '')),
    );
    return images[0].ref;
  }, [followEnabled, followQuery.data]);

  if (items.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-center text-sm text-fg-muted">
        {emptyHint ?? 'Nothing here yet — agents and workflow runs push content to this Canvas.'}
      </div>
    );
  }
  if (!active) return null;

  const displayRef = verdict === 'media-image' && followRef ? followRef : active.ref;
  const mediaSrc = displayRef?.url ? buildApiUrl(displayRef.url) : null;

  const body = (() => {
    switch (verdict) {
      case 'note':
        return (
          <div className="prose prose-sm min-h-0 max-w-none flex-1 overflow-auto dark:prose-invert">
            <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
              {active.content ?? ''}
            </ReactMarkdown>
          </div>
        );
      case 'web-external':
        return <ExternalSiteView url={active.url ?? ''} title={active.title} />;
      case 'media-image':
        return !mediaSrc || mediaFailed ? (
          <HonestFallback failed={mediaFailed} hasUrl={!!mediaSrc} />
        ) : (
          <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto">
            <img
              src={mediaSrc}
              alt={displayRef?.filename ?? 'canvas image'}
              onError={() => setMediaFailed(true)}
              className="max-h-full w-auto max-w-full rounded object-contain"
            />
          </div>
        );
      case 'media-video':
        return !mediaSrc || mediaFailed ? (
          <HonestFallback failed={mediaFailed} hasUrl={!!mediaSrc} />
        ) : (
          <video
            controls
            preload="metadata"
            src={mediaSrc}
            onError={() => setMediaFailed(true)}
            className="min-h-0 w-full flex-1 rounded"
          />
        );
      case 'media-audio':
        return !mediaSrc || mediaFailed ? (
          <HonestFallback failed={mediaFailed} hasUrl={!!mediaSrc} />
        ) : (
          <div className="flex flex-1 items-center px-2">
            <audio
              controls
              preload="metadata"
              src={mediaSrc}
              onError={() => setMediaFailed(true)}
              className="w-full"
            />
          </div>
        );
      case 'pdf':
        return active.ref ? <PdfView refFile={active.ref} /> : null;
      case 'web-srcdoc':
        return active.ref ? <WorkspaceHtmlView refFile={active.ref} /> : null;
      case 'markdown':
      case 'json':
      case 'code':
      case 'text':
        return active.ref ? (
          <TextFileView
            refFile={active.ref}
            verdict={verdict}
            language={prismLanguageFor(active)}
          />
        ) : null;
      default:
        return active.ref ? <BinaryView refFile={active.ref} /> : null;
    }
  })();

  return (
    <div
      className="flex min-h-0 flex-1 flex-col outline-none"
      tabIndex={0}
      role="group"
      aria-label="Canvas items"
      onKeyDown={handleKeyDown}
    >
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded border border-border-default bg-bg-panel p-3">
        {body}
      </div>

      {verdict === 'media-image' && workflowId && (
        <div className="mt-2 flex shrink-0 items-center gap-2">
          <Switch
            id="canvas-follow-latest"
            checked={followLatest}
            onCheckedChange={(checked) => {
              setFollowLatest(checked);
              onFollowLatestChange?.(checked);
            }}
          />
          <Label htmlFor="canvas-follow-latest" className="text-xs text-fg-muted">
            Follow latest image{followDir ? ` in ${followDir}/` : ''}
          </Label>
        </div>
      )}

      <div className="mt-2 flex shrink-0 items-center gap-1">
        {items.length > 1 && (
          <>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => goTo(activeIndex - 1)}
              disabled={activeIndex === 0}
              aria-label="Previous item"
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => goTo(activeIndex + 1)}
              disabled={activeIndex === items.length - 1}
              aria-label="Next item"
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </>
        )}
        <span className="min-w-0 flex-1 truncate text-xs text-fg-default" title={itemLabel(active)}>
          {itemLabel(active)}
        </span>
        {items.length > 1 && (
          <span className="shrink-0 text-xs tabular-nums text-fg-muted">
            {activeIndex + 1}/{items.length}
          </span>
        )}
        {onRemove && (
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => onRemove(active.id)}
            aria-label="Remove item"
          >
            <X className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
};

/**
 * `React.memo` equality for the board renderer — the `nodePropsEqual`
 * pattern (nodeMemoEquality.ts): compare only the props that affect the
 * rendered output. The dock re-renders on every drag tick (widthPx) and on
 * every broadcast-driven parent render during workflow runs; without this
 * the whole board (markdown parse, JSON tree, iframes) re-reconciled per
 * mousemove, which is what made the drag stutter while a workflow ran.
 *
 * Callback props (`onRemove` / `onFollowLatestChange`) are deliberately
 * skipped, like `xPos`/`yPos` there: they are fresh closures each render
 * over stable stores/mutations, and any change that makes them target a
 * different board also changes `items`. Items compare element-wise so the
 * dock's ephemeral single-item array stays equal across renders.
 */
function canvasContentPropsEqual(prev: Props, next: Props): boolean {
  return (
    prev.workflowId === next.workflowId &&
    prev.followLatestDefault === next.followLatestDefault &&
    prev.emptyHint === next.emptyHint &&
    prev.items.length === next.items.length &&
    prev.items.every((item, index) => item === next.items[index])
  );
}

export default React.memo(CanvasContent, canvasContentPropsEqual);
