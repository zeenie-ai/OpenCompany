# Credentials Panel — Logic Flow Documentation

> Reference for refactoring [client/src/components/CredentialsModal.tsx](../client/src/components/CredentialsModal.tsx) (~3000 lines).
> Every claim here cites file + line number so reviewers can validate post-refactor.
> Companion test suite in [server/tests/credentials/](../server/tests/credentials) and [client/src/test/](../client/src/test) locks in the invariants enumerated in section 5.

---

## 1. Architecture

### 1.1 Layout
Two-panel modal:
- **Left**: category sidebar (8 categories, ~20 items)
- **Right**: dynamic detail panel keyed off `selectedItem.panelType`

State lives entirely in `CredentialsModal` as `useState` hooks (no Zustand, no context for modal-local state). Cross-cutting status (paired devices, OAuth account info) comes from `WebSocketContext` push broadcasts.

### 1.2 State taxonomy

| Bucket | Variables | Provider scope |
|---|---|---|
| Standard API key inputs | `keys`, `validKeys`, `loading`, `models` | OpenAI/Anthropic/Gemini/Groq/Cerebras/OpenRouter/DeepSeek/Kimi/Mistral/Brave/Perplexity/Serper/Maps/Apify |
| Provider defaults (per-LLM) | `providerDefaults`, `defaultsDirty`, `modelConstraints` | LLM providers only |
| Usage panels | `usageSummary`, `apiUsage`, `usageExpanded` | LLM (token cost) + Twitter/Maps (API cost) |
| Twitter-specific | `twitterClientId`, `twitterClientSecret`, `twitterCredentialsStored`, `twitterLoading`, `twitterError` | twitter only |
| Google-specific | `gmailClientId`, `gmailClientSecret`, `gmailCredentialsStored`, `gmailLoading`, `gmailError` | google only |
| WhatsApp-specific | `rateLimitConfig`, `rateLimitStats`, `rateLimitExpanded`, `whatsappLoading`, `whatsappError` | whatsapp only |
| Android-specific | `androidApiKey`, `androidApiKeyStored`, `androidLoading`, `androidError` | android only |
| Telegram-specific | `telegramToken`, `telegramTokenStored`, `telegramLoading`, `telegramError` | telegram only |
| Email-specific | `emailProvider`, `emailAddress`, `emailPassword`, `emailImapHost/Port`, `emailSmtpHost/Port`, `emailStored`, `emailLoading`, `emailError` | email only |

### 1.3 Data sources

| Source | Purpose | File |
|---|---|---|
| `useApiKeys()` | CRUD + validation + provider defaults + usage + model constraints | [client/src/hooks/useApiKeys.ts](../client/src/hooks/useApiKeys.ts) |
| `useWhatsApp()` | WhatsApp send/QR/status helpers (also accessible via `WebSocketContext`) | [client/src/hooks/useWhatsApp.ts](../client/src/hooks/useWhatsApp.ts) |
| `useWebSocket()` | Status getters (`useWhatsAppStatus`, `useAndroidStatus`, `useTwitterStatus`, `useGoogleStatus`, `useTelegramStatus`), `sendRequest<T>(type, data, timeoutMs?)` | [client/src/contexts/WebSocketContext.tsx](../client/src/contexts/WebSocketContext.tsx) |

Push-driven status objects (`whatsappStatus`, `androidStatus`, `twitterStatus`, `googleStatus`, `telegramStatus`) update reactively from broadcast messages — the modal does not poll for them.

---

## 2. Per-Provider Flow Matrix

### 2.1 Pattern A — Simple API key (validated against provider)

**Providers**: openai, anthropic, gemini, groq, cerebras, openrouter, deepseek, kimi, mistral, brave_search, perplexity.

**Credentials stored**: one row in `EncryptedAPIKey` table, key `{session_id}_{provider}`, payload = encrypted API key + `{models: [...]}` JSON.

