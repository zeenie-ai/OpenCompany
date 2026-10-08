import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();
vi.mock('@/contexts/WebSocketContext', () => ({ useWebSocket: () => ({ sendRequest }), CREDENTIAL_PROBE_REQUEST_TIMEOUT: 60_000 }));
import OnePasswordField from '../OnePasswordField';

const REF = `op://${'a'.repeat(26)}/${'b'.repeat(26)}/credential`;
const onSaved = vi.fn();
const onError = vi.fn();
function panel(endpoint = false, saved = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><OnePasswordField provider={endpoint ? 'openai_compatible' : 'openai'} endpoint={endpoint} saved={saved ? { source: 'onepassword', reference: REF } : undefined} onSaved={onSaved} onError={onError} /></QueryClientProvider>);
}

describe('1Password credential enrollment', () => {
  beforeEach(() => { sendRequest.mockReset(); onSaved.mockReset(); onError.mockReset(); });
  it('loads saved references and submits no resolved API key', async () => {
    sendRequest.mockResolvedValue({ success: true, valid: true });
    panel(false, true);
    expect(screen.getByLabelText('1Password field reference')).toHaveValue(REF);
    await userEvent.click(screen.getByRole('button', { name: 'Validate 1Password binding' }));
    expect(sendRequest).toHaveBeenCalledWith('validate_api_key', { provider: 'openai', credential_source: 'onepassword', reference: REF }, 60_000);
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    expect(screen.queryByDisplayValue('secret')).not.toBeInTheDocument();
  });
  it('keeps the reference and exposes a safe validation failure', async () => {
    sendRequest.mockResolvedValue({ success: false, valid: false, error: 'Authorize the runtime.' });
    panel(false, true);
    await userEvent.click(screen.getByRole('button', { name: 'Validate 1Password binding' }));
    expect(onError).toHaveBeenCalledWith('Authorize the runtime.');
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByLabelText('1Password field reference')).toHaveValue(REF);
  });
  it('enrolls a public endpoint with an ID reference and clears the add form', async () => {
    sendRequest.mockResolvedValue({ success: true, valid: true });
    panel(true);
    await userEvent.type(screen.getByLabelText('Endpoint name'), 'my-server');
    await userEvent.type(screen.getByLabelText('Base URL'), 'https://models.example.com/v1');
    await userEvent.type(screen.getByLabelText('1Password field reference'), REF);
    await userEvent.click(screen.getByRole('button', { name: 'Add 1Password endpoint' }));
    expect(sendRequest).toHaveBeenCalledWith('onepassword_endpoint_validate', { provider: 'openai_compatible:my-server', reference: REF, base_url: 'https://models.example.com/v1', label: 'my-server' }, 60_000);
    await waitFor(() => expect(screen.getByLabelText('1Password field reference')).toHaveValue(''));
  });
});
