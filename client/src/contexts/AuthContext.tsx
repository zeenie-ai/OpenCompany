/* eslint-disable react-refresh/only-export-components -- canonical React Context pattern co-locates Provider + hooks/helpers in one file. */
/**
 * Authentication Context for user session management.
 *
 * The auth-status check runs through TanStack Query (`useQuery`), which
 * owns AbortController-based unmount cleanup and Strict-Mode safety. It
 * never retries in secret: when the server can't be reached the sign-in
 * gate (components/auth/ProtectedRoute) shows the Connecting screen and
 * checks again through `checkAuth` on a schedule the owner can see
 * (`CONNECT_RETRY`). `networkMode: 'always'` keeps the browser's idea of
 * being offline from pausing a check of a server on this computer.
 *
 * Login / register / logout are `useMutation`s that settle by invalidating
 * the `['auth', 'status']` query rather than calling a private setter — the
 * single source of truth stays the query cache, which TanStack Query
 * dedupes by reference equality, eliminating the spurious
 * `isAuthenticated` flips that closed the WS prematurely under React
 * Strict Mode.
 *
 * Two distinctions the surface makes deliberately, because collapsing
 * either one produced a user-visible bug:
 *
 *   `isLoading` vs `isSubmitting` — the former is the bootstrap query and
 *   gates the entire app in `ProtectedRoute`; the latter is per-request.
 *   Using `isLoading` to disable the login form disabled nothing, since
 *   the bootstrap query has long since settled by the time that form
 *   renders, so the form accepted unlimited concurrent submits.
 *
 *   `error` vs `submitError` — the former means "cannot reach the server",
 *   the latter is the server's own rejection text. Login failures used to
 *   be written into the status cache as `null`, which is a *success* value:
 *   `isError` stayed false so nothing was ever displayed, and
 *   `can_register` fell back to false, hiding the Register link entirely.
 *
 * `signingIn` is the sign-in requests' own state: in flight, or succeeded
 * and not yet finished. The gate keeps the sign-in screen up while it is
 * true, for the welcome; `finishSignIn` returns the requests to idle.
 */

import React, { createContext, useContext, useCallback, useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { API_CONFIG } from '../config/api';

export interface User {
  id: number;
  email: string;
  display_name: string;
  is_owner: boolean;
}

export interface AuthStatus {
  auth_enabled: boolean;
  auth_mode: 'single' | 'multi';
  authenticated: boolean;
  user: User | null;
  can_register: boolean;
}

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  /** Bootstrap-query pending state. Gates the whole app in
   *  `ProtectedRoute`; NOT a per-submit signal — use `isSubmitting`. */
  isLoading: boolean;
  /** True while a login/register request is in flight. */
  isSubmitting: boolean;
  /** A login/register request is in flight, or succeeded and has not been
   *  finished (`finishSignIn`): the sign-in screen stays up for the welcome. */
  signingIn: boolean;
  authMode: 'single' | 'multi';
  canRegister: boolean;
  /** Connectivity error from the bootstrap query only. */
  error: string | null;
  /** Server-rejection message from the last login/register attempt. */
  submitError: string | null;
  /** Message shown when logout could not be confirmed server-side. */
  logoutError: string | null;
  login: (email: string, password: string) => Promise<boolean>;
  register: (email: string, password: string, displayName: string) => Promise<boolean>;
  logout: () => Promise<void>;
  /** Check the session again; true when the server answered. */
  checkAuth: () => Promise<boolean>;
  /** Clear stale submit errors (mode toggle, field edit). */
  resetAuthErrors: () => void;
  /** The welcome after signing in is over: the app takes the screen. */
  finishSignIn: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const getApiBase = () => `${API_CONFIG.PYTHON_BASE_URL}/api/auth`;

const ANONYMOUS_USER: User = {
  id: 0,
  email: 'anonymous',
  display_name: 'Anonymous',
  is_owner: true,
};

// `['auth', 'status']` is the canonical key for the bootstrap query.
// Login / register / logout invalidate it via `queryClient.invalidateQueries`.
export const AUTH_STATUS_QUERY_KEY = ['auth', 'status'] as const;

const isJsonResponse = (response: Response): boolean =>
  (response.headers.get('content-type') ?? '').toLowerCase().includes('application/json');

/**
 * Turn a FastAPI error body into one displayable string.
 *
 * Three shapes reach us and only the first was ever handled:
 *   - `{"detail": "Invalid email or password"}`  — HTTPException
 *   - `{"detail": [{"msg": ..., "loc": [...]}]}` — 422 request validation
 *   - anything non-JSON                          — proxy/SPA-fallback HTML
 */
const extractErrorMessage = (body: unknown, fallback: string): string => {
  if (typeof body === 'string' && body.trim()) return body;
  if (body && typeof body === 'object') {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => (item && typeof item === 'object' ? (item as { msg?: unknown }).msg : null))
        .filter((msg): msg is string => typeof msg === 'string' && msg.trim().length > 0);
      if (messages.length) return messages.join('; ');
    }
  }
  return fallback;
};

