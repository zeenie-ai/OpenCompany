import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mocks = vi.hoisted(() => ({
  sendRequest: vi.fn(),
  validateApiKey: vi.fn(),
  saveApiKey: vi.fn(),
  removeApiKey: vi.fn(),
  getProviderDefaults: vi.fn(),
  getValidatedAiProviders: vi.fn(),
  getProviderUsageSummary: vi.fn(),
  getAPIUsageSummary: vi.fn(),
  getWhatsAppRateLimitConfig: vi.fn(),
  apiKeyStatuses: {},
}));

vi.mock('@/hooks/useApiKeys', () => ({
  useApiKeys: () => ({ ...mocks, isConnected: true }),
}));
vi.mock('@/contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ ...mocks, isConnected: true, isReady: true }),
  CREDENTIAL_PROBE_REQUEST_TIMEOUT: 60_000,
}));
vi.mock('../hooks', () => ({ useProviderStatus: () => null }));
vi.mock('@/assets/icons', () => ({ NodeIcon: () => null }));

import PanelRenderer from '../PanelRenderer';
import type { ProviderConfig } from '../types';
import { queryKeys } from '@/lib/queryConfig';

const API: ProviderConfig = {
  id: 'first', name: 'First provider', category: 'ai', categoryLabel: 'AI',
  color: '', kind: 'apiKey', iconRef: '', hasDefaults: true, usageServices: ['first-api'],
  fields: [{ key: 'apiKey', label: 'API key', placeholder: 'Provider key', required: true, help: 'Create a key in your account.' }],
};

const OAUTH: ProviderConfig = {
  ...API, id: 'oauth', name: 'OAuth provider', kind: 'oauth', hasDefaults: false,
  fields: [
    { key: 'client_id', label: 'Client ID', required: true },
    { key: 'client_secret', label: 'Client secret', secret: true, required: true },
  ],
  ws: { login: 'provider_login', logout: 'provider_logout', status: 'provider_status' },
  instructions: 'Register an application before signing in.',
  callbackUrl: 'http://localhost/callback',
};

function renderPanel(config: ProviderConfig, showTechnicalSections?: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const element = (nextConfig: ProviderConfig, technical?: boolean) => (
    <QueryClientProvider client={client}>
      <PanelRenderer config={nextConfig} visible showTechnicalSections={technical} />
    </QueryClientProvider>
  );
  const view = render(element(config, showTechnicalSections));
  return {
    ...view,
    change: (nextConfig: ProviderConfig, technical?: boolean) => view.rerender(element(nextConfig, technical)),
    loaded: (id: string) => waitFor(() => expect(client.getQueryData(queryKeys.credentialValues.byProvider(id).queryKey)).toBeDefined()),
  };
}

// Panels are lazy chunks: load them up front so no case's first 1 s wait includes the cold import.
beforeAll(async () => {
  await Promise.all([import('../panels/ApiKeyPanel'), import('../panels/OAuthPanel'), import('../panels/QrPairingPanel')]);
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.sendRequest.mockResolvedValue({ hasKey: false });
  mocks.validateApiKey.mockResolvedValue({ isValid: false, success: false, error: 'Key rejected' });
  mocks.getProviderDefaults.mockResolvedValue({});
  mocks.getValidatedAiProviders.mockResolvedValue({ providers: [] });
  mocks.getProviderUsageSummary.mockResolvedValue([]);
  mocks.getAPIUsageSummary.mockResolvedValue([]);
  mocks.getWhatsAppRateLimitConfig.mockResolvedValue({ success: true, config: { enabled: false } });
});

