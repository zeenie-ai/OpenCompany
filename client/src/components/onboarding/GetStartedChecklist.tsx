import React from 'react';
import { Sparkles, Check, ChevronDown, ChevronUp, X } from 'lucide-react';
import { toast } from 'sonner';

import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Button } from '@/components/ui/button';
import { ActionButton } from '@/components/ui/action-button';
import { cn } from '@/lib/utils';
import { useGetStarted } from '../../hooks/useGetStarted';
import { GET_STARTED_ITEMS, type GetStartedItemId } from './getStartedItems';

interface GetStartedChecklistProps {
  /** Per-item click actions, wired by the Dashboard. Items without an
   *  action (or already completed) render as plain rows. */
  actions?: Partial<Record<GetStartedItemId, () => void>>;
}

const GetStartedChecklist: React.FC<GetStartedChecklistProps> = ({ actions }) => {
  const { visible, items, completedCount, totalCount, dismiss } = useGetStarted();
  const [collapsed, setCollapsed] = React.useState(false);

  if (!visible) return null;

  const handleDismiss = () => {
    dismiss();
    toast.info('Get started hidden — reopen it anytime from Settings → Help.');
  };

  const allComplete = completedCount === totalCount;
  const completedById = new Map(items.map((item) => [item.id, item.completed]));

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        className="fixed bottom-14 right-4 z-40 inline-flex h-8 items-center gap-1.5 rounded-full border border-border bg-card px-3 text-xs font-medium shadow-md hover:bg-accent"
      >
        <Sparkles className="h-3.5 w-3.5" />
        Get started · {completedCount}/{totalCount}
        <ChevronUp className="h-3.5 w-3.5 text-muted-foreground" />
      </button>
    );
  }

  return (
    <Card className="fixed bottom-14 right-4 z-40 w-80 shadow-md">
      <CardHeader className="flex flex-row items-center gap-2 space-y-0 px-4 pt-3 pb-2">
        <Sparkles className="h-4 w-4 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold">
            {allComplete ? "You're all set!" : 'Get started'}
          </div>
          <div className="text-xs text-muted-foreground">
            {completedCount} of {totalCount} complete
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          onClick={() => setCollapsed(true)}
          aria-label="Collapse checklist"
        >
          <ChevronDown className="h-4 w-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          onClick={handleDismiss}
          aria-label="Dismiss checklist"
        >
          <X className="h-4 w-4" />
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-1 px-2 pb-3">
        <div className="px-2 pb-2">
          <Progress value={(completedCount / totalCount) * 100} className="h-1.5" />
        </div>

        {GET_STARTED_ITEMS.map((item) => {
          const completed = completedById.get(item.id) ?? false;
          const action = actions?.[item.id];
          const clickable = !completed && item.actionable && action !== undefined;
          const row = (
            <>
              <span
                className={cn(
                  'flex h-6 w-6 shrink-0 items-center justify-center rounded-full border',
                  completed
                    ? 'border-primary bg-primary text-primary-foreground'
                    : 'border-border text-muted-foreground',
                )}
              >
                {completed ? <Check className="h-3.5 w-3.5" /> : <item.icon className="h-3.5 w-3.5" />}
              </span>
              <span className="min-w-0 flex-1 text-left">
                <span
                  className={cn(
                    'block text-sm font-medium',
                    completed && 'text-muted-foreground line-through',
                  )}
                >
                  {item.label}
                </span>
                <span className="block text-xs text-muted-foreground">{item.sublabel}</span>
              </span>
            </>
          );

          return clickable ? (
            <button
              key={item.id}
              type="button"
              onClick={action}
              className="flex items-center gap-2.5 rounded-md px-2 py-1.5 hover:bg-accent"
            >
              {row}
            </button>
          ) : (
            <div key={item.id} className="flex items-center gap-2.5 rounded-md px-2 py-1.5">
              {row}
            </div>
          );
        })}

        {allComplete && (
          <div className="mt-1 px-2 text-center">
            <ActionButton intent="run" onClick={handleDismiss}>
              <Check className="h-4 w-4" />
              Done
            </ActionButton>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default GetStartedChecklist;
