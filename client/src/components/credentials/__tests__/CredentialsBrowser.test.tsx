import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { toast } from 'sonner';
import { ThemeProvider } from '@/contexts/ThemeContext';
import type { CredentialsCatalogue, ConsumerProvider } from '../catalogue';

const sendRequest = vi.fn();
const useCatalogue = vi.fn();
vi.mock('@/contexts/WebSocketContext', () => ({
  useWebSocketActions: () => ({ sendRequest, isReady: true }),
  CREDENTIAL_PROBE_REQUEST_TIMEOUT: 60_000,
}));
vi.mock('../catalogue', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../catalogue')>()),
  useCredentialsCatalogue: () => useCatalogue(),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { CredentialsBrowser } from '../CredentialsBrowser';

const CONNECTOR: ConsumerProvider = {
  id: 'mcp:orders', name: 'Orders', category: 'custom', category_label: 'Custom',
  consumer_category: 'custom', color: '', kind: 'mcp', connected: true, publisher: 'you', verified: false,
};

const ENDPOINT: ConsumerProvider = {
  id: 'openai_compatible', name: 'Model server', category: 'ai', category_label: 'AI',
  consumer_category: 'ai', color: '', kind: 'apiKey', connected: true,
  endpoints: [{ ref: 'openai_compatible:lab', label: 'Lab', base_url: 'http://gpu:8000', kind: 'generic', model_count: 1 }],
};

function catalogue(providers: ConsumerProvider[] = [ENDPOINT]): CredentialsCatalogue {
  return {
    catalogue: { data: { providers } }, providers, categories: [],
    isLoading: false, isError: false, error: null, refetch: vi.fn(),
  } as unknown as CredentialsCatalogue;
}

beforeEach(() => {
  sendRequest.mockReset().mockResolvedValue({ success: true });
  useCatalogue.mockReset();
});

describe('CredentialsBrowser', () => {
  it('uses its host catalogue without fetching another copy and can manage in Yours', () => {
    const onConnect = vi.fn();
    render(<ThemeProvider><CredentialsBrowser catalogue={catalogue()} onConnect={onConnect} /></ThemeProvider>);
    expect(useCatalogue).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('radio', { name: /Yours/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Manage Model server' }));
    expect(onConnect).toHaveBeenCalledWith('openai_compatible', 'manage');
    expect(sendRequest).not.toHaveBeenCalled();
  });

  it('hands named-endpoint removal to the panel instead of deleting the family id', async () => {
    const onConnect = vi.fn();
    render(<ThemeProvider><CredentialsBrowser catalogue={catalogue()} onConnect={onConnect} /></ThemeProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect Model server' }));
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
    await waitFor(() => expect(onConnect).toHaveBeenCalledWith('openai_compatible', 'manage'));
    expect(sendRequest).not.toHaveBeenCalled();
  });

  it('adds a custom connector, then opens its page once the catalogue holds it', async () => {
    const steps: string[] = [];
    const onConnect = vi.fn(() => steps.push('open'));
    const state = catalogue();
    vi.mocked(state.refetch).mockImplementation(async () => {
      steps.push('refetch');
      return undefined as never;
    });
    sendRequest.mockResolvedValue({ success: true, ref: 'mcp:orders', tools: 2 });
    render(<ThemeProvider><CredentialsBrowser catalogue={state} onConnect={onConnect} /></ThemeProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    const form = within(screen.getByRole('form', { name: 'Custom connector' }));
    expect(form.getByRole('button', { name: 'Add' })).toBeDisabled();
    fireEvent.change(form.getByLabelText('Server URL'), { target: { value: 'https://orders.example.com/mcp' } });
    fireEvent.click(form.getByRole('radio', { name: 'Token' }));
    expect(form.getByRole('button', { name: 'Add' })).toBeDisabled();
    fireEvent.change(form.getByLabelText('Token'), { target: { value: 's3cret' } });
    fireEvent.click(form.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(onConnect).toHaveBeenCalledWith('mcp:orders', 'manage'));
    expect(steps).toEqual(['refetch', 'open']);
    expect(sendRequest).toHaveBeenCalledWith(
      'mcp_connector_add',
      { name: '', url: 'https://orders.example.com/mcp', sign_in: { kind: 'bearer', token: 's3cret' } },
      60_000,
    );
    expect(screen.queryByRole('form', { name: 'Custom connector' })).not.toBeInTheDocument();
  });

  it('keeps the form and shows the server words when a connector cannot be added', async () => {
    sendRequest.mockResolvedValue({ success: false, error: "Couldn't connect to orders.example.com. Check that the server is running." });
    const onConnect = vi.fn();
    render(<ThemeProvider><CredentialsBrowser catalogue={catalogue()} onConnect={onConnect} /></ThemeProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    const form = within(screen.getByRole('form', { name: 'Custom connector' }));
    fireEvent.change(form.getByLabelText('Server URL'), { target: { value: 'https://orders.example.com/mcp' } });
    fireEvent.click(form.getByRole('button', { name: 'Add' }));
    expect(await form.findByRole('alert')).toHaveTextContent("Couldn't connect to orders.example.com.");
    expect(form.getByLabelText('Server URL')).toHaveValue('https://orders.example.com/mcp');
    expect(onConnect).not.toHaveBeenCalled();
  });

  it('has no Add where the host turns custom connectors off', () => {
    render(<ThemeProvider><CredentialsBrowser catalogue={catalogue()} onConnect={vi.fn()} customConnectors={false} /></ThemeProvider>);
    expect(screen.queryByRole('button', { name: 'Add' })).not.toBeInTheDocument();
  });

  it('removes a custom connector on Disconnect, and says why when the server refuses', async () => {
    render(<ThemeProvider><CredentialsBrowser catalogue={catalogue([CONNECTOR])} onConnect={vi.fn()} /></ThemeProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect Orders' }));
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('mcp_connector_remove', { ref: 'mcp:orders' }));
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith('Orders disconnected'));

    sendRequest.mockResolvedValue({ success: false, error: 'There is no such connector.' });
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect Orders' }));
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('There is no such connector.'));
  });

  it('offers a retry when no catalogue could be loaded', () => {
    const state = catalogue([]);
    state.catalogue = { ...state.catalogue, data: undefined } as CredentialsCatalogue['catalogue'];
    state.isError = true;
    render(<ThemeProvider><CredentialsBrowser catalogue={state} onConnect={vi.fn()} /></ThemeProvider>);
    expect(screen.getByText("Couldn't load connectors")).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(state.refetch).toHaveBeenCalledTimes(1);
  });
});
