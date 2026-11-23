/**
 * "Wake me when", the same button in Home's header (on an employee's page)
 * and in the editor's top toolbar (for the open workflow): the rule that
 * decides which app events the employee works on. An event the rule says is
 * not for them is dropped before any model runs (server
 * services/employees/triage.py); an empty rule means every event.
 *
 * Shown only for an employee an app event starts: the employee summary's
 * `wake_rule` is a string for one, null for any other, which hides it.
 * Saving changes the saved team, so a running employee takes the rule on
 * Apply, like any other change.
 *
 * Modal, like the Usage button beside it: the editor's canvas stops the
 * mousedown a press outside would close a plain popover with.
 */

import { useEffect, useState, type ComponentProps } from 'react';
import { AlarmClock } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Textarea } from '@/components/ui/textarea';
import { useEmployeeDetailQuery } from '@/features/home/data/employees';
import { WAKE_RULE_MAX, useSetWakeRule } from '@/features/home/data/wakeRule';

type WakeRuleButtonProps = { workflowId: string } & Pick<ComponentProps<typeof Button>, 'size' | 'className'>;

/** Home's look by default; the editor's toolbar passes its own. */
export function WakeRuleButton({ workflowId, size, className = 'h-8 gap-1.75 rounded-lg px-2.5 text-sm' }: WakeRuleButtonProps) {
  const { data: employee } = useEmployeeDetailQuery(workflowId);
  const save = useSetWakeRule();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const saved = employee?.wake_rule;

  useEffect(() => {
    if (open) setDraft(saved ?? '');
  }, [open, saved]);

  if (!employee || typeof saved !== 'string') return null;

  const rule = draft.replace(/\s+/g, ' ').trim();
  const submit = () =>
    save.mutate({ workflowId, rule }, { onSuccess: () => setOpen(false) });

  return (
    <Popover modal open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="quiet" size={size} title="Which events wake them" className={className}>
          <AlarmClock aria-hidden className="size-4" strokeWidth={1.75} />
          <span data-label>Wake</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" aria-label={`When ${employee.name} wakes`} className="flex w-80 flex-col gap-3 p-4">
        <h3 className="m-0 text-sm font-semibold text-fg-default">Wake {employee.name} when</h3>
        <Textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          maxLength={WAKE_RULE_MAX}
          rows={3}
          placeholder="Every event"
          aria-label="Wake them when"
        />
        <p className="m-0 text-xs text-fg-muted">
          An event that does not match is skipped before any work starts. Leave it empty to work on every event. While
          they run, a new rule starts when you apply your changes.
        </p>
        {save.isError && <p className="m-0 text-xs text-fg-default">That rule could not be saved. Try again.</p>}
        <div className="flex justify-end gap-2">
          <Button variant="quiet" size="sm" className="border border-border-default text-fg-default" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button variant="invert" size="sm" disabled={save.isPending || rule === (saved ?? '')} onClick={submit}>
            Save
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

export default WakeRuleButton;
