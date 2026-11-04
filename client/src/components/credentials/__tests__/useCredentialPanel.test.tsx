/**
 * useCredentialPanel's `stored` follows the server: removing a key leaves the
 * panel disconnected (no Connected badge, no Valid button, no status line)
 * instead of flipping back from the pre-removal cache entry.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();
const removeApiKey = vi.fn();

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ sendRequest }),
}));

vi.mock('../../../hooks/useApiKeys', () => ({
  useApiKeys: () => ({
    validateApiKey: vi.fn(),
    saveApiKey: vi.fn(),
    removeApiKey,
    getProviderDefaults: vi.fn(),
    saveProviderDefaults: vi.fn(),
    getProviderUsageSummary: vi.fn(),
    getAPIUsageSummary: vi.fn(),
    getStoredModels: vi.fn(),
    getModelConstraints: vi.fn(),
    isConnected: true,
  }),
}));

import { useCredentialPanel } from '../useCredentialPanel';
import type { ProviderConfig } from '../types';

const CONFIG: ProviderConfig = {
  id: 'opencompany_gateway',
  name: 'Gateway',
  category: 'ai',
  categoryLabel: 'AI models',
  color: '',
  kind: 'apiKey',
  iconRef: 'lucide:Sparkles',
  fields: [{ key: 'apiKey', label: 'Gateway key', secret: true }],
  ws: { login: '', logout: '', status: 'gateway_status' },
};

const CLI_CONFIG: ProviderConfig = {
  ...CONFIG, id: 'codex_cli', name: 'Codex CLI', kind: 'oauth', fields: [],
  ws: { login: 'codex_cli_login', logout: 'codex_cli_logout', status: 'codex_cli_status', device_login: 'codex_cli_device_login' },
};

let client: QueryClient;
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

describe('useCredentialPanel', () => {
  beforeEach(() => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    sendRequest.mockReset();
    removeApiKey.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    client.clear();
  });

  it('stays disconnected after its key is removed', async () => {
    let hasKey = true;
    // The post-removal refetch waits until released, so the panel renders in
    // between, as it does over a real socket.
    let release: () => void = () => {};
    let gate: Promise<void> | null = null;
    sendRequest.mockImplementation(async (type: string) => {
      if (type === 'get_stored_api_key') {
        if (gate) await gate;
        return { hasKey, apiKey: hasKey ? 'sk-gateway' : undefined };
      }
      if (type === 'gateway_status') return { summary: '$0.00 of $50.00 used' };
      return {};
    });
    removeApiKey.mockImplementation(async () => {
      hasKey = false;
      gate = new Promise((resolve) => {
        release = resolve;
      });
    });

    const { result } = renderHook(() => useCredentialPanel(CONFIG, true), { wrapper });
    await waitFor(() => expect(result.current.stored).toBe(true));
    await waitFor(() => expect(result.current.statusSummary).toBe('$0.00 of $50.00 used'));

    let removal: Promise<unknown> = Promise.resolve();
    act(() => {
      removal = result.current.actions.remove('opencompany_gateway');
    });
    await waitFor(() => expect(removeApiKey).toHaveBeenCalledWith('opencompany_gateway'));
    await act(async () => {
      release();
      await removal;
    });

    expect(result.current.stored).toBe(false);
  });

  it.each([
    { success: true, connected: true, pending: false },
    { success: false, connected: false, pending: false, error: 'Sign-in declined.' },
  ])('polls pending CLI login to its terminal outcome: $success', async (outcome) => {
    vi.useFakeTimers();
    sendRequest.mockImplementation(async (type: string) => type === 'codex_cli_login'
      ? { success: true, pending: true, url: 'https://auth.openai.com/oauth/authorize?state=test' }
      : outcome);
    vi.spyOn(window, 'open').mockReturnValue(null);
    const { result } = renderHook(() => useCredentialPanel(CLI_CONFIG, true), { wrapper });
    await act(async () => { await result.current.actions.oauthLogin(); });
    expect(result.current.oauthPending).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(sendRequest).toHaveBeenCalledWith('codex_cli_status', {});
    expect(result.current.oauthPending).toBe(false);
    expect(result.current.loginUrl).toBeNull();
    expect(result.current.error).toBe('error' in outcome ? outcome.error : null);
  });

  it('ignores a login response after switching providers', async () => {
    let finish!: (value: unknown) => void;
    sendRequest.mockReturnValue(new Promise(resolve => { finish = resolve; }));
    const openWindow = vi.spyOn(window, 'open').mockReturnValue(null);
    const { result, rerender } = renderHook(({ config }) => useCredentialPanel(config, true), {
      wrapper, initialProps: { config: CLI_CONFIG },
    });
    let login!: Promise<unknown>;
    act(() => { login = result.current.actions.oauthLogin(); });
    rerender({ config: { ...CLI_CONFIG, id: 'other_cli' } });
    await act(async () => {
      finish({ success: true, pending: true, url: 'https://auth.openai.com/oauth/authorize?state=old' });
      await login;
    });
    expect(result.current.oauthPending).toBe(false);
    expect(result.current.loginUrl).toBeNull();
    expect(openWindow).not.toHaveBeenCalled();
  });

  it('ignores an earlier login response after canceling sign-in', async () => {
    let finish!: (value: unknown) => void;
    sendRequest.mockImplementation((type: string) => type === 'codex_cli_login'
      ? new Promise(resolve => { finish = resolve; })
      : Promise.resolve({ success: true }));
    const openWindow = vi.spyOn(window, 'open').mockReturnValue(null);
    const { result } = renderHook(() => useCredentialPanel(CLI_CONFIG, true), { wrapper });
    let login!: Promise<unknown>;
    act(() => { login = result.current.actions.oauthLogin(); });
    await act(async () => { await result.current.actions.oauthLogout(); });
    await act(async () => {
      finish({ success: true, pending: true, url: 'https://auth.openai.com/oauth/authorize?state=old' });
      await login;
    });
    expect(result.current.oauthPending).toBe(false);
    expect(openWindow).not.toHaveBeenCalled();
  });

  it('starts device login and refreshes a failed status explicitly', async () => {
    sendRequest.mockResolvedValueOnce({ success: false, error: 'Sign-in failed.' })
      .mockResolvedValueOnce({ success: true, connected: true, pending: false });
    const { result } = renderHook(() => useCredentialPanel(CLI_CONFIG, true), { wrapper });
    await act(async () => { await result.current.actions.oauthLogin(true); });
    expect(sendRequest).toHaveBeenCalledWith('codex_cli_device_login', {});
    expect(result.current.error).toBe('Sign-in failed.');
    await act(async () => { await result.current.actions.oauthRefresh(); });
    expect(sendRequest).toHaveBeenCalledWith('codex_cli_status', { refresh: true });
    expect(result.current.error).toBeNull();
  });
});
