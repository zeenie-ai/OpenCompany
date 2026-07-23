# Python Backend - Environment Setup

## Quick Start

1. Copy the template to create your .env file:
```bash
cp .env.template .env
```

(If you run `company build` instead, its step [0/6] scaffolds `.env` from `.env.template` automatically and generates fresh random secrets — `secrets.token_hex(24)` — for `SECRET_KEY`, `JWT_SECRET_KEY`, and `API_KEY_ENCRYPTION_KEY` instead of copying the dev placeholders. An existing `.env` is never touched.)

2. Edit `.env` and add your API keys:
```bash
# Required for remote Android devices
WEBSOCKET_API_KEY=your-actual-key-here

# Optional: Default API keys (users can also add these through the UI)
GOOGLE_MAPS_API_KEY=your-google-maps-key
OPENAI_API_KEY=your-openai-key
```

3. Start the server:
```bash
python main.py
```

## Environment Variables

### Required

- `SECRET_KEY` - Server encryption key (32+ characters)
- `API_KEY_ENCRYPTION_KEY` - API key encryption (32+ characters)

### Optional but Recommended

- `WEBSOCKET_API_KEY` - Required for remote Android device connections
- `GOOGLE_MAPS_API_KEY` - Default Google Maps API key
- `OPENAI_API_KEY` - Default OpenAI API key
- `ANTHROPIC_API_KEY` - Default Anthropic API key
- `GOOGLE_AI_API_KEY` - Default Google AI API key

### Service URLs

- `WEBSOCKET_URL` - WebSocket server URL (default: ws://www.disutopia.xyz/ws)
- `WHATSAPP_SERVICE_URL` - WhatsApp service URL (default: http://localhost:3012)

### Development vs Production

The `.env.template` file contains development-safe defaults.

For production:
1. Change `DEBUG=false`
2. Generate new secure `SECRET_KEY` and `API_KEY_ENCRYPTION_KEY` (a `company build`-scaffolded `.env` already has fresh random values; a hand-copied template does not)
3. Set `RATE_LIMIT_ENABLED=true`
4. Configure proper CORS origins
5. Consider enabling Redis caching

The server also guards against placeholder secrets at startup: `server/core/config.py` exposes `DEV_SECRET_LITERALS` + `dev_secret_offenders()`, and the lifespan logs a non-fatal error banner if dev placeholder secrets are detected while auth is enabled or `DEPLOYMENT_MODE` is not `local`.

## Authentication & Encryption

### Authentication Toggle

Set `VITE_AUTH_ENABLED=false` in `.env` to bypass the login page entirely.

When auth is disabled:
- Login page is skipped
- User is set as anonymous with owner privileges
- Encryption service auto-initializes using `API_KEY_ENCRYPTION_KEY` as the password
- API keys can be saved/retrieved without user login

### Encryption Auto-Initialization

The encryption service requires initialization before encrypting/decrypting API keys. This normally happens during user login via `_initialize_encryption()`.

When `VITE_AUTH_ENABLED=false`, the encryption is auto-initialized at startup in `main.py`:

```python
# In main.py lifespan()
if settings.vite_auth_enabled == "false":
    encryption = container.encryption_service()
    if not encryption.is_initialized():
        encryption.initialize(settings.api_key_encryption_key, salt)
```

This allows API keys to be stored without authentication, useful for local development.

## Security Notes

**NEVER commit `.env` files to git!**

The `.env.template` file is safe to commit because it contains no real credentials.

All API keys should be:
- Stored in `.env` files (ignored by git)
- Or set as environment variables
- Or managed through the UI (stored encrypted in database)

## Files

- `.env` - Your local configuration (git-ignored, created from template)
- `.env.template` - Template with safe defaults (committed to git)
- `.env.example` - Full example with all options (committed to git)
- `.env.development` - Development environment (git-ignored)
- `.env.production` - Production environment (git-ignored)

## Google Workspace Integration

Google Workspace services (Gmail, Calendar, Drive, Sheets, Tasks, Contacts) share a single OAuth connection.

### Setup

1. Create a Google Cloud project and enable the required APIs
2. Create OAuth 2.0 credentials (Web Application type)
3. Add credentials via the Credentials Modal in the UI
4. Click "Login with Google" to authenticate

### Environment Variables (Optional)

```bash
# Custom OAuth redirect URI (defaults to localhost:3010)
GOOGLE_REDIRECT_URI=http://localhost:3010/api/google/callback
```

### Token Storage

All Google tokens use the `google_*` prefix:
- `google_client_id` - OAuth Client ID
- `google_client_secret` - OAuth Client Secret
- `google_access_token` - Access token for API calls
- `google_refresh_token` - Refresh token for renewal
- `google_user_info` - Connected user email and name

### API Handlers

| Service | Handler File | Node Types |
|---------|-------------|------------|
| Gmail | `handlers/gmail.py` | gmailSend, gmailSearch, gmailRead, gmailReceive |
| Calendar | `handlers/calendar.py` | calendarCreate, calendarList, calendarUpdate, calendarDelete |
| Drive | `handlers/drive.py` | driveUpload, driveDownload, driveList, driveShare |
| Sheets | `handlers/sheets.py` | sheetsRead, sheetsWrite, sheetsAppend |
| Tasks | `handlers/tasks.py` | tasksCreate, tasksList, tasksComplete |
| Contacts | `handlers/contacts.py` | contactsCreate, contactsList, contactsSearch |

### AI Agent Skills

Skills for AI agents are in `server/skills/productivity_agent/`:
- gmail-skill, calendar-skill, drive-skill, sheets-skill, tasks-skill, contacts-skill
