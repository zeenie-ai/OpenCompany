/**
 * What covers the chat while files are dragged over it (design handoff chat,
 * "Drop overlay"): a dashed frame saying the files go to the employee. It
 * only shows; ChatPane takes the drop and adds the files the way the Attach
 * button does (composer/attachments.ts).
 */

import { Upload } from 'lucide-react';
import { MAX_ATTACHMENTS } from './attachments';

export function DropOverlay({ name }: { name: string }) {
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-2.5 z-20 grid place-items-center rounded-xl border-2 border-dashed border-border-focus bg-bg-scrim-soft backdrop-blur-(--blur-scrim)"
    >
      <div className="flex flex-col items-center gap-2 text-center">
        <Upload aria-hidden className="size-7 text-border-focus" strokeWidth={1.75} />
        <span className="text-md font-semibold text-fg-default">Drop files for {name}</span>
        <span className="text-sm text-fg-muted">Documents, photos and spreadsheets, up to {MAX_ATTACHMENTS} at a time</span>
      </div>
    </div>
  );
}
