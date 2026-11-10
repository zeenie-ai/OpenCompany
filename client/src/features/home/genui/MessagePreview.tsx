/**
 * A message waiting to go out: the channel, who it is for, the text. The
 * setup screen's Draft shows one, and so does the approval step (the
 * employee page's "Waiting for you", as DraftMessagePreview). It lives on
 * its own so the employee page does not load the setup screen's renderer.
 */

import { cn } from '@/lib/utils';
import { MicroLabel } from '../ui/primitives';

export function MessagePreview({
  channel,
  to,
  subject,
  body,
  className,
}: {
  channel?: string;
  to?: string;
  subject?: string;
  body: string;
  className?: string;
}) {
  return (
    <div className={cn('overflow-hidden rounded-card border border-border-default bg-bg-elevated', className)}>
      <div className="flex items-center gap-2 border-b border-border-default bg-bg-panel px-3.5 py-2.5">
        <MicroLabel className="text-fg-muted">{channel || 'Draft'}</MicroLabel>
        <span className="truncate text-sm text-fg-muted">to {to || '…'}</span>
      </div>
      {subject && <div className="px-3.5 pt-3 text-base font-semibold text-fg-default">{subject}</div>}
      <div className="px-3.5 pt-2.5 pb-3.5 text-base leading-relaxed whitespace-pre-wrap text-fg-default">{body}</div>
    </div>
  );
}
