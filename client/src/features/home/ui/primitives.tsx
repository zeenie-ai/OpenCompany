/**
 * Normal mode's small building blocks (design handoff: sidebar rows, the
 * employee card, Connectors). Token classes only; the role and status
 * palettes come from features/home/data/presentation.
 */

import type { ReactNode } from 'react';
import { AvatarFace } from '@/components/catalog/primitives';
import { cn } from '@/lib/utils';
import {
  AVATAR_CLASS,
  STATUS_DOT_CLASS,
  STATUS_PILL_CLASS,
  type StatusTone,
} from '../data/presentation';
import type { ColorRole } from '../data/schemas';

const AVATAR_SIZE = {
  sm: 'size-7.5 text-xs',
  md: 'size-8 text-sm',
  lg: 'size-14 border-2 text-title',
} as const;

export function Avatar({
  name,
  colorRole,
  photo,
  size = 'md',
  status,
  pulse = false,
  className,
}: {
  name: string;
  colorRole: ColorRole;
  /** Their photo; without one (or while it fails to load), their initial. */
  photo?: string | null;
  size?: keyof typeof AVATAR_SIZE;
  /** Draws the status pip bottom-right (sidebar rows). */
  status?: StatusTone;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      className={cn(
        'relative grid shrink-0 place-items-center rounded-full border font-semibold',
        AVATAR_CLASS[colorRole],
        AVATAR_SIZE[size],
        className,
      )}
    >
      <AvatarFace name={name} photo={photo} />
      {status && (
        <StatusDot
          tone={status}
          pulse={pulse}
          className="absolute -right-px -bottom-px box-content size-2.25 border-2 border-bg-panel"
        />
      )}
    </span>
  );
}

export function StatusDot({ tone, pulse = false, className }: { tone: StatusTone; pulse?: boolean; className?: string }) {
  return (
    <span
      aria-hidden
      data-pip={pulse ? 'on' : undefined}
      className={cn('home-pip block size-1.75 rounded-full', STATUS_DOT_CLASS[tone], className)}
    />
  );
}

const PILL_SIZE = {
  md: 'h-7.5 px-3 text-sm',
  /** Beside a name (the employee page's header). */
  compact: 'h-6 px-2.25 text-xs',
  /** The Workspace header's mono micro pill. */
  sm: 'h-5.5 px-2 font-mono text-2xs tracking-label uppercase',
} as const;

export function StatusPill({
  tone,
  label,
  pulse = false,
  size = 'md',
}: {
  tone: StatusTone;
  label: string;
  pulse?: boolean;
  size?: keyof typeof PILL_SIZE;
}) {
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 rounded-pill border font-medium transition-colors',
        PILL_SIZE[size],
        STATUS_PILL_CLASS[tone],
      )}
    >
      <StatusDot tone={tone} pulse={pulse} />
      {label}
    </span>
  );
}

export function MicroLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={cn('font-mono text-2xs font-medium tracking-label text-fg-faint uppercase', className)}>{children}</span>
  );
}

export { AppMark, SearchField } from '@/components/catalog/primitives';
