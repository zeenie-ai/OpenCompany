/**
 * The Welcome guide on Home: it opens by itself once, at the saved step, for
 * an owner who has not finished it, and a remount never reopens it; the nav
 * reaches the steps already visited and Connect; the footer offers the next
 * move each step allows; every way out saves the step reached; Connect lists
 * every AI model and nothing else, opens a provider's page in place of the
 * list (Back and Esc return to it), and returns to the list when the model
 * connects; the last step creates the setup, or, with no AI model, sends
 * nothing until one is connected on step 2, which then finishes the guide.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ServerProviderConfig } from '@/hooks/useCatalogueQuery';

const sendRequest = vi.fn();
let settingsRow: Record<string, unknown> = {};
let persistSaves = true;
let providers: (ServerProviderConfig & { consumer_category: string })[] = [];
const job = { value: '', working: false, onChange: vi.fn(), pick: vi.fn(), submit: vi.fn() };

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));

vi.mock('@/components/credentials/catalogue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/credentials/catalogue')>();
  return {
    ...actual,
    useCredentialsCatalogue: () => ({
      catalogue: { data: {} },
      providers,
      categories: [],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    }),
    useConnectors: () => ({
      providers,
      categories: [],
      connectedApps: [],
      hasAi: providers.some((p) => p.consumer_category === 'ai' && actual.isConnected(p)),
      isLoading: false,
    }),
  };
});

// The draft pipeline has its own tests (useSendJob sends
// generate_employee_setup); here the guide only hands it a job.
vi.mock('../genui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../genui')>()),
  useJobComposer: () => job,
  useSendJob: () => job.submit,
}));
// A provider's panel (the key field) has its own tests.
vi.mock('@/components/credentials/PanelRenderer', () => ({ default: () => <p>The provider’s panel</p> }));
vi.mock('../../../app/ShellModeSwitch', () => ({ useShellMode: () => 'normal' }));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { ThemeProvider } from '@/contexts/ThemeContext';
import { WelcomeGuide } from '../onboarding/WelcomeGuide';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';

function provider(id: string, consumer: string, connected = false) {
  return { id, name: id, kind: 'apiKey', category: 'x', category_label: 'X', color: '', consumer_category: consumer, connected } as ServerProviderConfig & {
    consumer_category: string;
  };
}

function renderGuide() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = () => (
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <WelcomeGuide />
      </QueryClientProvider>
    </ThemeProvider>
  );
  const utils = render(tree());
  /** Render again: the catalogue mock reads `providers` anew. */
  return { ...utils, refresh: () => utils.rerender(tree()) };
}

const saves = () =>
  sendRequest.mock.calls.filter(([type]) => type === 'save_user_settings').map(([, data]) => (data as { settings: unknown }).settings);

beforeEach(() => {
  settingsRow = { onboarding_completed: false, onboarding_step: 0 };
  persistSaves = true;
  providers = [provider('openai', 'ai'), provider('anthropic', 'ai'), provider('whatsapp', 'messages')];
  Object.assign(job, { value: '', working: false });
  job.submit.mockReset();
  job.pick.mockReset();
  job.onChange.mockReset();
  vi.mocked(pillToast).mockClear();
  sendRequest.mockReset().mockImplementation(async (type: string, data?: { settings?: Record<string, unknown> }) => {
    if (type === 'get_user_settings') return { settings: { ...settingsRow } };
    if (type === 'save_user_settings' && persistSaves) Object.assign(settingsRow, data?.settings);
    return { success: true };
  });
  useHomeStore.setState({
    view: { kind: 'employee', workflowId: 'w1' },
    guide: { open: false, step: 'welcome', furthest: 0, checked: false, provider: null, pendingDraft: false },
  });
});