**Happy path**:
```
User types key in <Input.Password>
  -> handleValidate(provider)
     -> useApiKeys.validateApiKey(provider, key)
        -> WebSocketContext.validateApiKey(provider, key)
           -> ws.send({type: 'validate_api_key', provider, api_key, request_id})
backend handle_validate_api_key (websocket.py:990):
  -> ai_service.fetch_models(provider, key)   // hits provider /v1/models endpoint
  -> auth_service.store_api_key(provider, key, models, session_id='default')
     -> credentials_db.save_api_key  -> EncryptionService.encrypt -> EncryptedAPIKey row
     -> _api_key_cache[session_id_provider] = ApiKeyCacheEntry(key=..., models=[...])
  -> broadcaster.update_api_key_status(provider, valid=True, has_key=True, models=[...])
  -> return {valid: True, models: [...]}
Frontend resolves Promise:
  -> setValidKeys[provider] = true
  -> setModels[provider] = models
  -> Connected tag rendered
```

**WebSocket messages used**: `validate_api_key`, `get_stored_api_key`, `save_api_key`, `delete_api_key`, `get_ai_models`.

**Buttons**: Validate (disabled while empty/loading), Delete (only when stored).

**Error surface**: `loading[provider]` cleared in `finally`; `validKeys[provider]` stays `false`; `<Alert type="error">` with message from response.

### 2.2 Pattern B — Simple API key (custom validator)

**Providers**: google_maps, apify.

Same as Pattern A but validation routes to dedicated handler, not the generic `validate_api_key`:

| Provider | Hook method | WS message |
|---|---|---|
| google_maps | `validateGoogleMapsKey(key)` | `validate_maps_key` |
| apify | `validateApifyKey(key)` | `validate_apify_key` |

After validation succeeds, the frontend still calls `saveApiKey` (or backend stores it) under the provider name. Storage table identical to Pattern A.

### 2.3 Pattern C — Save-only API key (no validation)

**Providers**: android_remote, serper, twitter_client_id, twitter_client_secret, google_client_id, google_client_secret, telegram, email_*.

`saveApiKey(provider, key)` calls WebSocket `save_api_key` directly. Backend `handle_save_api_key` (websocket.py:1023) skips model fetch and just calls `auth_service.store_api_key(provider, key, models=[])`.

> **Invariant**: `auth_service.store_api_key()` requires `models` as a positional/keyword argument — passing `[]` is mandatory for non-LLM keys. See [server/services/auth.py:46](../server/services/auth.py).

### 2.4 Pattern D — OAuth 2.0 PKCE (Twitter)

**Credentials stored**:
- API key table: `twitter_client_id`, `twitter_client_secret` (user-entered)
- OAuth table: `EncryptedOAuthToken(provider='twitter', customer_id='owner')` with access_token + refresh_token + scopes

**Happy path**:
```
1. User saves client_id / client_secret
   -> 2x save_api_key WS calls
2. User clicks Login with Twitter
   -> sendRequest('twitter_oauth_login', {})
   handle_twitter_oauth_login (websocket.py:1062):
     -> auth_service.get_api_key('twitter_client_id')
     -> redirect_uri = get_redirect_uri(websocket, 'twitter')   // runtime-derived
     -> oauth.generate_authorization_url()
        -> generates PKCE code_verifier + S256 code_challenge
        -> stores state in _oauth_states[state] with code_verifier + redirect_uri
     -> returns {success, url, state}
3. Frontend window.open(response.url, '_blank')
4. User authorizes on x.com -> X redirects to /api/twitter/callback?code=...&state=...
5. routers/twitter.py callback:
     -> oauth.exchange_code(code, state)   // pops state, exchanges via PKCE
     -> auth_service.store_oauth_tokens('twitter', access, refresh, scopes=...)
6. Frontend periodically calls sendRequest('twitter_oauth_status')
   handle_twitter_oauth_status (websocket.py:1104):
     -> auth_service.get_oauth_tokens('twitter')   // OAuth table, NOT api_key table
     -> oauth.get_user_info(access_token)
     -> on 401: oauth.refresh_access_token(refresh) + re-store
     -> returns {connected, username, user_id, name, profile_image_url, verified}
```

