/**
 * One provider's page: back to the list, "Get a key from …" for the featured
 * AI providers, what it is (description, publisher, Verified, connected or
 * not, runs on this computer), and its panel (PanelRenderer). The
 * credentials dialog shows it as its second layer, under its own title bar;
 * the Welcome guide's Connect step shows it in place of the list, with its
 * own heading (`showHeading`).
 *
 * Rows keep the panel's 20px inset (ApiKeyPanel pads itself by `p-5`), so
 * a host adds only its outer margin.
 */

import { useEffect, useMemo, useRef } from 'react';
import { ArrowLeft, ExternalLink } from 'lucide-react';
import { useShellMode } from '@/app/ShellModeSwitch';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import type { CredentialsIntent } from '@/stores/shellDialogsStore';
import { FEATURED_AI_PROVIDERS } from './aiProviderLinks';
import { isConnected, type CredentialsCatalogue } from './catalogue';
import { rehydrateProvider } from './catalogueAdapter';
import PanelRenderer from './PanelRenderer';

export interface ProviderSelection {
  id: string;
  intent: CredentialsIntent;
}

export interface ProviderPageProps {
  view: CredentialsCatalogue;
  selection: ProviderSelection;
  backLabel: string;
  onBack: () => void;
  /** Whether the page is on screen (the panel's `visible`). */
  visible: boolean;
  /** "Connect {name}" / "Manage {name}" as the page's own heading. */
  showHeading?: boolean;
  /** Move focus to Back when the page appears (it replaced the list). */
  autoFocusBack?: boolean;
}

export function ProviderPage({ view, selection, backLabel, onBack, visible, showHeading = false, autoFocusBack = false }: ProviderPageProps) {
  const showTechnicalSections = useShellMode() === 'dev';
  const provider = view.providers.find((p) => p.id === selection.id) ?? null;
  const config = useMemo(() => (provider ? rehydrateProvider(provider) : null), [provider]);
  const featured = FEATURED_AI_PROVIDERS.find((p) => p.id === provider?.id);
  const connected = provider ? isConnected(provider) : false;
  const hasData = Boolean(view.catalogue.data) && !view.isLoading;
  const backRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (autoFocusBack) backRef.current?.focus();
  }, [autoFocusBack]);

  return (
    <>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 pt-4">
        <Button ref={backRef} variant="quiet" size="sm" onClick={onBack} className="-ml-2 gap-1">
          <ArrowLeft aria-hidden className="size-4" />
          {backLabel}
        </Button>
        {featured && (
          <a
            href={featured.keyUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto inline-flex items-center gap-1 text-sm text-fg-default underline-offset-4 hover:underline"
          >
            Get a key from {provider?.name}
            <ExternalLink aria-hidden className="size-3.5" />
          </a>
        )}
      </div>
      {showHeading && provider && (
        <h2 className="m-0 px-5 pt-2.5 text-lg font-semibold tracking-[-0.02em] text-fg-default">
          {selection.intent === 'manage' ? 'Manage' : 'Connect'} {provider.name}
        </h2>
      )}
      {!hasData ? (
        view.isError ? (
          <CatalogueError onRetry={() => void view.refetch()} />
        ) : (
          <div className="space-y-3 p-5" role="status" aria-label="Loading connector">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-24 w-full" />
          </div>
        )
      ) : !provider ? (
        <div className="p-5">
          <Alert>
            <AlertTitle>Connector unavailable</AlertTitle>
            <AlertDescription>This connector is missing or disabled. Choose another connector from the catalogue.</AlertDescription>
          </Alert>
        </div>
      ) : (
        <>
          <div className="space-y-2 px-5 pt-3 text-sm text-fg-muted">
            {(provider.description || featured?.hint) && <p>{provider.description || featured?.hint}</p>}
            <div className="flex flex-wrap items-center gap-2">
              {provider.publisher && <span>by {provider.publisher}</span>}
              {provider.verified && <Badge variant="outline">Verified</Badge>}
              <Badge variant={connected ? 'success' : 'secondary'}>{connected ? 'Connected' : 'Not connected'}</Badge>
              {connected && provider.account_label && <span>{provider.account_label}</span>}
              {provider.runs_locally && <span>Runs on this computer</span>}
            </div>
          </div>
          <PanelRenderer config={config} visible={visible} showTechnicalSections={showTechnicalSections} />
        </>
      )}
    </>
  );
}

function CatalogueError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="p-5">
      <Alert variant="destructive">
        <AlertTitle>Couldn't reach the credentials server</AlertTitle>
        <AlertDescription>Check your connection and try again.</AlertDescription>
      </Alert>
      <Button variant="outline" onClick={onRetry} className="mt-3">
        Try again
      </Button>
    </div>
  );
}

export default ProviderPage;