/** What a sign-in or registration says when the request never reached the server. */
export const AUTH_UNREACHABLE = 'Can’t reach OpenCompany. Make sure it’s running, then try again.';

/** POST to an auth endpoint, throwing an Error whose message is display-ready. */
const postAuth = async (path: string, payload: unknown): Promise<{ user: User }> => {
  let response: Response;
  try {
    response = await fetch(`${getApiBase()}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(payload),
    });
  } catch {
    // fetch rejects only when no response arrived (each browser words it differently).
    throw new Error(AUTH_UNREACHABLE);
  }

  let body: unknown = null;
  if (isJsonResponse(response)) {
    try {
      body = await response.json();
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    throw new Error(extractErrorMessage(body, `Request failed (HTTP ${response.status})`));
  }

  const parsed = body as { success?: boolean; user?: User } | null;
  if (!parsed?.success || !parsed.user) {
    throw new Error(extractErrorMessage(body, 'Unexpected response from server'));
  }
  return { user: parsed.user };
};

const fetchAuthStatus = async ({ signal }: { signal: AbortSignal }): Promise<AuthStatus> => {
  const response = await fetch(`${getApiBase()}/status`, {
    credentials: 'include',
    signal,
  });
  if (!response.ok) {
    throw new Error(`auth.status: HTTP ${response.status}`);
  }
  // A 200 carrying HTML means the SPA fallback swallowed /api/auth/status,
  // usually a proxy misroute; parsing it would only throw a SyntaxError.
  if (!isJsonResponse(response)) {
    throw new Error('auth.status: non-JSON response');
  }
  return response.json() as Promise<AuthStatus>;
};

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const queryClient = useQueryClient();

  // Bootstrap auth-status query. The `signal` plumbed through `queryFn`
  // is automatically aborted when the component unmounts (Strict Mode
  // double-mount lifecycle handled by TanStack Query, see
  // https://tanstack.com/query/v5/docs/react/guides/cancellation).
  const authQuery = useQuery({
    queryKey: AUTH_STATUS_QUERY_KEY,
    queryFn: fetchAuthStatus,
    // The gate checks again where the owner can see it (checkAuth).
    retry: false,
    networkMode: 'always',
    // Boot-once: never refetch on focus / mount / network reconnect.
    // Logout / login explicitly invalidate.
    staleTime: Infinity,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  const data = authQuery.data;
  const user: User | null = useMemo(() => {
    if (!data) return null;
    if (data.auth_enabled === false) return ANONYMOUS_USER;
    return data.authenticated ? data.user : null;
  }, [data]);

  const authMode: 'single' | 'multi' = data?.auth_mode ?? 'single';
  const canRegister = data?.can_register ?? false;
  const isAuthenticated = user !== null;
  const isLoading = authQuery.isPending;
  const error = authQuery.isError ? 'Failed to connect to server' : null;

  const invalidateAuth = useCallback(
    () => queryClient.invalidateQueries({ queryKey: AUTH_STATUS_QUERY_KEY }),
    [queryClient],
  );

  // Optimistically write the authenticated user so the UI updates this
  // render, then invalidate so the refetch supplies the server-derived
  // fields (auth_mode, can_register). `invalidateQueries` still refetches
  // despite `staleTime: Infinity` — it marks stale AND refetches actives.
  const applyAuthenticatedUser = useCallback((nextUser: User) => {
    queryClient.setQueryData<AuthStatus>(AUTH_STATUS_QUERY_KEY, (prev) => ({
      auth_enabled: true,
      auth_mode: prev?.auth_mode ?? 'single',
      authenticated: true,
      user: nextUser,
      can_register: prev?.can_register ?? false,
    }));
    return invalidateAuth();
  }, [queryClient, invalidateAuth]);

  const loginMutation = useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      postAuth('/login', { email, password }),
    onSuccess: ({ user: nextUser }) => applyAuthenticatedUser(nextUser),
  });

  const registerMutation = useMutation({
    mutationFn: ({ email, password, displayName }: {
      email: string; password: string; displayName: string;
    }) => postAuth('/register', { email, password, display_name: displayName }),
    onSuccess: ({ user: nextUser }) => applyAuthenticatedUser(nextUser),
  });

  const logoutMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(`${getApiBase()}/logout`, {
        method: 'POST',
        credentials: 'include',
      });
      // Do NOT swallow this. The cookie is HttpOnly, so if the server did
      // not clear it the client cannot either — the user looks logged out
      // until a reload silently restores the session.
      if (!response.ok) {
        throw new Error(`Logout may not have completed (HTTP ${response.status}). Reload to confirm.`);
      }
    },
    // onSettled, not onSuccess: consumers (notably the WebSocket teardown
    // effect) must see `authenticated: false` on this render regardless of
    // whether the server confirmed.
    onSettled: async () => {
      queryClient.setQueryData<AuthStatus>(AUTH_STATUS_QUERY_KEY, (prev) => ({
        ...(prev ?? { auth_enabled: true, auth_mode: 'single' as const, can_register: false }),
        authenticated: false,
        user: null,
      }));
      await invalidateAuth();
    },
  });

  const login = useCallback(async (email: string, password: string): Promise<boolean> => {
    try {
      await loginMutation.mutateAsync({ email, password });
      return true;
    } catch {
      // The message is on `loginMutation.error`; swallow so the caller is
      // not forced into a try/catch for an expected outcome.
      return false;
    }
  }, [loginMutation]);

  const register = useCallback(async (
    email: string,
    password: string,
    displayName: string,
  ): Promise<boolean> => {
    try {
      await registerMutation.mutateAsync({ email, password, displayName });
      return true;
    } catch {
      return false;
    }
  }, [registerMutation]);

  const logout = useCallback(async () => {
    try {
      await logoutMutation.mutateAsync();
    } catch {
      // Surfaced via `logoutError`.
    }
  }, [logoutMutation]);

  // Deliberately NOT `authQuery.refetch()`: `authQuery`'s identity changes on
  // essentially every render, which churned `checkAuth`, which broke the
  // context `useMemo` below and re-rendered every `useAuth()` consumer on
  // every provider render. A failed check keeps the last answer (a signed-in
  // owner stays signed in while the server is away) and sets `error`.
  const checkAuth = useCallback(async () => {
    await queryClient.refetchQueries({ queryKey: AUTH_STATUS_QUERY_KEY });
    return queryClient.getQueryState(AUTH_STATUS_QUERY_KEY)?.status === 'success';
  }, [queryClient]);

  // Both return the sign-in requests to idle: clearing a refusal before the
  // next try (`resetAuthErrors`), or ending the welcome (`finishSignIn`).
  // `reset` is stable across renders; the mutation results are not.
  const { reset: resetLogin } = loginMutation;
  const { reset: resetRegister } = registerMutation;
  const resetSignIn = useCallback(() => {
    resetLogin();
    resetRegister();
  }, [resetLogin, resetRegister]);

  const isSubmitting = loginMutation.isPending || registerMutation.isPending;
  const signingIn = isSubmitting || loginMutation.isSuccess || registerMutation.isSuccess;
  const submitError =
    (loginMutation.error instanceof Error ? loginMutation.error.message : null) ??
    (registerMutation.error instanceof Error ? registerMutation.error.message : null);
  const logoutError = logoutMutation.error instanceof Error ? logoutMutation.error.message : null;

  const value: AuthContextType = useMemo(() => ({
    user,
    isAuthenticated,
    isLoading,
    isSubmitting,
    signingIn,
    authMode,
    canRegister,
    error,
    submitError,
    logoutError,
    login,
    register,
    logout,
    checkAuth,
    resetAuthErrors: resetSignIn,
    finishSignIn: resetSignIn,
  }), [user, isAuthenticated, isLoading, isSubmitting, signingIn, authMode, canRegister, error,
       submitError, logoutError, login, register, logout, checkAuth, resetSignIn]);

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