**Logout path**: `sendRequest('twitter_logout')` → `handle_twitter_logout` (websocket.py:1179) revokes both tokens at REVOKE_URL, then `auth_service.remove_oauth_tokens('twitter')`. Stale legacy `twitter_access_token` API-key entries are best-effort cleaned (websocket.py:1217).

**PKCE invariants**:
- `code_verifier`: 96 random bytes → urlsafe base64 → trimmed to 128 chars (twitter_oauth.py:48).
- `code_challenge = base64url(sha256(code_verifier))` (twitter_oauth.py:59).
- Authorization URL params include `code_challenge_method=S256`.
- State is consumed exactly once (`_oauth_states.pop(state)` in `exchange_code`).
- Authorization codes expire in 30 seconds (X documentation), states expire after 10 minutes (`cleanup_expired_states`).

### 2.5 Pattern E — OAuth 2.0 with offline access (Google Workspace)

**Credentials stored**:
- API key table: `google_client_id`, `google_client_secret`
- OAuth table: `EncryptedOAuthToken(provider='google', customer_id='owner')` with access + refresh + email + name + scopes

**Differences from Twitter**:
- Uses `google-auth-oauthlib` `Flow` rather than hand-rolled PKCE (google-auth-oauthlib auto-generates PKCE under the hood; `code_verifier` is captured and saved into `_oauth_states` so the new `Flow` instance in `exchange_code` can restore it — google_oauth.py:189, 226).
- Authorization URL params include `access_type=offline` + `prompt=consent` (google_oauth.py:175) — both required to reliably receive a refresh_token.
- `_get_user_info` calls Google's oauth2/v2 userinfo endpoint to populate email/name/picture (google_oauth.py:258).
- `handle_google_oauth_status` (websocket.py:1272) proactively refreshes the token via `GoogleOAuth.refresh_credentials` and broadcasts `{type: 'google_status', data: {...}}` to all clients.
- `handle_google_logout` (websocket.py:1333) clears tokens and broadcasts disconnected status.

**Combined scopes** (loaded from [server/config/google_apis.json](../server/config/google_apis.json)): openid + email + profile + gmail.send/readonly/modify + calendar (full + events) + drive (full + file) + spreadsheets + tasks + contacts (full + readonly).

> **Invariant (load-bearing)**: Google access/refresh tokens are read via `auth_service.get_oauth_tokens('google')`, NEVER via `auth_service.get_api_key('google_access_token')`. The two storage systems are distinct tables with distinct caches. Earlier code mixed them and broke OAuth status.

### 2.6 Pattern F — OAuth via isolated subprocess (Claude)

**Credentials stored**: not in encrypted DB. Tokens persist in `~/.claude-machina/.credentials.json`, written by the official `@anthropic-ai/claude-code` CLI which is installed into `~/.claude-machina/npm/` to keep it isolated from the user's main Claude session.

**Happy path**:
```
sendRequest('claude_oauth_login')
  -> initiate_claude_oauth (claude_oauth.py:61)
     -> ensure CLI installed (npm install --prefix ~/.claude-machina/npm)
     -> spawn `claude login` with env CLAUDE_CONFIG_DIR=~/.claude-machina
     -> pipe "yes\nyes\n" to stdin to auto-accept prompts
     -> CLI opens browser; user authorizes on console.anthropic.com
     -> returns {success, pid, config_dir}

Frontend polls sendRequest('claude_oauth_status'):
  -> get_claude_credentials (claude_oauth.py:121)
     -> reads ~/.claude-machina/.credentials.json
     -> returns {success, has_token, access_token, expires_at}
```

No logout WebSocket handler — user can manually delete the credentials file or run `claude logout` separately.

