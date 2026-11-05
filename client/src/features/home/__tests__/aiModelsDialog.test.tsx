/**
 * Home's AI models dialog (onboarding/AiModelsDialog): it opens on every AI
 * model, Esc leaves a model's page before it closes the dialog, and focus
 * goes back to whatever opened it. A model that needs one key or one
 * address gets the plain page (what it is, where to get a key, the field,
 * the help line, Checking…, a refused key's reason with the draft kept);
 * a first connect is the list's to report, after which the dialog closes,
 * or stays on the list when the checklist opened it; Save on a connected
 * model says so and returns to the list; Disconnect asks first. The
 * Gateway, named endpoints, a 1Password-only install and a key held in
 * 1Password keep the full page.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ConsumerProvider } from '@/components/credentials/catalogue';

const ws = vi.hoisted(() => ({
  isConnected: true,
  isReady: true,
  sendRequest: vi.fn(),
  validateApiKey: vi.fn(),
  deleteApiKey: vi.fn(),
  addEventListener: () => () => {},
}));
let providers: ConsumerProvider[] = [];
let stored: Record<string, unknown> = {};

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocket: () => ws,
  useWebSocketActions: () => ws,
}));
vi.mock('@/components/credentials/catalogue', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/components/credentials/catalogue')>()),
  useCredentialsCatalogue: () => ({
    catalogue: { data: {} },
    providers,
    categories: [],
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
}));
// The full page's panel has its own tests.
vi.mock('@/components/credentials/PanelRenderer', () => ({ default: () => <p>The provider’s panel</p> }));
vi.mock('../../../app/ShellModeSwitch', () => ({ useShellMode: () => 'normal' }));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { ThemeProvider } from '@/contexts/ThemeContext';
import { AiModelsDialog } from '../onboarding/AiModelsDialog';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';

function provider(id: string, name: string, patch: Partial<ConsumerProvider> = {}): ConsumerProvider {
  return { id, name, kind: 'apiKey', category: 'ai', category_label: 'AI', color: '', consumer_category: 'ai', connected: false, ...patch };
}

const OPENAI = provider('openai', 'OpenAI', {
  description: 'GPT models from OpenAI.',
  fields: [{ key: 'apiKey', label: 'API Key', type: 'password', secret: true, required: true, placeholder: 'sk-...' }],
});
const OLLAMA = provider('ollama', 'Ollama', {
  description: 'Run AI models on this computer with Ollama.',
  runs_locally: true,
  fields: [{ key: 'ollama_proxy', label: 'Base URL', type: 'string', placeholder: 'http://localhost:11434/v1', required: true }],
});
const GATEWAY = provider('opencompany_gateway', 'OpenCompany Gateway', {
  fields: OPENAI.fields,
  ws: { status: 'gateway_status' } as ConsumerProvider['ws'],
});
const SERVER = provider('openai_compatible', 'Model server', {
  fields: [
    { key: 'openai_compatible_url', label: 'Base URL', type: 'string' },
    { key: 'openai_compatible_label', label: 'Label', type: 'string' },
    { key: 'openai_compatible_api_key', label: 'API key (optional)', type: 'password', secret: true },
  ],
  endpoints: [],
});

const connected = (p: ConsumerProvider) => ({ ...p, connected: true, stored: true });

function renderDialog() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = () => (
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <button type="button" onClick={() => useHomeStore.getState().openConnectAI()}>
          Open AI models
        </button>
        <AiModelsDialog />
      </QueryClientProvider>
    </ThemeProvider>
  );
  const utils = render(tree());
  /** Render again: the catalogue mock reads `providers` anew. */
  return { ...utils, refresh: () => utils.rerender(tree()) };
}

/** Open the dialog from its opener, then a model's page from its card. */
async function openPage(card: string) {
  const utils = renderDialog();
  fireEvent.click(screen.getByRole('button', { name: 'Open AI models' }));
  fireEvent.click(await screen.findByRole('button', { name: card }));
  return utils;
}

