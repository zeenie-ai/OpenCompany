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
import { formatBytes, formatModified, glyphToneFor } from './fileIcons';

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
          <DialogTitle className="truncate font-mono text-sm">{entry.name}</DialogTitle>
        </DialogHeader>

        <div className="flex min-h-0 flex-col gap-3">
          <div className="flex items-center justify-center rounded border border-border-default bg-bg-panel p-3">
            {!src || failed || kind === 'none' ? (
              // Three different reasons converge on one honest panel: no URL
              // (unsaved workflow), a load failure (cleaned up, or an expired
              // session surfacing as a silent `error` event), or a type the
              // server refuses to serve inline.
              <div className="flex flex-col items-center gap-2 py-10 text-center">
                <FileGlyph entry={entry} className={`h-10 w-10 ${glyphToneFor(entry)}`} />
                <p className="flex items-center gap-2 text-xs text-fg-muted">
                  {failed && <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />}
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
                className="max-h-[55vh] w-auto max-w-full rounded object-contain"
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
                className="max-h-[55vh] w-full rounded"
              />
            )}
          </div>

          <div className="flex items-end justify-between gap-4">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
              <dt className="text-fg-muted">Path</dt>
              <dd className="break-all font-mono text-fg-default">{entry.path}</dd>
              <dt className="text-fg-muted">Type</dt>
              <dd className="text-fg-default">{entry.mime_type || 'unknown'}</dd>
              <dt className="text-fg-muted">Size</dt>
              <dd className="text-fg-default">{formatBytes(entry.size_bytes, entry.is_dir)}</dd>
              <dt className="text-fg-muted">Modified</dt>
              <dd className="text-fg-default">{formatModified(entry.modified_at)}</dd>
            </dl>

            <div className="flex shrink-0 items-center gap-2">
              {entry.ref && (
                <Button
                  variant="outline"
                  size="sm"
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
                  <PanelRight className="h-4 w-4" /> Open in side panel
                </Button>
              )}
              {src && (
                <Button asChild variant="outline" size="sm">
                  <a href={src} download={entry.name} target="_blank" rel="noreferrer">
                    <Download className="h-4 w-4" /> Download
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
