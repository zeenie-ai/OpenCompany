/**
 * A routine, two ways.
 *
 * - As a timeline (onboarding handoff R2, the setup card): a thin rule down
 *   the left and one row per step, its role dot on the rule with a halo the
 *   colour of the card, a mono label in the role's ink (WHEN / THEY / USING
 *   / THEN), then the step on one line, its title and " · detail" in the
 *   muted colour. A row may end in an action (the setup card's "Change" on
 *   the When row).
 * - On one line (onboarding v3, a new hire's first-day strip): each step's
 *   mono label and title only, with a faint separator between steps.
 */

import type { ReactNode, Ref } from 'react';
import { cn } from '@/lib/utils';
import type { ColorRole } from '../data/schemas';
import { MicroLabel } from './primitives';

export type RoutineRole = Extract<ColorRole, 'trigger' | 'agent' | 'tool' | 'workflow'>;

const LABEL: Record<RoutineRole, string> = { trigger: 'When', agent: 'They', tool: 'Using', workflow: 'Then' };

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
}: {
  role: RoutineRole;
  title: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div data-routine-row className="relative grid min-h-9 grid-cols-[12px_52px_minmax(0,1fr)_auto] items-center gap-2.5">
      <span aria-hidden className="size-2.25 justify-self-center rounded-full bg-fg-faint ring-3 ring-bg-panel" />
      <MicroLabel className="text-fg-muted">{LABEL[role]}</MicroLabel>
      <span className="truncate text-base leading-snug">
        <span className="text-fg-default">{title}</span>
        {detail && <span className="text-fg-muted"> · {detail}</span>}
      </span>
      {action ?? <span />}
    </div>
  );
}

export function RoutineLine({
  steps,
  label,
  className,
}: {
  steps: readonly { role: RoutineRole; title: string }[];
  label: string;
  className?: string;
}) {
  return (
    <ul aria-label={label} className={cn('m-0 flex list-none flex-wrap items-center gap-x-2 gap-y-1.5 p-0 text-meta', className)}>
      {steps.map((step, index) => (
        <li key={index} className="inline-flex min-w-0 items-center gap-1.5">
          <MicroLabel className="text-fg-muted">{LABEL[step.role]}</MicroLabel>
          <span className="text-fg-muted">{step.title}</span>
          {index < steps.length - 1 && <span aria-hidden className="text-border-strong">·</span>}
        </li>
      ))}
    </ul>
  );
}