### 2.7 Pattern G — QR pairing (WhatsApp)

**Credentials stored**: nothing in MachinaOs DB. Session lives inside the bundled `whatsapp-rpc` Go service (default port 9400).

**Happy path**:
```
1. User clicks Start
   -> sendRequest('whatsapp_start')
      -> backend launches/restarts whatsapp-rpc, which generates QR
2. broadcast 'whatsapp_status' arrives with status.qr (base64 PNG payload)
   -> WebSocketContext exposes whatsappStatus
   -> CredentialsModal renders <QRCodeDisplay value={whatsappStatus.qr} />
3. User scans QR with phone -> Go service pairs
4. broadcast 'whatsapp_status' arrives with connected=true, has_session=true, connected_phone='...'
   -> QR replaced by "Connected" descriptions
```

**Buttons**: Start / Restart (regenerates QR) / Refresh (re-fetches status). All dispatch via `useWhatsApp` or `WebSocketContext`.

**Rate limit collapse panel**:
- On expand → `getWhatsAppRateLimitConfig()` (`whatsapp_rate_limit_get`) + stats fetch.
- Edits update `rateLimitConfig`, Save → `setWhatsAppRateLimitConfig()` (`whatsapp_rate_limit_set`).
- Pause notification with Unpause → `unpauseWhatsAppRateLimit()` (`whatsapp_rate_limit_unpause`).

### 2.8 Pattern H — Relay session (Android)

**Credentials stored**: API key table — `android_remote` (relay auth token).

**Happy path**:
```
1. User saves android_remote API key (Pattern C)
2. User clicks Connect
   -> sendRequest('android_relay_connect', {url: VITE_ANDROID_RELAY_URL, api_key})
   handle_android_relay_connect (websocket.py:1425):
      -> AndroidService.connect_relay(url, key)
      -> returns {success, qr_data, session_token}
3. Backend broadcasts 'android_status' with paired=false, qr_data, session_token
   -> WebSocketContext exposes androidStatus
   -> CredentialsModal renders QR
4. User scans QR with companion app -> device pairs
5. Backend broadcasts 'android_status' with connected=true, paired=true, device_id, device_name
```

**Two-state model** (load-bearing):
- `connected` = relay WebSocket up
- `paired` = at least one Android device paired via relay
- Reconnect button (`android_relay_reconnect`) issues new QR while keeping relay session.
- Disconnect (`android_relay_disconnect`) tears down everything.

### 2.9 Pattern I — Bot token + polling (Telegram)

**Credentials stored**: API key table — `telegram`. Owner chat ID auto-captured on first private message and stored as `telegram_owner_chat_id`.

**Happy path**:
```
1. User pastes BotFather token, clicks Save
   -> saveApiKey('telegram', token)   // Pattern C
2. User clicks Connect
   -> sendRequest('telegram_connect', {token})
   handle_telegram_connect (websocket.py:1844):
      -> TelegramService.connect(token)
         -> validate via getMe
         -> start long-polling (get_updates_read_timeout=30s)
      -> auth_service.store_api_key('telegram', token, models=[])
3. broadcast 'telegram_status' arrives with connected=true, bot_username, bot_name, bot_id
4. First private message captures owner_chat_id -> stored as telegram_owner_chat_id API key
5. broadcast 'telegram_status' updated with owner_chat_id
```

**Disconnect**: `telegram_disconnect` stops polling AND removes both `telegram` and `telegram_owner_chat_id` API keys.

**Reconnect**: Re-establishes polling using stored token (no token re-entry).

**Note**: `httpx.ReadError` during long-poll is expected (network/server drops). PTB's network_retry_loop handles it; logged at DEBUG. Polling timeout of 30s on `ApplicationBuilder` is mandatory — default 5s breaks long-polling.

### 2.10 Pattern J — Multi-field IMAP/SMTP (Email)

**Credentials stored** (all in API key table):

