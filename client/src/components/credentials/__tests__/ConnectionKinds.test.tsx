/** Connection essentials stay usable in both presentations through the real renderer. */
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mocks = vi.hoisted(() => ({
  sendRequest: vi.fn(), validateApiKey: vi.fn(), saveApiKey: vi.fn(), removeApiKey: vi.fn(),
  getStoredApiKey: vi.fn(), hasStoredKey: vi.fn(),
}));
vi.mock('@/hooks/useApiKeys', () => ({ useApiKeys: () => ({ ...mocks, isConnected: true }) }));
vi.mock('@/contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ ...mocks, isConnected: true, isReady: true }),
  useWebSocketActions: () => ({ sendRequest: mocks.sendRequest, isReady: true }),
  CREDENTIAL_PROBE_REQUEST_TIMEOUT: 60_000,
}));
vi.mock('../hooks', () => ({ useProviderStatus: () => null }));
vi.mock('@/assets/icons', () => ({ NodeIcon: () => null }));

import PanelRenderer from '../PanelRenderer';
import type { ProviderConfig } from '../types';
import { queryKeys } from '@/lib/queryConfig';

const BASE: ProviderConfig = {
  id: 'connection', name: 'Connection', category: 'apps', categoryLabel: 'Apps',
  color: '', iconRef: '', kind: 'oauth',
  ws: { login: 'provider_login', logout: 'provider_logout', status: 'provider_status' },
};

function mount(config: ProviderConfig, showTechnicalSections: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const element = (provider: ProviderConfig) => (
    <QueryClientProvider client={client}>
      <PanelRenderer config={provider} visible showTechnicalSections={showTechnicalSections} />
    </QueryClientProvider>
  );
  const view = render(element(config));
  return {
    change: (provider: ProviderConfig) => view.rerender(element(provider)),
    loaded: () => waitFor(() => expect(client.getQueryData(queryKeys.credentialValues.byProvider(config.id).queryKey)).toBeDefined()),
  };
}

// Panels are lazy chunks: load them up front so no case's first 1 s wait includes the cold import.
beforeAll(async () => {
  await Promise.all([import('../panels/EmailPanel'), import('../panels/BrowserProfilesPanel'), import('../panels/OAuthPanel')]);
});

beforeEach(() => {
  vi.resetAllMocks();
  mocks.sendRequest.mockResolvedValue({ success: true, hasKey: false });
  mocks.saveApiKey.mockResolvedValue({ isValid: true });
  mocks.validateApiKey.mockResolvedValue({ isValid: true });
  mocks.removeApiKey.mockResolvedValue(undefined);
  mocks.getStoredApiKey.mockResolvedValue(null);
  mocks.hasStoredKey.mockResolvedValue(false);
});

