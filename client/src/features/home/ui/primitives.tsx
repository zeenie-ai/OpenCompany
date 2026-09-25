/**
 * Normal mode's small building blocks (design handoff: sidebar rows, the
 * employee card, Connectors). Token classes only; the role and status
 * palettes come from features/home/data/presentation.
 */

import type { ReactNode } from 'react';
import { Search } from 'lucide-react';
import { NodeIcon } from '@/assets/icons';
import { Input } from '@/components/ui/input';
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

export function StatusPill({ tone, label, pulse = false }: { tone: StatusTone; label: string; pulse?: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex h-7.5 shrink-0 items-center gap-1.5 rounded-pill border px-3 text-sm font-medium transition-colors',
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
  xl: { box: 'size-12.5 rounded-card text-sm', icon: theme.iconSize.lg },
} as const;

/** An app's brand mark (the credential catalogue's icon), in a disc. Falls
 *  back to the app's first two letters, tinted in `tone` when given (a
 *  skill or starter tile). */
export function AppMark({
  name,
  iconRef,
  size = 'sm',
  tone,
  className,
}: {
  name: string;
  iconRef?: string | null;
  size?: keyof typeof MARK_SIZE;
  tone?: ColorRole;
  className?: string;
}) {
  const spec = MARK_SIZE[size];
  const letters = name.replace(/[^\p{L}\p{N}]/gu, '').slice(0, 2).toUpperCase();
  return (
    <span
      aria-hidden
      className={cn(
        'grid shrink-0 place-items-center rounded-full border font-mono text-2xs font-semibold',
        tone ? AVATAR_CLASS[tone] : 'border-border-default bg-bg-panel text-fg-muted',
        spec.box,
        className,
      )}
    >
      <NodeIcon icon={iconRef} size={spec.icon} fallback={<span>{letters}</span>} />
    </span>
  );
}

/** A search box on a Home surface (Settings nav, the catalog pages). The
 *  placeholder doubles as its accessible name unless `label` says otherwise. */
export function SearchField({
  value,
  onChange,
  placeholder,
  label,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  label?: string;
  className?: string;
}) {
  return (
    <div className={cn('relative min-w-0', className)}>
      <Search aria-hidden className="pointer-events-none absolute top-1/2 left-3 size-3.75 -translate-y-1/2 text-fg-faint" />
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-label={label ?? placeholder}
        className="h-9 rounded-row bg-bg-app pl-8.5 text-row font-normal md:text-row dark:bg-bg-app"
      />
    </div>
  );
}
