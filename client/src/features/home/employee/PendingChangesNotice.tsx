/**
 * Shown above the message box while saved changes wait for a safe handoff
 * (`pending_changes`: a tool or skill the employee added in Talk, or an
 * edit in Dev mode). The employee already uses what was added in this
 * conversation; Apply makes them available to later work.
 */

import { Button } from '@/components/ui/button';
import type { EmployeeSummary } from '../data/schemas';
import { useApplyChanges, useStopAndApply } from '../data/talk';
import { SPIKE, spikeOrb } from '../orb/orb';
import { pillToast } from '../ui/pillToast';

/** Both buttons are neutral outlines, as Pause is: colour marks a state or a
 *  role, and green `run` means Start. */
const APPLY_BUTTON = 'h-9 rounded-row border-border-strong px-4 font-semibold text-fg-default';

function applyErrorMessage(code: string, name: string): string {
  switch (code) {
    case 'not_found':
      return 'This employee is no longer on the team.';
    case 'conflict':
      return `${name} is in the middle of a change. Try again in a moment.`;
    case 'apply_failed':
    case 'safe_apply_runtime_required':
      return `${name} couldn’t apply the new abilities yet. Their current setup is still in place.`;
    default:
      return 'That did not work. Try again.';
  }
}

export function PendingChangesNotice({ employee }: { employee: EmployeeSummary }) {
  const apply = useApplyChanges();
  const interrupt = useStopAndApply();
  const { name } = employee;

  const onApply = () =>
    apply.mutate(employee.workflow_id, {
      onSuccess: () => {
        pillToast(`${name} will use the new abilities after current work finishes.`);
        spikeOrb(SPIKE.apply);
      },
      onError: (error) => pillToast(applyErrorMessage(error.message, name), { tone: 'error' }),
    });

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-card border border-border-default bg-bg-panel px-4 py-3">
      <p className="m-0 min-w-60 flex-1 text-sm text-fg-default">
        {name} has new abilities saved. Apply them after current work finishes. Their conversations and pending approvals stay in place.
      </p>
      <Button variant="quiet" disabled={apply.isPending} onClick={onApply} className={APPLY_BUTTON}>
        {apply.isPending ? 'Applying…' : 'Apply'}
      </Button>
      {(employee.pending_approvals > 0 || employee.job_progress) && <Button variant="quiet" disabled={apply.isPending || interrupt.isPending}
        onClick={() => interrupt.mutate(employee.workflow_id, { onSuccess: () => { pillToast('Stopping current work and applying the saved abilities.'); spikeOrb(SPIKE.apply); },
          onError: () => pillToast('The change could not finish. Your saved abilities are still here.', { tone: 'error' }) })} className={APPLY_BUTTON}>
        {interrupt.isPending ? 'Stopping…' : 'Stop work and apply'}
      </Button>}
    </div>
  );
}

export default PendingChangesNotice;
