import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ChevronRight,
  FolderOpen,
  Home,
  LayoutGrid,
  List,
  Pin,
  RefreshCw,
  Search,
} from 'lucide-react';
import { toast } from 'sonner';

import { useWebSocket } from '@/contexts/WebSocketContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Toggle } from '@/components/ui/toggle';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { buildApiUrl } from '@/config/api';
import { cn } from '@/lib/utils';
import type { ListWorkspaceFilesResponse, WorkspaceEntry } from '@/types/workspaceFiles';

import FileGlyph from './FileGlyph';
import { formatBytes, formatModified } from './fileIcons';

/**
 * The workspace browsing engine — listing, breadcrumbs, search, grid/list.
 *
 * Extracted so the gallery panel and the "Choose from workspace" picker are
 * one implementation rather than two. They differ only in what wraps it: the
 * panel stores `path` on its node and allows dragging; the picker keeps `path`
 * in local state and offers no drag surface at all (a non-drag alternative
 * that reintroduced a drag would defeat its purpose).
 *
 * `path` is controlled by the parent for exactly that reason.
 *
 * The look is the Workspace Library's (parameterPanel/canvas/CanvasLibrary):
 * a file is a card with a preview band over its name and a mono line about
 * it, and the list is the node panels' table. Nothing here takes a colour but
 * a notice: a file's kind is told by its glyph's shape.
 */
interface Props {
  workflowId?: string;
  path: string;
  onPathChange: (next: string) => void;
  /** Directory → navigate, file → whatever the host wants (preview or select). */
  onActivate: (entry: WorkspaceEntry) => void;
  /** Rows carry an HTML5 drag payload. Off inside the picker. */
  draggable?: boolean;
  onDragStart?: (event: React.DragEvent, entry: WorkspaceEntry) => void;
  /** Optional per-file pin (the panel's "pin to node output"). */
  selectionSet?: ReadonlySet<string>;
  onTogglePin?: (entry: WorkspaceEntry) => void;
  /** Highlighted row, for the picker's single-select. */
  activePath?: string | null;
  /** Bumping this forces a refetch — the panel uses it after an upload. */
  refreshToken?: number;
}

const POLL_INTERVAL_MS = 5000;
const SEARCH_DEBOUNCE_MS = 250;

const LIST_COLUMNS = ['Name', 'Size', 'Modified', 'Type'];

const NEUTRAL = 'border-border-default text-fg-default';
const HEAD_CELL = 'border-b border-border-default px-3 py-2 font-medium';
const META_CELL = 'px-3 py-2 font-mono text-xs whitespace-nowrap text-fg-muted tabular-nums';
// The picked layout: a raised segment with a hairline (the Normal/Dev switch).
const SEGMENT = 'h-6 px-2 data-[state=on]:border-border-default data-[state=on]:shadow-card';

