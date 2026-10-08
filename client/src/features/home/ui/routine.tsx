/**
 * A routine as a timeline (onboarding handoff R2): a thin rule down the left
 * and one row per step, its role dot on the rule with a halo the colour of
 * the card, a mono label in the role's ink (WHEN / THEY / USING / THEN),
 * then the step on one line, its title and " · detail" in the muted colour.
 * A row may end in an action (the setup card's "Change" on the When row).
 *
 * `md` is the setup card's size, `sm` a new hire's first day.
 */

import type { ReactNode, Ref } from 'react';
import { cn } from '@/lib/utils';
import type { ColorRole } from '../data/schemas';

export type RoutineRole = Extract<ColorRole, 'trigger' | 'agent' | 'tool' | 'workflow'>;

const LABEL: Record<RoutineRole, string> = { trigger: 'When', agent: 'They', tool: 'Using', workflow: 'Then' };

// Tailwind scans these literals.
const DOT: Record<RoutineRole, string> = {
  trigger: 'bg-node-trigger',
  agent: 'bg-node-agent',
  tool: 'bg-node-tool',
  workflow: 'bg-node-workflow',
};

const INK: Record<RoutineRole, string> = {
  trigger: 'text-node-trigger-ink',
  agent: 'text-node-agent-ink',
  tool: 'text-node-tool-ink',
  workflow: 'text-node-workflow-ink',
};

const SIZE = {
  md: { row: 'min-h-9', dot: 'size-2.25', text: 'text-base' },
  sm: { row: 'min-h-8', dot: 'size-2', text: 'text-row' },
} as const;

export function RoutineTimeline({ children, className, ref }: { children: ReactNode; className?: string; ref?: Ref<HTMLDivElement> }) {
  return (
    <div ref={ref} className={cn('relative flex flex-col', className)}>
      <span aria-hidden className="absolute top-3.5 bottom-3.5 left-1.25 w-px bg-border-default" />
      {children}
    </div>
  );
}

export function RoutineRow({
  role,
  title,
  detail,
  action,
  size = 'md',
}: {
  role: RoutineRole;
  title: string;
  detail?: string;
  action?: ReactNode;
  size?: keyof typeof SIZE;
}) {
  const s = SIZE[size];
  return (
    <div data-routine-row className={cn('relative grid grid-cols-[12px_52px_minmax(0,1fr)_auto] items-center gap-2.5', s.row)}>
      <span aria-hidden className={cn('justify-self-center rounded-full ring-3 ring-bg-panel', s.dot, DOT[role])} />
      <span className={cn('font-mono text-2xs font-medium tracking-label uppercase', INK[role])}>{LABEL[role]}</span>
      <span className={cn('truncate leading-snug', s.text)}>
        <span className="text-fg-default">{title}</span>
        {detail && <span className="text-fg-muted"> · {detail}</span>}
      </span>
      {action ?? <span />}
    </div>
  );
}
