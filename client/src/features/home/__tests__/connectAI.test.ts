/** Home's AI prompts open the AI models dialog on its list, without losing
 *  the employee, hire draft or Settings page underneath, and never the app
 *  shell's credentials dialog. */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../orb/orb', () => ({ SPIKE: { connect: 1, settings: 1 }, spikeOrb: vi.fn() }));

import { useShellDialogsStore } from '@/stores/shellDialogsStore';
import { SPIKE, spikeOrb } from '../orb/orb';
import { useHomeStore } from '../state/homeStore';

beforeEach(() => {
  vi.mocked(spikeOrb).mockClear();
  useShellDialogsStore.setState({
    credentialsOpen: false,
    credentialsOptions: { intent: 'manage' },
    credentialsRequestId: 0,
  });
  useHomeStore.setState({
    view: { kind: 'hire' },
    settingsOpen: false,
    settingsTab: 'profile',
    settingsCategory: 'all',
    connectAI: { open: false, provider: null, closeOnConnect: true },
  });
});

describe('Home AI connection navigation', () => {
  it('opens the AI models dialog on its list, to close once a model connects', () => {
    useHomeStore.getState().openConnectAI();

    expect(useHomeStore.getState().connectAI).toEqual({ open: true, provider: null, closeOnConnect: true });
    expect(spikeOrb).toHaveBeenCalledWith(SPIKE.connect);
    expect(useHomeStore.getState().view).toEqual({ kind: 'hire' });
    expect(useShellDialogsStore.getState().credentialsOpen).toBe(false);
  });

  it('keeps the employee and the Settings location underneath', () => {
    useHomeStore.setState({
      view: { kind: 'employee', workflowId: '4' },
      settingsOpen: true,
      settingsTab: 'connectors',
      settingsCategory: 'messages',
    });

    useHomeStore.getState().openConnectAI();

    expect(useHomeStore.getState()).toMatchObject({
      view: { kind: 'employee', workflowId: '4' },
      settingsOpen: true,
      settingsTab: 'connectors',
      settingsCategory: 'messages',
      connectAI: { open: true },
    });
  });

  it('stays on the list after a connect when asked to', () => {
    useHomeStore.getState().openConnectAI({ closeOnConnect: false });

    expect(useHomeStore.getState().connectAI).toEqual({ open: true, provider: null, closeOnConnect: false });
  });

  it('keeps its page while it closes, and opens on the list again', () => {
    useHomeStore.getState().openConnectAI();
    useHomeStore.getState().setConnectAIProvider({ id: 'openai', intent: 'connect' });
    useHomeStore.getState().closeConnectAI();
    expect(useHomeStore.getState().connectAI).toMatchObject({ open: false, provider: { id: 'openai', intent: 'connect' } });

    useHomeStore.getState().openConnectAI();
    expect(useHomeStore.getState().connectAI).toMatchObject({ open: true, provider: null });
  });
});
