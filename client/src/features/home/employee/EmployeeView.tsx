/**
 * One employee's page (design handoff chat): the conversation with them
 * (EmployeeChat) fills the page under the header, which names them; the
 * orb stays above the conversation, and a new hire's first-day strip
 * (FirstDay) and notes (HireNotice) open the thread.
 *
 * What to act on lives above the message box, and only while there is
 * something to do: their main action while they cannot read messages
 * (Resume, Start, or connect what they are missing; useEmployeeControl),
 * Apply, and Help in browser while they wait for the owner there; the
 * drafts to check follow the conversation. Pausing them is the
 * Workspace's, which the header's Workspace pill opens on the employee on
 * screen; the header's Dev switch opens their workflow.
 */

import { useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useEmployeeDetailQuery, useEmployeesQuery } from '../data/employees';
import { useLiveTask } from '../data/liveTask';
import type { EmployeeSummary } from '../data/schemas';
import { OrbSlot } from '../orb/OrbSlot';
import { SPIKE, spikeOrb } from '../orb/orb';
import { useHomeStore } from '../state/homeStore';
import { EmployeeChat } from './EmployeeChat';
import { useEmployeeControl } from './useEmployeeControl';

/** The orb stirs when their work moves on (design handoff "Live work"). */
function useTaskSpike(employee: EmployeeSummary): void {
  const live = useLiveTask(employee);
  const text = (live ?? employee.task)?.text;
  const shown = useRef(text);
  useEffect(() => {
    if (shown.current === text) return;
    shown.current = text;
    if (text) spikeOrb(SPIKE.task);
  }, [text]);
}

function EmployeePage({
  employee,
  onConnect,
  onScrolledChange,
}: {
  employee: EmployeeSummary;
  onConnect: (providerId: string) => void;
  onScrolledChange?: (scrolled: boolean) => void;
}) {
  const control = useEmployeeControl(employee, onConnect);
  useTaskSpike(employee);
  return <EmployeeChat employee={employee} control={control} onScrolledChange={onScrolledChange} />;
}

export function EmployeeView({
  workflowId,
  onConnect,
  onScrolledChange,
}: {
  workflowId: string;
  onConnect: (providerId: string) => void;
  /** The conversation left its top, or came back (the header's border). */
  onScrolledChange?: (scrolled: boolean) => void;
}) {
  const list = useEmployeesQuery();
  const fromList = list.data?.find((e) => e.workflow_id === workflowId) ?? null;
  // Only when the list has no row for it (just hired, or the list failed).
  const detailId = fromList || list.isPending ? null : workflowId;
  const detail = useEmployeeDetailQuery(detailId);
  const showHire = useHomeStore((s) => s.showHire);
  const employee = fromList ?? detail.data ?? null;

  if (!employee) {
    if (list.isPending || (detailId !== null && detail.isPending)) {
      return (
        <section aria-busy className="flex w-full flex-col items-center gap-4 px-6 pt-7">
          <OrbSlot size="employee" />
          <Skeleton className="h-10 w-full max-w-(--w-chat-column) rounded-card" />
        </section>
      );
    }
    return (
      <section className="flex w-full flex-col items-center gap-3 px-6 pt-16 text-center">
        <p className="m-0 text-md text-fg-default">
          {detail.isError ? 'Couldn’t load this employee.' : 'This employee is no longer on the team.'}
        </p>
        <div className="flex gap-2">
          {detail.isError && (
            <Button variant="quiet" onClick={() => void detail.refetch()} className="border-border-default text-fg-default">
              Try again
            </Button>
          )}
          <Button variant="quiet" onClick={() => showHire()} className="border-border-default text-fg-default">
            Back to hiring
          </Button>
        </div>
      </section>
    );
  }

  return <EmployeePage employee={employee} onConnect={onConnect} onScrolledChange={onScrolledChange} />;
}

export default EmployeeView;