describe('WelcomeGuide opening', () => {
  it('opens for an owner who has not finished it, at the step they left', async () => {
    settingsRow = { onboarding_completed: false, onboarding_step: 2 };
    renderGuide();
    expect(await screen.findByRole('heading', { name: 'Who should we hire first?' })).toBeInTheDocument();
    expect(screen.getByText('3 / 3')).toBeInTheDocument();
  });

  it('starts on Welcome when the saved step is not one of its steps', async () => {
    settingsRow = { onboarding_completed: false, onboarding_step: 7 };
    renderGuide();
    expect(await screen.findByRole('heading', { name: 'Your AI team, hired in plain words.' })).toBeInTheDocument();
  });

  it('never opens by itself for an owner who finished it', async () => {
    settingsRow = { onboarding_completed: true, onboarding_step: 4 };
    renderGuide();
    await waitFor(() => expect(useHomeStore.getState().guide.checked).toBe(true));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('does not reopen when Home mounts again, even before its save lands', async () => {
    persistSaves = false;
    const first = renderGuide();
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    first.unmount();
    renderGuide();
    await waitFor(() => expect(sendRequest.mock.calls.filter(([type]) => type === 'get_user_settings')).toHaveLength(2));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('WelcomeGuide steps', () => {
  it('greets the owner by name, and reaches only the visited steps and Connect', async () => {
    settingsRow = { onboarding_completed: false, onboarding_step: 0, profile_call_name: 'Jordan' };
    renderGuide();
    expect(await screen.findByText('Welcome, Jordan')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Your first hire' })).toBeDisabled();
    expect(screen.getByRole('tab', { name: 'Connect an AI model' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    expect(screen.getByText('1 / 3')).toBeInTheDocument();
  });

  it('shows the beat the owner picks', async () => {
    renderGuide();
    fireEvent.click(await screen.findByRole('button', { name: /Check and hire/ }));
    expect(screen.getByRole('button', { name: /Check and hire/ })).toHaveAttribute('aria-current', 'step');
    expect(screen.getByText('Ask before sending')).toBeInTheDocument();
  });

  it('moves on with Next and saves each step', async () => {
    renderGuide();
    fireEvent.click(await screen.findByRole('button', { name: /Next/ }));
    expect(useHomeStore.getState().guide.step).toBe('connect');
    await waitFor(() => expect(saves()).toContainEqual({ onboarding_step: 1 }));
    expect(screen.getByRole('button', { name: 'Back' })).toBeInTheDocument();
  });

  it('lists every AI model and no apps on Connect, and lets the owner do it later', async () => {
    settingsRow = { onboarding_completed: false, onboarding_step: 1 };
    providers = [...Array.from({ length: 10 }, (_, i) => provider(`ai${i}`, 'ai')), provider('whatsapp', 'messages')];
    renderGuide();
    expect(await screen.findByRole('heading', { name: 'Connect an AI model' })).toBeInTheDocument();
    expect(document.querySelectorAll('[data-catalog-item]')).toHaveLength(10);
    expect(document.querySelector('[data-catalog-item="whatsapp"]')).toBeNull();
    expect(screen.queryByRole('button', { name: /Next/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'I’ll do this later' }));
    expect(useHomeStore.getState().guide.step).toBe('first-hire');
  });

  it('opens a provider’s page in place of the list, and goes back with Back or Esc', async () => {
    settingsRow = { onboarding_completed: false, onboarding_step: 1 };
    renderGuide();
    fireEvent.click(await screen.findByRole('button', { name: 'Connect openai' }));
    expect(screen.getByRole('heading', { name: 'Connect openai' })).toBeInTheDocument();
    expect(screen.getByText('The provider’s panel')).toBeInTheDocument();
    // The page carries its own way back; the footer's moves step aside.
    expect(screen.queryByRole('button', { name: 'I’ll do this later' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'All AI models' }));
    expect(screen.queryByRole('heading', { name: 'Connect openai' })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Connect openai' })).toHaveFocus());

    fireEvent.click(screen.getByRole('button', { name: 'Connect anthropic' }));
    fireEvent.keyDown(screen.getByRole('button', { name: 'All AI models' }), { key: 'Escape' });
    expect(screen.queryByRole('heading', { name: 'Connect anthropic' })).not.toBeInTheDocument();
    expect(useHomeStore.getState().guide.open).toBe(true);
  });

  it('says so when the model connects, and returns to the list', async () => {
    settingsRow = { onboarding_completed: false, onboarding_step: 1 };
    const { refresh } = renderGuide();
    fireEvent.click(await screen.findByRole('button', { name: 'Connect openai' }));
    providers = [provider('openai', 'ai', true), provider('anthropic', 'ai')];
    refresh();
    expect(pillToast).toHaveBeenCalledWith('openai is connected');
    expect(screen.queryByRole('heading', { name: 'Connect openai' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'You’re connected' })).toBeInTheDocument();
    expect(useHomeStore.getState().guide.open).toBe(true);
  });

  it('says so once an AI model is connected, and offers Next', async () => {
    settingsRow = { onboarding_completed: false, onboarding_step: 1 };
    providers = [provider('openai', 'ai', true)];
    renderGuide();
    expect(await screen.findByRole('heading', { name: 'You’re connected' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Next/ })).toBeInTheDocument();
  });

  it('saves the step reached when the owner closes it', async () => {
    settingsRow = { onboarding_completed: false, onboarding_step: 1 };
    renderGuide();
    fireEvent.click(await screen.findByRole('button', { name: 'Close guide' }));
    await waitFor(() => expect(saves()).toContainEqual({ onboarding_completed: true, onboarding_step: 1 }));
    expect(useHomeStore.getState().guide.open).toBe(false);
  });

  it('saves the step reached when the owner skips it', async () => {
    renderGuide();
    fireEvent.click(await screen.findByRole('button', { name: 'Skip for now' }));
    await waitFor(() => expect(saves()).toContainEqual({ onboarding_completed: true, onboarding_step: 0 }));
    expect(useHomeStore.getState().guide.open).toBe(false);
  });

  it('closes with Esc at the step reached, after first leaving a provider’s page', async () => {
    settingsRow = { onboarding_completed: false, onboarding_step: 1 };
    renderGuide();
    fireEvent.click(await screen.findByRole('button', { name: 'Connect openai' }));
    fireEvent.keyDown(screen.getByRole('button', { name: 'All AI models' }), { key: 'Escape' });
    expect(screen.queryByRole('heading', { name: 'Connect openai' })).not.toBeInTheDocument();
    expect(useHomeStore.getState().guide.open).toBe(true);

    fireEvent.keyDown(screen.getByRole('button', { name: 'Connect openai' }), { key: 'Escape' });
    expect(useHomeStore.getState().guide.open).toBe(false);
    await waitFor(() => expect(saves()).toContainEqual({ onboarding_completed: true, onboarding_step: 1 }));
  });
});

describe('WelcomeGuide first hire', () => {
  it('leaves the composer’s Create as the only way on', async () => {
    settingsRow = { onboarding_completed: false, onboarding_step: 2 };
    providers = [provider('openai', 'ai', true)];
    renderGuide();
    await screen.findByRole('heading', { name: 'Who should we hire first?' });
    expect(screen.getByRole('button', { name: /Create their setup/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Next/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'I’ll do this later' })).not.toBeInTheDocument();
  });

  it('finishes the guide, shows the hire view and sends the job when there is a model', async () => {
    settingsRow = { onboarding_completed: false, onboarding_step: 2 };
    providers = [provider('openai', 'ai', true)];
    job.value = 'Answer my WhatsApp';
    renderGuide();
    fireEvent.click(await screen.findByRole('button', { name: /Create their setup/ }));
    expect(job.submit).toHaveBeenCalledTimes(1);
    expect(useHomeStore.getState().view).toEqual({ kind: 'hire' });
    expect(useHomeStore.getState().guide.open).toBe(false);
    await waitFor(() => expect(saves()).toContainEqual({ onboarding_completed: true, onboarding_step: 3 }));
  });

  it('sends nothing without an AI model, then finishes and sends once one is connected', async () => {
    settingsRow = { onboarding_completed: false, onboarding_step: 2 };
    job.value = 'Answer my WhatsApp';
    const { refresh } = renderGuide();
    expect(await screen.findByText('Connect an AI model first.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Create their setup/ }));
    expect(job.submit).not.toHaveBeenCalled();
    expect(useHomeStore.getState().guide).toMatchObject({ step: 'connect', pendingDraft: true });

    fireEvent.click(await screen.findByRole('button', { name: 'Connect openai' }));
    providers = [provider('openai', 'ai', true), provider('anthropic', 'ai')];
    refresh();
    expect(job.submit).toHaveBeenCalledTimes(1);
    expect(useHomeStore.getState().view).toEqual({ kind: 'hire' });
    expect(useHomeStore.getState().guide.open).toBe(false);
    await waitFor(() => expect(saves()).toContainEqual({ onboarding_completed: true, onboarding_step: 3 }));
  });

  it('carries no job to Connect when the box is empty', async () => {
    settingsRow = { onboarding_completed: false, onboarding_step: 2 };
    renderGuide();
    fireEvent.click(await screen.findByRole('button', { name: 'Connect an AI model' }));
    expect(useHomeStore.getState().guide).toMatchObject({ step: 'connect', pendingDraft: false });
  });
});
