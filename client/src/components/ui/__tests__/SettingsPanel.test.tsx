/**
 * Dev's Settings panel. Every change saves itself and sends only what it
 * changed. Save sends only what the loaded settings don't hold yet, so a
 * window whose copy is stale never writes back what another window saved
 * (Home's Profile shows Generated UI too). Reset sends every setting at its
 * default.
 */

import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocket: () => ({ sendRequest, isReady: true }),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from 'sonner';
import SettingsPanel from '../SettingsPanel';
import { defaultSettings, toServerRow, type WorkflowSettings } from '../settingsPanel/schema';

/** The row the server holds when the panel opens. */
const ROW = toServerRow({ ...defaultSettings, autoSave: false });

function Harness() {
  // AppShell keeps the panel's working copy; it starts from the defaults.
  const [settings, setSettings] = useState<WorkflowSettings>(defaultSettings);
  return <SettingsPanel isOpen onClose={() => {}} settings={settings} onSettingsChange={setSettings} />;
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
}

/** The switch in the row labelled `label`. */
function switchFor(label: string): HTMLElement {
  const row = screen.getByText(label).parentElement?.parentElement;
  if (!row) throw new Error(`no row for ${label}`);
  return within(row).getByRole('switch');
}

function saves(): Record<string, unknown>[] {
  return sendRequest.mock.calls.filter(([type]) => type === 'save_user_settings').map(([, data]) => data.settings);
}

async function loaded() {
  const save = screen.getByRole('button', { name: 'Save' });
  await waitFor(() => expect(save).toBeEnabled());
  return save;
}

beforeEach(() => {
  sendRequest.mockReset().mockImplementation(async (type: string) =>
    type === 'get_user_settings' ? { settings: ROW } : { success: true },
  );
  vi.mocked(toast.success).mockClear();
});

describe('SettingsPanel', () => {
  it('saves only the setting a change touched', async () => {
    renderPanel();
    await loaded();
    fireEvent.click(switchFor('Generated UI'));
    await waitFor(() => expect(saves()).toEqual([{ generated_ui: true }]));
  });

  it('writes nothing back on Save that the loaded settings already hold', async () => {
    renderPanel();
    const save = await loaded();
    // The panel shows the loaded row (autoSave off), not its starting copy.
    expect(switchFor('Enable Auto-save')).not.toBeChecked();
    // Another window may have turned Generated UI on since this row loaded:
    // Save must not send this window's copy back.
    fireEvent.click(save);
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Settings saved successfully'));
    expect(saves()).toEqual([]);
  });

  it('keeps Save off until the settings have loaded', () => {
    sendRequest.mockImplementation(() => new Promise(() => {}));
    renderPanel();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('resets every setting to its default', async () => {
    renderPanel();
    await loaded();
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    await waitFor(() => expect(saves()).toEqual([toServerRow(defaultSettings)]));
  });
});
