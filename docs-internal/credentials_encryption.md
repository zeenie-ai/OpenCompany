# Encrypted Credentials System

Locally entered API keys, OAuth tokens, and other local secrets in OpenCompany are stored in a separate encrypted SQLite database (`credentials.db`) using Fernet (AES-128-CBC + HMAC-SHA256). The encryption key is derived from a server-scoped config key using PBKDF2HMAC with 600,000 iterations, following the n8n pattern.

This document covers local encryption, the separate OAuth and API-key stores, the single-point-of-access rule, and the legacy multi-backend abstraction. Approved 1Password sources use a separate runtime-resolution path through the same `AuthService`; see [1Password credentials](onepassword_credentials.md) for setup and supported providers.

## Credential-source boundary

| Source | Saved state | Runtime access |
| --- | --- | --- |
| Local API key or OAuth token | Ciphertext in the machine-local `credentials.db` | Existing `AuthService` local accessors and caches |
| 1Password API field | Approved ID reference, principal and model/endpoint metadata in the application database | `resolve_api_key` inside the calling Activity |
| 1Password website login | Approved username/password references, exact origin and resource restrictions in the application database | `resolve_browser_credentials` inside the browser owner while observations are gated |

`get_credential_source`, `has_valid_key`, `get_stored_models` and
`get_model_params` read source metadata without resolving 1Password values.
`get_api_key` retains local form behavior and does not return a resolved
1Password value. Provider runtime callers use `resolve_api_key` with the
trusted principal. Resolution rechecks enrollment after authorization waits;
there is no application-level cache of resolved 1Password values or fallback
to a local key when a binding is unavailable or revoked.

