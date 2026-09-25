/**
 * Normal mode's view of the credential catalogue: the providers an owner
 * can connect (those with a `consumer_category`), grouped the way the
 * Connectors tab shows them. Same cache as the editor's Credentials modal,
 * read through the stable actions context.
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

export function consumerProviders(catalogue: CatalogueResponse | undefined): ConsumerProvider[] {
  return (catalogue?.providers ?? []).filter((provider): provider is ConsumerProvider =>
    Boolean(provider.consumer_category),
  );
}

export function isConnected(provider: ServerProviderConfig): boolean {
  return Boolean(provider.connected ?? provider.stored);
}

export interface ConnectorsView {
  categories: ServerCategory[];
  providers: ConsumerProvider[];
  connectedCount: number;
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
      connectedCount: connected.length,
      connectedApps: connected.filter((p) => p.consumer_category !== 'ai'),
      hasAi: connected.some((p) => p.consumer_category === 'ai'),
    };
  }, [data]);
  return { ...view, isLoading };
}

/** Providers matching a search term and a category ('all' for every one). */
export function filterProviders(providers: ConsumerProvider[], query: string, category: string): ConsumerProvider[] {
  const q = query.trim().toLowerCase();
  return providers.filter((provider) => {
    if (category !== 'all' && provider.consumer_category !== category) return false;
    if (!q) return true;
    return `${provider.name} ${provider.description ?? ''}`.toLowerCase().includes(q);
  });
}
