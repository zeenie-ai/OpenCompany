# Authentication System

## Overview
n8n-inspired authentication system with JWT tokens stored in HttpOnly cookies. Authentication can be completely disabled for development or supports two deployment modes for different use cases.

## Authentication Toggle
| Setting | Environment Variable | Description |
|---------|---------------------|-------------|
| **Enabled** | `VITE_AUTH_ENABLED=true` | Require login (default) |
| **Disabled** | `VITE_AUTH_ENABLED=false` | Bypass authentication, anonymous access |

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
- `register_user()` - Creates new user, sets as owner if first user in single mode
- `authenticate_user()` - Validates credentials, returns user
- `create_token()` - Generates JWT token
- `verify_token()` - Validates JWT token
- `get_auth_status()` - Returns mode, registration availability, user count

`UserAuthService` is also the integration point for the encrypted-credentials
system: `login()` calls `_initialize_encryption(password)` (derives the Fernet
key via PBKDF2 from the user's password + the credentials DB salt) and
`logout()` calls `self.encryption.clear()` to wipe the key from memory. See
[Credentials Encryption](./credentials_encryption.md) for the full pipeline.

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
`services/auth.py`. The same local-helper pattern is repeated in
`routers/twitter.py`, `routers/google.py`, and `routers/websocket.py`. In
handlers/services use `from core.container import container; auth = container.auth_service()`.

### Auth Middleware (`server/middleware/auth.py`)
Protects all routes except public paths:
```python
PUBLIC_PATHS = frozenset([
    "/health", "/docs", "/openapi.json", "/redoc",
    "/api/auth/status", "/api/auth/login", "/api/auth/register", "/api/auth/logout",
    "/ws/internal",   # Internal WebSocket for Temporal workers
])

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
- Dracula-themed login/register form
- Switches between login and register based on `canRegister`
- Displays errors from auth context

## Configuration
Environment variables in `.env`:
```bash
# Authentication Toggle (frontend - Vite)
VITE_AUTH_ENABLED=true              # 'true' or 'false' - disable to bypass login

# Authentication Mode (backend)
AUTH_MODE=single                    # 'single' or 'multi'
JWT_SECRET_KEY=your-secret-key-32   # Min 32 chars
JWT_EXPIRE_MINUTES=10080            # 7 days
JWT_COOKIE_NAME=opencompany_token
JWT_COOKIE_SECURE=false             # true for HTTPS
JWT_COOKIE_SAMESITE=lax
```

`core/config.py` carries the `vite_auth_enabled` field (required because
Pydantic Settings uses `extra="forbid"`).

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
| `server/services/user_auth.py` | JWT creation/verification + encryption init on login/logout |
| `server/routers/auth.py` | REST endpoints |
| `server/middleware/auth.py` | Route protection (`PUBLIC_PATHS` / `PUBLIC_PREFIXES`) |
| `server/core/config.py` | Settings with `vite_auth_enabled` field |

## Dependencies
```
# server/pyproject.toml
bcrypt>=4.1.0
pyjwt>=2.13.0
email-validator>=2.0.0
```

JWT handling uses **PyJWT** (`import jwt`, `jwt.encode` / `jwt.decode`, catch
`jwt.PyJWTError`) — HS256 with `Settings.jwt_secret_key`. Do **not** reintroduce
`python-jose`: it drags in pure-Python `ecdsa`, which carries an unpatchable
Minerva timing-attack advisory (GHSA-wj6h-64fc-37mp).
