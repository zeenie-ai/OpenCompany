# Encrypted Credentials System

API keys, OAuth tokens, and other secrets in OpenCompany are stored in a separate encrypted SQLite database (`credentials.db`) using Fernet (AES-128-CBC + HMAC-SHA256). The encryption key is derived from a server-scoped config key using PBKDF2HMAC with 600,000 iterations, following the n8n pattern.

This document covers the encryption pipeline, the two separate credential systems (OAuth vs API keys), the single-point-of-access rule, and the multi-backend abstraction.

## Why a Separate Database

Credentials are isolated from the main `workflow.db` for three reasons:

1. **Blast radius**: a dump of `workflow.db` for debugging never contains secrets.
2. **Independent backups**: `credentials.db` can be excluded from snapshots and SQLite dumps.
3. **Backend pluggability**: the file-backed SQLite can be swapped for OS keyring or AWS Secrets Manager without touching workflow storage.

## Files

```
server/core/
|-- encryption.py              EncryptionService (Fernet + PBKDF2)
|-- credentials_database.py    CredentialsDatabase (async SQLite, salt storage)
|-- credential_backends.py     Multi-backend abstraction (Fernet / Keyring / AWS)
`-- config.py                  credential_backend, aws_secret_arn settings

server/services/
`-- auth.py                    AuthService (single access point, caching)
```

## Cryptographic Pipeline

```
API_KEY_ENCRYPTION_KEY (from .env)
          +
     salt (random 32 bytes, stored in credentials.db)
          |
          v
   PBKDF2HMAC-SHA256
   (600,000 iterations, OWASP 2024)
          |
          v
   urlsafe_b64encode -> 32-byte Fernet key
          |
          v
   Fernet cipher (held in memory for process lifetime)
          |
          v
   encrypt(plaintext) / decrypt(ciphertext)
```