Distributed mode requires the shared PostgreSQL application database and
approved 1Password static bindings. Local encrypted keys, OAuth refresh stores,
ambient credentials and CLI-managed sessions remain local-only unless an
explicit static-secret adapter has been audited. Neither SQLite database may
be mounted over NFS/SMB. [Offline transfer](browser_agent_deployment.md#offline-sqlite-transfer)
preserves approved references and application IDs but excludes local secret
stores and token caches. The application database is therefore sensitive
metadata, even though approved references contain no resolved secret values.

## Why a Separate Database

Credentials are isolated from the main `workflow.db` for three reasons:

1. **Blast radius**: local credential ciphertext is kept out of `workflow.db`. Approved 1Password references and other private application data still require protected exports; legacy inline graph credentials must be removed before distributed transfer.
2. **Independent backups**: `credentials.db` can be excluded from snapshots and SQLite dumps.
3. **Backend pluggability**: the file-backed SQLite is designed to be swappable for OS keyring or AWS Secrets Manager without touching workflow storage (the abstraction exists but is not wired in yet; see [Multi-Backend Abstraction](#multi-backend-abstraction)).

## Files

```
server/core/
|-- encryption.py              EncryptionService (Fernet + PBKDF2)
|-- credentials_database.py    CredentialsDatabase (async SQLite, salt storage)
|-- credential_backends.py     Multi-backend abstraction (Fernet / Keyring / AWS; not wired in)
`-- config.py                  credential_backend, aws_secret_arn settings (read only by the uncalled create_backend)

server/services/
|-- auth.py                    AuthService (source metadata, runtime resolution, local caches)
`-- credentials/               1Password source storage, private CLI resolver and provisioning
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

`EncryptionService` (`server/core/encryption.py`) exposes `initialize(password, salt)` (derive + store the Fernet cipher), `encrypt(plaintext) -> str` (base64 ciphertext), `decrypt(ciphertext) -> str`, `clear()` (drop the in-memory key; only tests call it), `is_initialized() -> bool`, the lower-level `derive_key_from_password(password, salt) -> bytes` that `initialize` calls, and a static `generate_salt() -> bytes`. `CredentialsDatabase` (`server/core/credentials_database.py`) backs both systems with `initialize() -> bytes` (creates tables, returns the salt), `save_api_key` / `get_api_key` / `delete_api_key`, and `save_oauth_tokens` / `get_oauth_tokens` / `delete_oauth_tokens(provider, customer_id="owner")`.

## Lifecycle

```
Server startup (main.py lifespan)
    |
    v
CredentialsDatabase.initialize()  -> creates tables, returns existing or new salt
    |
    v
container.encryption_service().initialize(password=API_KEY_ENCRYPTION_KEY, salt=<bytes>)
    |
    v
AuthService caches decrypted local credentials in memory-only dicts
    |
    v
Routers call AuthService.get_api_key() / get_oauth_tokens() ...
    |
    v
(process exit) the key is held for the process lifetime; shutdown does not call
               EncryptionService.clear()
```

`encrypt()` and `decrypt()` raise `RuntimeError` if the cipher was never initialized, and the lifespan checks `is_initialized()` once before deriving the key.

Only a missing or short key is caught at startup. `API_KEY_ENCRYPTION_KEY` is a required `Settings` field with `min_length=32`, so `Settings()` fails before anything else runs. A key that is present but differs from the one the credentials were stored with is not detected: PBKDF2 derives a valid Fernet key from any string, so startup succeeds, and every later lookup of a stored credential fails instead. `decrypt()` raises `ValueError`, and `CredentialsDatabase.get_api_key` / `get_oauth_tokens` log `Failed to decrypt ...` at ERROR and return `None`, so the app behaves as if nothing were stored. `AuthService.get_api_key` does not cache a miss, so the error repeats on every lookup.

## Two Separate Credential Systems

There are **two distinct storage paths** inside `credentials.db`, and they are not interchangeable. This is the most common source of bugs in this area.

### 1. API Key System

For secrets the user enters manually in the Credentials modal (OpenAI API key, Anthropic key, Google client ID, Google client secret, Twitter client secret, Brave Search key, etc.).

- Model / table: `EncryptedAPIKey` (`encrypted_api_keys`)
- Access: `AuthService.store_api_key(provider, key, models=[...], session_id=..., model_params=...)` and `AuthService.get_api_key(provider)`
- Cache: `AuthService._api_key_cache: Dict[str, ApiKeyCacheEntry]` keyed `{session}_{provider}` (see "Source of Truth" below)
- **Per-model parameters** (Ollama, LM Studio and each named OpenAI-compatible endpoint): the `models` JSON column carries an optional `model_params` subkey alongside the model list — `{"models": [...], "model_params": {model_id: {context_length, vision, supports_tools, ...}}}`. Populated at save time by [`nodes/model/_local_validator.py`](../server/nodes/model/_local_validator.py): from the official SDK probes (`ollama.AsyncClient.ps()` / `lmstudio.AsyncClient.llm.list_loaded()`) for Ollama and LM Studio, and for an endpoint from llama.cpp's `/props`, vLLM's `max_model_len` or LiteLLM's model table ([RFC-0003](../RFC-0003-OPENAI-COMPATIBLE-PROVIDER-CONTRACT.md) §6.3), so the runtime knows the context the server actually serves instead of guessing from `llm_defaults.json`. Every row saved this way (`ollama`, `lmstudio`, or an endpoint's `openai_compatible:<slug>`) also carries a reserved `_endpoint` entry in `model_params` holding the label, the redacted base URL, the kind and the probe time; this JSON column is not encrypted, so the full URL lives only in the encrypted `{provider}_proxy` row. The same params are mirrored into `DATA_DIR/local_models.json` through `register_local_model()` for the synchronous registry lookups. Read back via `AuthService.get_model_params(provider)` or `CredentialsDatabase.get_api_key_model_params(provider)`. Cloud providers leave this empty — their per-model params live in `model_registry.json` (refreshed from OpenRouter).

### 2. OAuth Token System

For tokens obtained via OAuth 2.0 flows (Google Workspace, Twitter/X, Claude.ai).

- Model / table: `EncryptedOAuthToken` (`oauth_tokens`)
- Access: `AuthService.store_oauth_tokens(provider, access_token, refresh_token, ..., expiry=None)` and `AuthService.get_oauth_tokens(provider, customer_id="owner")`
- Cache: `AuthService._oauth_cache: Dict[str, Dict[str, Any]]`
- A caller that refreshes its own tokens reads them with `AuthService.get_stored_oauth_tokens(provider, customer_id="owner")`: `access_token`, `refresh_token`, `token_expiry` and `scopes`, from the encrypted DB every time, since another process may have refreshed them. Custom MCP connectors keep their OAuth sign-in this way, under `mcp:<slug>` ([MCP Connectors](./mcp_connectors.md#oauth)).

### The Mistake to Avoid

Google access tokens live in the OAuth system, not the API key system. Reading them via `get_api_key("google_access_token")` returns None even if the user is fully logged in. All Google Workspace handlers must use `get_google_credentials()` from `server/nodes/google/_auth_helper.py` (post-Wave-11.I, this replaced the retired `google_auth.py` handler module), which calls `get_oauth_tokens("google")` internally.

Twitter has the same split: `twitter_client_id` and `twitter_client_secret` are in the API key system, but `twitter_access_token` is in the OAuth system.

## Single Point of Access

**All credential operations must go through `AuthService`. Routers must never touch `CredentialsDatabase` directly.**

```python
# Correct:
from core.container import container
auth = container.auth_service()
tokens = await auth.get_oauth_tokens("google")

# Wrong (bypasses the AuthService cache):
credentials_db = get_credentials_db()
row = await credentials_db.query(...)
```

How the pieces are wired:

- The Fernet cipher is initialized in the `main.py` lifespan, not by `AuthService`: `container.credentials_database().initialize()` returns the salt, then `container.encryption_service().initialize(settings.api_key_encryption_key, salt)` derives the key. `CredentialsDatabase` receives that same `EncryptionService` singleton through DI and performs the encrypt/decrypt.
- `AuthService` maintains the memory-only decryption cache.
- `CredentialsDatabase` is a DI singleton (`container.credentials_database()`). The container injects it into `AuthService` and into `UserAuthService` (which holds it but never uses it), and `main.py` resolves it at startup to create tables and read the salt. The container does not stop anything else from resolving it, so "routers and services go through `AuthService`" is a rule to follow, not something DI enforces.

The local in-memory cache avoids repeated decryption without putting plaintext in Redis. Each `AuthService` instance caches decrypted local credentials in process memory only, and `AuthService.clear_cache()` flushes them on demand (`POST /api/auth/logout` calls it). These caches do not hold resolved 1Password values.

## Multi-Backend Abstraction

For deployment flexibility, `credential_backends.py` defines an abstract interface intended to be selected via the `CREDENTIAL_BACKEND` env var.

**Status: not wired in.** Nothing in the server calls `create_backend()`, and `Settings.credential_backend` / `aws_secret_arn` / `aws_region` are read only inside it. Local secrets use `CredentialsDatabase`; setting `CREDENTIAL_BACKEND` currently has no effect. 1Password sources are selected per saved binding through `AuthService`, independently of this factory. The value is still validated: the field is a `Literal["fernet", "keyring", "aws"]`, so any other value fails `Settings` at startup. The rest of this section describes the abstraction as written.

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
    "cryptography>=50.0.0",  # Fernet encryption (always required)
]

[project.optional-dependencies]
keyring = ["keyring>=25.0.0"]  # OS-native credential storage
aws = ["boto3>=1.34.0"]        # AWS Secrets Manager
```

## Configuration

```env
# .env at the repo root (scaffolded from .env.template; there is no server/.env)

# Required, at least 32 characters (Settings enforces min_length=32).
# Never change it once credentials are stored (see Placeholder secrets below).
API_KEY_ENCRYPTION_KEY=<48 random hex characters>

# Which backend to use -- currently has no effect (see Multi-Backend Abstraction),
# but a value other than fernet | keyring | aws fails Settings validation
CREDENTIAL_BACKEND=fernet

# Path to credentials SQLite file (a relative path resolves under DATA_DIR)
CREDENTIALS_DB_PATH=credentials.db

# AWS backend only (also unused until the backend layer is wired in)
AWS_SECRET_ARN=arn:aws:secretsmanager:...
AWS_REGION=us-east-1
```

A missing or short `API_KEY_ENCRYPTION_KEY` stops startup (see [Lifecycle](#lifecycle)). A changed one makes every existing ciphertext undecryptable: lookups return `None`, and users must re-enter their keys. There is no key-rotation mechanism today. This is a deliberate simplification inherited from the n8n pattern.

### Placeholder secrets

`.env.template` ships publicly known `dev-` placeholder values for `API_KEY_ENCRYPTION_KEY`, `SECRET_KEY` and `JWT_SECRET_KEY` (the exact literals are `DEV_SECRET_LITERALS` in `server/core/config.py`). Whether an install replaces them depends on how its `.env` was created:

| How `.env` was created | Secrets |
|---|---|
| `bun install` in a source checkout (the root `postinstall` hook runs `scripts/install.js`) | Copied from the template as-is: placeholders |
| Global install: `company provision`, the first `company` command, or the `install.sh` / `install.ps1` installers (all run `scripts/install.js`) | Copied from the template as-is: placeholders |
| `company build` when no `.env` exists yet (step `[0/6]`, `_scaffold_env_secrets` in `cli/commands/build.py`) | A fresh `secrets.token_hex(24)` value for each `dev-` placeholder |
| Docker (`docker/entrypoint.sh`) | Fresh values written to the env file on the data volume at first start |
| `company deploy` | Fresh values for every deploy (`cli/commands/deploy/_secrets.py`) |
| Desktop app | Nothing is generated: the template values apply unless `<userData>/desktop.env` sets them |

An existing `.env` is never modified. So the usual checkout flow (`bun install`, then `bun run build`) keeps the placeholders: `install.js` has already created `.env` by the time `company build` looks for it.

`dev_secret_offenders()` (`server/core/config.py`) makes startup log a non-fatal error banner only when login is on (`VITE_AUTH_ENABLED` is anything other than `false`) or `DEPLOYMENT_MODE` is not `local`. A default local install, with login off, runs on the placeholders without any warning.

To replace them, generate one value per key and set it in `.env` or the process environment, which wins over `.env`:

```bash
python -c "import secrets; print(secrets.token_hex(24))"
```

Do this before the first credential is saved. After that, never change `API_KEY_ENCRYPTION_KEY`, or every stored credential becomes unreadable (see above). Changing `JWT_SECRET_KEY` signs every user out.

## Security Properties

- **Server-scoped key**: not tied to user login sessions. JWT cookies expire, but the encryption key survives across restarts.
- **No plaintext on disk**: credentials are only decrypted in memory.
- **No plaintext in Redis**: credentials never pass through the cache layer. `AuthService` receives a `CacheService` handle but does not use it for credentials, so neither plaintext nor ciphertext reaches Redis, even with `REDIS_ENABLED=true`.
- **Salt per install**: different OpenCompany installs have different salts, so ciphertext is not portable across installs even with the same server key.
- **Held for the process lifetime**: the derived key is never written to disk, but it is not wiped at shutdown either. `EncryptionService.clear()` (which drops the Fernet reference) is called only by tests; the `main.py` lifespan shutdown does not call it, so the key leaves memory when the process exits.

## Source of Truth

`CredentialsDatabase` (encrypted SQLite at `credentials.db`) is the canonical
source for local API keys and OAuth tokens. 1Password values remain in
1Password; approved references and catalogue metadata live in the application
database. Source-status versions include principal-scoped saved-row changes,
so catalogue refreshes across replicas do not need to resolve secrets. The
following caches describe the local path:

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

All credential providers come from the backend `get_credential_catalogue` handler (which reads `server/config/credential_providers.json`). The retired `providers.tsx` static fallback is gone — adding a new provider is a backend-only change. On cold-boot the `CredentialsModal` renders a `<Skeleton>` palette while the WS catalogue arrives; on server-unreachable it shows an explicit error state, never stale fallback data.

## Related Docs

- [1Password credentials](onepassword_credentials.md) - source enrollment, runtime resolution, private login and compatibility
- [Browser deployment](browser_agent_deployment.md) - PostgreSQL prerequisites and offline transfer
- [DESIGN.md](DESIGN.md) - overall security posture
- [new_service_integration.md](ARCHIVE/new_service_integration.md) - where to put credentials for new service integrations
- [status_broadcaster.md](status_broadcaster.md) - WebSocket handlers for credentials (get/save/delete)