const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? '' : 's'}`;

const WorkspaceBrowser: React.FC<Props> = ({
  workflowId,
  path,
  onPathChange,
  onActivate,
  draggable = false,
  onDragStart,
  selectionSet,
  onTogglePin,
  activePath = null,
  refreshToken = 0,
}) => {
  const { sendRequest } = useWebSocket();

  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [crumbs, setCrumbs] = useState<Array<{ name: string; path: string }>>([]);
  const [parent, setParent] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [workspaceExists, setWorkspaceExists] = useState(true);
  const [pathExists, setPathExists] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<'grid' | 'list'>('grid');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');

  const searching = query.trim().length > 0;

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [search]);

  const refresh = useCallback(async (quiet = false) => {
    if (!workflowId) return;
    if (!quiet) setLoading(true);
    try {
      const term = query.trim();
      // The raw term goes over the wire; the server decides what searching
      // for it means and which directories that spans.
      const response = await sendRequest<ListWorkspaceFilesResponse>('list_workspace_files', {
        workflow_id: workflowId,
        path,
        ...(term ? { search: term } : {}),
        limit: 500,
      });

      if (!response.success) throw new Error(response.error || 'Could not read this workspace');

      setEntries(response.entries ?? []);
      setCrumbs(response.crumbs ?? []);
      setParent(response.parent ?? null);
      setTruncated(response.truncated ?? false);
      // Absent on the glob branch, which never reports them — treat a search
      // as always "inside" a workspace so it can't flash the empty-state.
      setWorkspaceExists(response.workspace_exists ?? true);
      setPathExists(response.path_exists ?? true);
      setError(null);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : 'Could not read this workspace';
      setError(message);
      if (!quiet) toast.error(message);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [path, query, sendRequest, workflowId]);

  useEffect(() => { void refresh(); }, [refresh, refreshToken]);

  useEffect(() => {
    const poll = window.setInterval(() => {
      // A background tab has nothing to show.
      if (document.visibilityState !== 'visible') return;
      void refresh(true);
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(poll);
  }, [refresh]);

  const navigate = useCallback((next: string) => {
    setSearch('');
    setQuery('');
    onPathChange(next);
  }, [onPathChange]);

  const visible = useMemo(() => {
    // The backend already filtered a search; this only narrows a plain listing.
    if (searching) return entries;
    const term = search.trim().toLowerCase();
    return term ? entries.filter((entry) => entry.name.toLowerCase().includes(term)) : entries;
  }, [entries, search, searching]);

  const fileCount = visible.filter((entry) => !entry.is_dir).length;
  const folderCount = visible.length - fileCount;

  const emptyMessage = !workflowId
    ? 'Save this workflow to give it a workspace.'
    : !workspaceExists
      ? 'No workspace yet. Run a node that writes a file — a download, a recording, a report — and it will appear here.'
      : !pathExists
        ? 'This folder no longer exists.'
        : searching
          ? 'Nothing in this workspace matches that search.'
          : search.trim()
            ? 'Nothing here matches that filter.'
            : 'This folder is empty.';

  const showPin = !!onTogglePin;

  const rowProps = (entry: WorkspaceEntry) => ({
    entry,
    pinned: selectionSet?.has(entry.path) ?? false,
    showPin,
    active: activePath === entry.path,
    showFullPath: searching,
    draggable: draggable && !entry.is_dir && !!entry.ref,
    onOpen: onActivate,
    onTogglePin,
    onDragStart,
  });

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-3 flex shrink-0 flex-wrap items-center gap-2">
        <nav
          aria-label="Workspace path"
          className="flex min-w-0 flex-1 basis-40 items-center gap-0.5 overflow-x-auto [scrollbar-width:none]"
        >
          <Button
            type="button"
            variant="quiet"
            size="icon-sm"
            className="shrink-0"
            onClick={() => navigate('')}
            aria-label="Workspace root"
          >
            <Home />
          </Button>
          {crumbs.map((crumb, index) => (
            <React.Fragment key={crumb.path}>
              <ChevronRight aria-hidden className="size-3.5 shrink-0 text-fg-faint" />
              <Button
                type="button"
                variant="quiet"
                size="sm"
                className={cn('shrink-0 px-1.5 font-mono text-xs', index === crumbs.length - 1 && 'text-fg-default')}
                onClick={() => navigate(crumb.path)}
              >
                {crumb.name}
              </Button>
            </React.Fragment>
          ))}
          {searching && (
            <>
              <ChevronRight aria-hidden className="size-3.5 shrink-0 text-fg-faint" />
              <span className="shrink-0 px-1.5 text-xs text-fg-default">Search results</span>
            </>
          )}
        </nav>

        <div className="relative w-56 shrink-0">
          <Search aria-hidden className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-fg-muted" />
          <Input
            className="pl-9"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search this workspace"
            aria-label="Search this workspace"
          />
        </div>

        <ToggleGroup
          type="single"
          variant="segmented"
          size="sm"
          aria-label="Layout"
          className="shrink-0"
          value={view}
          // Radix reports '' when the picked layout is clicked again.
          onValueChange={(next) => { if (next === 'grid' || next === 'list') setView(next); }}
        >
          <ToggleGroupItem value="grid" aria-label="Grid view" title="Grid" className={SEGMENT}>
            <LayoutGrid className="size-3.5" />
          </ToggleGroupItem>
          <ToggleGroupItem value="list" aria-label="List view" title="List" className={SEGMENT}>
            <List className="size-3.5" />
          </ToggleGroupItem>
        </ToggleGroup>

        <Button
          type="button"
          variant="quiet"
          size="icon"
          className={cn('shrink-0', NEUTRAL)}
          disabled={loading || !workflowId}
          onClick={() => void refresh()}
          aria-label="Refresh workspace files"
          title="Refresh"
        >
          <RefreshCw className={cn(loading && 'animate-spin')} />
        </Button>
      </div>

      {error && (
        <div
          role="alert"
          className="mb-3 flex shrink-0 items-center gap-2 rounded-lg border border-danger-border bg-danger-soft px-3 py-2 text-xs text-destructive"
        >
          <AlertTriangle aria-hidden className="size-4 shrink-0" /> {error}
        </div>
      )}

      {visible.length === 0 ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2.5 py-10 text-center">
          <FolderOpen aria-hidden strokeWidth={1.5} className="size-8 text-fg-faint" />
          <p className="max-w-sm text-sm text-fg-muted">{emptyMessage}</p>
          {!pathExists && workspaceExists && parent !== null && (
            <Button type="button" variant="quiet" size="sm" className={NEUTRAL} onClick={() => navigate(parent)}>
              Go up
            </Button>
          )}
        </div>
      ) : view === 'grid' ? (
        // The bleed gives a card's hover lift and focus ring room inside the
        // scroller, with the cards still on the toolbar's edges.
        <div className="-m-1 min-h-0 flex-1 overflow-y-auto p-1">
          <div className="grid grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))] gap-2.5">
            {visible.map((entry) => <GridTile key={entry.path} {...rowProps(entry)} />)}
          </div>
        </div>
      ) : (
        // The table is as tall as its rows, and scrolls once they outgrow the
        // panel.
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="min-h-0 overflow-auto rounded-lg border border-border-default bg-bg-elevated">
            <table className="w-full border-collapse text-sm">
              <thead className="sticky top-0 z-10 bg-bg-panel text-left text-xs text-fg-muted">
                <tr>
                  {LIST_COLUMNS.map((label) => <th key={label} scope="col" className={HEAD_CELL}>{label}</th>)}
                  {showPin && <th scope="col" className={HEAD_CELL}><span className="sr-only">Pin</span></th>}
                </tr>
              </thead>
              <tbody>
                {visible.map((entry) => <ListRow key={entry.path} {...rowProps(entry)} />)}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <footer className="mt-2.5 flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 font-mono text-2xs text-fg-muted">
        <span>
          {plural(fileCount, 'file')}
          {folderCount > 0 ? ` · ${plural(folderCount, 'folder')}` : ''}
        </span>
        {truncated && (
          <span className="flex items-center gap-1">
            <AlertTriangle aria-hidden className="size-3 text-warning" /> Showing the first {visible.length}. Narrow it with search.
          </span>
        )}
      </footer>
    </div>
  );
};

interface RowProps {
  entry: WorkspaceEntry;
  pinned: boolean;
  showPin: boolean;
  active: boolean;
  showFullPath: boolean;
  draggable: boolean;
  onOpen: (entry: WorkspaceEntry) => void;
  onTogglePin?: (entry: WorkspaceEntry) => void;
  onDragStart?: (event: React.DragEvent, entry: WorkspaceEntry) => void;
}

/** Enter and Space activate, matching what a `role="button"` promises. */
const activationKeys = (onOpen: () => void) => (event: React.KeyboardEvent) => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    onOpen();
  }
};

/** Pins a file to the node's output. It sits inside a row that opens the
 *  file, so its clicks and keys stop here. */
const PinToggle: React.FC<{
  entry: WorkspaceEntry;
  pinned: boolean;
  onTogglePin: (entry: WorkspaceEntry) => void;
  className?: string;
}> = ({ entry, pinned, onTogglePin, className }) => (
  <Toggle
    pressed={pinned}
    onPressedChange={() => onTogglePin(entry)}
    onClick={(event) => event.stopPropagation()}
    onKeyDown={(event) => event.stopPropagation()}
    aria-label={`Pin ${entry.name} to this node's output`}
    title={pinned ? 'Pinned to this node’s output' : 'Pin to this node’s output'}
    className={cn('size-6.5 px-0', className)}
  >
    <Pin className={cn('size-3.5', pinned && 'fill-current')} />
  </Toggle>
);

