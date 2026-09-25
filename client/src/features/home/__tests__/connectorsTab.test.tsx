/**
 * Settings > Connectors: Disconnect removes what the provider's own panel
 * would (an API key, a sign-in), and hands QR and email accounts to their
 * panel; a card glows and confirms only when a provider flips to connected
 * while the tab is open.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ServerProviderConfig } from '@/hooks/useCatalogueQuery';

const sendRequest = vi.fn();
let providers: (ServerProviderConfig & { consumer_category: string })[] = [];

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true }),
}));

vi.mock('../data/connectors', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../data/connectors')>();
  return {
    ...actual,
    useConnectors: () => ({
      providers,
      categories: [{ key: 'ai', label: 'AI' }, { key: 'messages', label: 'Messages' }],
      connectedCount: providers.filter(actual.isConnected).length,
      connectedApps: [],
      hasAi: true,
      isLoading: false,
    }),
  };
});

vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { ThemeProvider } from '@/contexts/ThemeContext';
import { ConnectorsTab } from '../settings/ConnectorsTab';

function ui(onConnect = vi.fn()) {
  return (
    <ThemeProvider>
      <ConnectorsTab onConnect={onConnect} />
    </ThemeProvider>
  );
}
import { pillToast } from '../ui/pillToast';

function provider(patch: Partial<ServerProviderConfig> & { id: string; kind: ServerProviderConfig['kind'] }) {
  return {
    name: patch.id,
    category: 'x',
    category_label: 'X',
    color: '',
    consumer_category: 'ai',
    ...patch,
  } as ServerProviderConfig & { consumer_category: string };
}

async function disconnect(name: string) {
  const card = screen.getByText(name).closest('[data-connector]') as HTMLElement;
  fireEvent.click(card.querySelector('button')!);
  fireEvent.click(await screen.findByRole('button', { name: 'Disconnect' }));
}

beforeEach(() => {
  sendRequest.mockReset().mockResolvedValue({ success: true });
  vi.mocked(pillToast).mockClear();
  providers = [
    provider({ id: 'openai', name: 'OpenAI', kind: 'apiKey', connected: true, fields: [{ key: 'apiKey' }] }),
    provider({ id: 'ollama', name: 'Ollama', kind: 'apiKey', connected: true, fields: [{ key: 'ollama_proxy' }], runs_locally: true }),
    provider({ id: 'google', name: 'Google', kind: 'oauth', connected: true, consumer_category: 'organize', ws: { login: 'google_oauth_login', logout: 'google_logout', status: 's' } }),
    provider({ id: 'whatsapp', name: 'WhatsApp', kind: 'qrPairing', connected: true, consumer_category: 'messages' }),
  ];
});

describe('ConnectorsTab', () => {
  it('deletes an API key', async () => {
    render(ui());
    await disconnect('OpenAI');
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('delete_api_key', { provider: 'openai' }));
    expect(sendRequest).toHaveBeenCalledTimes(1);
  });

  it('deletes a local server’s address too', async () => {
    render(ui());
    await disconnect('Ollama');
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('delete_api_key', { provider: 'ollama_proxy' }));
    expect(sendRequest).toHaveBeenCalledWith('delete_api_key', { provider: 'ollama' });
  });

  it('signs out of an OAuth provider', async () => {
    render(ui());
    await disconnect('Google');
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('google_logout', {}));
  });

  it('opens the panel for a paired phone', async () => {
    const onConnect = vi.fn();
    render(ui(onConnect));
    await disconnect('WhatsApp');
    await waitFor(() => expect(onConnect).toHaveBeenCalledWith('whatsapp'));
    expect(sendRequest).not.toHaveBeenCalled();
  });

  it('confirms a connection only when it happens while the tab is open', () => {
    providers = [provider({ id: 'openai', name: 'OpenAI', kind: 'apiKey', connected: true })];
    const { rerender } = render(ui());
    expect(pillToast).not.toHaveBeenCalled();

    providers = [provider({ id: 'openai', name: 'OpenAI', kind: 'apiKey', connected: false })];
    rerender(ui());
    providers = [provider({ id: 'openai', name: 'OpenAI', kind: 'apiKey', connected: true })];
    rerender(ui());
    expect(pillToast).toHaveBeenCalledTimes(1);
    expect(pillToast).toHaveBeenCalledWith('OpenAI is connected');
  });

  it('filters by category and search', () => {
    render(ui());
    fireEvent.change(screen.getByRole('textbox', { name: 'Search connectors' }), { target: { value: 'goo' } });
    expect(screen.getByText('Google')).toBeInTheDocument();
    expect(screen.queryByText('OpenAI')).not.toBeInTheDocument();
  });
});
