/**
 * Normal mode's view of the credential catalogue: every provider, in the
 * order of its `consumer_category` (apps before AI models). The server
 * gives every provider one; the filter only drops a malformed entry. Same
 * cache as the editor's Credentials modal, read through the stable
 * actions context.
 */

import { useMemo } from 'react';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { useNodeAllowlist } from '@/hooks/useNodeAllowlist';
import {
  useCatalogueQueryCore,
  type CatalogueResponse,
  type ServerCategory,
  type ServerProviderConfig,
} from '@/hooks/useCatalogueQuery';

export type ConsumerProvider = ServerProviderConfig & { consumer_category: string };

export function useCredentialsQuery() {
  const { sendRequest, isReady } = useWebSocketActions();
  return useCatalogueQueryCore(sendRequest, isReady);
}

/** Kept for existing Home readers of the raw catalogue query. */
export const useHomeCatalogue = useCredentialsQuery;

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

/** One visibility policy for the browser and its selected-provider host. */
export function useCredentialsCatalogue() {
  const catalogue = useCredentialsQuery();
  const { isCredentialCategoryDisabled, isLoading: allowlistIsLoading } = useNodeAllowlist();
  const waitingForAllowlist = Boolean(allowlistIsLoading);
  const providers = useMemo(
    () => waitingForAllowlist ? [] : consumerProviders(catalogue.data).filter((provider) => !isCredentialCategoryDisabled(provider.category)),
    [catalogue.data, isCredentialCategoryDisabled, waitingForAllowlist],
  );
  const categories = useMemo(() => {
    const visible = new Set(providers.flatMap((provider) => [provider.consumer_category, ...(provider.also_consumer_categories ?? [])]));
    return (catalogue.data?.consumer_categories ?? []).filter((category) => visible.has(category.key));
  }, [catalogue.data, providers]);
  return {
    catalogue,
    providers,
    categories,
    isLoading: catalogue.isLoading || waitingForAllowlist,
    isError: catalogue.isError,
    error: catalogue.error,
    refetch: catalogue.refetch,
  };
}

export type CredentialsCatalogue = ReturnType<typeof useCredentialsCatalogue>;

export interface ConnectorsView {
  categories: ServerCategory[];
  providers: ConsumerProvider[];
  /** Connected apps (not AI providers), for the composer's apps pill. */
  connectedApps: ConsumerProvider[];
  hasAi: boolean;
}

export function useConnectors(): ConnectorsView & { isLoading: boolean } {
  const { providers, categories, isLoading } = useCredentialsCatalogue();
  const view = useMemo<ConnectorsView>(() => {
    const connected = providers.filter(isConnected);
    return {
      categories,
      providers,
      connectedApps: connected.filter((p) => p.consumer_category !== 'ai'),
      hasAi: connected.some((p) => p.consumer_category === 'ai'),
    };
  }, [providers, categories]);
  return { ...view, isLoading };
}
