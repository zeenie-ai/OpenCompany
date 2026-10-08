/**
 * Tests for the TanStack-Query-backed AuthContext.
 *
 * Locks in the contracts that drive perceived launch time + the
 * disconnect-reconnect bug-fix:
 *
 *   1. Anonymous-mode happy path: backend reports `auth_enabled: false`
 *      → user is auto-set to the anonymous owner without further work.
 *   2. No hidden retries: a 503 is one failed check, reported at once;
 *      `checkAuth` (the sign-in gate's visible retry) recovers once the
 *      server answers, and says whether it did.
 *   3. A 401 or a non-JSON 200 is one failed check too.
 *   4. Logout invalidates the cache: after logout the cached data shows
 *      `authenticated: false` so the WebSocketContext logout effect
 *      fires deterministically.
 *
 * The gate's schedule (CONNECT_RETRY) is covered by
 * components/auth/__tests__.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider, useAuth, AUTH_STATUS_QUERY_KEY } from '../AuthContext';

// Mock the API config so the fetch URL is predictable in test logs.
vi.mock('../../config/api', () => ({
  API_CONFIG: { PYTHON_BASE_URL: 'http://test' },
}));

// Each test gets a fresh QueryClient with retries disabled by default;
// individual tests opt back into retries to exercise the retry path.
function makeQueryClient(opts?: { retry?: number }): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: opts?.retry ?? 0, retryDelay: 0 },
      mutations: { retry: 0 },
    },
  });
}

function Wrapper({
  children,
  client,
}: {
  children: React.ReactNode;
  client: QueryClient;
}) {
  return (
    <QueryClientProvider client={client}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  );
}

// Captures the `useAuth()` value across renders so tests can assert on
// state transitions without remembering to await effects manually.
function makeProbe() {
  const states: ReturnType<typeof useAuth>[] = [];
  function Probe() {
    states.push(useAuth());
    return null;
  }
  return { states, Probe };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('AuthContext (TanStack Query)', () => {
  it('sets the anonymous user when backend reports auth_enabled: false', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          auth_enabled: false,
          auth_mode: 'single',
          authenticated: false,
          user: null,
          can_register: false,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    const client = makeQueryClient();
    const { states, Probe } = makeProbe();
    render(
      <Wrapper client={client}>
        <Probe />
      </Wrapper>,
    );

    await waitFor(() => {
      const last = states[states.length - 1];
      expect(last.isLoading).toBe(false);
      expect(last.isAuthenticated).toBe(true);
      expect(last.user?.email).toBe('anonymous');
    });
  });

  it('reports a 503 at once, and checkAuth recovers when the server answers', async () => {
    let up = false;
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      up
        ? new Response(
            JSON.stringify({
              auth_enabled: true,
              auth_mode: 'single',
              authenticated: true,
              user: { id: 1, email: 'a@b', display_name: 'A', is_owner: true },
              can_register: false,
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          )
        : new Response('upstream not ready', { status: 503 }),
    );

    // A client-wide retry budget changes nothing: the query never retries.
    const client = makeQueryClient({ retry: 3 });
    const { states, Probe } = makeProbe();
    render(
      <Wrapper client={client}>
        <Probe />
      </Wrapper>,
    );

    await waitFor(() => {
      const last = states[states.length - 1];
      expect(last.isLoading).toBe(false);
      expect(last.error).not.toBeNull();
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    let answered: boolean | undefined;
    await act(async () => {
      answered = await states[states.length - 1].checkAuth();
    });
    expect(answered).toBe(false);

    up = true;
    await act(async () => {
      answered = await states[states.length - 1].checkAuth();
    });
    expect(answered).toBe(true);
    await waitFor(() => {
      const last = states[states.length - 1];
      expect(last.isAuthenticated).toBe(true);
      expect(last.user?.email).toBe('a@b');
      expect(last.error).toBeNull();
    });
  });

  it('reports a 401 as one failed check', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'unauthorized' }), { status: 401 }),
    );

    const client = makeQueryClient({ retry: 5 });
    const { states, Probe } = makeProbe();
    render(
      <Wrapper client={client}>
        <Probe />
      </Wrapper>,
    );

    await waitFor(() => {
      const last = states[states.length - 1];
      expect(last.isLoading).toBe(false);
      expect(last.isAuthenticated).toBe(false);
      expect(last.error).not.toBeNull();
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('logout invalidates the auth-status cache and flips authenticated → false', async () => {
    // Initial auth-enabled, logged-in user.
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : (input as Request).url;
      if (url.endsWith('/logout')) {
        return new Response('', { status: 200 });
      }
      // /status — return a logged-in user the first call, an
      // unauthenticated response on every subsequent call (after logout
      // invalidates the cache and refetches).
      return new Response(
        JSON.stringify({
          auth_enabled: true,
          auth_mode: 'single',
          authenticated: false,
          user: null,
          can_register: false,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    });

    const client = makeQueryClient();
    // Seed the cache with an authenticated user so we can observe the
    // transition triggered by `logout()`. This mirrors the real flow:
    // on first mount the query resolves authenticated, then later
    // logout flips it.
    client.setQueryData([...AUTH_STATUS_QUERY_KEY], {
      auth_enabled: true,
      auth_mode: 'single',
      authenticated: true,
      user: { id: 1, email: 'a@b', display_name: 'A', is_owner: true },
      can_register: false,
    });

    const { states, Probe } = makeProbe();
    render(
      <Wrapper client={client}>
        <Probe />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(states[states.length - 1].isAuthenticated).toBe(true);
    });

    await act(async () => {
      await states[states.length - 1].logout();
    });

    await waitFor(() => {
      const last = states[states.length - 1];
      expect(last.isAuthenticated).toBe(false);
      expect(last.user).toBeNull();
    });
  });
});

// ---------------------------------------------------------------------------
// login / register
//
// These had NO coverage, which is precisely where the bugs were: server
// rejections were parsed and then thrown away, a failed login poisoned the
// status cache with `null` (hiding the Register link), and `isLoading` was
// wired to the bootstrap query so the form never disabled.
// ---------------------------------------------------------------------------

const STATUS_BODY = {
  auth_enabled: true,
  auth_mode: 'single' as const,
  authenticated: false,
  user: null,
  can_register: true,
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

/** Mock /status plus one scripted response for /login or /register. */
function mockAuthEndpoints(handler: (url: string) => Response | undefined) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : (input as Request).url;
    const scripted = handler(url);
    if (scripted) return scripted;
    return json(STATUS_BODY);
  });
}