Always:
- `email_provider` ∈ {gmail, outlook, yahoo, icloud, protonmail, fastmail, custom}
- `email_address`
- `email_password` (App Password for Gmail/Outlook/Yahoo, regular for ProtonMail Bridge etc.)

When `email_provider == 'custom'` only:
- `email_imap_host`, `email_imap_port` (string), `email_imap_encryption`
- `email_smtp_host`, `email_smtp_port` (string), `email_smtp_encryption`

For named providers (gmail/outlook/...) the IMAP/SMTP host/port come from `server/config/email_providers.json` presets — they are NOT stored as API keys.

**Save flow**:
```
handleEmailSave():
  validate (address required; password required on first save; custom requires hosts/ports)
  -> 3 sequential await saveApiKey() calls (provider, address, password)
  -> when provider == 'custom': 4 additional await saveApiKey() calls (imap_host/port/enc, smtp_host/port/enc)
  -> setEmailPassword('')   // security: clear plaintext input after save
  -> setEmailStored(true)
```

**Update flow**: empty password input means "keep existing" — handler checks `!password.trim() && emailStored` and skips the password write.

**Remove flow**: `handleEmailRemove()` issues parallel `removeApiKey` calls for all stored fields.

---

## 3. Cross-Cutting Concerns

### 3.1 Loading convention
`[name]Loading: string | null` where the string is the action tag (`'login'`, `'save'`, `'connect'`, etc.). Buttons compute `loading={twitterLoading === 'login'}`. Always cleared in `finally`.

### 3.2 Error surfacing
`[name]Error: string | null`. Cleared on retry attempt (`setXError(null)` at start of handler), set in `catch`, rendered as `<Alert type="error" closable />` near the action area. Never `throw` past the handler.

### 3.3 Dirty tracking (Provider Defaults)
`defaultsDirty: Record<provider, boolean>`. `updateProviderDefault(provider, key, value)` mutates `providerDefaults` AND sets `defaultsDirty[provider]=true`. Save handler calls `saveProviderDefaults` then clears the flag. Save button is disabled when `!defaultsDirty[provider]`.

### 3.4 Lazy fetching
Usage panels (`Usage & Costs`, `API Usage`, model constraints) fetch inside `useEffect` gated on `usageExpanded === true`. Avoids paying for queries on every modal open.

### 3.5 Broadcast-driven status
`whatsappStatus`, `androidStatus`, `twitterStatus`, `googleStatus`, `telegramStatus` are subscribed via `useWebSocket()`. The modal NEVER calls a status WebSocket on mount — initial state arrives via `initial_status` broadcast on WS connect, and updates arrive via dedicated `*_status` broadcast types ([client/src/contexts/WebSocketContext.tsx](../client/src/contexts/WebSocketContext.tsx) message handler switch).

The "Refresh" buttons in OAuth panels (Twitter/Google/Telegram) explicitly call the corresponding status request handler to force a re-check, which in turn re-broadcasts.

---

## 4. Backend Contract

### 4.1 WebSocket handler registry
All handlers registered in [server/routers/websocket.py](../server/routers/websocket.py) `MESSAGE_HANDLERS` dict (lines ~3089–3153). Each handler:
- Uses `@ws_handler(*required_fields)` decorator (websocket.py:61) which validates required fields and wraps exceptions into `{success: False, error: str(e)}`.
- Reads dependencies via `container.auth_service()`, `container.ai_service()`, etc. — never touches `credentials_db` directly.
- Returns a `Dict[str, Any]` with `success: True` (auto-injected by decorator if missing).

