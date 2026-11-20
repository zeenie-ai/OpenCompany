/**
 * The cost panel: what model calls and paid tools cost this month, by
 * employee, and the most one chat reply may cost.
 *
 * One component for both modes. Home mounts it in Settings > Billing and Dev
 * in its Settings panel, unchanged, so the two always show the same figures
 * and the same limit.
 *
 * The figures come from the server's usage service (`get_workflow_costs`,
 * hooks/useUsageQueries.ts) and are estimates: the configured prices applied
 * to the usage each provider reported. A total that cannot be read shows a
 * dash, never a zero. The limit is the owner's `chat_run_spend_limit_usd`
 * setting; "Save limit" saves that one setting and nothing else.
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

const LIMIT_SETTING = 'chat_run_spend_limit_usd';
const LIMIT_FIELD_ID = 'cost-panel-spend-limit';
/** An empty box reads as 0, which means no limit. */
const limitSchema = z.coerce.number().min(0);

/** The limit's field and its Save. `saved` is the limit the settings hold. */
function SpendLimit({ saved }: { saved: number }) {
  const { sendRequest } = useWebSocketActions();
  const save = useSaveUserSettingsMutationCore(sendRequest);
  // What the owner typed and has not saved yet. While there is none the box
  // shows the saved limit, so a limit saved elsewhere shows here too, and a
  // save that failed keeps what was typed.
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? saved;
  const parsed = limitSchema.safeParse(shown);
  const limit = parsed.success ? parsed.data : null;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <Label htmlFor={LIMIT_FIELD_ID} className="text-sm font-medium text-fg-default">
            Spend limit per reply
          </Label>
          <p className="text-xs text-pretty text-fg-muted">
            A chat reply that reaches this cost stops and says so, and your next message carries on. Other work, such as
            scheduled runs, is not limited. 0 means no limit.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative w-28">
            <span aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-sm text-fg-muted">
              $
            </span>
            <Input
              id={LIMIT_FIELD_ID}
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
              if (limit !== null) save.mutate({ [LIMIT_SETTING]: limit }, { onSuccess: () => setDraft(null) });
            }}
          >
            Save limit
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
          The limit didn’t save. Try again.
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

      <div className="border-t border-border-default pt-4">
        {/* A settings row nobody has saved yet carries no limit: 0, no limit. */}
        {settings.data && <SpendLimit saved={settings.data[LIMIT_SETTING] ?? 0} />}
        {settings.isPending && <Skeleton className="h-9 w-full" />}
        {settings.isError && !settings.data && <p className="text-sm text-fg-muted">The spend limit can’t be read right now.</p>}
      </div>

      <p className="text-xs text-pretty text-fg-faint">
        Counted: model calls and paid tools made through OpenCompany. Other is calls that belong to no employee, such as
        drafting a new hire. Claude Code and Codex agents are not counted.
      </p>
    </section>
  );
}

export default CostPanel;