describe('shared credential panel policy', () => {
  it('mounts technical sections only in dev and keeps the connection draft across mode changes', async () => {
    const user = userEvent.setup();
    const view = renderPanel(API);
    await view.loaded(API.id);
    const field = await screen.findByPlaceholderText('Provider key');
    await user.type(field, 'draft-key');

    expect(screen.getByText('Create a key in your account.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Default Parameters' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Usage.*Costs/i })).not.toBeInTheDocument();
    expect(mocks.getProviderDefaults).not.toHaveBeenCalled();
    expect(mocks.getAPIUsageSummary).not.toHaveBeenCalled();
    expect(mocks.getProviderUsageSummary).not.toHaveBeenCalled();

    view.change(API, true);
    await waitFor(() => expect(mocks.getProviderDefaults).toHaveBeenCalledWith(API.id));
    expect(mocks.getAPIUsageSummary).toHaveBeenCalledWith();
    expect(screen.getByRole('button', { name: 'Default Parameters' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Provider key')).toBe(field);
    expect(field).toHaveValue('draft-key');

    await user.click(screen.getByRole('button', { name: 'Usage & Costs' }));
    await waitFor(() => expect(mocks.getProviderUsageSummary).toHaveBeenCalledOnce());
    view.change(API, false);
    expect(screen.queryByRole('button', { name: 'Default Parameters' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Usage.*Costs/i })).not.toBeInTheDocument();
    expect(field).toHaveValue('draft-key');
    expect(mocks.getProviderDefaults).toHaveBeenCalledOnce();
    expect(mocks.getAPIUsageSummary).toHaveBeenCalledOnce();
  });

  it('isolates drafts by provider and clears reveal and error state on a provider switch', async () => {
    const user = userEvent.setup();
    const view = renderPanel(API);
    await view.loaded(API.id);
    await user.type(await screen.findByPlaceholderText('Provider key'), 'first-draft');
    await user.click(screen.getByRole('button', { name: 'Show key' }));
    await user.click(screen.getByRole('button', { name: 'Validate' }));
    expect(await screen.findByText('Key rejected')).toBeInTheDocument();

    const second = { ...API, id: 'second', name: 'Second provider' };
    view.change(second);
    await view.loaded(second.id);
    expect(screen.getByPlaceholderText('Provider key')).toHaveValue('');
    expect(screen.getByPlaceholderText('Provider key')).toHaveAttribute('type', 'password');
    expect(screen.queryByText('Key rejected')).not.toBeInTheDocument();
    await user.type(screen.getByPlaceholderText('Provider key'), 'second-draft');

    view.change(API);
    expect(screen.getByPlaceholderText('Provider key')).toHaveValue('first-draft');
    expect(screen.getByPlaceholderText('Provider key')).toHaveAttribute('type', 'password');
    expect(screen.queryByText('Key rejected')).not.toBeInTheDocument();
  });

  it('keeps required OAuth setup and callbacks in both modes without normal-mode usage requests', async () => {
    const user = userEvent.setup();
    const view = renderPanel(OAUTH);
    await view.loaded(OAUTH.id);
    const secret = await screen.findByLabelText('Client secret', { exact: false });
    await user.type(secret, 'oauth-draft');
    expect(screen.getByLabelText('Client ID', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('Register an application before signing in.', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('http://localhost/callback')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Credentials' })).toBeInTheDocument();
    expect(mocks.getAPIUsageSummary).not.toHaveBeenCalled();
    expect(mocks.sendRequest.mock.calls.every(([command]) => command === 'get_stored_api_key')).toBe(true);

    view.change(OAUTH, true);
    await waitFor(() => expect(mocks.getAPIUsageSummary).toHaveBeenCalledOnce());
    expect(screen.getByLabelText('Client secret', { exact: false })).toBe(secret);
    expect(secret).toHaveValue('oauth-draft');
    expect(secret).toHaveAttribute('type', 'password');
    expect(screen.getByText('http://localhost/callback')).toBeInTheDocument();
  });

  it('keeps pairing available while mounting rate-limit requests only in dev', async () => {
    const qr: ProviderConfig = {
      ...API, id: 'whatsapp', name: 'WhatsApp', kind: 'qrPairing', fields: undefined,
      hasRateLimits: true,
      qr: {
        qrField: 'qr', isConnected: () => true, connectedTitle: 'Phone connected',
        connectedSubtitle: () => 'Your phone is ready', isLoading: () => false,
        emptyText: () => 'Scan to connect', scanText: 'Scan with your phone',
      },
    };
    const view = renderPanel(qr);
    expect(await screen.findByText('Phone connected')).toBeInTheDocument();
    expect(mocks.getWhatsAppRateLimitConfig).not.toHaveBeenCalled();
    view.change(qr, true);
    await waitFor(() => expect(mocks.getWhatsAppRateLimitConfig).toHaveBeenCalledOnce());
    view.change(qr, false);
    expect(screen.getByText('Phone connected')).toBeInTheDocument();
    expect(mocks.getWhatsAppRateLimitConfig).toHaveBeenCalledOnce();
  });
});
