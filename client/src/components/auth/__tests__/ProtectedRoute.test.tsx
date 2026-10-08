/**
 * The sign-in gate's states (ProtectedRoute): a loading screen while the
 * session is checked; Connecting while the server can't be reached, with
 * its countdown, attempts and help; "Connected" held a moment once it
 * answers, then sign-in or the app; the sign-in screen kept up while a
 * sign-in finishes, for its welcome; and, signed in, Connecting over the
 * app while the WebSocket is down, the app staying mounted, an expired
 * session going to sign-in. The theme shows as its base while a
 * Connecting or sign-in screen is up.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { useEffect } from 'react';

interface Auth {
  isAuthenticated: boolean;
  isLoading: boolean;
  signingIn: boolean;
  error: string | null;
  checkAuth: () => Promise<boolean>;
}

let auth: Auth;
let ws: { reconnecting: boolean };

vi.mock('../../../contexts/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../../../contexts/WebSocketContext', () => ({ useWebSocketActions: () => ws }));
vi.mock('../LoginPage', () => ({ default: () => <p>Sign in page</p> }));
vi.mock('@/features/home/header/ThemeButton', () => ({ ThemeButton: () => null }));

import { useShellDialogsStore } from '@/stores/shellDialogsStore';
import ProtectedRoute from '../ProtectedRoute';

let mounts = 0;
function TheApp() {
  useEffect(() => {
    mounts += 1;
  }, []);
  return <p>The app</p>;
}

const view = () => (
  <ProtectedRoute>
    <TheApp />
  </ProtectedRoute>
);

async function seconds(count: number) {
  await act(async () => {
    vi.advanceTimersByTime(count * 1000);
  });
  await act(async () => {});
}

let getContext: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
  // No WebGL in jsdom: the orb's slots show the mark.
  getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  mounts = 0;
  ws = { reconnecting: false };
  auth = {
    isAuthenticated: false,
    isLoading: false,
    signingIn: false,
    error: null,
    checkAuth: vi.fn(async () => false),
  };
});

afterEach(() => {
  getContext.mockRestore();
  vi.useRealTimers();
});

describe('the sign-in gate', () => {
  it('shows a loading screen while the session is checked', () => {
    auth.isLoading = true;
    render(view());
    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });

  it('counts down to each check while the server cannot be reached, and offers help from the third attempt', async () => {
    auth.error = 'Failed to connect to server';
    render(view());
    expect(screen.getByRole('heading', { name: 'Waking up OpenCompany' })).toBeInTheDocument();
    expect(screen.getByText('Connecting')).toBeInTheDocument();
    expect(screen.getByText('Trying again in 2s')).toBeInTheDocument();
    expect(screen.getByText('Attempt 1')).toBeInTheDocument();
    expect(useShellDialogsStore.getState().connectScreenOpen).toBe(true);

    await seconds(2);
    expect(auth.checkAuth).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Attempt 2')).toBeInTheDocument();
    expect(screen.getByText('Reconnecting')).toBeInTheDocument();
    expect(screen.queryByText(/Make sure OpenCompany is running/)).not.toBeInTheDocument();
    await seconds(3);
    expect(screen.getByText('Still nothing? Make sure OpenCompany is running.')).toBeInTheDocument();
  });

  it('says Connected for a moment once the server answers, then signs in', async () => {
    auth.error = 'Failed to connect to server';
    const { rerender } = render(view());
    auth = { ...auth, error: null, checkAuth: vi.fn(async () => true) };
    rerender(view());
    expect(screen.getByRole('heading', { name: 'Connected' })).toBeInTheDocument();
    expect(screen.queryByText('Sign in page')).not.toBeInTheDocument();
    await seconds(1.5);
    expect(screen.getByText('Sign in page')).toBeInTheDocument();
    // One screen throughout, so the orb glides from Connecting to sign-in.
    expect(useShellDialogsStore.getState().connectScreenOpen).toBe(true);
  });

  it('goes straight into the app after Connected when the session is good', async () => {
    auth.error = 'Failed to connect to server';
    const { rerender } = render(view());
    auth = { ...auth, error: null, isAuthenticated: true };
    rerender(view());
    expect(screen.getByRole('heading', { name: 'Connected' })).toBeInTheDocument();
    await seconds(1.5);
    expect(screen.getByText('The app')).toBeInTheDocument();
    expect(useShellDialogsStore.getState().connectScreenOpen).toBe(false);
  });

  it('keeps the sign-in screen up until the sign-in finishes, for its welcome', () => {
    const { rerender } = render(view());
    auth = { ...auth, isAuthenticated: true, signingIn: true };
    rerender(view());
    expect(screen.getByText('Sign in page')).toBeInTheDocument();
    expect(screen.queryByText('The app')).not.toBeInTheDocument();
    expect(useShellDialogsStore.getState().connectScreenOpen).toBe(true);

    auth = { ...auth, signingIn: false };
    rerender(view());
    expect(screen.getByText('The app')).toBeInTheDocument();
    expect(mounts).toBe(1);
    expect(useShellDialogsStore.getState().connectScreenOpen).toBe(false);
  });

  it('shows the app when the session is good, and sign-in when it is not', () => {
    auth.isAuthenticated = true;
    const { rerender } = render(view());
    expect(screen.getByText('The app')).toBeInTheDocument();
    auth = { ...auth, isAuthenticated: false };
    rerender(view());
    expect(screen.getByText('Sign in page')).toBeInTheDocument();
  });

  it('covers the app while the WebSocket is down, keeping it mounted, and goes once it is back', async () => {
    auth.isAuthenticated = true;
    const { rerender } = render(view());
    ws = { reconnecting: true };
    rerender(view());
    expect(screen.getByRole('dialog', { name: 'Reconnecting to OpenCompany' })).toBeInTheDocument();
    expect(screen.getByText('Reconnecting')).toBeInTheDocument();
    expect(screen.getByText('The app').closest('[inert]')).not.toBeNull();

    ws = { reconnecting: false };
    rerender(view());
    expect(screen.getByRole('heading', { name: 'Connected' })).toBeInTheDocument();
    await seconds(1.5);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByText('The app').closest('[inert]')).toBeNull();
    expect(mounts).toBe(1);
  });

  it('goes to sign-in when the session expired while the server was away', async () => {
    auth.isAuthenticated = true;
    const { rerender } = render(view());
    ws = { reconnecting: true };
    rerender(view());
    await seconds(2);
    expect(auth.checkAuth).toHaveBeenCalledTimes(1);
    auth = { ...auth, isAuthenticated: false };
    ws = { reconnecting: false };
    rerender(view());
    expect(screen.getByText('Sign in page')).toBeInTheDocument();
    expect(screen.queryByText('The app')).not.toBeInTheDocument();
  });
});
