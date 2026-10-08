# Authentication System

## Overview
n8n-inspired authentication system with JWT tokens stored in HttpOnly cookies. Authentication can be completely disabled for development or supports two deployment modes for different use cases.

## Authentication Toggle
| Setting | Environment Variable | Description |
|---------|---------------------|-------------|
| **Enabled** | `VITE_AUTH_ENABLED=true` | Require login (`company deploy` sets this on cloud VMs) |
| **Disabled** | `VITE_AUTH_ENABLED=false` | Bypass authentication, anonymous access (**default** in `.env.template`) |

When `VITE_AUTH_ENABLED=false`:
- Frontend skips login page entirely
- User is set to anonymous with owner privileges
- The client still asks `GET /api/auth/status` once at boot; it answers
  `auth_enabled: false`, and that is how the client knows to skip login
- Useful for local development and testing

## Deployment Modes (when auth enabled)
| Mode | Environment Variable | Description |
|------|---------------------|-------------|
| **Single Owner** | `AUTH_MODE=single` | First user becomes owner, registration disabled after |
| **Multi User** | `AUTH_MODE=multi` | Open registration for cloud deployments |

## Architecture
```
Frontend (LoginPage.tsx) → AuthContext → Backend (/api/auth/*) → JWT Cookie
                                              ↓
                                        AuthMiddleware
                                              ↓
                                      Protected Routes
```

## Backend Implementation

### User Model (`server/models/auth.py`)
```python
class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    display_name: str
    is_owner: bool = Field(default=False)
    is_active: bool = Field(default=True)
    created_at: datetime
    last_login: Optional[datetime]

    def set_password(self, password: str) -> None:
        # Uses bcrypt for secure hashing

    def verify_password(self, password: str) -> bool:
        # Verifies against bcrypt hash
```

### Auth Service (`server/services/user_auth.py`)
- `register()` - Creates a new user; sets `is_owner` if first user in single mode.
  Eligibility checks and the INSERT share ONE session (they used to span four,
  so two concurrent first-registrations could both be granted ownership), and a
  `IntegrityError` on the email UNIQUE index returns a 400-shaped error rather
  than surfacing as a 500.
- `login()` - Validates credentials, returns `(user, error)`. Every rejection
  returns the same `"Invalid email or password"`, and the unknown-email path
  still runs a bcrypt comparison against a dummy hash — distinct messages and
  an early return were both account-enumeration oracles on a public endpoint.
- `create_access_token()` - Mints the JWT (HS256, `sub`/`email`/`display_name`/
  `is_owner`/`exp`/`iat`/`nbf`/`jti`). No `iss`/`aud`: enforcing them would
  invalidate every token already held by a browser for negligible benefit in a
  single-audience app with a per-deployment secret.
- `verify_token()` - Validates JWT token
- `get_current_user()` - Resolves the token's subject to a `User`, rejecting a
  non-numeric `sub` and any account with `is_active = False`
- `get_auth_status()` - Returns `auth_mode` and `registration_enabled`
- `logout()` - A no-op log line. See Known Limitations.

**`UserAuthService` never initialises, derives or clears the encryption key.**
The container injects `EncryptionService` and `CredentialsDatabase` into it,
and neither is used: the only reference to the encryption service is
`is_encryption_initialized()` (a pass-through to
`EncryptionService.is_initialized()`), which nothing calls, and
`credentials_db` is held and never read. Earlier revisions of this document
described `login()` calling `_initialize_encryption(password)` and `logout()` calling
`self.encryption.clear()`. Neither method has ever existed. The Fernet key is
**server-scoped**: initialised once during startup in `main.py` from
`API_KEY_ENCRYPTION_KEY`, never derived from a user password and never
cleared on logout. See [Credentials Encryption](./credentials_encryption.md)
for the real pipeline.

### Auth Router (`server/routers/auth.py`)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/status` | GET | Get auth mode and registration status |
| `/api/auth/register` | POST | Register new user |
| `/api/auth/login` | POST | Login and set cookie |
| `/api/auth/logout` | POST | Clear auth cookie |
| `/api/auth/me` | GET | Get current user info |

`routers/auth.py` defines its own local `get_auth_service()` helper
(`return container.auth_service()`) — `get_auth_service` is NOT exported from
`services/auth.py`. `routers/websocket.py` repeats the same local helper.
The OAuth callback routers moved into their plugin folders
(`nodes/twitter/_router.py`, `nodes/google/_router.py`, built by
`services.events.oauth_lifecycle.make_oauth_callback_router`) and resolve the
service through `services.plugin.deps.get_auth_service` — the same call-time
container lookup, deliberately not memoised. In handlers/services use
`from core.container import container; auth = container.auth_service()`.

