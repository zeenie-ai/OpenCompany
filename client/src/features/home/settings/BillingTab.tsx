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

/** "Oct 1": the server's YYYY-MM-DD read as a calendar date, not an instant. */
function monthDay(date: string): string {
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(`${date}T00:00:00Z`));
}

function Stat({ label, value, note, loading }: { label: string; value: number | null; note: string | null; loading: boolean }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-card border border-border-default bg-bg-elevated p-4.5">
      <MicroLabel>{label}</MicroLabel>
      {loading ? (
        <Skeleton className="h-9 w-16" />
      ) : (
        <span className="text-2xl font-semibold tracking-hero text-fg-default tabular-nums">{value ?? '—'}</span>
      )}
      {note && <span className="text-meta text-fg-muted">{note}</span>}
    </div>
  );
}

export function BillingTab() {
  const usage = useEmployeeUsageQuery();
  const team = useEmployeesQuery();
  return (
    <div className="flex max-w-170 flex-col gap-5 px-8 pt-7 pb-8">
      <div data-stagger className="flex flex-col gap-1 pr-9">
        <h2 className="text-xl font-semibold tracking-[-0.01em] text-fg-default">Billing</h2>
        <p className="text-sm text-pretty text-fg-muted">What your team has done this month.</p>
      </div>
      <div data-stagger className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-3">
        <Stat
          label="Tasks this month"
          value={usage.data?.tasksThisMonth ?? null}
          note={usage.data ? `Since ${monthDay(usage.data.since)}, across your whole team` : null}
          loading={usage.isPending}
        />
        <Stat label="Employees" value={team.data?.length ?? null} note="In your sidebar now" loading={team.isPending} />
      </div>
    </div>
  );
}

export default BillingTab;
