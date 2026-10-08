/**
 * Tests for LoginPage: the sign-in card (onboarding handoff R5) and the
 * welcome after it.
 *
 * The page's first tests caught three real defects, kept below: server
 * rejections were never displayed, the Register link disappeared after one
 * failed login, and the submit button never disabled (so the form accepted
 * unlimited concurrent requests). The restyle adds the words for each
 * mistake, the password toggle, the shake, the orb following the form, and
 * the welcome, which ends the sign-in once it has risen in and the screen
 * the app opens on has loaded.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { User } from '../../../contexts/AuthContext';
import { preloadHome } from '../../../app/ShellModeSwitch';
import { ENERGY, SPIKE, orbState, resetOrbForTests } from '@/features/home/orb/orb';
import { installWaapiStub } from '../../../test/waapi';

const authState = {
  login: vi.fn(),
  register: vi.fn(),
  canRegister: true,
  submitError: null as string | null,
  isSubmitting: false,
  resetAuthErrors: vi.fn(),
  finishSignIn: vi.fn(),
  isAuthenticated: false,
  user: null as User | null,
};

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => authState,
}));
vi.mock('../../../app/ShellModeSwitch', () => ({
  useShellMode: () => 'normal',
  preloadHome: vi.fn(),
  preloadEditor: vi.fn(),
}));

import LoginPage from '../LoginPage';

const JORDAN: User = { id: 1, email: 'jordan@example.com', display_name: 'Jordan Lee', is_owner: true };

beforeEach(() => {
  vi.clearAllMocks();
  authState.login = vi.fn().mockResolvedValue(true);
  authState.register = vi.fn().mockResolvedValue(true);
  authState.canRegister = true;
  authState.submitError = null;
  authState.isSubmitting = false;
  authState.resetAuthErrors = vi.fn();
  authState.finishSignIn = vi.fn();
  authState.isAuthenticated = false;
  authState.user = null;
  vi.mocked(preloadHome).mockResolvedValue(undefined);
  resetOrbForTests();
});

const fillCredentials = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.type(screen.getByLabelText(/^email$/i), 'a@b.com');
  await user.type(screen.getByLabelText(/^password$/i), 'hunter2hunter2');
};

function signIn() {
  authState.isAuthenticated = true;
  authState.user = JORDAN;
}

describe('LoginPage', () => {
  it('says Welcome back over the sign-in form', () => {
    render(<LoginPage />);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Welcome back');
    expect(screen.getByLabelText(/^email$/i)).toHaveAttribute('placeholder', 'you@example.com');
    expect(screen.getByLabelText(/^password$/i)).toHaveAttribute('placeholder', 'Your password');
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument();
  });

  it('submits credentials to login', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    await fillCredentials(user);
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => {
      expect(authState.login).toHaveBeenCalledWith('a@b.com', 'hunter2hunter2');
    });
  });

  it('displays the server rejection in an alert', () => {
    authState.submitError = 'Invalid email or password';
    render(<LoginPage />);
    expect(screen.getByRole('alert')).toHaveTextContent('Invalid email or password');
  });

  it('keeps the Register link visible when a login has failed', () => {
    authState.submitError = 'Invalid email or password';
    authState.canRegister = true;
    render(<LoginPage />);
    expect(screen.getByRole('button', { name: /^register$/i })).toBeInTheDocument();
  });

  it('hides the Register link when registration is closed', () => {
    authState.canRegister = false;
    render(<LoginPage />);
    expect(screen.queryByRole('button', { name: /^register$/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/have an account/i)).not.toBeInTheDocument();
  });

  it('asks for an email', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.type(screen.getByLabelText(/^password$/i), 'hunter2hunter2');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByText('Enter your email.')).toBeInTheDocument();
    expect(authState.login).not.toHaveBeenCalled();
  });

  it('rejects a malformed email without calling login', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.type(screen.getByLabelText(/^email$/i), 'not-an-email');
    await user.type(screen.getByLabelText(/^password$/i), 'hunter2hunter2');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByText(/look like an email address/i)).toBeInTheDocument();
    expect(authState.login).not.toHaveBeenCalled();
  });

  it('asks for a password', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.type(screen.getByLabelText(/^email$/i), 'a@b.com');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByText('Enter your password.')).toBeInTheDocument();
    expect(authState.login).not.toHaveBeenCalled();
  });

  it('disables the fields and the button while signing in', () => {
    authState.isSubmitting = true;
    render(<LoginPage />);

    expect(screen.getByLabelText(/^email$/i)).toBeDisabled();
    expect(screen.getByLabelText(/^password$/i)).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Signing in…' })).toBeDisabled();
  });

  it('does not fire a second request while one is in flight', async () => {
    const user = userEvent.setup();
    // The component is driven by `isSubmitting`, so assert the disabled
    // attribute blocks the second click.
    const { rerender } = render(<LoginPage />);
    await fillCredentials(user);

    authState.isSubmitting = true;
    rerender(<LoginPage />);

    await user.click(screen.getByRole('button', { name: 'Signing in…' })).catch(() => undefined);
    expect(authState.login).not.toHaveBeenCalled();
  });

  it('switches to Create your account and asks for a name', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.click(screen.getByRole('button', { name: /^register$/i }));
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Create your account');
    expect(screen.getByLabelText(/^password$/i)).toHaveAttribute('placeholder', 'At least 8 characters');

    await fillCredentials(user);
    await user.click(screen.getByRole('button', { name: /create account/i }));

    expect(await screen.findByText('Enter your name.')).toBeInTheDocument();
    expect(authState.register).not.toHaveBeenCalled();
  });

  it('enforces the 8-character minimum only when registering', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.click(screen.getByRole('button', { name: /^register$/i }));
    await user.type(screen.getByLabelText(/your name/i), 'A');
    await user.type(screen.getByLabelText(/^email$/i), 'a@b.com');
    await user.type(screen.getByLabelText(/^password$/i), 'short');
    await user.click(screen.getByRole('button', { name: /create account/i }));

    expect(await screen.findByText('Use at least 8 characters.')).toBeInTheDocument();
    expect(authState.register).not.toHaveBeenCalled();
  });

  it('registers with a name', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.click(screen.getByRole('button', { name: /^register$/i }));
    await user.type(screen.getByLabelText(/your name/i), 'Alice');
    await fillCredentials(user);
    await user.click(screen.getByRole('button', { name: /create account/i }));

    await waitFor(() => {
      expect(authState.register).toHaveBeenCalledWith('a@b.com', 'hunter2hunter2', 'Alice');
    });
  });

  it('clears stale errors when toggling mode', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.click(screen.getByRole('button', { name: /^register$/i }));
    expect(authState.resetAuthErrors).toHaveBeenCalled();
  });

  it('shows and hides the password', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    const password = screen.getByLabelText(/^password$/i);
    expect(password).toHaveAttribute('type', 'password');

    await user.click(screen.getByRole('button', { name: 'Show password' }));
    expect(password).toHaveAttribute('type', 'text');
    expect(screen.getByRole('button', { name: 'Hide password' })).toHaveAttribute('aria-pressed', 'true');

    await user.click(screen.getByRole('button', { name: 'Hide password' }));
    expect(password).toHaveAttribute('type', 'password');
    expect(authState.login).not.toHaveBeenCalled();
  });

  it('livens the orb while a field has focus, more with text, most while signing in', async () => {
    const user = userEvent.setup();
    const { rerender, unmount } = render(<LoginPage />);
    expect(orbState.target).toBe(ENERGY.idle);

    await user.click(screen.getByLabelText(/^email$/i));
    expect(orbState.target).toBe(ENERGY.focus);
    await user.keyboard('a');
    expect(orbState.target).toBe(ENERGY.typing);
    await user.click(screen.getByRole('heading', { level: 1 }));
    expect(orbState.target).toBe(ENERGY.idle);

    authState.isSubmitting = true;
    rerender(<LoginPage />);
    expect(orbState.target).toBe(ENERGY.generating);

    unmount();
    expect(orbState.target).toBe(ENERGY.idle);
  });
});

describe('LoginPage motion', () => {
  let waapi: ReturnType<typeof installWaapiStub>;
  beforeEach(() => {
    waapi = installWaapiStub();
  });
  afterEach(() => {
    waapi.restore();
    vi.useRealTimers();
  });

  const card = () => screen.getByRole('heading', { level: 1 }).parentElement as HTMLElement;
  const shakes = () =>
    waapi.callsFor(card()).filter((call) => JSON.stringify(call.keyframes).includes('translateX(-6px)'));

  it('rises in, and shakes when a field is wrong', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    expect(waapi.callsFor(card())).toHaveLength(1);
    expect(shakes()).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: /sign in/i }));
    await waitFor(() => expect(shakes()).toHaveLength(1));
  });

  it('shakes when the server refuses', async () => {
    authState.login = vi.fn().mockResolvedValue(false);
    const user = userEvent.setup();
    render(<LoginPage />);
    await fillCredentials(user);
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => expect(shakes()).toHaveLength(1));
    expect(authState.login).toHaveBeenCalledTimes(1);
  });

  it('ends the sign-in only once the welcome has risen in', async () => {
    // `finished` also gives up on an animation after its own timing; with
    // the clock stopped, the rise ending is the only way through.
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    signIn();
    render(<LoginPage />);

    const welcome = screen.getByRole('heading', { name: 'Welcome back, Jordan' }).parentElement as HTMLElement;
    const [riseIn] = waapi.callsFor(welcome);
    await act(async () => {});
    expect(authState.finishSignIn).not.toHaveBeenCalled();

    await act(async () => riseIn.animation.finish());
    await act(async () => {});
    expect(authState.finishSignIn).toHaveBeenCalledTimes(1);
  });
});

describe('after signing in', () => {
  it('says who signed in, and ends the sign-in once Home has loaded', async () => {
    let loaded!: () => void;
    vi.mocked(preloadHome).mockReturnValue(new Promise<void>((resolve) => (loaded = resolve)));
    const { rerender } = render(<LoginPage />);
    signIn();
    rerender(<LoginPage />);

    expect(screen.queryByLabelText(/^email$/i)).not.toBeInTheDocument();
    expect(screen.getByText('Signed in')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Welcome back, Jordan' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Opening your team…');
    expect(orbState.spike).toBe(SPIKE.signedIn);
    expect(preloadHome).toHaveBeenCalled();
    await act(async () => {});
    expect(authState.finishSignIn).not.toHaveBeenCalled();

    await act(async () => loaded());
    await waitFor(() => expect(authState.finishSignIn).toHaveBeenCalledTimes(1));
  });

  it('ends the sign-in when Home fails to load, for its own error to show', async () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(preloadHome).mockRejectedValue(new Error('chunk load failed'));
    signIn();
    render(<LoginPage />);

    await waitFor(() => expect(authState.finishSignIn).toHaveBeenCalledTimes(1));
    expect(quiet).toHaveBeenCalled();
    quiet.mockRestore();
  });

  it('says the account was created after registering', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<LoginPage />);
    await user.click(screen.getByRole('button', { name: /^register$/i }));

    signIn();
    rerender(<LoginPage />);

    expect(screen.getByText('Account created')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Welcome, Jordan' })).toBeInTheDocument();
  });
});
