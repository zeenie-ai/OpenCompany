/**
 * Connect an AI model (onboarding handoff A2): the Welcome guide's step 2,
 * and the list in Home's AI models dialog. The shared credentials browser,
 * cut to AI models: every AI provider shows (no "Show all" cap, so models
 * that run on this computer are not hidden), Yours counts AI models only,
 * and there is no category filter. The host keeps which page is open.
 *
 * Connect and Manage open the provider's page in place of the list
 * (ProviderPage, the same page the credentials dialog shows, unless the
 * host renders its own). The list stays mounted underneath, hidden, so a
 * card that turns connected still glows and reports it: then a pill says
 * so, and the step returns to the list after a connect (Manage stays open).
 * When the host's task waits for a model (the guide's job, the dialog
 * opened for one), connecting one finishes it instead. Back returns focus
 * to the card it came from.
 */

import { useEffect, useMemo, useRef, type ReactNode } from 'react';
import { CredentialsBrowser, type CredentialsBrowserCopy } from '@/components/credentials/CredentialsBrowser';
import { ProviderPage, type ProviderSelection } from '@/components/credentials/ProviderPage';
import { isConnected, useCredentialsCatalogue, type CredentialsCatalogue } from '../../data/connectors';
import { pillToast } from '../../ui/pillToast';

const AI_COPY = (connected: boolean): CredentialsBrowserCopy => ({
  title: connected ? 'You’re connected' : 'Connect an AI model',
  searchPlaceholder: 'Search AI models',
  listLabel: 'AI models to show',
  discoverTitle: 'AI models',
  yoursEmpty: { title: 'No AI models connected yet', detail: 'Connect one so your employees can think.' },
});

/** What a provider's page is given: the AI models and which one to show. */
export interface ConnectPage {
  view: CredentialsCatalogue;
  selection: ProviderSelection;
  onBack: () => void;
}

/** A provider's full page in place of the list (ProviderPage). */
export function FullProviderPage({ view, selection, onBack }: ConnectPage) {
  return (
    <div className="px-3 pb-8">
      <ProviderPage view={view} selection={selection} backLabel="All AI models" onBack={onBack} visible showHeading autoFocusBack />
    </div>
  );
}

export interface ConnectStepProps {
  /** The provider page shown in place of the list (null: the list). */
  selection: ProviderSelection | null;
  onSelect: (selection: ProviderSelection | null) => void;
  /** Connecting a model finishes the host's task rather than returning to
   *  the list. */
  finishOnConnect: boolean;
  onFinish: () => void;
  /** A provider's page. Default: the full page (FullProviderPage). */
  renderPage?: (page: ConnectPage) => ReactNode;
}

export function ConnectStep({ selection, onSelect, finishOnConnect, onFinish, renderPage }: ConnectStepProps) {
  const catalogue = useCredentialsCatalogue();
  const providers = useMemo(() => catalogue.providers.filter((provider) => provider.consumer_category === 'ai'), [catalogue.providers]);
  const view = { ...catalogue, providers, categories: [] };
  const connected = providers.some(isConnected);

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
    if (finishOnConnect) {
      onFinish();
      return;
    }
    if (selection?.id === id && selection.intent === 'connect') onSelect(null);
  };

  const page = selection && { view, selection, onBack: () => onSelect(null) };

  return (
    <>
      <div hidden={selection !== null}>
        <CredentialsBrowser
          catalogue={view}
          onConnect={(id, intent = 'connect') => onSelect({ id, intent })}
          copy={AI_COPY(connected)}
          variant="embedded"
          discoverLimit={null}
          onItemAdded={onItemAdded}
          customConnectors={false}
        />
      </div>
      {page && (renderPage ? renderPage(page) : <FullProviderPage {...page} />)}
    </>
  );
}

export default ConnectStep;