- **AES-128-CBC** for confidentiality (Fernet's block cipher).
- **HMAC-SHA256** for authenticity (Fernet appends a MAC).
- **PKCS7 padding** (handled by Fernet).
- **600,000 iterations** is the OWASP 2024 recommendation for PBKDF2-SHA256.
- **Salt is 256 bits**, generated once on first startup, stored in `credentials.db`.

The derived Fernet key lives only in `EncryptionService._fernet` in process memory. It is never written to disk or to Redis.

`EncryptionService` (`server/core/encryption.py`) exposes exactly five methods: `initialize(password, salt)` (derive + store the Fernet cipher), `encrypt(plaintext) -> str` (base64 ciphertext), `decrypt(ciphertext) -> str`, `clear()` (drop the in-memory key), and `is_initialized() -> bool`. `CredentialsDatabase` (`server/core/credentials_database.py`) backs both systems with `initialize() -> bytes` (creates tables, returns the salt), `save_api_key` / `get_api_key` / `delete_api_key`, and `save_oauth_tokens` / `get_oauth_tokens` / `delete_oauth_tokens(provider, customer_id="owner")`.

## Lifecycle

```
Server startup (main.py lifespan)
    |
    v
CredentialsDatabase.initialize()  -> creates tables, returns existing or new salt
    |
    v
EncryptionService.initialize(password=API_KEY_ENCRYPTION_KEY, salt=<bytes>)
    |
    v
AuthService caches decrypted credentials in memory-only dicts
    |
    v
Routers call AuthService.get_api_key() / get_oauth_tokens() ...
    |
    v
(on shutdown) EncryptionService.clear() wipes the in-memory key
```

`EncryptionService.is_initialized()` is checked before any encrypt/decrypt call. If the server key is misconfigured, the service raises at startup rather than returning unusable ciphertext later.

## Two Separate Credential Systems

There are **two distinct storage paths** inside `credentials.db`, and they are not interchangeable. This is the most common source of bugs in this area.

### 1. API Key System

For secrets the user enters manually in the Credentials modal (OpenAI API key, Anthropic key, Google client ID, Google client secret, Twitter client secret, Brave Search key, etc.).

- Table: `EncryptedAPIKey`
- Access: `AuthService.store_api_key(provider, key, models=[...], session_id=..., model_params=...)` and `AuthService.get_api_key(provider)`
- Cache: `AuthService._api_key_cache: Dict[str, str]`
- **Per-model parameters** (Ollama / LM Studio): the `models` JSON column carries an optional `model_params` subkey alongside the model list — `{"models": [...], "model_params": {model_id: {context_length, vision, supports_tools, ...}}}`. Populated by [`nodes/model/_local_validator.py`](../server/nodes/model/_local_validator.py) from the official SDK probes (`ollama.AsyncClient.ps()` / `lmstudio.AsyncClient.llm.list_loaded()`) so the runtime knows the user's actual loaded n_ctx instead of guessing from `llm_defaults.json`. Read back via `AuthService.get_model_params(provider)` or `CredentialsDatabase.get_api_key_model_params(provider)`. Cloud providers leave this empty — their per-model params live in `model_registry.json` (refreshed from OpenRouter).

### 2. OAuth Token System

For tokens obtained via OAuth 2.0 flows (Google Workspace, Twitter/X, Claude.ai).

- Table: `EncryptedOAuthToken`
- Access: `AuthService.store_oauth_tokens(provider, access_token, refresh_token, ...)` and `AuthService.get_oauth_tokens(provider, customer_id="owner")`
- Cache: `AuthService._oauth_cache: Dict[str, Dict[str, Any]]`

### The Mistake to Avoid

Google access tokens live in the OAuth system, not the API key system. Reading them via `get_api_key("google_access_token")` returns None even if the user is fully logged in. All Google Workspace handlers must use `get_google_credentials()` from `server/nodes/google/_auth_helper.py` (post-Wave-11.I, this replaced the retired `server/services/handlers/google_auth.py`), which calls `get_oauth_tokens("google")` internally.

Twitter has the same split: `twitter_client_id` and `twitter_client_secret` are in the API key system, but `twitter_access_token` is in the OAuth system.

## Single Point of Access

**All credential operations must go through `AuthService`. Routers must never touch `CredentialsDatabase` directly.**

```python
# Correct:
from core.container import container
auth = container.auth_service()
tokens = await auth.get_oauth_tokens("google")

# Wrong (will not go through cache, will not respect backend abstraction):
credentials_db = get_credentials_db()
row = await credentials_db.query(...)
```

Enforcement:

- `AuthService` owns the Fernet cipher initialization.
- `AuthService` maintains the memory-only decryption cache.
- `CredentialsDatabase` is injected into `AuthService` and not exposed via DI to other services.

The in-memory cache is important: decrypting on every request would be slow, and writing decrypted values to Redis would defeat the encryption. Each `AuthService` instance caches decrypted credentials in process memory only, and `AuthService.clear_cache()` flushes them on demand (used by the logout handler).

## Multi-Backend Abstraction

For deployment flexibility, `credential_backends.py` defines an abstract interface that can be swapped via the `CREDENTIAL_BACKEND` env var.

```python
class CredentialBackend(ABC):
    async def store(self, key: str, value: str, metadata: Dict = None) -> bool
    async def retrieve(self, key: str) -> Optional[str]
    async def delete(self, key: str) -> bool
    def is_available(self) -> bool
```

| Backend | Use Case | Env var value |
|---|---|---|
| `FernetBackend` | Default; Fernet-encrypted SQLite | `fernet` |
| `KeyringBackend` | Desktop apps; Windows Credential Locker, macOS Keychain, Linux Secret Service | `keyring` |
| `AWSSecretsBackend` | Cloud deployments; AWS Secrets Manager | `aws` |

The factory `create_backend(settings, credentials_db)` returns the selected backend with automatic fallback to Fernet if the requested backend is unavailable (e.g. `boto3` not installed for AWS).

Dependencies (`server/pyproject.toml`) — the core `cryptography` package is always required for the default Fernet backend; the keyring / AWS packages are optional extras:

```toml
[project]
dependencies = [
    "cryptography>=44.0.0",  # Fernet encryption (always required)
]

[project.optional-dependencies]
keyring = ["keyring>=25.0.0"]  # OS-native credential storage
aws = ["boto3>=1.34.0"]        # AWS Secrets Manager
```

## Configuration

```env
# server/.env

# Required for Fernet backend
API_KEY_ENCRYPTION_KEY=<any string, at least 32 chars for good entropy>

# Which backend to use
CREDENTIAL_BACKEND=fernet         # fernet | keyring | aws

# Path to credentials SQLite file
CREDENTIALS_DB_PATH=credentials.db

# AWS backend only
AWS_SECRET_ARN=arn:aws:secretsmanager:...
AWS_REGION=us-east-1
```

If `API_KEY_ENCRYPTION_KEY` is missing or changed, existing ciphertext becomes undecryptable. There is no key-rotation mechanism today: users re-enter their keys after a key change. This is a deliberate simplification inherited from the n8n pattern.

When `company build` scaffolds `.env` from `.env.template` (step `[0/6]`), it generates a fresh `secrets.token_hex(24)` value for `API_KEY_ENCRYPTION_KEY` (and `SECRET_KEY` / `JWT_SECRET_KEY`) instead of copying the dev placeholder; an existing `.env` is never touched. If the dev placeholder survives while auth is enabled or `DEPLOYMENT_MODE != local`, startup logs a non-fatal error banner (`dev_secret_offenders()` in `core/config.py`).

## Security Properties

- **Server-scoped key**: not tied to user login sessions. JWT cookies expire, but the encryption key survives across restarts.
- **No plaintext on disk**: credentials are only decrypted in memory.
- **No plaintext in Redis**: even in Redis mode, only encrypted envelopes cross the wire (the cache layer never stores decrypted credentials).
- **Salt per install**: different OpenCompany installs have different salts, so ciphertext is not portable across installs even with the same server key.
- **Wipes on shutdown**: `EncryptionService.clear()` zeroes the Fernet reference, preventing cold-boot recovery of the derived key.

## Source of Truth

`CredentialsDatabase` (encrypted SQLite at `credentials.db`) is the **canonical source** for every credential. Two derived in-memory caches exist for performance, both invalidated atomically on every DB write/delete:

- **Backend** (`server/services/auth.py`):
  - `_api_key_cache: Dict[str, ApiKeyCacheEntry]` keyed by `{session}_{provider}`. Single dataclass entry per provider carries decrypted key + models + `stored_at`. Replaces the previous pair of `_memory_cache` (key) + `_models_cache` (models) which shared the same key shape but had separate write/evict sites — invitation to drift.
  - `_oauth_cache: Dict[str, Dict]` keyed by `{customer}_{provider}`. Holds **only** access token + display fields (`email`, `name`, `scopes`). Per [RFC 9700](https://datatracker.ietf.org/doc/rfc9700/) (OAuth 2.0 Security BCP, 2024) §5.1 the **refresh token is not memory-cached** — `AuthService.get_oauth_refresh_token(provider, customer_id)` reads from the encrypted DB on every call (rare path; refresh tokens are accessed only at access-token renewal + on revoke / logout).

- **Frontend in-memory**:
  - `useCatalogueQuery['credentialCatalogue']` — provider list + per-provider `stored: boolean` flag. Single source for "do we have a credential for X?". Replaces the retired `apiKeyStatuses[id].hasKey` mirror that duplicated this answer.
  - `apiKeyStatuses[id]` — narrowly the **validation result** (`valid`, `models`, `message`, `timestamp`). NOT a duplicate of `provider.stored` — it answers "does the stored key still validate against upstream?".

- **Frontend warm-start**:
  - IndexedDB key `credentials:catalogue:current` for the provider list (idb-keyval; ~50 ms hydration on return visit).
  - **localStorage holds NO decrypted credential values.** Per [OWASP HTML5 Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html) and ASVS V9.9, plaintext credentials must not live in `localStorage`. The previous `'credentialValues'` prefix was removed from `PERSISTED_KEY_PREFIXES` in `client/src/lib/queryPersist.ts`. The in-memory TanStack Query cache (`gcTime: ∞`) keeps the form populated for the session lifetime; on reload the panel refetches via WS — one round-trip cost, fine because the modal only opens on user action.

## Broadcast Contract

Every backend handler that mutates credential state MUST emit one or both of:

- **`api_key_status`** — per-provider validation state change. Payload: `{valid, models, message, timestamp}`. Used for validation results and to clear `apiKeyStatuses[provider]` on every connected client (e.g. after `delete_api_key`).
- **`credential_catalogue_updated`** — refetch signal carrying a CloudEvents v1.0 envelope. Body shape: `WorkflowEvent` from [`server/services/events/envelope.py`](../server/services/events/envelope.py) — same envelope the Wave 12 EventSource framework already uses. CloudEvents `type` follows the convention `credential.<area>.<action>` (e.g. `credential.api_key.saved`, `credential.api_key.deleted`, `credential.oauth.disconnected`). The wire-format outer `type` stays `credential_catalogue_updated` for frontend back-compat; future external interop (EventBridge / Knative) is a JSON-schema swap rather than a rewrite.

Helper: `broadcaster.broadcast_credential_event(event_type, *, provider, customer_id=None)` in `services/status_broadcaster.py` wraps `WorkflowEvent` with `source="opencompany://services/credentials"` and `subject=provider`.

Delete-style mutations emit **both** events. The frontend's `WebSocketContext` handles both and refreshes `apiKeyStatuses` plus the `useCatalogueQuery` cache. The 300 ms debounce in `invalidateCatalogue(queryClient)` (`client/src/hooks/useCatalogueQuery.ts`) coalesces simultaneous events into one refetch.

Pytest invariant `server/tests/credentials/test_credential_broadcasts.py` locks the contract:
- Each canonical handler (`handle_validate_api_key`, `handle_save_api_key`, `handle_delete_api_key`, `handle_twitter_logout`, `handle_google_logout`) must contain a call to `broadcaster.update_api_key_status(...)` or `broadcaster.broadcast_credential_event(...)`.
- `delete_api_key` must contain BOTH (clears the in-memory map AND invalidates the catalogue).
- `AuthService.store_*` / `remove_*` must call `credentials_db.<method>` (canonical) and `_oauth_cache` entries must NOT carry `refresh_token`.

## No Hand-Maintained Frontend Provider Lists

All credential providers come from the backend `get_credential_catalogue` handler (which reads `server/config/credential_providers.json`). The retired `client/src/components/credentials/providers.tsx` static fallback is gone — adding a new provider is a backend-only change. On cold-boot the `CredentialsModal` renders a `<Skeleton>` palette while the WS catalogue arrives; on server-unreachable it shows an explicit error state, never stale fallback data.

## Related Docs

- [DESIGN.md](DESIGN.md) - overall security posture
- [new_service_integration.md](new_service_integration.md) - where to put credentials for new service integrations
- [status_broadcaster.md](status_broadcaster.md) - WebSocket handlers for credentials (get/save/delete)
