/**
 * The Canvas Library (design handoff "Workspace panel", Canvas): every item
 * on a board, by what it is. *Artifacts* are what the employee wrote (notes,
 * with their version); *Files* are what was put there (screenshots, uploads,
 * PDFs, pages), as a grid with image thumbnails. `LibraryStrip` is the
 * board's footer that opens it: the Library button with the count, the six
 * newest items as chips, and Latest while an older item is shown.
 */

import { LayoutGrid } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { buildApiUrl } from '../../../config/api';
import type { CanvasItem } from '../../../lib/canvasBoard';
import { formatBytes } from '../gallery/fileIcons';
import { itemLabel, resolveRenderKind, type RenderVerdict } from './canvasKinds';

const TOOLS = 'border-action-tools-border bg-action-tools-soft text-action-tools-ink';
const RUN = 'border-action-run-border bg-action-run-soft text-action-run-ink';
const STOP = 'border-action-stop-border bg-action-stop-soft text-action-stop-ink';
const SAVE = 'border-action-save-border bg-action-save-soft text-action-save-ink';
const CONFIG = 'border-action-config-border bg-action-config-soft text-action-config-ink';
const NEUTRAL = 'border-border-default bg-bg-app text-fg-muted';

/** Each kind's tile: a short mark and a tint. A file shows its extension. */
const KIND: Record<RenderVerdict, { mark: string; tone: string; label: string }> = {
  note: { mark: 'MD', tone: TOOLS, label: 'Note' },
  markdown: { mark: 'MD', tone: TOOLS, label: 'Document' },
  'web-external': { mark: 'WEB', tone: STOP, label: 'Web page' },
  'web-srcdoc': { mark: '</>', tone: STOP, label: 'Web page' },
  json: { mark: '{ }', tone: CONFIG, label: 'JSON' },
  code: { mark: '</>', tone: CONFIG, label: 'Code' },
  text: { mark: 'TXT', tone: RUN, label: 'Text' },
  'media-image': { mark: 'IMG', tone: SAVE, label: 'Image' },
  'media-video': { mark: 'VID', tone: SAVE, label: 'Video' },
  'media-audio': { mark: 'AUD', tone: SAVE, label: 'Audio' },
  pdf: { mark: 'PDF', tone: STOP, label: 'PDF' },
  binary: { mark: 'FILE', tone: NEUTRAL, label: 'File' },
};

function kindOf(item: CanvasItem) {
  const kind = KIND[resolveRenderKind(item)];
  const name = item.ref?.filename ?? '';
  const dot = name.lastIndexOf('.');
  const extension = dot > 0 ? name.slice(dot + 1).toUpperCase() : '';
  return { ...kind, mark: extension && extension.length <= 4 ? extension : kind.mark };
}

function Tile({ item, className }: { item: CanvasItem; className?: string }) {
  const kind = kindOf(item);
  return (
    <span aria-hidden className={cn('grid shrink-0 place-items-center border font-mono font-semibold', kind.tone, className)}>
      {kind.mark}
    </span>
  );
}

/** What a row or card says under the title. */
function meta(item: CanvasItem): string {
  const kind = kindOf(item);
  if (item.kind === 'note') return `${kind.label} · v${item.version ?? 1}`;
  if (item.kind === 'url' && item.url) return `${kind.label} · ${URL.canParse(item.url) ? new URL(item.url).host : item.url}`;
  return item.ref ? `${kind.label} · ${formatBytes(item.ref.size_bytes ?? 0)}` : kind.label;
}

