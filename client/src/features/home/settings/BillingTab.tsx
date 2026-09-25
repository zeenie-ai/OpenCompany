/**
 * Settings > Billing, usage only: what the team did this month, and how
 * many employees it has (the sidebar's count). No billing account sits
 * behind OpenCompany yet, so there are no plans, payment method or
 * invoices (docs-internal/normal_mode.md, Known gaps). A count that can't
 * be read shows a dash, never a false zero.
 */

import { Skeleton } from '@/components/ui/skeleton';
import { useEmployeesQuery } from '../data/employees';
import { useEmployeeUsageQuery } from '../data/usage';
import { MicroLabel } from '../ui/primitives';

function Stat({ label, value, loading }: { label: string; value: number | null; loading: boolean }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-card border border-border-default bg-bg-elevated p-4">
      <MicroLabel>{label}</MicroLabel>
      {loading ? (
        <Skeleton className="h-7 w-14" />
      ) : (
        <span className="text-title font-semibold tracking-[-0.02em] text-fg-default tabular-nums">{value ?? '—'}</span>
      )}
    </div>
  );
}

export function BillingTab() {
  const usage = useEmployeeUsageQuery();
  const team = useEmployeesQuery();
  return (
    <div className="flex max-w-170 flex-col gap-5 px-8 pt-7 pb-8">
      <div data-stagger className="flex flex-col gap-1 pr-9">
        <h2 className="text-lg font-semibold tracking-[-0.01em] text-fg-default">Billing</h2>
        <p className="text-sm text-pretty text-fg-muted">What your employees have done this month.</p>
      </div>
      <div data-stagger className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-2.5">
        <Stat label="Tasks done this month" value={usage.data?.tasksThisMonth ?? null} loading={usage.isPending} />
        <Stat label="Employees" value={team.data?.length ?? null} loading={team.isPending} />
      </div>
    </div>
  );
}

export default BillingTab;
