/**
 * The files in the message box (design handoff chat, "Composer"): an image
 * as a thumbnail, any other file as a chip with its type, name and size,
 * each with Remove. One still uploading shows a spinner; one that failed
 * says why, and Remove takes it out.
 */

import { Loader2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { BoxAttachment } from '../state/attachmentStore';
import { extensionLabel, sizeLabel } from './attachments';

function RemoveButton({ name, onRemove }: { name: string; onRemove: () => void }) {
  return (
    <Button
      variant="quiet"
      size="icon-xs"
      onClick={onRemove}
      aria-label={`Remove ${name}`}
      className="absolute -top-1.5 -right-1.5 size-5 rounded-full border border-border-default bg-bg-panel"
    >
      <X aria-hidden className="size-3" strokeWidth={2.2} />
    </Button>
  );
}

export function AttachmentChips({ items, onRemove }: { items: readonly BoxAttachment[]; onRemove: (id: string) => void }) {
  if (items.length === 0) return null;
  return (
    <ul aria-label="Attached files" className="m-0 flex list-none flex-wrap gap-2 p-0">
      {items.map((item) => (
        <li key={item.id} className="relative" title={item.error ?? item.name}>
          {item.preview && item.state !== 'failed' ? (
            <img src={item.preview} alt={item.name} className="block size-14 rounded-lg border border-border-default object-cover" />
          ) : (
            <span
              className={cn(
                'flex h-12 max-w-55 items-center gap-2.5 rounded-lg border bg-bg-panel py-0 pr-3.5 pl-2',
                item.state === 'failed' ? 'border-action-stop-border' : 'border-border-default',
              )}
            >
              <span className="grid size-8 shrink-0 place-items-center rounded-lg border border-border-default bg-bg-active font-mono text-2xs font-semibold text-fg-muted">
                {extensionLabel(item.name)}
              </span>
              <span className="flex min-w-0 flex-col">
                <span className="truncate text-sm font-medium text-fg-default">{item.name}</span>
                <span className={cn('truncate font-mono text-2xs', item.state === 'failed' ? 'text-action-stop-ink' : 'text-fg-faint')}>
                  {item.state === 'failed' ? item.error : sizeLabel(item.size)}
                </span>
              </span>
            </span>
          )}
          {item.state === 'uploading' && (
            <span role="status" aria-label={`Uploading ${item.name}`} className="absolute inset-0 grid place-items-center rounded-lg bg-bg-scrim-soft">
              <Loader2 aria-hidden className="size-4 animate-spin text-fg-muted" />
            </span>
          )}
          <RemoveButton name={item.name} onRemove={() => onRemove(item.id)} />
        </li>
      ))}
    </ul>
  );
}
