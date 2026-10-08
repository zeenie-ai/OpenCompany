/** Real catalogue, shell state and provider forms with only transport mocked. */
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { makeTestQueryClient, renderWithProviders } from '../../test/providers';
import { CATALOGUE_QUERY_KEY, type CatalogueResponse } from '@/hooks/useCatalogueQuery';
import { useShellDialogsStore, type CredentialsOptions } from '@/stores/shellDialogsStore';
import { useAppStore } from '@/store/useAppStore';
const featureFlags = vi.hoisted(() => ({ normalMode: true }));
vi.mock('@/lib/featureFlags', () => ({ featureFlags }));

const ws = vi.hoisted(() => ({
  isConnected: true, isReady: true, apiKeyStatuses: {},
  sendRequest: vi.fn(), validateApiKey: vi.fn(),
}));
vi.mock('@/contexts/WebSocketContext', () => ({
  useWebSocket: () => ws, useWebSocketActions: () => ws,
  CREDENTIAL_PROBE_REQUEST_TIMEOUT: 180_000,
}));
vi.mock('idb-keyval', () => ({ get: vi.fn().mockResolvedValue(undefined), set: vi.fn().mockResolvedValue(undefined) }));
vi.mock('@/assets/icons', () => ({ NodeIcon: () => null }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
import CredentialsModal from '../CredentialsModal';

let catalogue: CatalogueResponse;
let disabled: string[];
function Harness({ options }: { options?: CredentialsOptions }) {
  const open = useShellDialogsStore((s) => s.openCredentials);
  const close = useShellDialogsStore((s) => s.closeCredentials);
  const visible = useShellDialogsStore((s) => s.credentialsOpen);
  return <><button onClick={() => open(options)}>Open credentials</button><CredentialsModal visible={visible} onClose={close} /></>;
}
function mount(options?: CredentialsOptions) {
  const queryClient = makeTestQueryClient();
  renderWithProviders(<Harness options={options} />, { queryClient });
  return queryClient;
}
async function open() { await userEvent.click(screen.getByRole('button', { name: 'Open credentials' })); }
const keyInput = () => screen.findByPlaceholderText('Enter API key...');

beforeEach(() => {
  vi.clearAllMocks();
  featureFlags.normalMode = true;
  useAppStore.setState({ shellMode: 'normal' });
  useShellDialogsStore.setState({ credentialsOpen: false, credentialsOptions: { intent: 'manage' }, credentialsRequestId: 0 });
  disabled = [];
  catalogue = {
    version: 'test', categories: [{ key: 'ai', label: 'AI', order: 0 }, { key: 'messaging', label: 'Messaging', order: 1 }],
    consumer_categories: [{ key: 'messages', label: 'Messages', order: 0 }, { key: 'ai', label: 'AI', order: 1 }],
    providers: [
      { id: 'openai', name: 'OpenAI', category: 'ai', category_label: 'AI', color: '', kind: 'apiKey',
        consumer_category: 'ai', description: 'GPT models', publisher: 'OpenAI', verified: true,
        fields: [{ key: 'apiKey', label: 'API key', secret: true }], has_defaults: true, stored: false, connected: false },
      { id: 'telegram', name: 'Telegram', category: 'messaging', category_label: 'Messaging', color: '', kind: 'apiKey',
        consumer_category: 'messages', fields: [{ key: 'apiKey', label: 'Bot token', secret: true }], stored: true, connected: true },
    ],
  };
  ws.sendRequest.mockImplementation(async (type: string) => {
    if (type === 'get_credential_catalogue') return catalogue;
    if (type === 'get_node_allowlist') return { disabled_credential_categories: disabled };
    if (type === 'get_stored_api_key') return { hasKey: false };
    if (type === 'get_provider_defaults') return { defaults: { default_model: '', temperature: 0.5, max_tokens: 2048 } };
    if (type === 'get_validated_ai_providers') return { providers: [], global_provider: null, global_model: null };
    if (type === 'get_provider_usage_summary') return { providers: [] };
    return { success: true };
  });
  ws.validateApiKey.mockResolvedValue({ valid: false, message: 'That key was rejected.' });
});

describe('shared credentials host', () => {
  it('browses the same cards in either mode and offers Manage for connected providers', async () => {
    mount(); await open();
    expect(await screen.findByRole('button', { name: 'Connect OpenAI' })).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Manage Telegram' }));
    expect(await screen.findByRole('dialog', { name: 'Manage Telegram' })).toBeVisible();
    expect(toast.success).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'All connectors' }));
    act(() => useAppStore.setState({ shellMode: 'dev' }));
    expect(screen.getByRole('button', { name: 'Manage Telegram' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Connect OpenAI' })).toBeVisible();
    expect(ws.sendRequest.mock.calls.some(([type]) => /oauth_status|telegram_status/.test(type))).toBe(false);
  });

  it('adds technical sections on mode change without remounting or clearing the connection draft', async () => {
    mount({ providerId: 'openai' }); await open();
    const input = await keyInput();
    await userEvent.type(input, 'draft-secret');
    expect(screen.queryByText('Usage & Costs')).not.toBeInTheDocument();
    expect(ws.sendRequest.mock.calls.some(([type]) => type === 'get_provider_defaults')).toBe(false);
    act(() => useAppStore.setState({ shellMode: 'dev' }));
    expect(await screen.findByText('Usage & Costs')).toBeVisible();
    await waitFor(() => expect(ws.sendRequest).toHaveBeenCalledWith('get_provider_defaults', { provider: 'openai' }));
    expect(await keyInput()).toBe(input);
    expect(input).toHaveValue('draft-secret');
    act(() => useAppStore.setState({ shellMode: 'normal' }));
    expect(screen.queryByText('Usage & Costs')).not.toBeInTheDocument();
    expect(input).toHaveValue('draft-secret');
    expect(ws.sendRequest.mock.calls.some(([type]) => type === 'save_provider_defaults')).toBe(false);
  });

  it('shows Dev sections when Home is disabled', async () => {
    featureFlags.normalMode = false;
    mount({ providerId: 'openai' }); await open();
    expect(await screen.findByText('Usage & Costs')).toBeVisible();
  });

  it('waits for a directly requested provider instead of selecting a different one', async () => {
    let resolve!: (value: CatalogueResponse) => void;
    const pending = new Promise<CatalogueResponse>((done) => { resolve = done; });
    const original = ws.sendRequest.getMockImplementation()!;
    ws.sendRequest.mockImplementation((type: string, ...args: unknown[]) => type === 'get_credential_catalogue' ? pending : original(type, ...args));
    mount({ providerId: 'openai', intent: 'connect' }); await open();
    expect(screen.getByRole('status', { name: 'Loading connector' })).toBeVisible();
    expect(screen.queryByText('Connector unavailable')).not.toBeInTheDocument();
    await act(async () => resolve(catalogue));
    expect(await screen.findByRole('dialog', { name: 'Connect OpenAI' })).toBeVisible();
    expect(await keyInput()).toBeVisible();
  });

  it.each(['missing', 'disabled'])('explains a %s direct target without mounting its credential form', async (kind) => {
    if (kind === 'disabled') disabled = ['ai'];
    mount({ providerId: kind === 'missing' ? 'unknown' : 'openai' }); await open();
    expect(await screen.findByText('Connector unavailable')).toBeVisible();
    expect(screen.queryByPlaceholderText('Enter API key...')).not.toBeInTheDocument();
  });

  it.each(['connect', 'manage'] as const)('only auto-closes successful guided connections (%s)', async (intent) => {
    const queryClient = mount({ providerId: 'openai', intent }); await open();
    await keyInput();
    act(() => queryClient.setQueryData(CATALOGUE_QUERY_KEY, {
      ...catalogue, providers: catalogue.providers.map((p) => p.id === 'openai' ? { ...p, connected: true, stored: true } : p),
    }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('OpenAI is connected'));
    expect(useShellDialogsStore.getState().credentialsOpen).toBe(intent === 'manage');
    expect(toast.success).toHaveBeenCalledTimes(1);
  });

  it('waits for category restrictions before mounting a direct target from cached data', async () => {
    let resolve!: (value: unknown) => void;
    const pending = new Promise((done) => { resolve = done; });
    const original = ws.sendRequest.getMockImplementation()!;
    ws.sendRequest.mockImplementation((type: string, ...args: unknown[]) => type === 'get_node_allowlist' ? pending : original(type, ...args));
    const queryClient = mount({ providerId: 'openai' });
    act(() => queryClient.setQueryData(CATALOGUE_QUERY_KEY, catalogue));
    await open();
    expect(screen.getByRole('status', { name: 'Loading connector' })).toBeVisible();
    expect(ws.sendRequest.mock.calls.some(([type]) => type === 'get_stored_api_key')).toBe(false);
    await act(async () => resolve({ disabled_credential_categories: ['ai'] }));
    expect(await screen.findByText('Connector unavailable')).toBeVisible();
    expect(ws.sendRequest.mock.calls.some(([type]) => type === 'get_stored_api_key')).toBe(false);
  });

  it('opens on Connect an AI model with key links, and a refused save preserves the input and shows its reason', async () => {
    mount({ categoryId: 'ai', intent: 'connect' }); await open();
    expect(await screen.findByRole('dialog', { name: 'Connect an AI model' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Manage Telegram' })).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: 'Connect OpenAI' }));
    expect(screen.getByRole('link', { name: 'Get a key from OpenAI' })).toHaveAttribute('href', 'https://platform.openai.com/api-keys');
    await userEvent.type(await keyInput(), 'rejected-key');
    await userEvent.click(screen.getByRole('button', { name: 'Validate' }));
    expect(await screen.findByText('That key was rejected.')).toBeVisible();
    expect(await keyInput()).toHaveValue('rejected-key');
    expect(useShellDialogsStore.getState().credentialsOpen).toBe(true);
  });

  it('closes with Escape and restores focus to the original entry point', async () => {
    mount({ providerId: 'openai' }); await open(); await keyInput();
    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open credentials' })).toHaveFocus());
  });

  it('closes the provider before the browser and supports the real close button', async () => {
    mount(); await open();
    await userEvent.click(await screen.findByRole('button', { name: 'Manage Telegram' }));
    const detail = await screen.findByRole('dialog', { name: 'Manage Telegram' });
    await userEvent.click(within(detail).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Manage Telegram' })).not.toBeInTheDocument());
    expect(screen.getByRole('dialog', { name: 'Connectors' })).toBeVisible();
    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open credentials' })).toHaveFocus());
  });

  it('retains the original focus target when a direct provider opens the browser', async () => {
    mount({ providerId: 'openai' }); await open(); await keyInput();
    await userEvent.click(screen.getByRole('button', { name: 'All connectors' }));
    const browser = await screen.findByRole('dialog', { name: 'Connectors' });
    await waitFor(() => expect(browser.contains(document.activeElement)).toBe(true));
    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open credentials' })).toHaveFocus());
  });
});
