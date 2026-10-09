import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ServerMcpConnector } from '@/hooks/useCatalogueQuery';
import type { ProviderConfig } from '../../types';

const mocks = vi.hoisted(() => ({ sendRequest: vi.fn() }));
vi.mock('@/contexts/WebSocketContext', () => ({
  useWebSocketActions: () => ({ sendRequest: mocks.sendRequest, isReady: true }),
  CREDENTIAL_PROBE_REQUEST_TIMEOUT: 60_000,
}));
vi.mock('sonner', () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }));

import McpConnectorPanel from '../McpConnectorPanel';

const MCP: ServerMcpConnector = {
  slug: 'orders',
  address: 'https://orders.example.com/mcp',
  transport: 'streamable_http',
  sign_in: { kind: 'header', header: 'X-API-Key' },
  server: { name: 'orders', title: 'Orders', version: '1.2.0' },
  tools: [
    { name: 'lookup_order', title: 'Look up an order', description: 'What an order holds.', read_only: true, usable: true, reason: null, enabled: true, ask: false },
    { name: 'send_reply', title: null, description: 'Reply to a customer.', read_only: false, usable: true, reason: null, enabled: true, ask: true },
    { name: 'listing', title: null, description: '', read_only: false, usable: false, reason: "Its inputs aren't described as an object.", enabled: false, ask: true },
  ],
  pending: null,
  read_at: '2026-10-09T10:00:00+00:00',
};

function config(mcp: Partial<ServerMcpConnector>): ProviderConfig {
  return { id: 'mcp:orders', name: 'Orders', category: 'custom', categoryLabel: 'Custom', color: '', kind: 'mcp', iconRef: 'lucide:Plug', mcp: { ...MCP, ...mcp } };
}

function mount({ mcp = {}, onLeave }: { mcp?: Partial<ServerMcpConnector>; onLeave?: () => void } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  render(
    <QueryClientProvider client={client}>
      <McpConnectorPanel config={config(mcp)} visible onLeave={onLeave} />
    </QueryClientProvider>,
  );
  return { invalidate };
}

beforeEach(() => {
  mocks.sendRequest.mockReset().mockResolvedValue({ success: true });
});

describe('McpConnectorPanel', () => {
  it('shows the server, how it signs in, and each tool as the server describes it', () => {
    mount();
    expect(screen.getByText('Orders 1.2.0')).toBeInTheDocument();
    expect(screen.getByText('https://orders.example.com/mcp')).toBeInTheDocument();
    expect(screen.getByText('Header X-API-Key')).toBeInTheDocument();
    expect(screen.getByText('Look up an order')).toBeInTheDocument();
    expect(screen.getByText('Read-only')).toBeInTheDocument();
    expect(screen.getByText("Can't be used. Its inputs aren't described as an object.")).toBeInTheDocument();
    // A tool no model could call has no switches.
    expect(screen.queryByRole('switch', { name: 'Let employees use listing' })).not.toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Ask first before Look up an order' })).not.toBeChecked();
    expect(screen.getByRole('switch', { name: 'Ask first before send_reply' })).toBeChecked();
  });

  it('tests the connection and says what the server answered', async () => {
    mocks.sendRequest.mockResolvedValue({ success: true, ok: false, message: "Couldn't connect to orders.example.com. Check that the server is running." });
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole('button', { name: 'Test' }));
    expect(mocks.sendRequest).toHaveBeenCalledWith('mcp_connector_test', { ref: 'mcp:orders' }, 60_000);
    expect(await screen.findByRole('status')).toHaveTextContent("Couldn't connect to orders.example.com.");
  });

  it('says so when a refresh found nothing new, and shows a refusal in the server words', async () => {
    mocks.sendRequest.mockResolvedValueOnce({ success: true, changes: null });
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole('button', { name: 'Refresh tools' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Nothing changed');
    mocks.sendRequest.mockResolvedValueOnce({ success: false, error: 'orders.example.com refused the sign-in (HTTP 401). Check the token or header.' });
    await user.click(screen.getByRole('button', { name: 'Refresh tools' }));
    expect(await screen.findByText('orders.example.com refused the sign-in (HTTP 401). Check the token or header.')).toHaveAttribute('role', 'alert');
  });

  it('holds what a refresh found until the owner accepts it', async () => {
    const user = userEvent.setup();
    const { invalidate } = mount({ mcp: { pending: { added: ['refund_order'], removed: [], changed: ['send_reply'], instructions: true, read_at: MCP.read_at } } });
    expect(screen.getByText('The server changed its tools')).toBeInTheDocument();
    expect(screen.getByText('New: refund_order')).toBeInTheDocument();
    expect(screen.getByText('Changed: send_reply')).toBeInTheDocument();
    expect(screen.getByText("The server's instructions changed.")).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Accept' }));
    expect(mocks.sendRequest).toHaveBeenCalledWith('mcp_connector_review', { ref: 'mcp:orders', accept: true }, 60_000);
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['credentialCatalogue'] }));
  });

  it('turns a tool off, showing the new position while the server saves it', async () => {
    let answer: (value: unknown) => void = () => undefined;
    mocks.sendRequest.mockImplementation(() => new Promise((resolve) => (answer = resolve)));
    const user = userEvent.setup();
    const { invalidate } = mount();
    const use = screen.getByRole('switch', { name: 'Let employees use send_reply' });
    await user.click(use);
    expect(mocks.sendRequest).toHaveBeenCalledWith('mcp_connector_set_tool', { ref: 'mcp:orders', tool: 'send_reply', enabled: false }, 60_000);
    expect(use).not.toBeChecked();
    answer({ success: true, tool: 'send_reply', enabled: false, ask: true });
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['credentialCatalogue'] }));
  });

  it('removes the connector only after a confirmation, then leaves its page', async () => {
    const onLeave = vi.fn();
    const user = userEvent.setup();
    mount({ onLeave });
    await user.click(screen.getByRole('button', { name: 'Remove' }));
    expect(mocks.sendRequest).not.toHaveBeenCalled();
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(onLeave).toHaveBeenCalledTimes(1));
    expect(mocks.sendRequest).toHaveBeenCalledWith('mcp_connector_remove', { ref: 'mcp:orders' }, 60_000);
  });
});
