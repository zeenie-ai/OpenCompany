/**
 * Guide step 3, Your first hire (onboarding handoff A3, trimmed by R1): the
 * starter chips and the same composer as Home, bound to the same draft.
 * "Create their setup" finishes the guide, shows the hire view and sends
 * the job; the setup then writes itself under Home's composer. Without an
 * AI model nothing is sent: a notice and the button lead to step 2, and the
 * job written so far waits there; connecting a model sends it
 * (`pendingDraft`, ConnectStep).
 */

import { ActionButton } from '@/components/ui/action-button';
import { useConnectors } from '../../data/connectors';
import { useJobComposer } from '../../genui';
import { Composer } from '../../hire/Composer';
import { TemplateChips } from '../../hire/TemplateChips';
import { HIRE_TEMPLATES } from '../../hire/templates';
import { useHomeStore } from '../../state/homeStore';

export function FirstHireStep({ onDone, onNeedsModel }: { onDone: () => void; onNeedsModel: () => void }) {
  const job = useJobComposer();
  const { connectedApps, hasAi, isLoading } = useConnectors();
  const openSettings = useHomeStore((s) => s.openSettings);
  const needsAi = !isLoading && !hasAi;
  const picked = HIRE_TEMPLATES.find((template) => template.job === job.value.trim()) ?? null;
  const apps = connectedApps.map((provider) => ({ id: provider.id, name: provider.name, icon_ref: provider.icon_ref }));

  // Off to Connect; the job waits when there is one.
  const toConnect = () => {
    useHomeStore.getState().setGuidePendingDraft(job.value.trim() !== '');
    onNeedsModel();
  };

  const create = () => {
    if (needsAi) {
      toConnect();
      return;
    }
    onDone();
    useHomeStore.getState().showHire();
    void job.submit();
  };

  return (
    <div className="flex flex-col gap-4.5 px-8 pt-6.5 pb-8">
      <h2 data-stagger className="m-0 pr-10 text-lg font-semibold tracking-[-0.02em] text-fg-default">
        Who should we hire first?
      </h2>
      <div data-stagger>
        <TemplateChips picked={picked} onPick={(template) => job.pick(template.job)} disabled={job.working} className="justify-start" />
      </div>
      <div data-stagger>
        <Composer
          value={job.value}
          onChange={job.onChange}
          onSubmit={create}
          refining={false}
          onStopRefining={() => {}}
          working={job.working}
          apps={apps}
          onOpenApps={() => openSettings('connectors')}
          idleLabel="Create their setup"
          flat
          maxLength={2000}
        />
      </div>
      {needsAi && (
        <div
          data-stagger
          className="flex flex-wrap items-center gap-3 rounded-card border border-node-trigger-border bg-node-trigger-soft px-3.5 py-3"
        >
          <span aria-hidden className="size-2 shrink-0 rounded-full bg-node-trigger" />
          <span className="min-w-50 flex-1 text-sm text-fg-muted">Connect an AI model first.</span>
          <ActionButton intent="run" onClick={toConnect}>
            Connect an AI model
          </ActionButton>
        </div>
      )}
    </div>
  );
}

export default FirstHireStep;
