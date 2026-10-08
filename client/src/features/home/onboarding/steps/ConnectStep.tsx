/**
 * Guide step 2, Connect an AI model (onboarding handoff A2): the shared
 * credentials browser, cut to AI models. Every AI provider shows (no
 * "Show all" cap, so models that run on this computer are not hidden),
 * Yours counts AI models only, and there is no category filter.
 *
 * Connect and Manage open the provider's page in place of the list
 * (ProviderPage, the same page the credentials dialog shows). The list stays
 * mounted underneath, hidden, so a card that turns connected still glows
 * and reports it: then a pill says so, and the step returns to the list
 * after a connect (Manage stays open). When the last step's job is waiting
 * for a model, connecting one finishes the guide and sends it. Back returns
 * focus to the card it came from.
 */

import { useEffect, useMemo, useRef } from 'react';
import { CredentialsBrowser, type CredentialsBrowserCopy } from '@/components/credentials/CredentialsBrowser';
import { ProviderPage } from '@/components/credentials/ProviderPage';
import { isConnected, useCredentialsCatalogue } from '../../data/connectors';
import { useJobComposer } from '../../genui';
import { useHomeStore } from '../../state/homeStore';
import { pillToast } from '../../ui/pillToast';

const AI_COPY = (connected: boolean): CredentialsBrowserCopy => ({
  title: connected ? 'You’re connected' : 'Connect an AI model',
  searchPlaceholder: 'Search AI models',
  listLabel: 'AI models to show',
  discoverTitle: 'AI models',
  yoursEmpty: { title: 'No AI models connected yet', detail: 'Connect one so your employees can think.' },
});

export function ConnectStep({ onFinish }: { onFinish: () => void }) {
  const catalogue = useCredentialsCatalogue();
  const providers = useMemo(() => catalogue.providers.filter((provider) => provider.consumer_category === 'ai'), [catalogue.providers]);
  const view = { ...catalogue, providers, categories: [] };
  const connected = providers.some(isConnected);
  const selection = useHomeStore((s) => s.guide.provider);
  const setProvider = useHomeStore((s) => s.setGuideProvider);
  const job = useJobComposer();

  // Back on the list: focus the card the page was opened from.
  const shown = useRef<string | null>(null);
  useEffect(() => {
    if (selection) {
      shown.current = selection.id;
      return;
    }
    const id = shown.current;
    shown.current = null;
    if (id) document.querySelector<HTMLElement>(`[data-catalog-item="${id}"] button`)?.focus();
  }, [selection]);

  const onItemAdded = (id: string) => {
    const name = providers.find((provider) => provider.id === id)?.name ?? 'Your AI model';
    pillToast(`${name} is connected`);
    const { guide } = useHomeStore.getState();
    if (guide.pendingDraft) {
      onFinish();
      useHomeStore.getState().showHire();
      void job.submit();
      return;
    }
    if (guide.provider?.id === id && guide.provider.intent === 'connect') setProvider(null);
  };

  return (
    <>
      <div hidden={selection !== null}>
        <CredentialsBrowser
          catalogue={view}
          onConnect={(id, intent = 'connect') => setProvider({ id, intent })}
          copy={AI_COPY(connected)}
          variant="embedded"
          discoverLimit={null}
          onItemAdded={onItemAdded}
        />
      </div>
      {selection && (
        <div className="px-3 pb-8">
          <ProviderPage
            view={view}
            selection={selection}
            backLabel="All AI models"
            onBack={() => setProvider(null)}
            visible
            showHeading
            autoFocusBack
          />
        </div>
      )}
    </>
  );
}

export default ConnectStep;
