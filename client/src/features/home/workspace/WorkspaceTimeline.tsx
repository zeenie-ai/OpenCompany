/**
 * The Workspace footer's timeline (design handoff "Workspace panel"): one
 * segment per step, coloured by its surface (Browser cyan, Mobile green,
 * Canvas purple), and under it the step shown, "n/N" and its words. It
 * follows the newest step; picking an older one shows that step, opens its
 * surface's tab and offers Jump to live. A list of what happened, not a
 * replay: the surfaces stay live. `children` sits at the end of the line
 * (Take over).
 */

import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import type { WorkspaceTab } from '@/components/workspace/WorkspaceTabs';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useWorkspaceSteps, type StepSurface, type WorkspaceStep } from './steps';

const DONE: Record<StepSurface, string> = {
  browser: 'bg-action-save-border',
  mobile: 'bg-action-run-border',
  canvas: 'bg-action-tools-border',
};
const SHOWN: Record<StepSurface, string> = {
  browser: 'bg-action-save-ink',
  mobile: 'bg-action-run-ink',
  canvas: 'bg-action-tools-ink',
};
const TAB: Record<StepSurface, WorkspaceTab> = { browser: 'browser', mobile: 'android', canvas: 'board' };

function clock(at: string): string {
  return new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function WorkspaceTimeline({
  workflowId,
  onTab,
  children,
}: {
  workflowId: string;
  onTab: (tab: WorkspaceTab) => void;
  children?: ReactNode;
}) {
  const steps = useWorkspaceSteps(workflowId).data ?? [];
  const [picked, setPicked] = useState<number | null>(null);
  const pickedIndex = picked === null ? -1 : steps.findIndex((step) => step.id === picked);
  const shown = pickedIndex >= 0 ? pickedIndex : steps.length - 1;
  const step: WorkspaceStep | undefined = steps[shown];

  // The bar keeps the newest step in view as steps arrive.
  const bar = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (pickedIndex < 0 && bar.current) bar.current.scrollLeft = bar.current.scrollWidth;
  }, [steps.length, pickedIndex]);

  const pick = (index: number) => {
    const next = steps[index];
    setPicked(index === steps.length - 1 ? null : next.id);
    onTab(TAB[next.surface]);
  };

  return (
    <div className="flex min-w-0 flex-col gap-2.5">
      {steps.length > 0 && (
        <div ref={bar} role="list" aria-label="Steps" className="flex h-3.5 items-center gap-0.75 overflow-x-auto [scrollbar-width:none]">
          {steps.map((item, index) => (
            <button
              key={item.id}
              type="button"
              role="listitem"
              aria-label={`${item.text}, ${clock(item.at)}`}
              aria-current={index === shown ? 'step' : undefined}
              title={`${item.text} · ${clock(item.at)}`}
              onClick={() => pick(index)}
              className={cn(
                'h-1.5 min-w-1.5 flex-1 rounded-pill transition-[height,background-color] duration-(--dur-default) hover:h-2.5 motion-reduce:transition-none',
                index === shown && 'h-2.5',
                index < shown ? DONE[item.surface] : index === shown ? SHOWN[item.surface] : 'bg-border-default',
              )}
            />
          ))}
        </div>
      )}
      <div className="flex items-center gap-2.5">
        <p className="m-0 min-w-0 flex-1 truncate text-meta text-fg-muted" aria-live="polite">
          {step ? (
            <>
              <span className="mr-1.5 font-mono text-2xs text-fg-faint">
                {shown + 1}/{steps.length}
              </span>
              {step.text} · {clock(step.at)}
            </>
          ) : (
            'Waiting for work'
          )}
        </p>
        {pickedIndex >= 0 && (
          <Button variant="invert" onClick={() => setPicked(null)} className="h-6 shrink-0 rounded-pill px-2.5 text-xs font-semibold">
            Jump to live
          </Button>
        )}
        {children}
      </div>
    </div>
  );
}