export function LibraryView({
  items,
  activeId,
  onOpen,
  onBack,
}: {
  items: readonly CanvasItem[];
  activeId: string | null;
  onOpen: (item: CanvasItem) => void;
  onBack: () => void;
}) {
  const newestFirst = [...items].reverse();
  const artifacts = newestFirst.filter((item) => item.kind === 'note');
  const files = newestFirst.filter((item) => item.kind !== 'note');
  return (
    <section aria-label="Library" className="flex min-h-0 flex-1 flex-col gap-4.5 overflow-x-hidden overflow-y-auto p-0.5 pr-1">
      <div className="flex items-center gap-2">
        <h3 className="m-0 text-base font-semibold text-fg-default">Library</h3>
        <span className="rounded-md border border-border-default bg-bg-panel px-1.75 py-0.5 font-mono text-2xs font-medium text-fg-muted">
          {items.length}
        </span>
        <Button variant="quiet" onClick={onBack} className="ml-auto h-7 border-border-default px-3 text-meta text-fg-default">
          Back
        </Button>
      </div>
      {artifacts.length > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="m-0 font-mono text-2xs font-medium tracking-label text-fg-faint uppercase">Artifacts · {artifacts.length}</h4>
          {artifacts.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => onOpen(item)}
              aria-current={item.id === activeId ? 'true' : undefined}
              className="flex items-center gap-3 rounded-xl border border-border-default bg-bg-panel px-3 py-2.5 text-left text-fg-default transition-[border-color,transform] duration-(--dur-default) hover:-translate-y-px hover:border-border-strong aria-current:border-action-tools-border motion-reduce:hover:translate-y-0"
            >
              <Tile item={item} className="size-9 rounded-lg text-2xs" />
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-row font-medium">{itemLabel(item)}</span>
                <span className="font-mono text-2xs text-fg-faint">{meta(item)}</span>
              </span>
            </button>
          ))}
        </div>
      )}
      {files.length > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="m-0 font-mono text-2xs font-medium tracking-label text-fg-faint uppercase">Files · {files.length}</h4>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2">
            {files.map((item) => {
              const image = resolveRenderKind(item) === 'media-image' && item.ref?.url ? buildApiUrl(item.ref.url) : null;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onOpen(item)}
                  aria-current={item.id === activeId ? 'true' : undefined}
                  className="flex flex-col overflow-hidden rounded-xl border border-border-default bg-bg-panel text-left text-fg-default transition-[border-color,transform] duration-(--dur-default) hover:-translate-y-px hover:border-border-strong aria-current:border-action-tools-border motion-reduce:hover:translate-y-0"
                >
                  {image ? (
                    <img src={image} alt="" loading="lazy" className="h-18 w-full border-b border-border-default object-cover" />
                  ) : (
                    <Tile item={item} className="h-18 w-full border-x-0 border-t-0 text-sm" />
                  )}
                  <span className="flex min-w-0 flex-col gap-0.5 px-2.5 py-2">
                    <span className="truncate text-meta font-medium">{itemLabel(item)}</span>
                    <span className="truncate font-mono text-2xs text-fg-faint">{meta(item)}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}

export function LibraryStrip({
  items,
  activeId,
  following,
  onOpen,
  onLibrary,
  onLatest,
}: {
  items: readonly CanvasItem[];
  activeId: string | null;
  /** The board follows the newest item (no Latest chip then). */
  following: boolean;
  onOpen: (item: CanvasItem) => void;
  onLibrary: () => void;
  onLatest: () => void;
}) {
  const newest = items.slice(-6).reverse();
  return (
    <div className="flex min-w-0 flex-1 items-center gap-1.5">
      <Button
        variant="quiet"
        onClick={onLibrary}
        title="All artifacts and files"
        aria-label={`Library, ${items.length} items`}
        className="h-7.5 shrink-0 gap-1.5 border-border-default bg-bg-panel px-2.5 text-meta text-fg-default"
      >
        <LayoutGrid aria-hidden className="size-3.5" strokeWidth={1.75} />
        {items.length}
      </Button>
      <div className="flex min-w-0 flex-1 gap-1.5 overflow-x-auto py-0.5 [scrollbar-width:none]">
        {newest.map((item) => (
          <button
            key={item.id}
            type="button"
            title={itemLabel(item)}
            onClick={() => onOpen(item)}
            aria-current={item.id === activeId ? 'true' : undefined}
            className="flex h-7.5 max-w-37.5 shrink-0 items-center gap-1.5 rounded-lg border border-border-default bg-bg-panel pr-2.25 pl-1.25 text-xs font-medium text-fg-default transition-colors duration-(--dur-default) hover:border-border-strong aria-current:border-action-tools-border aria-current:bg-action-tools-soft"
          >
            <Tile item={item} className="size-5 rounded-md text-micro" />
            <span className="truncate">{itemLabel(item)}</span>
          </button>
        ))}
      </div>
      {!following && (
        <Button
          variant="quiet"
          onClick={onLatest}
          className="h-6 shrink-0 rounded-pill border-action-tools-border bg-action-tools-soft px-2 text-xs text-action-tools-ink hover:bg-action-tools-hover hover:text-action-tools-ink"
        >
          Latest
        </Button>
      )}
    </div>
  );
}