### 4.2 Single point of access
All credential reads/writes go through [`AuthService`](../server/services/auth.py):
- API keys: `store_api_key(provider, api_key, models, session_id='default')` / `get_api_key(provider, session_id)` / `remove_api_key(provider, session_id)` / `get_stored_models(provider, session_id)`.
- OAuth tokens: `store_oauth_tokens(provider, access, refresh, email=None, name=None, scopes=None, customer_id='owner')` / `get_oauth_tokens(provider, customer_id)` / `remove_oauth_tokens(provider, customer_id)` / `get_oauth_refresh_token(provider, customer_id)` (DB-only — see RFC 9700 below).
- Memory caches: `_api_key_cache: Dict[str, ApiKeyCacheEntry]` (decrypted API keys + models in one dataclass entry per `{session}_{provider}`) and `_oauth_cache` (access tokens + display fields only). Both hit BEFORE the encrypted DB; `clear_cache()` empties both (called on logout). Per RFC 9700 (OAuth 2.0 BCP, 2024) the `_oauth_cache` does NOT carry `refresh_token` — refresh tokens are long-lived secrets and must not live in process memory; `get_oauth_refresh_token()` reads from the encrypted DB on every call. Pre-Wave-12 the cache was split into `_memory_cache` (key) + `_models_cache` (models) sharing the same key shape with no shared invalidation; now collapsed into one entry so the two values can never drift.

### 4.3 Two-table split

| Table | Storage | Used for |
|---|---|---|
| `EncryptedAPIKey` | `{session_id}_{provider}` PK, encrypted single key payload + JSON models | All Pattern A/B/C providers, including `*_client_id` / `*_client_secret` for OAuth providers |
| `EncryptedOAuthToken` | `(provider, customer_id)` composite, encrypted access + refresh, plus email/name/scopes/expiry | Pattern D/E only — Google + Twitter access/refresh |

Provider × table examples:
- `google_client_id`, `google_client_secret` → API key table
- Google access/refresh tokens → OAuth token table under provider=`google`
- `twitter_client_id`, `twitter_client_secret` → API key table
- Twitter access/refresh → OAuth token table under provider=`twitter`
- `telegram` → API key table (Pattern I uses no OAuth table)

### 4.4 Encryption layer
[server/core/encryption.py](../server/core/encryption.py): `EncryptionService` uses Fernet (AES-128-CBC + HMAC-SHA256) with PBKDF2-SHA256 key derivation (600,000 iterations, OWASP 2024). Salt is 32 random bytes, generated on first DB init and stored in `credentials_metadata` table. The encryption password is the server-scoped `API_KEY_ENCRYPTION_KEY` env var, loaded once at startup ([server/main.py](../server/main.py) lifespan).

`encrypt(plaintext) -> str` / `decrypt(ciphertext) -> str`. Decryption raises `ValueError` on tampering/wrong-key, `RuntimeError` if `initialize()` was never called.

### 4.5 Runtime redirect URI derivation
[server/services/oauth_utils.py](../server/services/oauth_utils.py) `get_redirect_uri(connection, provider)` derives the OAuth callback URI from the live `WebSocket.base_url` (or `Request.base_url`) — converting `ws(s)` to `http(s)` and appending the path from `config/google_apis.json`. No hardcoded hostnames or ports. Works in dev (`http://localhost:3010/api/google/callback`) and prod (`https://flow.zeenie.xyz/api/google/callback`) without env vars.

---

## 5. Refactor Invariants

These are the non-negotiable behaviours the test suite enforces. The refactor is acceptable iff every test in [server/tests/credentials/](../server/tests/credentials) and [client/src/test/](../client/src/test) still passes without modification.

