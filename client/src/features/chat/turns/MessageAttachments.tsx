/**
 * The files the owner sent with a message (design handoff chat, "Thread"):
 * images as thumbnails that open full size, other files as chips with their
 * type, name and size that download. They come from the workflow's
 * workspace (the same-origin workspace route), so nothing remote loads.
 */

import { buildApiUrl } from '@/config/api';
import { isWorkspaceFileRef, type WorkspaceFileRef } from '@/types/workspaceFiles';
import { extensionLabel, sizeLabel } from '../composer/attachments';

export function MessageAttachments({ attachments }: { attachments: readonly unknown[] }) {
  const refs = attachments.filter((item): item is WorkspaceFileRef => isWorkspaceFileRef(item) && Boolean(item.url));
  if (refs.length === 0) return null;
  return (
    <ul aria-label="Attached files" className="m-0 flex max-w-[85%] list-none flex-wrap justify-end gap-2 p-0">
      {refs.map((ref) => {
        const href = buildApiUrl(ref.url ?? '');
        const image = ref.kind === 'image' || (ref.mime_type ?? '').startsWith('image/');
        return (
          <li key={ref.path}>
            {image ? (
              <a href={href} target="_blank" rel="noreferrer" className="block" title={ref.filename}>
                <img src={href} alt={ref.filename} className="block h-22.5 w-30 rounded-xl border border-border-default object-cover" />
              </a>
            ) : (
              <a
                href={href}
                download={ref.filename}
                className="flex h-12 max-w-55 items-center gap-2.5 rounded-xl border border-border-default bg-bg-panel py-0 pr-3.5 pl-2 text-fg-default no-underline hover:bg-bg-hover"
              >
                <span className="grid size-8 shrink-0 place-items-center rounded-lg border border-border-default bg-bg-active font-mono text-2xs font-semibold text-fg-muted">
                  {extensionLabel(ref.filename)}
                </span>
                <span className="flex min-w-0 flex-col">
                  <span className="truncate text-sm font-medium">{ref.filename}</span>
                  {typeof ref.size_bytes === 'number' && <span className="font-mono text-2xs text-fg-faint">{sizeLabel(ref.size_bytes)}</span>}
                </span>
              </a>
            )}
          </li>
        );
      })}
    </ul>
  );
}
