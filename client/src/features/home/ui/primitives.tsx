/**
 * Normal mode's small building blocks (design handoff: sidebar rows, the
 * employee card, Connectors). Token classes only; the role and status
 * palettes come from features/home/data/presentation.
 */

import type { ReactNode } from 'react';
import { NodeIcon } from '@/assets/icons';
import { cn } from '@/lib/utils';
import { theme } from '@/styles/theme';
import {
  AVATAR_CLASS,
  STATUS_DOT_CLASS,
  STATUS_PILL_CLASS,
  initialOf,
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
  size = 'md',
  status,
  pulse = false,
  className,
}: {
  name: string;
  colorRole: ColorRole;
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
      {initialOf(name)}
      {status && <StatusDot tone={status} pulse={pulse} className="absolute -right-px -bottom-px size-2.25 outline-2 outline-bg-panel" />}
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

export function StatusPill({ tone, label, pulse = false }: { tone: StatusTone; label: string; pulse?: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex h-7 shrink-0 items-center gap-1.5 rounded-pill border px-3 text-sm font-medium transition-colors',
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

const MARK_SIZE = {
  xs: { box: 'size-5', icon: theme.iconSize.xs },
  sm: { box: 'size-5.5', icon: theme.iconSize.xs },
  lg: { box: 'size-9.5 rounded-row', icon: theme.iconSize.md },
} as const;

/** An app's brand mark (the credential catalogue's icon), in a disc. Falls
 *  back to the app's first two letters. */
export function AppMark({
  name,
  iconRef,
  size = 'sm',
  className,
}: {
  name: string;
  iconRef?: string | null;
  size?: keyof typeof MARK_SIZE;
  className?: string;
}) {
  const spec = MARK_SIZE[size];
  const letters = name.replace(/[^\p{L}\p{N}]/gu, '').slice(0, 2).toUpperCase();
  return (
    <span
      aria-hidden
      className={cn(
        'grid shrink-0 place-items-center rounded-full border border-border-default bg-bg-panel font-mono text-2xs font-semibold text-fg-muted',
        spec.box,
        className,
      )}
    >
      <NodeIcon icon={iconRef} size={spec.icon} fallback={<span>{letters}</span>} />
    </span>
  );
}
