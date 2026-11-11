import React, { useCallback, useRef, useState } from 'react';
import { Upload } from 'lucide-react';
import { toast } from 'sonner';

import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { uploadToWorkspace } from '@/lib/workspaceUpload';
import type { WorkspaceEntry } from '@/types/workspaceFiles';

import WorkspaceBrowser from './WorkspaceBrowser';

// Colour marks state or role (docs-internal/theme_system.md): these buttons
// are neutral outlines, and a panel's one strong action is `invert`.
const ACTION_SHAPE = 'rounded-md px-3.5 text-[13px] font-semibold';

/**
 * Pick a workspace file for a parameter, using only discrete clicks.
 *
 * This is the WCAG 2.2 SC 2.5.7 alternative to dragging a file out of the
 * gallery — and, because the gallery panel and a parameter input can never be
 * on screen together (they are the two branches of one ternary inside a
 * single-node modal), it is also the only assignment path that works in-app.
 *
 * It deliberately renders **no drag surface**: an alternative that reintroduced
 * the barrier it exists to remove would be pointless. `draggable` is off, so no
 * row carries a drag payload.
 */
interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflowId?: string;
  /** Called with the chosen row. The dialog never writes a parameter itself. */
  onChoose: (entry: WorkspaceEntry) => void;
}

const parentOf = (path: string): string =>
  path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';

const WorkspaceFilePickerDialog: React.FC<Props> = ({
  open, onOpenChange, workflowId, onChoose,
}) => {
  const [path, setPath] = useState('');
  const [selected, setSelected] = useState<WorkspaceEntry | null>(null);
  const [uploading, setUploading] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleActivate = useCallback((entry: WorkspaceEntry) => {
    if (entry.is_dir) {
      setSelected(null);
      setPath(entry.path);
      return;
    }
    // Single click selects. Confirming is a separate, explicit click — so the
    // whole operation is two discrete activations with no pointer travel.
    setSelected(entry);
  }, []);

  const handlePathChange = useCallback((next: string) => {
    setSelected(null);
    setPath(next);
  }, []);

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
      // An empty workspace must not be a dead end, so go to where it landed.
      if (landedIn !== null && landedIn !== path) handlePathChange(landedIn);
      else setRefreshToken((token) => token + 1);
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : 'Upload failed');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }, [handlePathChange, path, workflowId]);

  const confirm = () => {
    if (!selected) return;
    onChoose(selected);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[70vh] max-w-4xl flex-col">
        <DialogHeader>
          <DialogTitle>Choose a workspace file</DialogTitle>
        </DialogHeader>

        <WorkspaceBrowser
          workflowId={workflowId}
          path={path}
          onPathChange={handlePathChange}
          onActivate={handleActivate}
          activePath={selected?.path ?? null}
          refreshToken={refreshToken}
        />

        <div className="flex shrink-0 items-center justify-between gap-3 border-t border-border-default pt-3">
          <div className="min-w-0 text-xs text-fg-muted">
            {selected
              ? <span className="truncate font-mono text-fg-default">{selected.path}</span>
              : 'Select a file to continue.'}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(event) => void handleUpload(event.target.files)}
            />
            <Button
              variant="outline"
              size="sm"
              disabled={!workflowId || uploading}
              onClick={() => fileInputRef.current?.click()}
            >
              <Upload className="h-4 w-4" /> {uploading ? 'Uploading…' : 'Upload'}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="button" variant="invert" className={ACTION_SHAPE} disabled={!selected} onClick={confirm}>
              Use this file
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default WorkspaceFilePickerDialog;