### Auth Middleware (`server/middleware/auth.py`)
Protects all routes except public paths:
```python
PUBLIC_PATHS = frozenset(
    [
        "/health",
        "/health/ready",  # readiness probe for the desktop shell splash / CI
        "/api/desktop/shutdown",  # desktop shell; gated by X-Desktop-Token, not the cookie
        "/docs",
        "/openapi.json",
        "/redoc",
        "/api/auth/status",
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/logout",
        "/ws/internal",  # Internal WebSocket for Temporal workers
    ]
)

# Path prefixes that are public. ``/mcp/`` is the CLI-agent MCP server, which
# enforces its own per-batch bearer-token auth (cookies don't apply to it), so
# it bypasses this cookie gate.
PUBLIC_PREFIXES = ("/webhook/", "/mcp/")
```

The middleware also lets the static SPA shell, built assets, and client-side
routes load BEFORE login (the SPA renders the login page itself) when the
container serves the client on a single port.

## Frontend Implementation

### Auth Context (`client/src/contexts/AuthContext.tsx`)
```typescript
interface AuthContextValue {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  authMode: 'single' | 'multi';
  canRegister: boolean;
  login: (email: string, password: string) => Promise<boolean>;
  register: (email: string, password: string, displayName: string) => Promise<boolean>;
  logout: () => Promise<void>;
  /** Check the session again; true when the server answered. */
  checkAuth: () => Promise<boolean>;
  /** A login/register request is in flight, or succeeded and its welcome
   *  is still showing. */
  signingIn: boolean;
  /** End the welcome: the requests go back to idle and the app shows. */
  finishSignIn: () => void;
}
```

### Protected Route (`client/src/components/auth/ProtectedRoute.tsx`)
The sign-in gate (onboarding handoff R4), in front of the whole app:

| State | Shows |
|-------|-------|
| the session is being checked (`isLoading`) | a loading screen |
| the server can't be reached (`error`, not signed in) | Connecting (`ConnectingPanel`): a countdown to each check, Try now, "Attempt n", and from the third attempt "Still nothing? Make sure OpenCompany is running." |
| the server answered | "Connected", held `CONNECT_RETRY.CONNECTED_HOLD_MS` (1.5 s, `useHold`) |
| then | sign-in, or the app when the session is good or login is off |
| a sign-in from the form in flight or just done (`signingIn`) | sign-in, then its welcome (`SignedIn`) until `finishSignIn` |
| signed in, the WebSocket down (`reconnecting` from `useWebSocketActions`) | Connecting over the app (`role="dialog"`, `z-60`); the app stays mounted, `inert` |

Connecting, sign-in and the welcome render inside one `ConnectScreen` (the
whole window, the orb filling it, the logo and the theme button on top), so
the orb glides from one's slot to the other's. The schedule is `useRetrySchedule`:
`CONNECT_RETRY.DELAYS_S` (2, 3, 5, 8, 8 s), each check being `checkAuth`;
over the app the checks are HTTP session checks too, so a session that
expired while the server was away goes to sign-in, and the overlay goes
once the WebSocket is back. While a `ConnectScreen` shows, the theme is the
base of the chosen family (`shellDialogsStore.connectScreenOpen`, read by
`app/ShellThemeProvider.tsx`).

### Login Page (`client/src/components/auth/LoginPage.tsx`)
Inside the gate's `ConnectScreen` (onboarding handoff R5): the orb's `login`
slot above a card that rises in. "Welcome back" or "Create your account", the
fields ("Your name" when registering, "Email", "Password" with a show/hide
toggle), Sign in or Create account, and, while `canRegister`, the way to the
other mode.
- shadcn `Form` composition (react-hook-form + zod), matching `EmailPanel` —
  schema and the field messages in `components/auth/schemas/login.ts`.
  `FormControl` supplies `aria-invalid` + `aria-describedby` per field; the
  form is `noValidate` so zod is the single validation authority rather than
  native browser bubbles.
