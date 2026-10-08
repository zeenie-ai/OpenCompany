/**
 * What the employee did while answering (design handoff chat, "working
 * steps"): a pill that says "Working…" while the steps come and "Worked for
 * 12s · 3 steps" after, opening to the list: each step's name, what it found,
 * and a spinner, tick or cross. Open from when the run is seen working;
 * closed when read back later, until the owner opens it.
 */

import { Check, ChevronDown, Minus, X } from 'lucide-react';
import { useState } from 'react';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { RingSpinner } from '@/components/ui/ring-spinner';
import type { RunStep } from '@/lib/agui/reduceRun';
import { cn } from '@/lib/utils';
import type { TurnWork } from '../thread/model';
import { stepDetail, workLabel } from './runCopy';

function StepIcon({ step }: { step: RunStep }) {
  if (step.state === 'running') return <RingSpinner className="size-2.5 border-border-default" />;
  if (step.state === 'failed') return <X aria-hidden className="size-3 text-action-stop-ink" strokeWidth={2.2} />;
  if (step.state === 'skipped') return <Minus aria-hidden className="size-3 text-fg-faint" strokeWidth={2.2} />;
  return <Check aria-hidden className="size-3 text-action-run-ink" strokeWidth={2.2} />;
}

function StepRow({ step }: { step: RunStep }) {
  const detail = stepDetail(step);
  return (
    <li
      data-step={step.stepId}
      data-state={step.state}
      className={cn('flex items-start gap-2.5 py-1.25 transition-opacity duration-(--dur-default)', step.state === 'skipped' && 'opacity-40')}
    >
      <span className="grid size-5.5 shrink-0 place-items-center rounded-md border border-border-default bg-bg-panel">
        <StepIcon step={step} />
      </span>
      <span className="flex min-w-0 flex-col gap-px">
        <span className="text-sm font-medium text-fg-default">{step.name}</span>
        {detail && <span className="font-mono text-xs wrap-anywhere text-fg-faint">{detail}</span>}
      </span>
    </li>
  );
}

export function StepsDisclosure({ work, compact = false }: { work: TurnWork; compact?: boolean }) {
  // Open while working, and still open once the answer is in; a run read
  // back later starts closed. The owner's own choice wins either way.
  const [chosen, setChosen] = useState<boolean | null>(work.live ? true : null);
  const open = chosen ?? work.live;
  const label = workLabel(work.steps, work.live, work.durationMs);

  return (
    <Collapsible open={open} onOpenChange={setChosen} className="flex max-w-full flex-col self-start">
      <CollapsibleTrigger
        className={cn(
          'flex h-7 items-center gap-2 self-start rounded-pill border border-border-default bg-transparent pr-2.5 pl-2 text-xs font-medium whitespace-nowrap text-fg-muted transition-colors duration-(--dur-default) hover:bg-bg-hover hover:text-fg-default',
          compact && 'h-6 text-2xs',
        )}
      >
        {work.live ? (
          <RingSpinner className="size-3" />
        ) : (
          <Check aria-hidden className="size-3.25 text-action-run-ink" strokeWidth={2.4} />
        )}
        <span className={cn(work.live && 'opencompany-shimmer-text')}>{label}</span>
        <ChevronDown
          aria-hidden
          className={cn('size-3.25 transition-transform duration-(--dur-default) ease-(--ease-spring)', open && 'rotate-180')}
        />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ol aria-label="Steps" className="m-0 mt-2 mb-0.5 ml-3.25 flex list-none flex-col border-l border-border-default p-0 pl-4">
          {work.steps.map((step) => (
            <StepRow key={step.stepId} step={step} />
          ))}
        </ol>
      </CollapsibleContent>
    </Collapsible>
  );
}
