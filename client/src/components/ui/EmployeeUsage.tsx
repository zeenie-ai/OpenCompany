/**
 * One employee's usage this month: what it cost, the model tokens it used
 * and the tasks it finished.
 *
 * One component for both modes. Home shows it from the Usage button on an
 * employee's page, and the cost panel (Home's Settings > Billing and Dev's
 * Settings panel) when an employee's row is opened.
 *
 * The cost and tokens are the cost panel's own figures (`get_workflow_costs`,
 * hooks/useUsageQueries.ts), so the two always agree; the tasks come from
 * `get_employee_usage {workflow_id}`. An employee with nothing booked this
 * month shows $0.00 and 0 tokens. A figure that can't be read shows a dash,
 * never a zero.
 */

import { Skeleton } from '@/components/ui/skeleton';
import { useEmployeeUsageQuery, useWorkflowCostsQuery } from '@/hooks/useUsageQueries';
import { monthDay, usd } from '@/lib/format';

function Figure({ label, value, loading }: { label: string; value: string | null; loading: boolean }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="m-0 truncate text-base font-semibold text-fg-default tabular-nums">
        {loading ? <Skeleton className="h-6 w-14" /> : (value ?? '—')}
      </dd>
    </div>
  );
}

export function EmployeeUsage({ workflowId }: { workflowId: string }) {
  const costs = useWorkflowCostsQuery();
  const tasks = useEmployeeUsageQuery(workflowId);
  // No row means nothing was booked for this employee this month.
  const row = costs.data?.workflows.find((workflow) => workflow.workflowId === workflowId);
  const since = costs.data?.since ?? tasks.data?.since;

  return (
    <div className="flex flex-col gap-2">
      <dl aria-label="Usage this month" className="m-0 grid grid-cols-3 gap-3">
        <Figure label="Cost" value={costs.data ? usd(row?.totalCost ?? 0) : null} loading={costs.isPending} />
        <Figure label="Tokens" value={costs.data ? (row?.totalTokens ?? 0).toLocaleString() : null} loading={costs.isPending} />
        <Figure label="Tasks done" value={tasks.data ? tasks.data.tasksThisMonth.toLocaleString() : null} loading={tasks.isPending} />
      </dl>
      {since && <p className="m-0 text-xs text-fg-muted">Since {monthDay(since)}. The cost is an estimate.</p>}
    </div>
  );
}

export default EmployeeUsage;
