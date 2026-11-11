import React, { useCallback, useMemo, useRef, useState } from 'react';
import { Pin, Upload } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { uploadToWorkspace } from '@/lib/workspaceUpload';
import { useDragWorkspaceFile } from '@/hooks/useDragWorkspaceFile';
import type { WorkspaceEntry } from '@/types/workspaceFiles';

import FilePreviewDialog from './gallery/FilePreviewDialog';
import WorkspaceBrowser from './gallery/WorkspaceBrowser';
import { NodePanel } from './NodePanel';

// Colour marks state or role (docs-internal/theme_system.md): the panel's
// buttons are neutral outlines.
const NEUTRAL = 'border-border-default text-fg-default';

interface Props {
  workflowId?: string;
  parameters: Record<string, any>;
  onParameterChange: (name: string, value: any) => void;
}

/** Directory of an uploaded file, so the panel can go to where it landed. */
const parentOf = (path: string): string =>
  path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';

const GalleryPanel: React.FC<Props> = ({ workflowId, parameters, onParameterChange }) => {
  // `path` and `selection` live on the node, not in local state: what you
  // browse to is what the node lists when it runs, and what you pin is what
  // it emits. One source of truth, so the panel can't drift from the node.
  const path = typeof parameters.path === 'string' ? parameters.path : '';
  // Memoised because the `[]` fallback is a fresh array every render, which
  // would churn every dependency list that reads it.
  const selection = useMemo<string[]>(
    () => (Array.isArray(parameters.selection) ? parameters.selection : []),
    [parameters.selection],
  );
  const selectionSet = useMemo(() => new Set(selection), [selection]);

  const [preview, setPreview] = useState<WorkspaceEntry | null>(null);
  const [uploading, setUploading] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { handleFileDragStart } = useDragWorkspaceFile();

  const navigate = useCallback((next: string) => {
    onParameterChange('path', next);
  }, [onParameterChange]);

  const openEntry = useCallback((entry: WorkspaceEntry) => {
    if (entry.is_dir) navigate(entry.path);
    else setPreview(entry);
  }, [navigate]);

  const togglePin = useCallback((entry: WorkspaceEntry) => {
    const next = selectionSet.has(entry.path)
      ? selection.filter((item) => item !== entry.path)
      : [...selection, entry.path];
    onParameterChange('selection', next);
  }, [onParameterChange, selection, selectionSet]);

  const handleUpload = useCallback(async (files: FileList | null) => {
    if (!files?.length || !workflowId) return;
    setUploading(true);
    try {
      let landedIn: string | null = null;
      for (const file of Array.from(files)) {
        const ref = await uploadToWorkspace(file, workflowId);
        landedIn = parentOf(ref.path);
      }
      toast.success(files.length === 1 ? `Uploaded ${files[0].name}` : `Uploaded ${files.length} files`);
      // Uploads always land in the workspace's uploads/ directory, not the
      // folder being browsed — so go there, or the file appears to vanish.
      if (landedIn !== null && landedIn !== path) navigate(landedIn);
      else setRefreshToken((token) => token + 1);
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : 'Upload failed');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }, [navigate, path, workflowId]);

  return (
    <NodePanel
      title="Workspace Files"
      // Deliberately does NOT promise drag-to-parameter: this panel and a
      // parameter input are the two branches of one ternary inside a
      // single-node modal, so they are never on screen together. The working
      // path is the picker on the destination field.
      description={(
        <>
          Everything this workflow has produced. To use a file in another node, open that node and
          use <strong className="font-medium text-fg-default">Choose from workspace</strong> on a
          file parameter.
        </>
      )}
      actions={(
        <>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => void handleUpload(event.target.files)}
          />
          <Button
            type="button"
            variant="quiet"
            className={NEUTRAL}
            disabled={!workflowId || uploading}
            onClick={() => fileInputRef.current?.click()}
            title={workflowId ? 'Upload files into this workspace' : 'Save the workflow first'}
          >
            <Upload />
            {uploading ? 'Uploading…' : 'Upload'}
          </Button>
        </>
      )}
    >
      <div className="flex min-h-0 flex-1 flex-col p-4">
        <WorkspaceBrowser
          workflowId={workflowId}
          path={path}
          onPathChange={navigate}
          onActivate={openEntry}
          draggable
          onDragStart={handleFileDragStart}
          selectionSet={selectionSet}
          onTogglePin={togglePin}
          refreshToken={refreshToken}
        />

        {selection.length > 0 && (
          <div className="mt-2.5 flex shrink-0 items-center gap-2 text-xs text-fg-muted">
            <Pin aria-hidden className="size-3.5 fill-current text-fg-default" />
            <span>
              <span className="font-medium text-fg-default tabular-nums">{selection.length}</span>
              {' '}pinned for this node&apos;s output
            </span>
            <Button
              type="button"
              variant="quiet"
              size="xs"
              className={NEUTRAL}
              onClick={() => onParameterChange('selection', [])}
            >
              Clear
            </Button>
          </div>
        )}
      </div>

      <FilePreviewDialog entry={preview} onOpenChange={(open) => { if (!open) setPreview(null); }} />
    </NodePanel>
  );
};

export default GalleryPanel;
