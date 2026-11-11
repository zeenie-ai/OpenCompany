import React, { useEffect, useState } from 'react';
import { AlertCircle, Download, PanelRight } from 'lucide-react';

import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { buildApiUrl } from '@/config/api';
import type { CanvasItem } from '../../../lib/canvasBoard';
import { useAppStore } from '../../../store/useAppStore';
import { useCanvasDockStore } from '../../../stores/canvasDockStore';
import type { WorkspaceEntry } from '@/types/workspaceFiles';

import FileGlyph from './FileGlyph';
import { formatBytes, formatModified } from './fileIcons';

const NEUTRAL = 'border-border-default text-fg-default';
const FACT_LABEL = 'text-fg-muted';
const FACT_VALUE = 'font-mono text-fg-default';

interface Props {
  entry: WorkspaceEntry | null;
  onOpenChange: (open: boolean) => void;
}

const FilePreviewDialog: React.FC<Props> = ({ entry, onOpenChange }) => {
  const [failed, setFailed] = useState(false);

  // A new file gets a fresh chance to load; without this a single failure
  // would poison every subsequent preview in the same dialog instance.
  useEffect(() => { setFailed(false); }, [entry?.path]);

  if (!entry) return null;

  const kind = entry.preview;
  const src = entry.url ? buildApiUrl(entry.url) : null;

  return (
    <Dialog open onOpenChange={onOpenChange}>
      {/* `sm:`: the primitive's own `sm:max-w-sm` beats an unprefixed width. */}
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="truncate pr-8 font-mono text-sm">{entry.name}</DialogTitle>
        </DialogHeader>

        <div className="flex min-h-0 flex-col gap-3">
          <div className="flex items-center justify-center rounded-lg border border-border-default bg-bg-panel p-3">
            {!src || failed || kind === 'none' ? (
              // Three different reasons converge on one honest panel: no URL
              // (unsaved workflow), a load failure (cleaned up, or an expired
              // session surfacing as a silent `error` event), or a type the
              // server refuses to serve inline.
              <div className="flex flex-col items-center gap-2.5 py-10 text-center">
                <FileGlyph entry={entry} strokeWidth={1.5} className="size-10 text-fg-muted" />
                <p className="flex items-center gap-2 text-xs text-fg-muted">
                  {failed && <AlertCircle aria-hidden className="size-3.5 shrink-0 text-destructive" />}
                  {failed
                    ? 'Could not load this file. It may have been removed, or the session expired.'
                    : !src
                      ? 'Save and run this workflow to give its files a stable address.'
                      : 'No inline preview for this file type.'}
                </p>
              </div>
            ) : kind === 'image' ? (
              <img
                src={src}
                alt={entry.name}
                onError={() => setFailed(true)}
                className="max-h-[55vh] w-auto max-w-full rounded-md object-contain"
              />
            ) : kind === 'audio' ? (
              <audio
                controls
                preload="metadata"
                src={src}
                onError={() => setFailed(true)}
                className="w-full"
              />
            ) : (
              <video
                controls
                preload="metadata"
                src={src}
                onError={() => setFailed(true)}
                className="max-h-[55vh] w-full rounded-md"
              />
            )}
          </div>

          <div className="flex flex-wrap items-end justify-between gap-4">
            <dl className="grid min-w-0 grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
              <dt className={FACT_LABEL}>Path</dt>
              <dd className={`break-all ${FACT_VALUE}`}>{entry.path}</dd>
              <dt className={FACT_LABEL}>Type</dt>
              <dd className={FACT_VALUE}>{entry.mime_type || 'unknown'}</dd>
              <dt className={FACT_LABEL}>Size</dt>
              <dd className={FACT_VALUE}>{formatBytes(entry.size_bytes, entry.is_dir)}</dd>
              <dt className={FACT_LABEL}>Modified</dt>
              <dd className={FACT_VALUE}>{formatModified(entry.modified_at)}</dd>
            </dl>

            <div className="flex shrink-0 items-center gap-2">
              {entry.ref && (
                <Button
                  type="button"
                  variant="quiet"
                  className={NEUTRAL}
                  onClick={() => {
                    // Move the preview to the persistent docked surface. The
                    // dock sits behind this 95vw parameter modal, so opening
                    // there must also close the modal — that is the point of
                    // the affordance.
                    const item: CanvasItem = {
                      id: `ephemeral-${entry.path}`,
                      kind: 'file',
                      title: null,
                      ref: entry.ref,
                      url: null,
                      content: null,
                      language: null,
                      source: 'workflow',
                      created_at: entry.modified_at,
                    };
                    useCanvasDockStore.getState().showEphemeral(item);
                    onOpenChange(false);
                    useAppStore.getState().setSelectedNode(null);
                  }}
                >
                  <PanelRight /> Open in side panel
                </Button>
              )}
              {src && (
                <Button asChild variant="quiet" className={NEUTRAL}>
                  <a href={src} download={entry.name} target="_blank" rel="noreferrer">
                    <Download /> Download
                  </a>
                </Button>
              )}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default FilePreviewDialog;
