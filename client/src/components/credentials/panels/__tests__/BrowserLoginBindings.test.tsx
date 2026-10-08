import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mocks = vi.hoisted(() => ({ sendRequest: vi.fn(), ready: true }));
vi.mock('@/contexts/WebSocketContext', () => ({
  useWebSocketActions: () => ({ sendRequest: mocks.sendRequest, isReady: mocks.ready }),
}));

import BrowserLoginBindings from '../BrowserLoginBindings';

const username = `op://${'a'.repeat(26)}/${'b'.repeat(26)}/username`;
const password = `op://${'a'.repeat(26)}/${'b'.repeat(26)}/password`;
const binding = { id: 'login-1', label: 'Account', origin: 'https://accounts.example.com',
  profile_id: 'work', employee_id: 'employee-1', workflow_id: 'workflow-1',
  success_origin: 'https://app.example.com', success_path: '/account', success_selector: '[data-account]' };

function mount(visible = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const element = (shown: boolean) => <QueryClientProvider client={client}><BrowserLoginBindings visible={shown} profiles={[{ id: 'work', name: 'Work' }]} /></QueryClientProvider>;
  const view = render(element(visible));
  return { client, change: (shown: boolean) => view.rerender(element(shown)) };
}

beforeEach(() => {
  mocks.ready = true;
  mocks.sendRequest.mockReset();
  mocks.sendRequest.mockImplementation(async (command: string) => command === 'browser_credential_bindings_list' ? { bindings: [] } : {});
});

async function fillReferences(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Username 1Password reference'), username);
  await user.type(screen.getByLabelText('Password 1Password reference'), password);
}

describe('BrowserLoginBindings', () => {
  it('lists metadata and replaces references without retrieving or prefilling saved credentials', async () => {
    mocks.sendRequest.mockImplementation(async (command: string) => command === 'browser_credential_bindings_list'
      ? { bindings: [{ ...binding, username_reference: 'PRIVATE-REF', password_reference: 'SECRET-CANARY' }] } : {});
    const user = userEvent.setup();
    mount();
    expect(await screen.findByText('Account')).toBeInTheDocument();
    expect(screen.getByText('Profile: Work')).toBeInTheDocument();
    expect(screen.queryByText('SECRET-CANARY')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Replace Account' }));
    expect(screen.getByLabelText('Username 1Password reference')).toHaveValue('');
    expect(screen.getByLabelText('Password 1Password reference')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Save website login' })).toBeDisabled();
    await fillReferences(user);
    await user.click(screen.getByRole('button', { name: 'Save website login' }));
    await waitFor(() => expect(mocks.sendRequest).toHaveBeenCalledWith('browser_credential_binding_save', {
      binding_id: 'login-1', label: 'Account', origin: binding.origin, username_reference: username, password_reference: password,
      success_origin: binding.success_origin, success_path: binding.success_path, success_selector: binding.success_selector,
      profile_id: 'work', employee_id: 'employee-1', workflow_id: 'workflow-1',
    }));
    await waitFor(() => expect(screen.queryByLabelText('Password 1Password reference')).not.toBeInTheDocument());
    expect(mocks.sendRequest.mock.calls.every(([command]) => !['get_api_key', 'validate_api_key', 'onepassword_endpoint_validate'].includes(command))).toBe(true);
  });

  it('creates an exact-origin binding with an optional profile and refreshes metadata', async () => {
    const user = userEvent.setup();
    const { client } = mount();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    await screen.findByText('No website login bindings saved.');
    await user.click(screen.getByRole('button', { name: 'Add website login' }));
    await user.type(screen.getByLabelText('Login label'), '  Work account  ');
    await user.type(screen.getByLabelText('Exact login origin'), binding.origin);
    await fillReferences(user);
    await user.type(screen.getByLabelText('Successful login path'), '/dashboard');
    await user.type(screen.getByLabelText('Successful login selector (optional)'), '#signed-in');
    await user.selectOptions(screen.getByLabelText('Restrict to browser profile (optional)'), 'work');
    await user.click(screen.getByRole('button', { name: 'Save website login' }));
    await waitFor(() => expect(mocks.sendRequest).toHaveBeenCalledWith('browser_credential_binding_save', {
      label: 'Work account', origin: binding.origin, username_reference: username, password_reference: password,
      success_origin: binding.origin, success_path: '/dashboard', success_selector: '#signed-in', profile_id: 'work',
    }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['browserCredentialBindings'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['credentialCatalogue'] });
  });

  it('requires confirmation before removing a binding and sends only its opaque ID', async () => {
    mocks.sendRequest.mockImplementation(async (command: string) => command === 'browser_credential_bindings_list' ? { bindings: [binding] } : {});
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole('button', { name: 'Remove website login Account' }));
    expect(mocks.sendRequest).not.toHaveBeenCalledWith('browser_credential_binding_delete', expect.anything());
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Remove login' }));
    await waitFor(() => expect(mocks.sendRequest).toHaveBeenCalledWith('browser_credential_binding_delete', { binding_id: 'login-1' }));
  });

  it('keeps the draft on a safe server validation failure', async () => {
    mocks.sendRequest.mockImplementation(async (command: string) => command === 'browser_credential_bindings_list'
      ? { bindings: [binding] } : { success: false, error: 'Use vault and item IDs.' });
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole('button', { name: 'Replace Account' }));
    await fillReferences(user);
    await user.click(screen.getByRole('button', { name: 'Save website login' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Use vault and item IDs.');
    expect(screen.getByLabelText('Password 1Password reference')).toHaveValue(password);
    expect(screen.getByRole('button', { name: 'Save website login' })).toBeEnabled();
  });

  it('does not load a hidden or disconnected panel', async () => {
    const view = mount(false);
    expect(mocks.sendRequest).not.toHaveBeenCalled();
    mocks.ready = false;
    view.change(true);
    expect(screen.getByRole('button', { name: 'Add website login' })).toBeDisabled();
    expect(mocks.sendRequest).not.toHaveBeenCalled();
  });
});
