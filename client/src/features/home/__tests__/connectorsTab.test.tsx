/**
 * Settings > Connectors: Disconnect removes what the provider's own panel
 * would (an API key, a sign-in), and hands QR and email accounts to their
 * panel; connection feedback belongs to the shared host; the page opens on the category it was asked for,
 * and search also matches who makes the app.
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

vi.mock('@/components/credentials/catalogue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/credentials/catalogue')>();
  return {
    ...actual,
    useCredentialsCatalogue: () => ({
      providers,
      categories: [{ key: 'ai', label: 'AI' }, { key: 'messages', label: 'Messages' }],
      connectedApps: providers.filter(actual.isConnected).filter((p) => p.consumer_category !== 'ai'),
      hasAi: true,
      isLoading: false,
    }),
  };
});

vi.mock('sonner', () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { ThemeProvider } from '@/contexts/ThemeContext';
import { ConnectorsTab } from '../settings/ConnectorsTab';

function ui(onConnect = vi.fn(), initialCategory?: string) {
  return (
    <ThemeProvider>
      <ConnectorsTab onConnect={onConnect} initialCategory={initialCategory} />
    </ThemeProvider>
  );
}
import { toast } from 'sonner';

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
  fireEvent.click(screen.getByRole('button', { name: `Disconnect ${name}` }));
  fireEvent.click(await screen.findByRole('button', { name: 'Disconnect' }));
}

beforeEach(() => {
  sendRequest.mockReset().mockResolvedValue({ success: true });
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.info).mockClear();
  vi.mocked(toast.error).mockClear();
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
    await waitFor(() => expect(onConnect).toHaveBeenCalledWith('whatsapp', 'manage'));
    expect(sendRequest).not.toHaveBeenCalled();
  });

  it('leaves connection-success feedback to the shared host', () => {
    providers = [provider({ id: 'openai', name: 'OpenAI', kind: 'apiKey', connected: true })];
    const { rerender } = render(ui());
    expect(toast.success).not.toHaveBeenCalled();

    providers = [provider({ id: 'openai', name: 'OpenAI', kind: 'apiKey', connected: false })];
    rerender(ui());
    providers = [provider({ id: 'openai', name: 'OpenAI', kind: 'apiKey', connected: true })];
    rerender(ui());
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Manage OpenAI' })).toBeInTheDocument();
  });

  it('opens existing connections for management and new providers for connection', () => {
    providers.push(provider({ id: 'gemini', name: 'Gemini', kind: 'apiKey', connected: false }));
    const onConnect = vi.fn();
    render(ui(onConnect));
    fireEvent.click(screen.getByRole('button', { name: 'Manage OpenAI' }));
    expect(onConnect).toHaveBeenLastCalledWith('openai', 'manage');
    fireEvent.click(screen.getByRole('button', { name: 'Connect Gemini' }));
    expect(onConnect).toHaveBeenLastCalledWith('gemini', 'connect');
    expect(sendRequest).not.toHaveBeenCalled();
  });

  it('filters by category and search', () => {
    render(ui());
    fireEvent.change(screen.getByRole('textbox', { name: 'Search connectors' }), { target: { value: 'goo' } });
    expect(screen.getByText('Google')).toBeInTheDocument();
    expect(screen.queryByText('OpenAI')).not.toBeInTheDocument();
  });

  it('opens on the category it was asked for, with the filter showing', () => {
    render(ui(vi.fn(), 'messages'));
    expect(screen.getByText('Messages').closest('button')).toHaveAttribute('data-state', 'on');
    expect(screen.getByText('WhatsApp')).toBeInTheDocument();
    expect(screen.queryByText('OpenAI')).not.toBeInTheDocument();
  });

  it('searches who makes the app', () => {
    providers = [
      provider({ id: 'gemini', name: 'Gemini', kind: 'apiKey', publisher: 'Google', verified: true }),
      provider({ id: 'openai', name: 'OpenAI', kind: 'apiKey', publisher: 'OpenAI' }),
    ];
    render(ui());
    fireEvent.change(screen.getByRole('textbox', { name: 'Search connectors' }), { target: { value: 'google' } });
    expect(screen.getByText('Gemini')).toBeInTheDocument();
    expect(screen.getByText('by Google')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Verified' })).toBeInTheDocument();
    expect(screen.queryByText('OpenAI')).not.toBeInTheDocument();
  });

  // The browser takes other words, an embedded size and no cap for the
  // Welcome guide; Settings keeps the defaults.
  it('keeps its own words, and shows the top eight on Discover until Show all', () => {
    providers = Array.from({ length: 10 }, (_, i) => provider({ id: `app${i}`, name: `App ${i}`, kind: 'apiKey', consumer_category: 'messages' }));
    render(ui());
    expect(screen.getByRole('heading', { name: 'Connectors' })).toBeInTheDocument();
    expect(screen.getByLabelText('Connectors to show')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Search connectors' })).toBeInTheDocument();
    expect(screen.getByText('Top connectors')).toBeInTheDocument();
    expect(document.querySelectorAll('[data-catalog-item]')).toHaveLength(8);
    fireEvent.click(screen.getByRole('button', { name: /Show all/ }));
    expect(document.querySelectorAll('[data-catalog-item]')).toHaveLength(10);
  });
});
