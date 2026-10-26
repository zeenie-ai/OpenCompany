/**
 * "Allowed without asking…", from the Ask first menu (RFC-0007 §4.4, D19;
 * wording R5 §5): what the employee does without asking first, whatever
 * the mode. The rules the owner made with Always allow on a card, each with
 * Remove, and a switch for each of the employee's money tools, since a card
 * never offers Always allow for money. A change applies from the
 * employee's next call (data/allows.ts).
 */

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { useAddApprovalAllow, useApprovalAllows, useRemoveApprovalAllow } from '../data/allows';

export function AllowedWithoutAsking({
  workflowId,
  name,
  open,
  onOpenChange,
  onError,
}: {
  workflowId: string;
  name: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onError: (message: string) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="gap-5 sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Allowed without asking</DialogTitle>
          <DialogDescription>{name} does these without asking you first, whatever Ask me first says.</DialogDescription>
        </DialogHeader>
        {/* Mounted only while open, so every open reads the list again. */}
        <AllowList workflowId={workflowId} name={name} onError={onError} />
      </DialogContent>
    </Dialog>
  );
}

function added(createdAt: string | null): string | null {
  const date = createdAt ? new Date(createdAt) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : null;
}

function AllowList({ workflowId, name, onError }: { workflowId: string; name: string; onError: (message: string) => void }) {
  const query = useApprovalAllows(workflowId);
  const add = useAddApprovalAllow(workflowId, onError);
  const remove = useRemoveApprovalAllow(workflowId, onError);
  const busy = add.isPending || remove.isPending;

  if (query.isPending) return <Skeleton className="h-16 w-full rounded-card" />;
  if (query.isError) {
    return (
      <p className="m-0 flex items-center gap-2 text-sm text-fg-muted">
        Couldn’t load the list.
        <Button variant="quiet" size="sm" onClick={() => void query.refetch()} className="font-semibold">
          Try again
        </Button>
      </p>
    );
  }
  const { rules, money } = query.data;
  return (
    <div className="flex flex-col gap-5">
      {rules.length > 0 ? (
        <ul className="m-0 flex list-none flex-col p-0">
          {rules.map((rule) => {
            const when = added(rule.createdAt);
            return (
              <li key={rule.id} className="flex items-center gap-3 border-b border-border-default py-1.5 last:border-b-0">
                <span className="min-w-0 flex-1 text-sm text-fg-default">
                  {rule.label}
                  {when && <span className="text-xs text-fg-muted"> · added {when}</span>}
                </span>
                <Button
                  variant="quiet"
                  size="sm"
                  disabled={busy}
                  aria-label={`Remove ${rule.label}`}
                  onClick={() => remove.mutate(rule.id)}
                  className="font-semibold"
                >
                  Remove
                </Button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="m-0 text-sm text-fg-muted">Nothing yet. When {name} asks, choose Always allow to add it here.</p>
      )}
      {money.length > 0 && (
        <section aria-label="Money and deletes" className="flex flex-col gap-2">
          <h3 className="m-0 text-sm font-semibold text-fg-default">Money and deletes</h3>
          <p className="m-0 text-xs text-fg-muted">These always ask unless you switch them on here.</p>
          {money.map((option) => (
            <div key={`${option.nodeType}:${option.operation}`} className="flex items-center gap-3 py-1">
              <span className="min-w-0 flex-1 text-sm text-fg-default">{option.label}</span>
              <Switch
                tone="run"
                aria-label={`Allow ${option.label} without asking`}
                checked={option.allowId !== null}
                disabled={busy}
                onCheckedChange={(on) => {
                  if (on) add.mutate(option);
                  else if (option.allowId) remove.mutate(option.allowId);
                }}
              />
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

export default AllowedWithoutAsking;