const GridTile: React.FC<RowProps> = ({
  entry, pinned, showPin, active, showFullPath, draggable, onOpen, onTogglePin, onDragStart,
}) => {
  const [thumbFailed, setThumbFailed] = useState(false);
  const showThumb = entry.preview === 'image' && !!entry.url && !thumbFailed;

  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={active}
      draggable={draggable}
      onDragStart={onDragStart ? (event) => onDragStart(event, entry) : undefined}
      onClick={() => onOpen(entry)}
      onKeyDown={activationKeys(() => onOpen(entry))}
      title={entry.path}
      className={cn(
        'group relative flex cursor-pointer flex-col overflow-hidden rounded-xl border bg-bg-elevated text-left transition-[border-color,transform] duration-(--dur-default) hover:-translate-y-px focus-visible:ring-2 focus-visible:ring-border-focus focus-visible:outline-none motion-reduce:hover:translate-y-0',
        // The picked file: a foreground border, stronger than a hover's.
        active ? 'border-fg-default' : 'border-border-default hover:border-border-strong',
      )}
    >
      <div className="grid h-20 place-items-center overflow-hidden border-b border-border-default bg-bg-panel">
        {showThumb ? (
          <img
            src={buildApiUrl(entry.url as string)}
            alt={entry.name}
            loading="lazy"
            draggable={false}
            onError={() => setThumbFailed(true)}
            className="size-full object-cover"
          />
        ) : (
          <FileGlyph entry={entry} strokeWidth={1.5} className="size-7 text-fg-muted" />
        )}
      </div>

      <div className="flex min-w-0 flex-col gap-0.5 px-2.5 py-2">
        <span className="truncate text-meta font-medium text-fg-default">{entry.name}</span>
        <span className="truncate font-mono text-2xs text-fg-muted">
          {showFullPath ? entry.path : entry.is_dir ? 'Folder' : formatBytes(entry.size_bytes)}
        </span>
      </div>

      {showPin && !entry.is_dir && onTogglePin && (
        <PinToggle
          entry={entry}
          pinned={pinned}
          onTogglePin={onTogglePin}
          className={cn(
            // Opaque in every state: it sits over the preview band.
            'absolute top-1.5 right-1.5 border-border-default bg-bg-elevated hover:bg-bg-elevated',
            // A pinned file keeps its pin on show; the rest offer it on hover.
            !pinned && 'opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100',
          )}
        />
      )}
    </div>
  );
};

