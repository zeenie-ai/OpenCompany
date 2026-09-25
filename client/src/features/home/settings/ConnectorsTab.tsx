/**
 * Settings > Connectors (design handoff): every app the owner can connect,
 * on the shared catalog page. Discover lists them all, apps before AI
 * models; Yours lists the connected ones.
 *
 * "+" opens the provider's own credential panel (ConnectDialog).
 * Disconnect removes what the panel would remove for API-key and signed-in
 * providers, after a confirmation; for the rest (a paired phone, an email
 * account) it opens the panel, which owns those steps. A card glows and a
 * toast confirms only when a provider actually flips to connected while
 * the page is open, never on first render. There is no custom connector:
 * nothing serves one yet, so the page has no Add button.
 */

import { useMemo, useState } from 'react';
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
import { isConnected, useConnectors, type ConsumerProvider } from '../data/connectors';
import { pillToast } from '../ui/pillToast';
import type { CatalogItem } from './catalog';
import { CatalogLayout } from './CatalogLayout';

/** Remove a provider's credential the way its panel would. False when the
 *  panel has to do it (QR pairing, the IMAP/SMTP account). */
async function disconnect(
  provider: ConsumerProvider,
  sendRequest: <T = unknown>(type: string, data?: Record<string, unknown>) => Promise<T>,
): Promise<boolean> {
  if (provider.kind === 'apiKey') {
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

export function ConnectorsTab({
  onConnect,
  initialCategory = 'all',
}: {
  onConnect: (providerId: string) => void;
  initialCategory?: string;
}) {
  const { providers, categories, isLoading } = useConnectors();
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
      if (done) pillToast(`${provider.name} disconnected`, { tone: 'info' });
      else onConnect(provider.id);
    } catch (error) {
      pillToast(error instanceof Error ? error.message : `Couldn't disconnect ${provider.name}`, { tone: 'error' });
    }
  };

  return (
    <>
      <CatalogLayout
        title="Connectors"
        searchPlaceholder="Search connectors"
        loading={isLoading}
        categories={categories}
        initialCategory={initialCategory}
        yours={{
          items: connected,
          sectionTitle: 'Connected',
          empty: { title: 'No apps connected yet', detail: 'Connect the apps your employees should work in.' },
        }}
        discover={{ items, sectionTitle: 'Top connectors' }}
        verbs={{ add: 'Connect', remove: 'Disconnect' }}
        onAdd={(item) => onConnect(item.id)}
        onRemove={(item) => setConfirming(byId.get(item.id) ?? null)}
        onItemAdded={(item) => pillToast(`${item.name} is connected`)}
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

export default ConnectorsTab;
