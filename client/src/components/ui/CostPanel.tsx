/**
 * The cost panel: what model calls and paid tools cost this month, by
 * employee, the most one chat reply may cost, and the most one employee may
 * cost in a month.
 *
 * One component for both modes. Home mounts it in Settings > Billing and Dev
 * in its Settings panel, unchanged, so the two always show the same figures
 * and the same limits.
 *
 * The figures come from the server's usage service (`get_workflow_costs`,
 * hooks/useUsageQueries.ts) and are estimates: the configured prices applied
 * to the usage each provider reported. A total that cannot be read shows a
 * dash, never a zero. Each limit is one of the owner's settings
 * (`chat_run_spend_limit_usd`, `employee_monthly_budget_usd`), and its Save
 * saves that one setting and nothing else.
 */

import { useState } from 'react';
import { z } from 'zod';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { useWorkflowCostsQuery } from '@/hooks/useUsageQueries';
import { useSaveUserSettingsMutationCore, useUserSettingsQueryCore } from '@/hooks/useUserSettingsQuery';
import { monthDay, usd } from '@/lib/format';

interface Limit {
  /** The setting the field shows and its Save writes. */
  setting: string;
  label: string;
  help: string;
  /** What its Save and a failed save call it. */
  noun: string;
}

const LIMITS: Limit[] = [
  {
    setting: 'chat_run_spend_limit_usd',
    label: 'Spend limit per reply',
    help: 'A chat reply that reaches this cost stops and says so, and your next message carries on. It covers the reply itself; work it hands to the team counts toward the monthly budget. 0 means no limit.',
    noun: 'limit',
  },
  {
    setting: 'employee_monthly_budget_usd',
    label: 'Monthly budget per employee',
    help: 'An employee whose cost this month reaches this pauses and shows as needing attention, team and scheduled work included. Raise the amount, then resume them. 0 means no budget.',
    noun: 'budget',
  },
];

/** An empty box reads as 0, which means off. */
const amountSchema = z.coerce.number().min(0);

/** One limit's field and its Save. `saved` is the amount the settings hold. */
function LimitField({ setting, label, help, noun, saved }: Limit & { saved: number }) {
  const { sendRequest } = useWebSocketActions();
  const save = useSaveUserSettingsMutationCore(sendRequest);
  // What the owner typed and has not saved yet. While there is none the box
  // shows the saved amount, so one saved elsewhere shows here too, and a
  // save that failed keeps what was typed.
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? saved;
  const parsed = amountSchema.safeParse(shown);
  const limit = parsed.success ? parsed.data : null;
  const fieldId = `cost-panel-${setting}`;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <Label htmlFor={fieldId} className="text-sm font-medium text-fg-default">
            {label}
          </Label>
          <p className="text-xs text-pretty text-fg-muted">{help}</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative w-28">
            <span aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-sm text-fg-muted">
              $
            </span>
            <Input
              id={fieldId}
              type="number"
              inputMode="decimal"
              min={0}
              step="any"
              value={shown}
              aria-invalid={limit === null}
              onChange={(event) => setDraft(event.target.value)}
              className="h-9 pl-6 tabular-nums"
            />
          </div>
          <Button
            variant="quiet"
            className="h-9 border-border-default px-3 text-fg-default"
            disabled={limit === null || limit === saved || save.isPending}
            onClick={() => {
              if (limit !== null) save.mutate({ [setting]: limit }, { onSuccess: () => setDraft(null) });
            }}
          >
            Save {noun}
          </Button>
        </div>
      </div>
      {limit === null && (
        <p role="alert" className="text-xs text-destructive">
          Enter 0 or more.
        </p>
      )}
      {save.isError && (
        <p role="alert" className="text-xs text-destructive">
          The {noun} didn’t save. Try again.
        </p>
      )}
    </div>
  );
}

export function CostPanel() {
  const { sendRequest, isReady } = useWebSocketActions();
  const costs = useWorkflowCostsQuery();
  const settings = useUserSettingsQueryCore(sendRequest, isReady);

  return (
    <section aria-label="Cost" className="flex flex-col gap-4 rounded-card border border-border-default bg-bg-elevated p-4.5">
      <div className="flex flex-col gap-1">
        <h3 className="text-base font-semibold text-fg-default">Cost</h3>
        <p className="text-sm text-pretty text-fg-muted">An estimate of what model calls and paid tools cost this month.</p>
      </div>

      <div className="flex items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-medium text-fg-default">This month</span>
          {costs.data && <span className="text-xs text-fg-muted">Since {monthDay(costs.data.since)}</span>}
        </div>
        {costs.isPending ? (
          <Skeleton className="h-8 w-20" />
        ) : (
          <span className="text-2xl font-semibold tracking-hero text-fg-default tabular-nums">
            {costs.data ? usd(costs.data.totalCost) : '—'}
          </span>
        )}
      </div>

      {costs.isError && !costs.data && <p className="text-sm text-fg-muted">This month’s cost can’t be read right now.</p>}

      {costs.data && costs.data.workflows.length > 0 && (
        <ul aria-label="Cost by employee" className="flex flex-col divide-y divide-border-default border-t border-border-default">
          {costs.data.workflows.map((workflow) => (
            <li key={workflow.workflowId ?? 'other'} className="flex items-center justify-between gap-3 py-2 text-sm">
              <span className="truncate text-fg-default">{workflow.name ?? 'Other'}</span>
              <span className="text-fg-muted tabular-nums">{usd(workflow.totalCost)}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-col gap-4 border-t border-border-default pt-4">
        {/* A settings row nobody has saved yet carries neither amount: 0, off. */}
        {settings.data &&
          LIMITS.map((limit) => <LimitField key={limit.setting} {...limit} saved={settings.data[limit.setting] ?? 0} />)}
        {settings.isPending && <Skeleton className="h-9 w-full" />}
        {settings.isError && !settings.data && <p className="text-sm text-fg-muted">The limits can’t be read right now.</p>}
      </div>

      <p className="text-xs text-pretty text-fg-faint">
        Counted: model calls and paid tools made through OpenCompany. Other is calls that belong to no employee, such as
        drafting a new hire. Claude Code and Codex agents are not counted.
      </p>
    </section>
  );
}

export default CostPanel;
