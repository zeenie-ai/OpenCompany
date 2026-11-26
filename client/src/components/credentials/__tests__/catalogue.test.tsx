import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import type { CatalogueResponse, ServerProviderConfig } from '@/hooks/useCatalogueQuery';

let data: CatalogueResponse | undefined;
let disabled: string[] = [];
const sendRequest = vi.fn();
const refetch = vi.fn();

vi.mock('@/contexts/WebSocketContext', () => ({
  useWebSocketActions: () => ({ sendRequest, isReady: true }),
}));
vi.mock('@/hooks/useCatalogueQuery', () => ({
  useCatalogueQueryCore: () => ({ data, isLoading: !data, isError: false, error: null, refetch }),
}));
vi.mock('@/hooks/useNodeAllowlist', () => ({
  useNodeAllowlist: () => ({ isCredentialCategoryDisabled: (category: string) => disabled.includes(category) }),
}));

import { isConnected, useConnectors, useCredentialsCatalogue } from '../catalogue';

function provider(id: string, category: string, consumerCategory: string): ServerProviderConfig {
  return { id, name: id, category, category_label: category, consumer_category: consumerCategory, color: '', kind: 'apiKey' };
}

beforeEach(() => {
  disabled = [];
  data = {
    providers: [provider('openai', 'ai', 'ai'), provider('phone', 'android', 'devices'), provider('github', 'vcs', 'developer')],
    categories: [],
    consumer_categories: [
      { key: 'developer', label: 'Developer', order: 0 },
      { key: 'devices', label: 'Devices', order: 1 },
      { key: 'ai', label: 'AI', order: 2 },
    ],
    version: '1',
  };
});

describe('shared credentials catalogue', () => {
  it('uses the same category blocklist for providers and visible category chips', () => {
    const { result, rerender } = renderHook(useCredentialsCatalogue);
    expect(result.current.providers.map((p) => p.id)).toEqual(['github', 'phone', 'openai']);
    disabled = ['android'];
    rerender();
    expect(result.current.providers.map((p) => p.id)).toEqual(['github', 'openai']);
    expect(result.current.categories.map((c) => c.key)).toEqual(['developer', 'ai']);
    expect(result.current.catalogue.data).toBe(data);
  });

  it('shows the extra categories a card is listed under, without making it a chat model', () => {
    data = {
      ...data!,
      providers: [{ ...provider('typesafe', 'ai', 'developer'), also_consumer_categories: ['ai'], connected: true }],
    };
    const { result } = renderHook(useCredentialsCatalogue);
    expect(result.current.categories.map((c) => c.key)).toEqual(['developer', 'ai']);

    const connectors = renderHook(useConnectors).result.current;
    expect(connectors.hasAi).toBe(false);
    expect(connectors.connectedApps.map((p) => p.id)).toEqual(['typesafe']);
  });

  it('keeps query state and retry available with an empty cold catalogue', () => {
    data = undefined;
    const { result } = renderHook(useCredentialsCatalogue);
    expect(result.current.providers).toEqual([]);
    expect(result.current.categories).toEqual([]);
    expect(result.current.isLoading).toBe(true);
    expect(result.current.refetch).toBe(refetch);
  });

  it('uses live connection state before stored credentials', () => {
    const item = provider('phone', 'android', 'devices');
    expect(isConnected({ ...item, stored: true, connected: false })).toBe(false);
    expect(isConnected({ ...item, stored: true })).toBe(true);
  });
});