beforeEach(() => {
  providers = [OPENAI, OLLAMA];
  stored = {};
  vi.mocked(pillToast).mockClear();
  ws.validateApiKey.mockReset().mockResolvedValue({ valid: true });
  ws.deleteApiKey.mockReset().mockImplementation((id: string) => ws.sendRequest('delete_api_key', { provider: id }));
  ws.sendRequest.mockReset().mockImplementation(async (type: string, data?: { provider?: string }) => {
    if (type === 'get_stored_api_key') return stored[data?.provider ?? ''] ?? { hasKey: false };
    return { success: true };
  });
  useHomeStore.setState({ connectAI: { open: false, provider: null, closeOnConnect: true } });
});

describe('AiModelsDialog', () => {
  it('opens on every AI model, closes with Close, and gives focus back to its opener', async () => {
    renderDialog();
    const opener = screen.getByRole('button', { name: 'Open AI models' });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole('dialog', { name: 'AI models' });
    expect(within(dialog).getByRole('heading', { name: 'Connect an AI model' })).toBeInTheDocument();
    expect(dialog.querySelectorAll('[data-catalog-item]')).toHaveLength(2);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    expect(useHomeStore.getState().connectAI.open).toBe(false);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it('leaves a model’s page with Esc, then closes with it', async () => {
    await openPage('Connect OpenAI');
    expect(screen.getByRole('heading', { name: 'OpenAI' })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText('API key'), { key: 'Escape' });
    expect(screen.queryByRole('heading', { name: 'OpenAI' })).not.toBeInTheDocument();
    expect(useHomeStore.getState().connectAI.open).toBe(true);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Connect OpenAI' })).toHaveFocus());
    fireEvent.keyDown(screen.getByRole('button', { name: 'Connect OpenAI' }), { key: 'Escape' });
    expect(useHomeStore.getState().connectAI.open).toBe(false);
  });

  it('shows a key’s plain page: what it is, where to get a key, and Connect once one is typed', async () => {
    await openPage('Connect OpenAI');
    // The list stays mounted, hidden, so read what the page's header says.
    const header = screen.getByRole('heading', { name: 'OpenAI' }).parentElement!;
    expect(within(header).getByText('GPT models from OpenAI.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Get a key from OpenAI' })).toHaveAttribute('href', 'https://platform.openai.com/api-keys');
    expect(screen.getByText('OpenCompany keeps your key encrypted.')).toBeInTheDocument();
    expect(screen.queryByText('Your key stays on this computer.')).not.toBeInTheDocument();
    // Not the full page.
    expect(screen.queryByRole('heading', { name: 'Connect OpenAI' })).not.toBeInTheDocument();
    expect(screen.queryByText('The provider’s panel')).not.toBeInTheDocument();
    const key = screen.getByLabelText('API key');
    expect(key).toHaveAttribute('placeholder', 'sk-...');
    const connect = screen.getByRole('button', { name: 'Connect' });
    expect(connect).toBeDisabled();
    fireEvent.change(key, { target: { value: 'sk-test' } });
    expect(connect).toBeEnabled();
  });

  it('says it is checking, and a refused key keeps the draft and says why', async () => {
    let answer!: (value: unknown) => void;
    ws.validateApiKey.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    await openPage('Connect OpenAI');
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-wrong' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    expect(await screen.findByRole('button', { name: 'Checking…' })).toBeDisabled();
    expect(ws.validateApiKey).toHaveBeenCalledWith('openai', 'sk-wrong');
    await act(async () => answer({ valid: false, message: 'That key was rejected.' }));
    expect(screen.getByRole('alert')).toHaveTextContent('That key was rejected.');
    const key = screen.getByLabelText('API key');
    expect(key).toHaveValue('sk-wrong');
    expect(key).toHaveAttribute('aria-invalid', 'true');
    expect(useHomeStore.getState().connectAI).toMatchObject({ open: true, provider: { id: 'openai' } });
  });

  it('closes once the list says the model connected', async () => {
    const { refresh } = await openPage('Connect OpenAI');
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-good' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(ws.validateApiKey).toHaveBeenCalledWith('openai', 'sk-good'));
    providers = [connected(OPENAI), OLLAMA];
    refresh();
    expect(pillToast).toHaveBeenCalledTimes(1);
    expect(pillToast).toHaveBeenCalledWith('OpenAI is connected');
    expect(useHomeStore.getState().connectAI.open).toBe(false);
  });

  it('stays on the list after a connect when the checklist opened it', async () => {
    const { refresh } = renderDialog();
    act(() => useHomeStore.getState().openConnectAI({ closeOnConnect: false }));
    fireEvent.click(await screen.findByRole('button', { name: 'Connect OpenAI' }));
    providers = [connected(OPENAI), OLLAMA];
    refresh();
    expect(pillToast).toHaveBeenCalledWith('OpenAI is connected');
    expect(useHomeStore.getState().connectAI).toMatchObject({ open: true, provider: null });
    expect(screen.getByRole('heading', { name: 'You’re connected' })).toBeInTheDocument();
  });

  it('asks a local server for its address, the saved one or the default', async () => {
    stored = { ollama_proxy: { hasKey: false, apiKey: 'http://localhost:11434/v1' } };
    await openPage('Connect Ollama');
    const address = screen.getByLabelText('Server address');
    await waitFor(() => expect(address).toHaveValue('http://localhost:11434/v1'));
    expect(screen.getByText('Start Ollama first. OpenCompany connects to it at this address.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Get a key/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
    await waitFor(() => expect(ws.validateApiKey).toHaveBeenCalledWith('ollama', 'http://localhost:11434/v1'));
  });

  it('says a model is connected, and Save says so again and returns to the list', async () => {
    providers = [connected(OPENAI), OLLAMA];
    await openPage('Manage OpenAI');
    expect(screen.getByText('Connected')).toBeInTheDocument();
    const key = screen.getByLabelText('API key');
    expect(key).toHaveValue('');
    expect(key).toHaveAttribute('placeholder', '•••••••• saved');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    fireEvent.change(key, { target: { value: 'sk-new' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('OpenAI is connected'));
    expect(useHomeStore.getState().connectAI).toMatchObject({ open: true, provider: null });
    expect(screen.getByRole('heading', { name: 'You’re connected' })).toBeInTheDocument();
  });

  it('asks before disconnecting: Keep it keeps the model, Disconnect removes it', async () => {
    providers = [connected(OPENAI), OLLAMA];
    await openPage('Manage OpenAI');
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
    fireEvent.click(within(await screen.findByRole('alertdialog', { name: 'Disconnect OpenAI?' })).getByRole('button', { name: 'Keep it' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
    expect(ws.sendRequest).not.toHaveBeenCalledWith('delete_api_key', expect.anything());
    expect(screen.getByRole('heading', { name: 'OpenAI' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Disconnect' }));
    await waitFor(() => expect(ws.sendRequest).toHaveBeenCalledWith('delete_api_key', { provider: 'openai' }));
    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('OpenAI disconnected', { tone: 'info' }));
    expect(useHomeStore.getState().connectAI.provider).toBeNull();
  });

  it.each([
    ['the Gateway', GATEWAY],
    ['a server with named endpoints', SERVER],
    ['an install that requires 1Password', { ...OPENAI, sourceRequired: true }],
  ])('keeps the full page for %s', async (_, model) => {
    providers = [model];
    await openPage(`Connect ${model.name}`);
    expect(screen.getByRole('heading', { name: `Connect ${model.name}` })).toBeInTheDocument();
    expect(screen.getByText('The provider’s panel')).toBeInTheDocument();
  });

  it('keeps the full page for a key held in 1Password', async () => {
    providers = [connected(OPENAI)];
    stored = { openai: { hasKey: true, credentialSource: { source: 'onepassword', reference: 'op://Private/OpenAI/key' } } };
    await openPage('Manage OpenAI');
    expect(await screen.findByRole('heading', { name: 'Manage OpenAI' })).toBeInTheDocument();
    expect(screen.getByText('The provider’s panel')).toBeInTheDocument();
  });
});
