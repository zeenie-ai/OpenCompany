/**
 * Settings > Profile: the behaviour switches are ink (they show a setting,
 * not a status), and Save changes is the page's one primary action, the
 * invert button: disabled while the save is under way, then the same button
 * pulses. A save sends only what was changed on the page.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { ThemeProvider } from '@/contexts/ThemeContext';
import { installWaapiStub } from '@/test/waapi';
import { ProfileTab } from '../settings/ProfileTab';
import { pillToast } from '../ui/pillToast';

const SETTINGS = { settings: { profile_full_name: 'Alex Rivera' } };

async function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <ProfileTab onDone={vi.fn()} />
      </QueryClientProvider>
    </ThemeProvider>,
  );
  // The form fills once the settings row arrives.
  await screen.findByText('Alex Rivera');
}

beforeEach(() => {
  sendRequest.mockReset().mockResolvedValue(SETTINGS);
  vi.mocked(pillToast).mockClear();
});

describe('ProfileTab', () => {
  it('shows the behaviour switches in ink', async () => {
    await renderTab();
    for (const name of ['Memory across chats', 'Keep everything on this computer', 'Generated UI']) {
      const toggle = screen.getByRole('switch', { name });
      expect(toggle).toHaveClass('data-checked:bg-fg-default');
      expect(toggle.className).not.toMatch(/action-run/);
      expect(toggle.querySelector('[data-slot="switch-thumb"]')).toHaveClass('data-checked:bg-bg-app');
    }
  });

  it('sends only what was changed on the page', async () => {
    await renderTab();
    const toggle = screen.getByRole('switch', { name: 'Generated UI' });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('save_user_settings', expect.anything()));
    const [, payload] = sendRequest.mock.calls.find(([type]) => type === 'save_user_settings')!;
    // The browser's timezone always rides along; nothing else was touched.
    const { profile_timezone: _zone, ...sent } = payload.settings;
    expect(sent).toEqual({ generated_ui: true });
  });

  it('saves with the invert button, disabled while saving, then pulses it', async () => {
    const waapi = installWaapiStub();
    try {
      let finish!: (reply: unknown) => void;
      sendRequest.mockImplementation((type: string) =>
        type === 'save_user_settings' ? new Promise((resolve) => { finish = resolve; }) : Promise.resolve(SETTINGS),
      );
      await renderTab();
      const save = screen.getByRole('button', { name: 'Save changes' });
      expect(save).toHaveAttribute('data-variant', 'invert');
      expect(save).toHaveAttribute('type', 'submit');
      expect(save).toHaveClass('h-8.5', 'px-4', 'font-semibold');
      expect(save.className).not.toMatch(/action-/);

      fireEvent.click(save);
      await waitFor(() => expect(save).toBeDisabled());
      expect(save).toHaveTextContent('Saving…');
      await act(async () => { finish({ success: true }); });
      await waitFor(() => expect(pillToast).toHaveBeenCalledWith('Profile saved'));
      expect(save).toHaveTextContent('Saved');
      expect(waapi.callsFor(save).some((call) => JSON.stringify(call.keyframes).includes('scale(1.06)'))).toBe(true);
    } finally {
      waapi.restore();
    }
  });
});
