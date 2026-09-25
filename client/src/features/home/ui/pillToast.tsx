/* eslint-disable react-refresh/only-export-components -- the toaster and its trigger function travel together. */
/**
 * Normal mode's toast (design handoff "Toast"): a bottom-centre pill on the
 * elevated surface with a status-coloured border, held for
 * --dur-toast-hold. It renders in its own sonner Toaster (`id="pill"`), so
 * Normal mode's toasts never mix with the editor's top-right stack and the
 * editor's never land here (sonner routes by `toasterId`).
 */

import { toast } from 'sonner';
import { Toaster } from '@/components/ui/sonner';
import { dur } from '@/lib/motion';
import { Sounds } from '@/lib/sound';
import { cn } from '@/lib/utils';
import { STATUS_DOT_CLASS } from '../data/presentation';

export const PILL_TOASTER_ID = 'pill';

export type PillTone = 'success' | 'info' | 'error';

const TONE: Record<PillTone, { border: string; dot: string }> = {
  success: { border: 'border-status-working-border', dot: STATUS_DOT_CLASS.working },
  info: { border: 'border-status-ready-border', dot: STATUS_DOT_CLASS.ready },
  error: { border: 'border-status-attention-border', dot: STATUS_DOT_CLASS.attention },
};

function PillToast({ message, tone }: { message: string; tone: PillTone }) {
  return (
    <div
      role="status"
      className={cn(
        'flex items-center gap-2.5 rounded-pill border bg-bg-elevated px-4 py-2.5 font-body text-sm font-medium text-fg-default shadow-float',
        TONE[tone].border,
      )}
    >
      <span aria-hidden className={cn('size-1.75 shrink-0 rounded-full', TONE[tone].dot)} />
      {message}
    </div>
  );
}

export function pillToast(message: string, options: { tone?: PillTone } = {}): string | number {
  const tone = options.tone ?? 'success';
  if (tone === 'success') Sounds.play('success');
  else if (tone === 'error') Sounds.play('error');
  return toast.custom(() => <PillToast message={message} tone={tone} />, {
    toasterId: PILL_TOASTER_ID,
    duration: dur('toast-hold'),
  });
}

export function PillToaster() {
  return <Toaster id={PILL_TOASTER_ID} position="bottom-center" toastOptions={{ unstyled: true }} />;
}
