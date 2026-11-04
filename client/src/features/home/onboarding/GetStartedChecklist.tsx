/**
 * The Get started checklist (onboarding handoff D): a card in Home's
 * bottom-right corner once the Welcome guide is finished, whose four steps
 * tick themselves as the owner gets going (useGetStarted). A step not done
 * yet takes the owner there. The card folds to a "Get started · n/4" pill,
 * and hiding it says where it comes back (Settings > Help). It stays out of
 * the way while the guide is open.
 */

import { Check, ChevronDown, ChevronUp, Sparkles, X } from 'lucide-react';
import { useState } from 'react';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';
import { useGetStarted, type GetStartedRow } from './useGetStarted';

function Step({ row }: { row: GetStartedRow }) {
  const body = (
    <>
      <span
        className={cn(
          'grid size-6 shrink-0 place-items-center rounded-full border',
          row.done ? 'border-action-run-border bg-action-run-soft text-action-run-ink' : 'border-border-default text-fg-muted',
        )}
      >
        {row.done ? <Check aria-hidden className="size-3.5" strokeWidth={2.5} /> : <row.icon aria-hidden className="size-3.5" />}
      </span>
      <span className="min-w-0 flex-1 text-left">
        <span className={cn('block text-sm font-medium', row.done ? 'text-fg-muted line-through' : 'text-fg-default')}>
          {row.label}
        </span>
        <span className="block truncate text-xs text-fg-muted">{row.sub}</span>
      </span>
    </>
  );
  if (row.done || !row.act) return <div className="flex items-center gap-2.5 px-2 py-1.5">{body}</div>;
  return (
    <button
      type="button"
      onClick={row.act}
      className="flex w-full items-center gap-2.5 rounded-row px-2 py-1.5 transition-colors hover:bg-bg-hover"
    >
      {body}
    </button>
  );
}

export function GetStartedChecklist() {
  const guideOpen = useHomeStore((s) => s.guide.open);
  const { visible, rows, doneCount, dismiss } = useGetStarted();
  const [folded, setFolded] = useState(false);
  if (!visible || guideOpen) return null;

  const total = rows.length;
  const allDone = doneCount === total;
  const hide = () => {
    dismiss();
    pillToast('Get started hidden. Reopen it from Settings → Help.');
  };

  if (folded) {
    return (
      <Button
        variant="quiet"
        onClick={() => setFolded(false)}
        className="fixed right-4 bottom-4 z-40 h-8 gap-1.5 rounded-pill border-border-default bg-bg-panel px-3 text-xs font-medium text-fg-default shadow-popover"
      >
        <Sparkles aria-hidden className="size-3.5" />
        Get started · {doneCount}/{total}
        <ChevronUp aria-hidden className="size-3.5 text-fg-muted" />
      </Button>
    );
  }

  return (
    <section
      aria-label="Get started"
      className="fixed right-4 bottom-4 z-40 flex w-80 flex-col gap-1 rounded-card border border-border-default bg-bg-panel p-2 shadow-popover"
    >
      <div className="flex items-center gap-2 px-2 pt-1">
        <Sparkles aria-hidden className="size-4 shrink-0 text-node-agent-ink" />
        <div className="min-w-0 flex-1">
          <h2 className="m-0 text-sm font-semibold text-fg-default">{allDone ? 'You’re all set!' : 'Get started'}</h2>
          <p className="m-0 text-xs text-fg-muted">
            {doneCount} of {total} done
          </p>
        </div>
        <Button variant="quiet" size="icon-sm" onClick={() => setFolded(true)} aria-label="Fold the checklist" className="rounded-lg">
          <ChevronDown className="size-4" />
        </Button>
        <Button variant="quiet" size="icon-sm" onClick={hide} aria-label="Hide the checklist" className="rounded-lg">
          <X className="size-4" />
        </Button>
      </div>
      <div className="px-2 pb-1.5">
        <Progress value={(doneCount / total) * 100} aria-label="Steps done" className="h-1.5" />
      </div>
      <ul className="m-0 flex list-none flex-col p-0">
        {rows.map((row) => (
          <li key={row.id}>
            <Step row={row} />
          </li>
        ))}
      </ul>
      {allDone && (
        <div className="px-2 pt-1 pb-1 text-center">
          <ActionButton intent="run" onClick={hide}>
            <Check aria-hidden className="size-4" />
            Done
          </ActionButton>
        </div>
      )}
    </section>
  );
}

export default GetStartedChecklist;
