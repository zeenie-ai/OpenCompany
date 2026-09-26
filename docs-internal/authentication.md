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
- No backend auth API calls are made
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

**`UserAuthService` does not touch the encryption service.** Earlier revisions
of this document described `login()` calling `_initialize_encryption(password)`
and `logout()` calling `self.encryption.clear()`. Neither method has ever
existed. The Fernet key is **server-scoped**: initialised once during startup
in `main.py` from `API_KEY_ENCRYPTION_KEY`, never derived from a user password
and never cleared on logout. See
[Credentials Encryption](./credentials_encryption.md) for the real pipeline.

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
  checkAuth: () => Promise<void>;
}
```

### Protected Route (`client/src/components/auth/ProtectedRoute.tsx`)
Wraps protected content, shows LoginPage if not authenticated:
```typescript
const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated, isLoading } = useAuth();
  if (isLoading) return <LoadingSpinner />;
  if (!isAuthenticated) return <LoginPage />;
  return <>{children}</>;
};
```

### Login Page (`client/src/components/auth/LoginPage.tsx`)
- shadcn `Form` composition (react-hook-form + zod), matching `EmailPanel` —
  schema in `components/auth/schemas/login.ts`. `FormControl` supplies
  `aria-invalid` + `aria-describedby` per field; the form is `noValidate` so
  zod is the single validation authority rather than native browser bubbles.
- `canRegister` gates the footer link only; the mode itself is local state.
- **Two error channels, deliberately distinct.** `submitError` is the server's
  own rejection text (wrong password, duplicate email, 429) and takes
  precedence; `error` is a bootstrap-query connectivity failure. Both render in
  a `role="alert"` region.
- Inputs and the submit button gate on `isSubmitting` (per-request), **not**
  `isLoading` (bootstrap query). The latter has always settled by the time this
  page renders, so using it disabled nothing and the form accepted unlimited
  concurrent submits.

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
  (`internal_socket_token` in `services/authz/ws_surface.py`); both worker
  clients (`services/temporal/activities.py`, `ws_client.py`) send it, and any
  new internal client must too. A loopback-address check would not do: a
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
is not `local`. A `company build`-scaffolded `.env` avoids this by generating
fresh `secrets.token_hex(24)` values at scaffold time.

## Race Condition Handling (TanStack Query bootstrap)
The frontend starts before the backend is ready during cold launch, so the
auth-status check must tolerate transient failures.

The `AuthContext` bootstraps the auth-status check through TanStack Query
(`useQuery({ queryKey: AUTH_STATUS_QUERY_KEY, queryFn: fetchAuthStatus, retry, retryDelay, signal })`),
which replaced the previous recursive `setTimeout` retry chain. Behaviour:

- **Full-jitter exponential backoff** per the AWS Architecture Blog formula:
  `random(0, min(CAP_MS, BASE_MS * 2^attempt))`. Constants live in
  [`client/src/lib/connectionConfig.ts`](../client/src/lib/connectionConfig.ts)
  under `AUTH_RETRY`: `BASE_MS = 50`, `CAP_MS = 4000`, `MAX_ATTEMPTS = 7`.
  Cumulative budget is ~10 s (vs. the old ~31 s); sub-second granularity early
  covers the typical 4 s backend cold-start window in 4–5 attempts.
- **401/403 short-circuit the retry chain** — those are valid responses meaning
  "auth disabled / not logged in", not "backend unavailable", so no retry
  budget is burned (`authShouldRetry` returns `false` when the wrapped error
  message contains `HTTP 401` / `HTTP 403`).
- **AbortController `signal`** is plumbed through `queryFn` so unmount + React
  Strict Mode cleanup cancel in-flight requests automatically.
- `login` / `register` / `logout` invalidate the cache via
  `queryClient.invalidateQueries({ queryKey: AUTH_STATUS_QUERY_KEY })`
  (`AUTH_STATUS_QUERY_KEY = ['auth', 'status']`).

> Historical note: the original implementation used 5 fixed retries with
> exponential backoff (1 s, 2 s, 4 s, 8 s, 16 s) and a recursive `setTimeout`
> chain, surfacing "Failed to connect to server" only after all retries were
> exhausted. This was superseded by the TanStack Query bootstrap above.

## Cookie-Based Auth for API Calls
All API calls must include `credentials: 'include'` for the HttpOnly cookie:
```typescript
// In workflowApi.ts, all fetch calls include:
fetch(url, { credentials: 'include' })
```

## WebSocket Authentication
WebSocket checks the cookie before accepting the connection:
```python
# In websocket.py
token = websocket.cookies.get(settings.jwt_cookie_name)
if not token:
    await websocket.close(code=4001, reason="Not authenticated")
    return
```

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
| `client/src/config/api.ts` | API config with AUTH_ENABLED toggle |
| `client/src/contexts/AuthContext.tsx` | React auth state with TanStack Query bootstrap + retry logic |
| `client/src/lib/connectionConfig.ts` | `AUTH_RETRY` backoff constants (`BASE_MS` / `CAP_MS` / `MAX_ATTEMPTS`) |
| `client/src/components/auth/LoginPage.tsx` | Login UI |
| `client/src/components/auth/ProtectedRoute.tsx` | Route guard |
| `server/models/auth.py` | User SQLModel with bcrypt |
| `server/services/user_auth.py` | `UserAuthService`: register / login / JWT mint + verify / `get_current_user`. No encryption-service coupling (see the note under Auth Service) |
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
