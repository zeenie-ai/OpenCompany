/**
 * What the employee is doing, above the message box while their run works
 * (design handoff chat V2 §3, "Live work"): one pill with the step under way,
 * how many steps have finished of those begun and how long the run has
 * worked ("2/3 · 4s"), opening to the steps' names once there are any. It
 * reads the steps the turn reads (`withApprovalSteps`), and while the run
 * waits for the owner's OK on a call it says so beside the waiting dot. The
 * turn keeps the full record; the pill goes when the run ends.
 *
 * It stays mounted without a run, so whether the owner opened it carries
 * over to the next one. When it goes while its trigger has focus, focus
 * moves to the message box (onLeave), not to the page.
 */

import { Check, ChevronDown, Minus, X } from 'lucide-react';
import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { RingSpinner } from '@/components/ui/ring-spinner';
import type { RunSnapshot, RunStep } from '@/lib/agui/reduceRun';
import { cn } from '@/lib/utils';
import { ApprovalsContext } from '../approval/context';
import { NO_APPROVALS, turnApprovalIds, waitsOnOwner } from '../data/approvals';
import { liveText, liveWorkLabel, liveWorkMeta, withApprovalSteps } from '../turns/runCopy';

/** The spinner in the pill's neutral ink, not the chat's working tint. */
const SPINNER = 'size-3 border-border-default border-t-fg-default';

/** What a screen reader hears for a step's mark. */
const STEP_STATE: Record<RunStep['state'], string> = {
  running: 'in progress',
  done: 'done',
  failed: 'failed',
  skipped: 'skipped',
};

function StepMark({ state }: { state: RunStep['state'] }) {
  if (state === 'running') return <RingSpinner className={SPINNER} />;
  const Mark = state === 'done' ? Check : state === 'failed' ? X : Minus;
  return <Mark aria-hidden className="size-3 shrink-0" strokeWidth={2.4} />;
}

export function LiveWork({
  run,
  name,
  stopping,
  compact,
  onLeave,
}: {
  /** The run working now; null while none is. */
  run: RunSnapshot | null;
  name: string;
  stopping: boolean;
  compact: boolean;
  /** The pill went while its trigger had focus. Keep it stable: a new
   *  function re-attaches the trigger's ref. */
  onLeave?: () => void;
}) {
  const [open, setOpen] = useState(false);
  // React runs a ref's cleanup before the node leaves the page, while it can
  // still hold focus.
  const triggerRef = useCallback(
    (node: HTMLButtonElement | null) => {
      if (!node) return;
      return () => {
        if (document.activeElement === node) onLeave?.();
      };
    },
    [onLeave],
  );
  const live = run !== null;
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!live) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [live]);
  const known = useContext(ApprovalsContext)?.approvals ?? NO_APPROVALS;
  const steps = useMemo(
    () => (run ? withApprovalSteps(run.steps, turnApprovalIds(null, run, known), known) : []),
    [run, known],
  );
  if (!run) return null;

  const waiting = waitsOnOwner(known, run.runId);
  const since = Date.parse(run.startedAt ?? run.createdAt ?? '');
  const meta = liveWorkMeta(steps, Number.isNaN(since) ? null : Math.max(0, now - since));
  // Before its first step there is nothing to open: most replies take none.
  const expandable = steps.length > 0;
  const shown = open && expandable;

  return (
    <Collapsible asChild open={shown} onOpenChange={setOpen} disabled={!expandable}>
      <section
        aria-label={`${name}’s progress`}
        className="flex max-w-full flex-col self-center overflow-hidden rounded-card border border-border-default bg-bg-panel"
      >
        <CollapsibleTrigger
          ref={triggerRef}
          className={cn(
            'flex h-8.5 max-w-full items-center gap-2 pr-2.5 pl-3 text-xs font-medium text-fg-default focus-visible:-outline-offset-2 enabled:hover:bg-bg-hover',
            compact && 'h-7 text-2xs',
          )}
        >
          {waiting ? (
            <span aria-hidden className="grid size-3 shrink-0 place-items-center">
              <span className="opencompany-pip-pulse size-1.5 rounded-full bg-action-config-ink" />
            </span>
          ) : (
            <RingSpinner className={SPINNER} />
          )}
          <span role="status" className="min-w-0 truncate">
            {liveWorkLabel(steps, { writing: liveText(run).streaming, waiting, stopping })}
          </span>
          {/* Out of the button's name, which would otherwise change every second. */}
          {meta && (
            <span aria-hidden className="shrink-0 text-fg-muted tabular-nums">
              {meta}
            </span>
          )}
          {expandable && (
            <ChevronDown
              aria-hidden
              strokeWidth={2.2}
              className={cn(
                'size-3 shrink-0 text-fg-muted transition-transform duration-(--dur-default) motion-reduce:transition-none',
                shown && 'rotate-180',
              )}
            />
          )}
        </CollapsibleTrigger>
        <CollapsibleContent>
          <ol
            aria-label="Steps"
            className="m-0 flex max-h-30 list-none flex-col gap-0.5 overflow-y-auto border-t border-border-default px-3 pt-1 pb-2.5"
          >
            {steps.map((step) => (
              <li
                key={step.stepId}
                className={cn('flex min-h-6 items-center gap-2 text-xs', step.state === 'running' ? 'text-fg-default' : 'text-fg-muted')}
              >
                <StepMark state={step.state} />
                <span className="min-w-0 truncate">{step.name}</span>
                <span className="sr-only">, {STEP_STATE[step.state]}</span>
              </li>
            ))}
          </ol>
        </CollapsibleContent>
      </section>
    </Collapsible>
  );
}
