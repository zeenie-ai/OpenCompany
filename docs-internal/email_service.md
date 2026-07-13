# Email Service

IMAP/SMTP email integration via the [Himalaya CLI](https://github.com/pimalaya/himalaya). Supports any IMAP/SMTP provider: Gmail, Outlook/Office 365, Yahoo, iCloud, ProtonMail (Bridge), Fastmail, and custom/self-hosted servers. Three workflow nodes (`emailSend`, `emailRead`, `emailReceive`) with dual-purpose workflow + AI tool integration for send/read, and a polling trigger for receive.

## Architecture

```
                   ┌──────────────────────────────────────────────┐
                   │          EmailService (singleton)             │
                   │                                              │
  plugin ──────────►  resolve_credentials(params) -> dict         │
  execute_op        │    params > preset (email_providers.json)   │
  (nodes/email/...) │    > stored API keys (AuthService)           │
                   │                                              │
                   │  send(params)  -> dict                       │
                   │  read(params)  -> dict (7 operations)        │
                   │  poll_ids(creds, folder) -> set[str]         │
                   │  fetch_detail(creds, msg_id, folder) -> dict │
                   │  resolve_poll_params(params) -> dict         │
                   └───────────┬───────────────────┬──────────────┘
                               │                   │
                               ▼                   ▼
                      HimalayaService         AuthService
                      (CLI wrapper)          (get_api_key,
                       │                      store_api_key)
                       │ subprocess
                       ▼
                 `himalaya` CLI binary
                 generates temp TOML config,
                 calls IMAP/SMTP backends
```

`EmailService` and `HimalayaService` are both singletons living in the
self-contained email plugin folder (`server/nodes/email/_service.py` and
`server/nodes/email/_himalaya.py`). `EmailService` exposes `HimalayaService`
via its `himalaya` property (lazy import).

### Request Flow (emailSend)

```
emailSend node
   │
   ▼
EmailSendNode.execute_op (server/nodes/email/email_send/__init__.py)
   │
   ▼
EmailService.send(params)
   │
   ├── resolve_credentials(params)
   │      node params > email_providers.json preset
   │      > stored "email_*" API keys
   │
   ▼
HimalayaService.send_email(creds, to, subject, body, ...)
   │
   ├── _account_name(creds) = email prefix, sanitized
   ├── _generate_config() writes TOML to tempfile
   ├── compose MIME message (RFC 2822)
   ├── subprocess: himalaya -c <tmp> -a <acct> --output json message send
   │    (stdin = MIME message)
   ├── parse JSON stdout, delete tempfile
   ▼
{"success": True, "result": {...}, "execution_time": 0.34}
```

### Credentials Resolution Precedence

For every field in the returned credentials dict:

| Priority | Source | Example |
|---|---|---|
| 1 (highest) | **Node parameter** (per-node override) | `params["imap_host"]` |
| 2 | **Provider preset** from `email_providers.json` | `providers.gmail.imap_host = "imap.gmail.com"` |
| 3 (lowest) | **Stored API key** via `AuthService.get_api_key()` | `email_imap_host` |

Custom/self-hosted providers rely on stored custom keys because their presets are empty. Gmail and other named presets skip the custom-key lookup because the preset always wins first.

## Key Files

Self-contained plugin folder (Wave 11.I) — everything email-specific lives under `server/nodes/email/`.

| File | Description |
|------|-------------|
| `server/nodes/email/__init__.py` | Self-registration: `register_filter_builder("emailReceive", build_email_filter)` + `register_canary_trigger_type("emailReceive", "com.opencompany.email.message.received")` + re-export of `dispatch_email_received`. |
| `server/nodes/email/_himalaya.py` | `HimalayaService` (`ServiceSingleton`). Subprocess-based invocation, temp TOML config generation, JSON output parsing. Singleton via `get_himalaya_service()`. |
| `server/nodes/email/_service.py` | `EmailService` (`ServiceSingleton`). Credential resolution, operation dispatch, polling helpers. Exposes `HimalayaService` via the `himalaya` property. Singleton via `get_email_service()`. |
| `server/nodes/email/email_send/__init__.py` | `EmailSendNode(ActionNode)` — dual-purpose send plugin (`group = ("email", "tool")`, `usable_as_tool = True`). |
| `server/nodes/email/email_read/__init__.py` | `EmailReadNode(ActionNode)` — dual-purpose read/search/manage plugin (7 operations). |
| `server/nodes/email/email_receive/__init__.py` | `EmailReceiveNode(PollingTriggerNode)` — polling trigger; baseline + diff loop with `poll_ids` / `fetch_detail` hooks. |
| `server/nodes/email/_filters.py` | `build_filter` (registered as `build_email_filter`) — server-side filter closure for the `emailReceive` trigger. |
| `server/nodes/email/_events.py` | `email_message_received` `WorkflowEvent` factory + `dispatch_email_received` (single `dispatch.emit`, CloudEvents type `com.opencompany.email.message.received`). |
| `server/nodes/email/email_{send,read,receive}/icon.svg` + `meta.json` | Per-plugin icon (served at `/api/schemas/nodes/<type>/icon`) + color metadata. |
| `server/config/email_providers.json` | Provider presets (IMAP/SMTP host/port/encryption per provider) + defaults + polling config. Cached on first load. |
| `server/constants.py` | `EMAIL_TYPES`, `EMAIL_TOOL_TYPES`, plus `emailReceive` in `POLLING_TRIGGER_TYPES` and `WORKFLOW_TRIGGER_TYPES`. |
| `client/src/components/CredentialsModal.tsx` | Email credentials panel (provider dropdown, email/password inputs, conditional custom IMAP/SMTP section). |

**AI tool schema** is derived automatically from each plugin's `Params` Pydantic model — there is no hand-written `EmailSendSchema` / `EmailReadSchema`. Dual-purpose dispatch goes through the generic plugin fast-path in `server/services/handlers/tools.py` (`instance.execute_as_tool(...)`), not a per-email `_execute_email_tool` branch.

## EmailService API

**File:** `server/nodes/email/_service.py`

### `async resolve_credentials(params: Dict) -> Dict`

Async. Builds the credentials dict consumed by `HimalayaService` (reads stored keys via `AuthService.get_api_key`). Required before every operation. Raises `ValueError` if the email address or password is not set (neither in params nor stored).

Returned keys:
- `email` (str, required) — account address, used as IMAP/SMTP login
- `password` (str, required) — raw password or app password
- `display_name` (str) — optional display name (only written to TOML if non-empty)
- `imap_host`, `imap_port`, `imap_encryption` — resolved via precedence chain
- `smtp_host`, `smtp_port`, `smtp_encryption` — resolved via precedence chain

Ports come from any level of precedence as `int` (the internal `_coerce_port` helper converts stored string values safely).

### `send(params: Dict) -> Dict`

Resolves credentials, calls `HimalayaService.send_email()`, and returns the result merged with `{"from": creds["email"]}`. Validates `to` and `subject` are non-empty.

### `read(params: Dict) -> Dict`

Operation dispatcher keyed by `params["operation"]` (default `"list"`):

| Operation | HimalayaService method | Required params |
|---|---|---|
| `list` | `list_envelopes` | - |
| `search` | `search_envelopes` | `query` |
| `read` | `read_message` | `message_id` |
| `folders` | `list_folders` | - |
| `move` | `move_message` | `message_id`, `target_folder` |
| `delete` | `delete_message` | `message_id` |
| `flag` | `flag_message` | `message_id` |

Return shape: `{"operation": ..., "folder": ..., ...data}` where `data` is merged in if it's a dict, or wrapped as `{"data": data}` otherwise.

### `resolve_poll_params(params: Dict) -> Dict`

Reads polling config from `email_providers.json` (`polling.interval`, `polling.min_interval`, `polling.max_interval`) and clamps the user-provided `poll_interval` into range. Returns `{"interval", "folder", "mark_as_read"}`.

### Polling helpers

- `poll_ids(creds, folder) -> Set[str]` — Calls `list_envelopes` with `baseline_page_size` (from JSON config), extracts envelope IDs as strings for baseline/diff.
- `fetch_detail(creds, msg_id, folder) -> Dict` — Calls `read_message` and merges `{message_id, folder}` into the result for downstream consumers.

## HimalayaService API

**File:** `server/nodes/email/_himalaya.py`

### CLI execution model

Every operation follows the same pattern inside `execute()`:

1. `ensure_binary()` — locate `himalaya` on `PATH` via `shutil.which`. Caches the path on the singleton. Raises `RuntimeError` with install instructions if missing.
2. `_generate_config(account_name, credentials)` — build TOML config as a single string (no template file; uses Python f-strings).
3. `tempfile.NamedTemporaryFile(suffix=".toml", delete=False)` — write config to a temp file.
4. `asyncio.create_subprocess_exec(binary, -c <tmp>, -a <account>, --output json, <args>)`
5. Pipe `stdin_data` if provided (used by `send_email` to deliver the MIME message).
6. `asyncio.wait_for(proc.communicate(...), timeout=60)` — enforces a 60s budget.
7. Parse JSON stdout (`json.loads`). Fall back to `{"raw_output": stdout_str}` on JSON decode error.
8. `finally`: delete the temp config file with `Path.unlink(missing_ok=True)`.

**Error handling:** non-zero exit raises `RuntimeError(f"himalaya error: {stderr}")`. The handler layer catches this and returns the standard error-shaped result dict.

### High-level methods

| Method | Himalaya subcommand | Notes |
|---|---|---|
| `send_email(creds, to, subject, body, cc, bcc, body_type)` | `message send` (stdin) | Composes MIME via `email.mime` stdlib. `body_type="html"` wraps in `MIMEMultipart("alternative")`; otherwise `MIMEText`. |
| `list_envelopes(creds, folder, page, page_size)` | `envelope list -f <folder> --page N --page-size M` | |
| `search_envelopes(creds, query, folder)` | `envelope list -f <folder> --query <q>` | |
| `read_message(creds, message_id, folder)` | `message read <id> -f <folder>` | |
| `move_message(creds, message_id, target_folder, folder)` | `message move <id> <target> -f <folder>` | |
| `delete_message(creds, message_id, folder)` | `message delete <id> -f <folder>` | |
| `flag_message(creds, message_id, flag, action, folder)` | `flag add/remove <id> --flag <name> -f <folder>` | `action` must be `"add"` or `"remove"`. |
| `list_folders(creds)` | `folder list` | |

### Account naming

`_account_name(credentials)` derives a consistent TOML section name from the email address:

```python
"jane.doe+bot@example.com" -> "jane_doe_bot"
```

Dots and `+` are replaced with underscores so the name is valid as a TOML table key. The account name is arbitrary -- Himalaya just needs a consistent label between config and CLI invocation.

## Node Catalog

### emailSend — Dual-purpose (workflow node + AI tool)

Send email via SMTP. Group: `['email', 'tool']`. Two outputs (`main`, `tool`).

**Parameters:**
| Name | Type | Required | Description |
|---|---|---|---|
| `provider` | options | yes | `gmail`, `outlook`, `yahoo`, `icloud`, `protonmail`, `fastmail`, `custom` |
| `to` | string | yes | Recipient(s), comma-separated |
| `subject` | string | yes | |
| `body` | string | yes | Plain text or HTML (per `body_type`) |
| `cc` | string | no | |
| `bcc` | string | no | |
| `body_type` | options | no | `text` (default) or `html` |

**AI tool schema:** derived from `EmailSendParams` (same fields as the node, `body_type` default `"text"`, `cc`/`bcc` optional). When the LLM invokes the tool, the generic plugin tool fast-path (`instance.execute_as_tool` in `handlers/tools.py`) runs `EmailSendNode`'s send operation with the LLM args merged over the node params.

### emailRead — Dual-purpose (workflow node + AI tool)

Read/search/manage emails via IMAP. Group: `['email', 'tool']`. Two outputs (`main`, `tool`).

**Parameters** (all conditional via `displayOptions.show` on `operation`):
| Name | Shown when operation is | Description |
|---|---|---|
| `operation` | always | list / search / read / folders / move / delete / flag |
| `folder` | list, search, read, move, delete, flag | default `INBOX` |
| `query` | search | Himalaya search syntax (`from:`, `subject:`, etc.) |
| `message_id` | read, move, delete, flag | |
| `target_folder` | move | |
| `flag` | flag | Seen / Answered / Flagged / Draft / Deleted |
| `flag_action` | flag | add / remove |
| `page` | list | default 1 |
| `page_size` | list | default 20, max 100 |

**AI tool schema:** derived from `EmailReadParams`, exposing every operation and all operation-specific fields. The LLM picks an `operation` and fills the relevant subset.

### emailReceive — Polling trigger

Group: `['email', 'trigger']`. No inputs. Single output `main` with the new email data.

**Parameters:**
| Name | Default | Description |
|---|---|---|
| `provider` | gmail | Provider preset |
| `folder` | INBOX | Mailbox to monitor |
| `poll_interval` | 60 | seconds (clamped 30..3600) |
| `filter_query` | `""` | Server-side filter (currently applied client-side in `build_email_filter`) |
| `mark_as_read` | false | If true, adds `Seen` flag to new messages after fetch |

**Baseline detection:** On first execution the handler calls `poll_ids(creds, folder)` to capture the set of currently-existing envelope IDs. The poll loop then diffs against this baseline -- only newly appearing IDs trigger the workflow. This avoids firing on existing historical mail.

**Event dispatch:** When a new message arrives, `EmailReceiveNode.execute_op`:
1. Fetches full detail via `fetch_detail`
2. Optionally flags it as read
3. Calls `dispatch_email_received(email_data)` (in `_events.py`), which builds a `WorkflowEvent` (`type="com.opencompany.email.message.received"`) and routes it via a single `dispatch.emit` so Temporal-durable `TriggerListenerWorkflow` consumers fire
4. Returns the first new email's result (single-shot in standalone mode; the `PollingTriggerNode` deployment loop keeps polling)

## Deployment Mode (Continuous Polling)

When a workflow containing `emailReceive` is **deployed** (not just run once), the trigger runs as a continuous polling loop owned by its `PollingTriggerNode` base class — not by a hand-written factory in the deployment manager.

`EmailReceiveNode(PollingTriggerNode)` declares the polling hooks (`poll_ids`, `fetch_detail`); the base class's poll-coroutine factory self-registers via `services.deployment.poll_registry.register_poll_coroutine_factory`. The deployment manager's per-type factories (`_create_poll_coroutine` / `_create_gmail_poll_coroutine` / `_create_email_poll_coroutine`) were **removed in Wave 11.I, milestone L** — the dispatch now flows through `DeploymentManager._setup_event_trigger`, which looks up the registered factory.

The poll loop:
1. Resolves credentials once via `EmailService.resolve_credentials`
2. Establishes the baseline via `svc.poll_ids(creds, folder)`
3. On each iteration (clamped interval from `resolve_poll_params`):
   - Polls for current IDs and computes `new_ids = current - seen`
   - For each new ID: fetches detail, optionally marks read, dispatches the event
   - Handles `asyncio.CancelledError` cleanly on teardown

All credential resolution and IMAP access still delegate to `EmailService` — nothing is duplicated in the deployment manager. (Post-2026-05-15, with the event framework enabled, deployed `emailReceive` triggers run as a Temporal-durable `PollingTriggerWorkflow` via the canary registration in `nodes/email/__init__.py`.)

## Credentials Storage

### API key names

Stored via `AuthService.store_api_key()` / read via `.get_api_key()`. All keys live in the `EncryptedAPIKey` table (separate from OAuth tokens).

**Required keys (any provider):**
| Key | Stored by | Read by |
|---|---|---|
| `email_provider` | Credentials Modal | `resolve_credentials` (defaults to `gmail`) |
| `email_address` | Credentials Modal | `resolve_credentials` |
| `email_password` | Credentials Modal | `resolve_credentials` |

**Optional keys (custom provider only):**
| Key | Purpose |
|---|---|
| `email_imap_host` | Fallback IMAP hostname when preset is empty |
| `email_imap_port` | Fallback IMAP port (stored as string, coerced to int) |
| `email_imap_encryption` | `tls` / `start-tls` / `none` |
| `email_smtp_host` | Fallback SMTP hostname |
| `email_smtp_port` | Fallback SMTP port |
| `email_smtp_encryption` | `tls` / `start-tls` / `none` |

These custom keys are **only used when the preset for the selected provider has empty host/port fields** (i.e., `provider == 'custom'`). For named providers like Gmail, the preset always wins before the stored custom keys are consulted.

### Credentials Modal UI

**File:** `client/src/components/CredentialsModal.tsx`

The Email category appears between Productivity and Android in the sidebar. The panel provides:

- **Provider dropdown** (7 options mirroring `email_providers.json`)
- **Email address** input
- **Password** input (secret, with "leave blank to keep existing" placeholder when already stored)
- **Per-provider auth note** (e.g., "Use an App Password from Google Account > Security > 2-Step Verification")
- **Conditional custom IMAP/SMTP block** shown only when `provider === 'custom'`:
  - IMAP host (text) + IMAP port (number, default 993)
  - SMTP host (text) + SMTP port (number, default 465)
- **Save** button — writes `email_provider`, `email_address`, and conditionally `email_password` (only if user typed a new one) + the four custom IMAP/SMTP keys when applicable.
- **Remove** button — clears all seven `email_*` keys.

Status is shown via `getSpecialStatus(item)` returning `{ connected: !!emailStored, label: emailAddress || 'Not configured' }`. No WebSocket connection is needed because email credentials are pure API keys (no live session like Telegram or WhatsApp).

## Configuration File

**File:** `server/config/email_providers.json`

```json
{
  "defaults": {
    "provider": "gmail",
    "folder": "INBOX",
    "body_type": "text",
    "page_size": 20,
    "flag": "Seen",
    "flag_action": "add"
  },
  "polling": {
    "interval": 60,
    "min_interval": 30,
    "max_interval": 3600,
    "baseline_page_size": 50
  },
  "providers": {
    "gmail": {
      "name": "Gmail",
      "imap_host": "imap.gmail.com",
      "imap_port": 993,
      "imap_encryption": "tls",
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 465,
      "smtp_encryption": "tls",
      "auth_note": "Use App Password from Google Account > Security > 2-Step Verification"
    },
    "outlook": { "...": "..." },
    "yahoo": { "...": "..." },
    "icloud": { "...": "..." },
    "protonmail": {
      "imap_host": "127.0.0.1", "imap_port": 1143, "imap_encryption": "none",
      "smtp_host": "127.0.0.1", "smtp_port": 1025, "smtp_encryption": "none",
      "auth_note": "Requires ProtonMail Bridge running locally"
    },
    "fastmail": { "...": "..." },
    "custom": {
      "imap_host": "", "imap_port": 993, "imap_encryption": "tls",
      "smtp_host": "", "smtp_port": 465, "smtp_encryption": "tls",
      "auth_note": "Enter your mail server details"
    }
  }
}
```

Loaded lazily by the module-level `_load_config()` in `_service.py` and cached in a module-level `_CONFIG` variable (surfaced through `EmailService.config` / `.defaults` / `.polling` properties). To add a new provider, edit only this JSON — no code changes required.

**Zero magic numbers:** every default, clamp, and preset lives in this file. `resolve_poll_params` reads the polling defaults; `EmailService.read()` reads `defaults.page_size`, `defaults.flag`, etc.; the provider dropdown options in `CredentialsModal.tsx` mirror the `providers` keys.

## Installation Requirement

The `himalaya` CLI must be installed and on `PATH`. Install via:

```bash
# macOS
brew install himalaya

# Linux / macOS with Rust toolchain
cargo install himalaya

# Pre-built binaries (Linux, macOS, Windows)
# https://github.com/pimalaya/himalaya/releases
```

`HimalayaService.ensure_binary()` caches the resolved path on the singleton after first detection. If missing, the handler returns:

```json
{
  "success": false,
  "error": "himalaya CLI not found in PATH. Install via: cargo install himalaya, brew install himalaya, ..."
}
```

## Provider-Specific Notes

| Provider | Password type | Notes |
|---|---|---|
| **Gmail** | App Password | Requires 2-Step Verification enabled first |
| **Outlook / Office 365** | Account or App Password | STARTTLS on port 587 |
| **Yahoo** | App Password | Requires "Allow apps that use less secure sign-in" or App Password |
| **iCloud** | App-Specific Password | Generate from appleid.apple.com |
| **ProtonMail** | Bridge password | Requires running ProtonMail Bridge locally. IMAP `localhost:1143`, SMTP `localhost:1025`, encryption `none` (bridge handles TLS internally). |
| **Fastmail** | App Password | Generate from Settings > Privacy & Security |
| **Custom** | Whatever the server accepts | Must fill IMAP/SMTP host + port in the Credentials Modal's custom block. |

## Related Docs

- [Node Creation Guide](./node_creation.md) — canonical plugin recipe (covers dual-purpose nodes; `emailSend`/`emailRead` are live examples)
- [Event Waiter System](./event_waiter_system.md) — trigger registration for `emailReceive`
- [New Service Integration](./new_service_integration.md) — end-to-end integration pattern (use Google Workspace as a richer OAuth example)
- [Credentials Encryption](./credentials_encryption.md) — how `email_*` keys are encrypted on disk
