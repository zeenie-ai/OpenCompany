/**
 * Normal mode's view of the credential catalogue: the providers an owner
 * can connect (those with a `consumer_category`), in the order of their
 * categories (apps before AI models). Same cache as the editor's
 * Credentials modal, read through the stable actions context.
 */

import { useMemo } from 'react';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import {
  useCatalogueQueryCore,
  type CatalogueResponse,
  type ServerCategory,
  type ServerProviderConfig,
} from '@/hooks/useCatalogueQuery';

export type ConsumerProvider = ServerProviderConfig & { consumer_category: string };

export function useHomeCatalogue() {
  const { sendRequest, isReady } = useWebSocketActions();
  return useCatalogueQueryCore(sendRequest, isReady);
}

/** Consumer providers, sorted by their category's place in `consumer_categories`
 *  (a stable sort, so the catalogue's own order holds within a category). */
export function consumerProviders(catalogue: CatalogueResponse | undefined): ConsumerProvider[] {
  const order = new Map((catalogue?.consumer_categories ?? []).map((category, index) => [category.key, index]));
  const rank = (provider: ConsumerProvider) => order.get(provider.consumer_category) ?? order.size;
  return (catalogue?.providers ?? [])
    .filter((provider): provider is ConsumerProvider => Boolean(provider.consumer_category))
    .sort((a, b) => rank(a) - rank(b));
}

export function isConnected(provider: ServerProviderConfig): boolean {
  return Boolean(provider.connected ?? provider.stored);
}

export interface ConnectorsView {
  categories: ServerCategory[];
  providers: ConsumerProvider[];
  /** Connected apps (not AI providers), for the composer's apps pill. */
  connectedApps: ConsumerProvider[];
  hasAi: boolean;
}

export function useConnectors(): ConnectorsView & { isLoading: boolean } {
  const { data, isLoading } = useHomeCatalogue();
  const view = useMemo<ConnectorsView>(() => {
    const providers = consumerProviders(data);
    const connected = providers.filter(isConnected);
    return {
      categories: data?.consumer_categories ?? [],
      providers,
      connectedApps: connected.filter((p) => p.consumer_category !== 'ai'),
      hasAi: connected.some((p) => p.consumer_category === 'ai'),
    };
  }, [data]);
  return { ...view, isLoading };
}
