/**
 * A skill's state in an agent's run (services/skill_runtime.py), as the
 * Master Skill editor and an agent's Connected Skills list show it: a dot
 * beside a neutral word (StatePill), pulsing while the skill loads, an ink
 * tick once the agent used it. With no state to mark, `idle` is the word
 * alone.
 */

import { cn } from '@/lib/utils';
import { StateMark, type StateMarkProps } from './StatePill';

const LOADED: { label: string; mark: StateMarkProps } = { label: 'Loaded', mark: { tone: 'ready' } };

const STATES: Record<string, { label: string; mark: StateMarkProps }> = {
  loading: { label: 'Loading', mark: { tone: 'working', pulse: true } },
  loaded: LOADED,
  // It read one of the skill's files: still a loaded skill.
  resource_read: LOADED,
  used: { label: 'Used', mark: { tone: 'ready', done: true } },
  failed: { label: 'Failed', mark: { tone: 'attention' } },
};

export function SkillState({ state, idle, className }: { state?: string; idle?: string; className?: string }) {
  const known = state ? (STATES[state] ?? LOADED) : undefined;
  if (!known && !idle) return null;
  return (
    <span className={cn('inline-flex shrink-0 items-center gap-1 font-medium text-fg-muted', className)}>
      {known && <StateMark {...known.mark} />}
      {known ? known.label : idle}
    </span>
  );
}
