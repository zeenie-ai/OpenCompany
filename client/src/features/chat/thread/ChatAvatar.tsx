/**
 * The answering employee's avatar beside their turns: their photo, else
 * their initial, on the accent disc (chat V2 colour pass, 4 Nov), and a
 * spinning ring while they work (design handoff chat, "Streaming": the live
 * ring).
 */

import { ACCENT_AVATAR_CLASS } from '@/components/catalog/presentation';
import { AvatarFace } from '@/components/catalog/primitives';
import { cn } from '@/lib/utils';
import type { ChatPersona } from '../host';

export function ChatAvatar({ persona, live = false, compact = false }: { persona: ChatPersona; live?: boolean; compact?: boolean }) {
  return (
    <span aria-hidden className={cn('relative shrink-0', compact ? 'size-6' : '-mt-0.5 size-7.5')}>
      {live && <span data-live-ring className="opencompany-live-ring absolute -inset-0.75 rounded-full" />}
      <span
        className={cn(
          'absolute inset-0 grid place-items-center rounded-full border font-semibold',
          ACCENT_AVATAR_CLASS,
          compact ? 'text-2xs' : 'text-xs',
        )}
      >
        <AvatarFace name={persona.name} photo={persona.photo} />
      </span>
    </span>
  );
}