async function mountProbe(client: QueryClient) {
  const { states, Probe } = makeProbe();
  render(
    <Wrapper client={client}>
      <Probe />
    </Wrapper>,
  );
  await waitFor(() => expect(states[states.length - 1].isLoading).toBe(false));
  return {
    states,
    latest: () => states[states.length - 1],
  };
}

describe('AuthContext login/register', () => {
  it('surfaces the server detail string on a failed login', async () => {
    mockAuthEndpoints((url) =>
      url.endsWith('/login')
        ? json({ detail: 'Invalid email or password' }, 401)
        : undefined,
    );

    const { latest } = await mountProbe(makeQueryClient());

    let returned: boolean | undefined;
    await act(async () => {
      returned = await latest().login('a@b.com', 'wrong');
    });

    expect(returned).toBe(false);
    await waitFor(() => {
      expect(latest().submitError).toBe('Invalid email or password');
    });
  });

  it('keeps canRegister true after a failed login', async () => {
    // The regression: login failure wrote `null` into the status cache, so
    // `can_register` fell back to false and the Register link vanished until
    // a full page reload.
    mockAuthEndpoints((url) =>
      url.endsWith('/login') ? json({ detail: 'Invalid email or password' }, 401) : undefined,
    );

    const { latest } = await mountProbe(makeQueryClient());
    expect(latest().canRegister).toBe(true);

    await act(async () => {
      await latest().login('a@b.com', 'wrong');
    });

    await waitFor(() => expect(latest().submitError).not.toBeNull());
    expect(latest().canRegister).toBe(true);
  });

  it('renders a 422 validation array as a readable string', async () => {
    mockAuthEndpoints((url) =>
      url.endsWith('/register')
        ? json(
            {
              detail: [
                { msg: 'value is not a valid email address', loc: ['body', 'email'] },
                { msg: 'String should have at least 1 character', loc: ['body', 'display_name'] },
              ],
            },
            422,
          )
        : undefined,
    );

    const { latest } = await mountProbe(makeQueryClient());

    await act(async () => {
      await latest().register('nope', 'hunter2hunter2', '');
    });

    await waitFor(() => {
      const message = latest().submitError;
      expect(message).toContain('valid email address');
      // Never the raw "[object Object]" the old path would have produced.
      expect(message).not.toContain('object');
    });
  });

  it('surfaces a rate-limit 429 detail verbatim', async () => {
    mockAuthEndpoints((url) =>
      url.endsWith('/login')
        ? json({ detail: 'Too many attempts. Please wait and try again.' }, 429)
        : undefined,
    );

    const { latest } = await mountProbe(makeQueryClient());
    await act(async () => {
      await latest().login('a@b.com', 'whatever');
    });

    await waitFor(() => {
      expect(latest().submitError).toContain('Too many attempts');
    });
  });

  it('falls back to a generic message when the error body is not JSON', async () => {
    mockAuthEndpoints((url) =>
      url.endsWith('/login')
        ? new Response('<html>502 Bad Gateway</html>', {
            status: 502,
            headers: { 'Content-Type': 'text/html' },
          })
        : undefined,
    );

    const { latest } = await mountProbe(makeQueryClient());
    await act(async () => {
      await latest().login('a@b.com', 'pw');
    });

    await waitFor(() => {
      expect(latest().submitError).toContain('502');
    });
  });

  it('sets the user and clears submitError on a successful login', async () => {
    // Stateful on purpose: `onSuccess` writes the user optimistically and then
    // invalidates, so /status refetches and is authoritative. A mock that kept
    // reporting `authenticated: false` would (correctly) undo the login.
    const user = { id: 7, email: 'a@b.com', display_name: 'A', is_owner: true };
    let loggedIn = false;

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : (input as Request).url;
      if (url.endsWith('/login')) {
        loggedIn = true;
        return json({ success: true, user });
      }
      return json({
        ...STATUS_BODY,
        authenticated: loggedIn,
        user: loggedIn ? user : null,
      });
    });

    const { latest } = await mountProbe(makeQueryClient());

    let returned: boolean | undefined;
    await act(async () => {
      returned = await latest().login('a@b.com', 'correct-horse');
    });

    expect(returned).toBe(true);
    await waitFor(() => {
      expect(latest().user?.email).toBe('a@b.com');
      expect(latest().submitError).toBeNull();
    });
  });

  it('resetAuthErrors clears a stale submit error', async () => {
    mockAuthEndpoints((url) =>
      url.endsWith('/login') ? json({ detail: 'Invalid email or password' }, 401) : undefined,
    );

    const { latest } = await mountProbe(makeQueryClient());
    await act(async () => {
      await latest().login('a@b.com', 'wrong');
    });
    await waitFor(() => expect(latest().submitError).not.toBeNull());

    await act(async () => {
      latest().resetAuthErrors();
    });
    await waitFor(() => expect(latest().submitError).toBeNull());
  });

  it('reports a non-JSON 200 from /status as one failed check', async () => {
    // A 200 carrying HTML means the SPA fallback swallowed the API route.
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('<!doctype html><html></html>', {
        status: 200,
        headers: { 'Content-Type': 'text/html' },
      }),
    );

    const client = makeQueryClient({ retry: 5 });
    const { states, Probe } = makeProbe();
    render(
      <Wrapper client={client}>
        <Probe />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(states[states.length - 1].isLoading).toBe(false);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('reports a logout that the server did not confirm', async () => {
    mockAuthEndpoints((url) =>
      url.endsWith('/logout') ? new Response('', { status: 500 }) : undefined,
    );

    const { latest } = await mountProbe(makeQueryClient());
    await act(async () => {
      await latest().logout();
    });

    await waitFor(() => {
      // Local state still flips (the WS teardown depends on it) ...
      expect(latest().isAuthenticated).toBe(false);
      // ... but the failure is no longer silent.
      expect(latest().logoutError).not.toBeNull();
    });
  });

  it('keeps the context value referentially stable across re-renders', async () => {
    // `checkAuth` used to depend on `authQuery`, whose identity changes on
    // essentially every render, breaking the context useMemo and re-rendering
    // every useAuth() consumer in the app.
    mockAuthEndpoints(() => undefined);

    const client = makeQueryClient();
    const { states } = await mountProbe(client);

    const before = states[states.length - 1];
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    const after = states[states.length - 1];

    expect(after).toBe(before);
  });
});