const ListRow: React.FC<RowProps> = ({
  entry, pinned, showPin, active, showFullPath, draggable, onOpen, onTogglePin, onDragStart,
}) => (
  // role/tabIndex/keydown so list view is operable without a pointer — the
  // grid tiles always were, and a view toggle must not change what is reachable.
  <tr
    role="button"
    tabIndex={0}
    aria-pressed={active}
    draggable={draggable}
    onDragStart={onDragStart ? (event) => onDragStart(event, entry) : undefined}
    onClick={() => onOpen(entry)}
    onKeyDown={activationKeys(() => onOpen(entry))}
    title={entry.path}
    className={cn(
      'cursor-pointer border-b border-border-default last:border-b-0 hover:bg-bg-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-border-focus',
      active && 'bg-bg-active',
    )}
  >
    <td className="max-w-0 px-3 py-2">
      <div className="flex items-center gap-2">
        <FileGlyph entry={entry} className="size-4 shrink-0 text-fg-muted" />
        <span className="truncate text-fg-default">{showFullPath ? entry.path : entry.name}</span>
      </div>
    </td>
    <td className={META_CELL}>{formatBytes(entry.size_bytes, entry.is_dir)}</td>
    <td className={META_CELL}>{formatModified(entry.modified_at)}</td>
    <td className="max-w-[14rem] truncate px-3 py-2 text-xs text-fg-muted">
      {entry.is_dir ? 'Folder' : entry.mime_type || 'unknown'}
    </td>
    {showPin && (
      <td className="w-px px-2 py-1 text-right">
        {!entry.is_dir && onTogglePin && <PinToggle entry={entry} pinned={pinned} onTogglePin={onTogglePin} />}
      </td>
    )}
  </tr>
);

export default WorkspaceBrowser;
