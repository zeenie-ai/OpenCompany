/**
 * The shared credentials browser in Home and Dev. Discover lists every
 * visible provider, apps before AI models; Yours lists connected ones.
 *
 * Connect and Manage open the shared host with the corresponding intent.
 * Disconnect removes what the panel would remove for API-key and signed-in
 * providers, after a confirmation; for the rest (a paired phone, an email
 * account) it opens the panel, which owns those steps. A card glows when
 * it turns connected. Connection-success feedback belongs to the host.
 * There is no custom connector:
 * nothing serves one yet, so the page has no Add button.
 */

import { useMemo, useState } from 'react';
import { toast } from 'sonner';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { isConnected, useCredentialsCatalogue, type ConsumerProvider, type CredentialsCatalogue } from './catalogue';
import type { CatalogItem } from '@/components/catalog/catalog';
import { CatalogLayout } from '@/components/catalog/CatalogLayout';

/** Remove a provider's credential the way its panel would. False when the
 *  panel has to do it (QR pairing, the IMAP/SMTP account). */
async function disconnect(
  provider: ConsumerProvider,
  sendRequest: <T = unknown>(type: string, data?: Record<string, unknown>) => Promise<T>,
): Promise<boolean> {
  if (provider.kind === 'apiKey') {
    // A named-endpoint provider owns several rows. Its panel removes each
    // by reference; deleting the family id would leave those rows intact.
    if (provider.endpoints) return false;
    await sendRequest('delete_api_key', { provider: provider.id });
    // Local model servers also keep their base URL under the field key.
    const fieldKey = provider.fields?.[0]?.key;
    if (fieldKey && fieldKey !== 'apiKey') await sendRequest('delete_api_key', { provider: fieldKey });
    return true;
  }
  if (provider.kind === 'oauth' && provider.ws?.logout) {
    await sendRequest(provider.ws.logout, {});
    return true;
  }
  return false;
}

function toItem(provider: ConsumerProvider): CatalogItem {
  const connected = isConnected(provider);
  const meta = [
    connected && provider.account_label ? `Connected as ${provider.account_label}` : null,
    provider.runs_locally ? 'Runs on this computer' : null,
  ]
    .filter(Boolean)
    .join(' · ');
  return {
    id: provider.id,
    name: provider.name,
    description: provider.description,
    byline: provider.publisher ? `by ${provider.publisher}` : undefined,
    verified: provider.verified,
    category: provider.consumer_category,
    meta: meta || undefined,
    tile: { iconRef: provider.icon_ref },
    state: connected ? 'added' : 'available',
  };
}

/** What the browser says. Settings > Connectors uses the defaults; the
 *  Welcome guide's Connect step speaks of AI models. */
export interface CredentialsBrowserCopy {
  title: string;
  searchPlaceholder: string;
  /** The Yours / Discover switch's accessible name. */
  listLabel: string;
  discoverTitle: string;
  yoursEmpty: { title: string; detail: string };
}

const CONNECTORS_COPY: CredentialsBrowserCopy = {
  title: 'Connectors',
  searchPlaceholder: 'Search connectors',
  listLabel: 'Connectors to show',
  discoverTitle: 'Top connectors',
  yoursEmpty: { title: 'No apps connected yet', detail: 'Connect the apps your employees should work in.' },
};

export interface CredentialsBrowserProps {
  onConnect: (providerId: string, intent?: 'connect' | 'manage') => void;
  initialCategory?: string;
  /** The host can share its query/visibility result without another hook. */
  catalogue?: CredentialsCatalogue;
  copy?: CredentialsBrowserCopy;
  /** Passed to CatalogLayout (the Welcome guide embeds the browser). */
  variant?: 'page' | 'embedded';
  discoverLimit?: number | null;
  /** A provider on screen turned connected (after its card's glow starts). */
  onItemAdded?: (providerId: string) => void;
}

export function CredentialsBrowser(props: CredentialsBrowserProps) {
  return props.catalogue
    ? <CredentialsBrowserContent {...props} catalogue={props.catalogue} />
    : <ConnectedCredentialsBrowser {...props} />;
}

function ConnectedCredentialsBrowser(props: CredentialsBrowserProps) {
  const catalogue = useCredentialsCatalogue();
  return <CredentialsBrowserContent {...props} catalogue={catalogue} />;
}

function CredentialsBrowserContent({
  onConnect,
  initialCategory = 'all',
  catalogue,
  copy = CONNECTORS_COPY,
  variant,
  discoverLimit,
  onItemAdded,
}: CredentialsBrowserProps & { catalogue: CredentialsCatalogue }) {
  const { providers, categories, isLoading, isError, refetch } = catalogue;
  const { sendRequest } = useWebSocketActions();
  const [confirming, setConfirming] = useState<ConsumerProvider | null>(null);
  const byId = useMemo(() => new Map(providers.map((provider) => [provider.id, provider])), [providers]);
  const items = useMemo(() => providers.map(toItem), [providers]);
  const connected = useMemo(() => items.filter((item) => item.state === 'added'), [items]);

  const confirmDisconnect = async () => {
    const provider = confirming;
    setConfirming(null);
    if (!provider) return;
    try {
      const done = await disconnect(provider, sendRequest);
      if (done) toast.info(`${provider.name} disconnected`);
      else onConnect(provider.id, 'manage');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : `Couldn't disconnect ${provider.name}`);
    }
  };

  if (isError && !catalogue.catalogue.data) {
    return (
      <Alert variant="destructive" className="m-8 w-auto">
        <AlertTitle>Couldn't load connectors</AlertTitle>
        <AlertDescription>Check your connection and try again.</AlertDescription>
        <Button variant="outline" onClick={() => void refetch()}>Try again</Button>
      </Alert>
    );
  }

  return (
    <>
      <CatalogLayout
        title={copy.title}
        searchPlaceholder={copy.searchPlaceholder}
        listLabel={copy.listLabel}
        variant={variant}
        discoverLimit={discoverLimit}
        loading={isLoading}
        categories={categories}
        initialCategory={initialCategory}
        yours={{ items: connected, sectionTitle: 'Connected', empty: copy.yoursEmpty }}
        discover={{ items, sectionTitle: copy.discoverTitle }}
        verbs={{ add: 'Connect', remove: 'Disconnect' }}
        onAdd={(item) => onConnect(item.id, 'connect')}
        onManage={(item) => onConnect(item.id, 'manage')}
        onRemove={(item) => setConfirming(byId.get(item.id) ?? null)}
        onItemAdded={onItemAdded && ((item) => onItemAdded(item.id))}
      />

      <AlertDialog open={confirming !== null} onOpenChange={(open) => !open && setConfirming(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disconnect {confirming?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Employees that use {confirming?.name} stop using it until you connect it again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction onClick={() => void confirmDisconnect()}>Disconnect</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

export default CredentialsBrowser;