1. **WebSocket message types** for save/validate/delete/get of any API key are exactly `validate_api_key` / `save_api_key` / `get_stored_api_key` / `delete_api_key`, with payload shape `{provider, api_key, [session_id], [models]}`. The response of `get_stored_api_key` exposes `hasKey` (camelCase post-Wave-12; the WebSocketContext consumes it directly). Note: `ApiKeyStatus.hasKey` was retired on the frontend — "is stored" comes from the catalogue's `provider.stored` flag via `useProviderStored(id)` (single source of truth).
2. **OAuth login flows** never expose `client_secret` in the rendered DOM after save — the input is replaced by a "Configured" indicator.
3. **Google tokens** are read exclusively via `auth_service.get_oauth_tokens('google')`. No call site uses `get_api_key('google_access_token')` or any equivalent.
4. **Email** with `email_provider='custom'` writes BOTH `email_imap_*` and `email_smtp_*` keys (host + port + encryption for each side). With any other provider, those keys MUST NOT be written.
5. **Status objects** for whatsapp/android/twitter/google/telegram come from `WebSocketContext` broadcasts. The modal calls the corresponding `*_status` handler ONLY in response to an explicit Refresh click — never on mount, never on a polling timer.
6. **Provider Defaults** save uses `save_provider_defaults` with payload `{provider, defaults: {...}}` — never `save_api_key`.
7. **`auth_service.store_api_key`** receives `models` as required keyword arg; non-LLM keys pass `models=[]`.
8. **`AuthService` is the only route** to `credentials_db`. Routers and handlers must not import or call `CredentialsDatabase` directly.
9. **PKCE state** is consumed exactly once in `exchange_code`; a second call with the same state returns `{success: False, error: 'Invalid or expired state'}`.
10. **Twitter PKCE** authorization URL contains `code_challenge_method=S256` and `code_challenge = base64url(sha256(code_verifier))`.
11. **Google authorization URL** contains `access_type=offline` and `prompt=consent` (otherwise no refresh_token is returned).
12. **`get_redirect_uri`** strips the path from `connection.base_url` and converts `ws://` → `http://`, `wss://` → `https://`.
13. **Encryption** is reversible for unicode and large payloads; tampered ciphertext raises `ValueError`; uninitialized service raises `RuntimeError`.

---

## 6. Critical Files Cheat Sheet

| Layer | File | What lives here |
|---|---|---|
| UI | [client/src/components/CredentialsModal.tsx](../client/src/components/CredentialsModal.tsx) | All panels, all per-provider state, all click handlers |
| UI hook | [client/src/hooks/useApiKeys.ts](../client/src/hooks/useApiKeys.ts) | API key CRUD, defaults, usage, constraints |
| UI hook | [client/src/hooks/useWhatsApp.ts](../client/src/hooks/useWhatsApp.ts) | WhatsApp-specific operations |
| UI context | [client/src/contexts/WebSocketContext.tsx](../client/src/contexts/WebSocketContext.tsx) | `sendRequest<T>`, push status hooks |
| WS handlers | [server/routers/websocket.py](../server/routers/websocket.py) | All ~125 handlers; credentials block at 985–1670 |
| Credential service | [server/services/auth.py](../server/services/auth.py) | Single point of access; memory caches |
| Encryption | [server/core/encryption.py](../server/core/encryption.py) | Fernet + PBKDF2 |
| DB | [server/core/credentials_database.py](../server/core/credentials_database.py) | Two encrypted tables + metadata |
| OAuth (Google) | [server/services/google_oauth.py](../server/services/google_oauth.py) | Flow, PKCE, refresh, service builders |
| OAuth (Twitter) | [server/services/twitter_oauth.py](../server/services/twitter_oauth.py) | Manual PKCE, token exchange/refresh/revoke |
| OAuth (Claude) | [server/services/claude_oauth.py](../server/services/claude_oauth.py) | Subprocess-based isolated login |
| OAuth utils | [server/services/oauth_utils.py](../server/services/oauth_utils.py) | Runtime redirect URI derivation |
| Container | [server/core/container.py](../server/core/container.py) | DI wiring for `auth_service` etc. |

---

## 7. Test Suite Index

Run the matching tests after every refactor commit:

```bash
# Backend
cd server && uv run pytest tests/credentials/ -v

# Frontend
cd client && npm run test
```

See [server/tests/credentials/README.md](../server/tests/credentials/README.md) and [client/src/test/README.md](../client/src/test/README.md) for fixture details.