- `canRegister` gates the footer link only; the mode itself is local state.
- **The card shows one error: what the server said.** `submitError` is the
  server's own rejection text, shown as it is: `UserAuthService` words it for
  the owner ("An account with this email already exists.", "This OpenCompany
  already has its owner. Sign in instead.", a wrong password, a 429). When the
  request got no answer it is `AUTH_UNREACHABLE` ("Can't reach OpenCompany.
  Make sure it's running, then try again."). The bootstrap `error` (the server
  can't be reached at all) is the gate's Connecting screen, so this page never
  shows it.
- Inputs and the submit button gate on `isSubmitting` (per-request), **not**
  `isLoading` (bootstrap query). The latter has always settled by the time this
  page renders, so using it disabled nothing and the form accepted unlimited
  concurrent submits.
- The card shakes (`shake` in `lib/motion.ts`) when a field is wrong or the
  server refuses. The orb follows the form: livelier while a field has focus
  (`ENERGY.focus`), more with text in it (`ENERGY.typing`), most while the
  request is out (`ENERGY.generating`).
- **After signing in** the card gives way to `SignedIn`: "Signed in" or
  "Account created", "Welcome back, {first name}" (or "Welcome, …" for a new
  account) and "Opening your team…", and the orb spikes. The gate keeps the
  screen up while `signingIn` is true, which is the login and register
  mutations' own state: in flight, or succeeded and not yet finished. The
  welcome hands over the way a mode switch does (`app/useShellActions.ts`):
  the screen the app opens on (Home or the editor) loads while the welcome
  rises in, then `finishSignIn` returns the mutations to idle and the app
  shows. A session that was already good never sees it.

## Configuration
Environment variables in `.env`:
```bash
# Authentication Toggle (frontend - Vite)
VITE_AUTH_ENABLED=false             # 'false' (template default) bypasses login; 'true' requires it

# Authentication Mode (backend)
AUTH_MODE=single                    # 'single' or 'multi'
JWT_SECRET_KEY=your-secret-key-32   # Min 32 chars
JWT_EXPIRE_MINUTES=10080            # 7 days
JWT_COOKIE_NAME=opencompany_token
JWT_COOKIE_SECURE=false             # true for HTTPS
JWT_COOKIE_SAMESITE=lax             # 'none' REQUIRES SECURE=true (enforced)

# Login/registration throttling (core/rate_limit.py)
AUTH_RATE_LIMIT_ENABLED=true
AUTH_RATE_LIMIT_ATTEMPTS=10         # per window, per (client IP, email)
AUTH_RATE_LIMIT_WINDOW=300          # seconds
```

`cookie_posture_warnings(settings)` (in `core/config.py`, beside
`dev_secret_offenders`) logs a non-fatal banner at startup for an insecure
cookie outside `DEPLOYMENT_MODE=local`, and for `"*"` in `CORS_ORIGINS` while
credentialed CORS is on. Warnings rather than failures on purpose: `company
deploy` intentionally sets `JWT_COOKIE_SECURE=false` because the VM is reached
over plain HTTP on its IP, so raising would break every LAN/IP deployment with
the worst possible symptom — login appearing to succeed, then immediately
logging out. The one combination that *does* hard-fail is
`JWT_COOKIE_SAMESITE=none` with `JWT_COOKIE_SECURE=false`, which no browser
accepts.

## Known Limitations

Recorded explicitly because each of these is easy to assume is handled.

- **`AUTH_MODE=multi` provides authentication, not isolation.**
  The identity is now *resolved* — `services/authz/ws_surface.py`
  (`execution_principal`) reads the handshake identity and decides what a given
  execution runs as, and cron/deploy carry the deployer's `user_id` through to
  every spawned run. What is still missing is *enforcement*: there is no
  ownership check on workflow get / list / delete, and the checks that exist on
  the Context and Memory panels are fail-open (`if stored_owner and
  stored_owner != caller`), so a row with an empty `owner_id` — every legacy
  row, and every row written through the REST path — authorizes everyone.
  `get_all_workflows` returns every tenant's rows unfiltered. `is_owner` is
  still decorative. Until ownership moves to an indexed column with scoped
  accessors, treat `multi` as "several people who fully trust each other".
- **`/ws/internal` skips the cookie gate and must stay narrow.** It is in
  `PUBLIC_PATHS` because a Temporal worker has no session cookie. It used to
  accept any peer, and its `execute_node` runs whatever node type and parameters
  the message names, so anyone who could reach the app port (published by
  Docker and by `company deploy`), or any web page open on the same machine,
  could run a `shell` node. The handshake now requires the
  `X-OpenCompany-Internal-Token` header, an HMAC of `SECRET_KEY`
  (`internal_socket_token` in `services/authz/ws_surface.py`); the worker
  clients (`services/temporal/activities.py`, `ws_client.py`, and a standalone
  worker's chat relay, `services/chat/relay.py`) send it, and any new internal
  client must too. A loopback-address check would not do: a
  reverse proxy on the same host makes public traffic arrive from 127.0.0.1,
  and a page's socket to `localhost` is itself loopback. Even with the token
  the socket reaches only the deny-by-default allowlist
  (`INTERNAL_SOCKET_HANDLERS`), which exists because the socket once
  dispatched through the same registry as the authenticated one —
  `save_workflow`, `delete_workflow` and all six Memory handlers were
  reachable. Refusal is deliberately indistinguishable from an unknown message
  type so the socket cannot be probed, and the allowlist test is generated from
  the live registry, so a newly added handler is closed by default.
- **Every WebSocket handshake checks `Origin`.** A browser lets any page open a
  socket to any host, `localhost` included, and with login off (the local
  default) there is no cookie to check, so any page open on the machine could
  drive `/ws/status`. `services/authz/ws_session.py` admits a handshake only
  when `Origin` is absent (not a browser), matches the request's `Host`, or is
  listed in `CORS_ORIGINS` (`*` there allows every origin, as it already does
  for HTTP CORS); anything else closes with `4003` before `accept()`.
  `authenticate_ws` (Origin, then the session cookie) serves every
  browser-facing socket; `admit_internal_ws` (Origin, then the worker token)
  serves `/ws/internal`. A reverse proxy that rewrites `Host` must list the
  public origin in `CORS_ORIGINS`. Locked by `tests/test_internal_socket_surface.py`,
  which runs real handshakes.
- **No CSRF token.** The API is cookie-authenticated and `SameSite` is the only
  defence. Mitigated by every mutating endpoint being `POST` under `/api/` and
  by `SameSite=none` + insecure being rejected at startup. A real double-submit
  scheme would touch every `fetch` and the WebSocket handshake.
- **No token revocation.** `logout` clears the cookie; there is no `jti`
  denylist, so a token captured beforehand stays valid until `exp` (default 7
  days). The practical lever is `User.is_active`, enforced in
  `get_current_user`.
- **Rate-limit counters are per-process** — see `core/rate_limit.py`.
- **`/docs`, `/redoc`, `/openapi.json` are served only when auth is disabled or
  `DEPLOYMENT_MODE=local`.** They were previously public on every deployment
  because `AuthMiddleware` gates by exclusion: any GET/HEAD outside `/api/` and
  `/ws/` is unauthenticated so the SPA shell can load pre-login. That rule is
  safe only while every router lives under `/api/`; the invariant test in
  `tests/auth/test_auth_middleware.py` fails if a new router breaks it.

`core/config.py` carries the `vite_auth_enabled` field (required because
`Settings` reads it to gate the auth middleware in `middleware/auth.py`;
`model_config` uses `extra="ignore"`, so stale `.env` vars do not raise). It also exposes `DEV_SECRET_LITERALS`
and `dev_secret_offenders()`: server startup (lifespan) logs a non-fatal error
banner when `SECRET_KEY` / `JWT_SECRET_KEY` / `API_KEY_ENCRYPTION_KEY` still
carry the dev template placeholders while auth is enabled or `DEPLOYMENT_MODE`
is not `local`. Only a `.env` that `company build` creates itself gets fresh
`secrets.token_hex(24)` values; `bun install` in a checkout and
`company provision` copy the template as-is, and with login off in a local
install nothing warns. See
[Credentials Encryption → Placeholder secrets](./credentials_encryption.md#placeholder-secrets).

## Race Condition Handling (the bootstrap check)
The frontend starts before the backend is ready during cold launch, so the
auth-status check must tolerate the server not being there yet.

The `AuthContext` runs the check through TanStack Query
(`useQuery({ queryKey: AUTH_STATUS_QUERY_KEY, queryFn: fetchAuthStatus, retry: false, networkMode: 'always' })`):

- **No hidden retries.** A failed check (no answer, a 5xx, a 401/403 or a
  non-JSON 200) sets `error` at once, and the sign-in gate shows Connecting,
  which checks again through `checkAuth` on a schedule the owner can see
  (`CONNECT_RETRY` in
  [`client/src/lib/connectionConfig.ts`](../client/src/lib/connectionConfig.ts)).
  `checkAuth` refetches and says whether the server answered; a failed check
  keeps the last answer, so a signed-in owner stays signed in while the
  server is away. Nothing ever writes a failure into the status cache.
- **`networkMode: 'always'`**: the browser's idea of being offline never
  pauses a check of a server on this computer.
- **AbortController `signal`** is plumbed through `queryFn` so unmount + React
  Strict Mode cleanup cancel in-flight requests automatically.
- `login` / `register` / `logout` invalidate the cache via
  `queryClient.invalidateQueries({ queryKey: AUTH_STATUS_QUERY_KEY })`
  (`AUTH_STATUS_QUERY_KEY = ['auth', 'status']`).

> Historical note: the check used to retry on its own, first through a
> recursive `setTimeout` chain (1, 2, 4, 8, 16 s), then through TanStack
> Query's full-jitter backoff (`AUTH_RETRY`, about 10 s in all), after which
> the login page showed "Failed to connect to server" and nothing checked
> again. The visible Connecting screen replaced both.

## Cookie-Based Auth for API Calls
All API calls must include `credentials: 'include'` for the HttpOnly cookie:
```typescript
// In workflowApi.ts, all fetch calls include:
fetch(url, { credentials: 'include' })
```

## WebSocket Authentication
Every handshake is admitted by a helper in `services/authz/ws_session.py`
before `accept()`. `/ws/status` (and `/ws/browser`) call `authenticate_ws`:
```python
# In routers/websocket.py
authenticated_user_id = await authenticate_ws(
    websocket,
    settings=container.settings(),
    user_auth_service=container.user_auth_service,
)
if authenticated_user_id is None:
    return  # already closed
websocket.state.user_id = authenticated_user_id
```
`authenticate_ws` runs, in order:
1. The `Origin` check: a foreign origin closes with `4003` ("Origin not
   allowed"). See Known Limitations for which origins are admitted.
2. With login disabled (`VITE_AUTH_ENABLED=false`), every same-origin caller
   is the owner principal.
3. Otherwise `core.auth_cookies.get_session_token` reads the session cookie
   (`JWT_COOKIE_NAME`, falling back to the legacy `machina_token`), and
   `UserAuthService.verify_token` checks it. A missing, invalid or subject-less
   token closes with `4001`.

`/ws/internal` uses `admit_internal_ws` instead: the same `Origin` check
(`4003`), then the `X-OpenCompany-Internal-Token` header, an HMAC of
`SECRET_KEY` (`internal_socket_token` / `is_internal_caller` in
`services/authz/ws_surface.py`); a missing or wrong token closes with `4001`.

`WebSocketProvider` only connects when authenticated:
```typescript
// In WebSocketContext.tsx
const { isAuthenticated, isLoading: authLoading } = useAuth();

useEffect(() => {
  if (authLoading || !isAuthenticated) {
    // Disconnect if logged out
    return;
  }
  connect();
}, [isAuthenticated, authLoading]);
```

## Key Files
| File | Description |
|------|-------------|
| `client/src/config/api.ts` | The backend's base URL (same origin unless `VITE_PYTHON_SERVICE_URL`) |
| `client/src/contexts/AuthContext.tsx` | React auth state: the TanStack Query bootstrap check, `checkAuth` |
| `client/src/lib/connectionConfig.ts` | `CONNECT_RETRY` (the Connecting screen's schedule) and the WebSocket envelope |
| `client/src/components/auth/LoginPage.tsx`, `schemas/login.ts` | Sign in and register, and their messages |
| `client/src/components/auth/SignedIn.tsx` | The welcome after signing in, until the app is ready |
| `client/src/components/auth/ProtectedRoute.tsx` | The sign-in gate (states above) |
| `client/src/components/auth/ConnectScreen.tsx`, `ConnectingPanel.tsx` | The screen around Connecting and sign-in, and Connecting itself |
| `client/src/components/auth/useRetrySchedule.ts`, `useHold.ts` | The countdown to each check; "Connected" held a moment |
| `server/models/auth.py` | User SQLModel with bcrypt |
| `server/services/user_auth.py` | `UserAuthService`: register / login / JWT mint + verify / `get_current_user`. Holds the encryption service and credentials database but uses neither; its `is_encryption_initialized()` has no callers (see the note under Auth Service) |
| `server/routers/auth.py` | REST endpoints |
| `server/middleware/auth.py` | Route protection (`PUBLIC_PATHS` / `PUBLIC_PREFIXES`) |
| `server/core/config.py` | Settings with `vite_auth_enabled` field |

## Dependencies
```
# server/pyproject.toml
bcrypt>=4.2.0
pyjwt>=2.13.0
email-validator>=2.0.0
```

JWT handling uses **PyJWT** (`import jwt`, `jwt.encode` / `jwt.decode`, catch
`jwt.PyJWTError`) — HS256 with `Settings.jwt_secret_key`. Do **not** reintroduce
`python-jose`: it drags in pure-Python `ecdsa`, which carries an unpatchable
Minerva timing-attack advisory (GHSA-wj6h-64fc-37mp).