describe.each([false, true])('connection forms with technical sections %s', (showTechnicalSections) => {
  it('shows custom IMAP/SMTP and saves the same complete email configuration', async () => {
    const user = userEvent.setup();
    const saved: Record<string, string> = {
      email_provider: 'custom', email_imap_port: '1993', email_imap_encryption: 'tls',
      email_smtp_port: '1587', email_smtp_encryption: 'start-tls',
    };
    mocks.getStoredApiKey.mockImplementation(async (key: string) => saved[key] ?? null);
    mount({ ...BASE, id: 'email', name: 'Email', kind: 'email' }, showTechnicalSections);

    await user.type(await screen.findByLabelText('IMAP Host'), 'imap.example.com');
    await user.type(screen.getByLabelText('SMTP Host'), 'smtp.example.com');
    await user.type(screen.getByLabelText('Email Address'), 'alice@example.com');
    await user.type(screen.getByLabelText('Password', { exact: true }), 'app-password');
    await user.type(screen.getByLabelText(/Display Name/), 'Alice');
    expect(screen.getByLabelText('IMAP Port')).toHaveValue(1993);
    expect(screen.getByLabelText('SMTP Port')).toHaveValue(1587);
    expect(screen.getByLabelText('IMAP Security')).toHaveTextContent('TLS / SSL');
    expect(screen.getByLabelText('SMTP Security')).toHaveTextContent('STARTTLS');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mocks.saveApiKey).toHaveBeenCalledTimes(10));
    expect(mocks.saveApiKey.mock.calls).toEqual([
      ['email_provider', 'custom'], ['email_address', 'alice@example.com'],
      ['email_password', 'app-password'], ['email_display_name', 'Alice'],
      ['email_imap_host', 'imap.example.com'], ['email_imap_port', '1993'], ['email_imap_encryption', 'tls'],
      ['email_smtp_host', 'smtp.example.com'], ['email_smtp_port', '1587'], ['email_smtp_encryption', 'start-tls'],
    ]);
    expect(mocks.removeApiKey).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/Password/)).toHaveValue('');
  });

  it('keeps browser profile creation, import and confirmed deletion available', async () => {
    const user = userEvent.setup();
    mocks.sendRequest.mockImplementation(async (command: string) => command === 'browser_profiles_list' ? {
      success: true,
      profiles: [
        { id: 'work', name: 'Work', kind: 'shared', sites: [{ domain: 'example.com', cookie_count: 2 }], in_use: null },
        { id: 'busy', name: 'Employee', kind: 'employee', sites: [], in_use: { label: 'Assistant' } },
      ],
    } : { success: true });
    mount({ ...BASE, id: 'browser', name: 'Web browser', kind: 'browserProfiles' }, showTechnicalSections);

    expect(await screen.findByText('Work')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Import logins' })).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Delete Employee' })).toBeDisabled();
    await user.type(screen.getByRole('textbox', { name: 'New profile name' }), '  Personal  ');
    await user.click(screen.getByRole('button', { name: 'Add profile' }));
    await waitFor(() => expect(mocks.sendRequest).toHaveBeenCalledWith('browser_profile_create', { name: 'Personal' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Delete Work' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: 'Delete Work' }));
    expect(mocks.sendRequest.mock.calls.some(([command]) => command === 'browser_profile_delete')).toBe(false);
    const confirmation = screen.getByRole('alertdialog');
    await user.click(within(confirmation).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(mocks.sendRequest).toHaveBeenCalledWith('browser_profile_delete', { profile_id: 'work' }));
  });

  it('starts fieldless CLI OAuth without stored fields and displays its one-time code', async () => {
    const user = userEvent.setup();
    const openWindow = vi.spyOn(window, 'open').mockReturnValue(null);
    mocks.sendRequest.mockResolvedValue({ success: true, url: 'https://example.com/device', verification_code: 'ABCD-1234' });
    mount({ ...BASE, id: 'cli', name: 'CLI provider' }, showTechnicalSections);

    const login = await screen.findByRole('button', { name: 'Login with CLI provider' });
    expect(login).toBeEnabled();
    expect(mocks.sendRequest).not.toHaveBeenCalled();
    await user.click(login);
    expect(mocks.sendRequest).toHaveBeenCalledWith('provider_login', {});
    expect(openWindow).toHaveBeenCalledWith('https://example.com/device', '_blank', 'noopener,noreferrer');
    expect(await screen.findByText('ABCD-1234')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save Credentials' })).not.toBeInTheDocument();
    openWindow.mockRestore();
  });

  it('keeps a healthy connected CLI panel focused on disconnect and refresh', async () => {
    mount({
      ...BASE, id: 'codex_cli', name: 'Codex CLI', stored: true,
      ws: { ...BASE.ws!, device_login: 'codex_cli_device_login' },
    }, showTechnicalSections);

    expect(await screen.findByRole('button', { name: 'Disconnect' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /(?:Login|Reconnect) with Codex CLI/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Use device code' })).not.toBeInTheDocument();
    expect(mocks.sendRequest).not.toHaveBeenCalled();
  });

  it('offers reconnect after a failed refresh and shows pending device instructions with a stored marker', async () => {
    const user = userEvent.setup();
    const openWindow = vi.spyOn(window, 'open').mockReturnValue(null);
    const instructions = {
      success: true, connected: false, pending: true,
      url: 'https://example.com/device', verification_code: 'CODE-128',
      message: 'Complete Codex sign-in in your browser.',
    };
    const preparationError = 'The managed Codex CLI is missing or needs an update. Click Login to prepare it, then try again.';
    mocks.sendRequest.mockResolvedValueOnce({ success: false, error: preparationError }).mockResolvedValue(instructions);
    mount({
      ...BASE, id: 'codex_cli', name: 'Codex CLI', stored: true,
      ws: {
        login: 'codex_cli_login', logout: 'codex_cli_logout',
        status: 'codex_cli_status', device_login: 'codex_cli_device_login',
      },
    }, showTechnicalSections);

    await user.click(await screen.findByRole('button', { name: 'Refresh' }));
    expect(mocks.sendRequest).toHaveBeenCalledWith('codex_cli_status', { refresh: true });
    expect(await screen.findByText(preparationError)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reconnect with Codex CLI' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Use device code' }));

    expect(mocks.sendRequest).toHaveBeenCalledWith('codex_cli_device_login', {});
    expect(await screen.findByText(instructions.verification_code)).toBeInTheDocument();
    expect(screen.getByText(instructions.message)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open sign-in page' })).toHaveAttribute('href', instructions.url);
    expect(screen.getByRole('button', { name: 'Cancel sign-in' })).toBeEnabled();
    expect(screen.getByText('Connected')).toBeInTheDocument();
    expect(screen.queryByText(preparationError)).not.toBeInTheDocument();
    expect(openWindow).toHaveBeenCalledWith(instructions.url, '_blank', 'noopener,noreferrer');
    openWindow.mockRestore();
  });

  it('keeps optional tokens usable alongside OAuth without making them a login prerequisite', async () => {
    const user = userEvent.setup();
    const provider: ProviderConfig = {
      ...BASE, id: 'dual', name: 'Dual provider',
      fields: [
        { key: 'apiKey', label: 'Access token', secret: true, required: false, placeholder: 'Optional access token' },
        { key: 'account_email', label: 'Account email', required: false },
      ],
    };
    const view = mount(provider, showTechnicalSections);
    await view.loaded();
    expect(await screen.findByRole('button', { name: 'Login with Dual provider' })).toBeEnabled();
    await user.type(screen.getByPlaceholderText('Optional access token'), 'optional-token');
    await user.click(screen.getByRole('button', { name: 'Validate' }));
    expect(mocks.validateApiKey).toHaveBeenCalledWith('dual', 'optional-token');
    view.change({ ...provider, stored: true, account_label: 'Alice' });
    expect(screen.getByRole('button', { name: 'Disconnect' })).toBeEnabled();
    expect(screen.getByPlaceholderText('Optional access token')).toBeInTheDocument();
    expect(screen.getByLabelText('Account email')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Disconnect' }));
    expect(mocks.sendRequest).toHaveBeenCalledWith('provider_logout', {});
  });
});
