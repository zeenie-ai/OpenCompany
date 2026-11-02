# Email Service

IMAP/SMTP email integration via the [Himalaya CLI](https://github.com/pimalaya/himalaya). Supports any IMAP/SMTP provider: Gmail, Outlook/Office 365, Yahoo, iCloud, ProtonMail (Bridge), Fastmail, and custom/self-hosted servers. Three workflow nodes (`emailSend`, `emailRead`, `emailReceive`) with dual-purpose workflow + AI tool integration for send/read, and a polling trigger for receive.

## Architecture

```
                   ┌──────────────────────────────────────────────┐
                   │          EmailService (singleton)             │
                   │                                              │
  plugin ──────────►  resolve_credentials(params) -> dict         │
  @Operation        │    preset (email_providers.json) > stored    │
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
EmailSendNode @Operation("send") (server/nodes/email/email_send/__init__.py)
   │
   ▼
EmailService.send(params)
   │
   ├── resolve_credentials(params)
   │      email_providers.json preset > stored "email_*" API keys
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
| 1 (highest) | **Provider preset** from `email_providers.json` | `providers.gmail.imap_host = "imap.gmail.com"` |
| 2 (lowest) | **Stored API key** via `AuthService.get_api_key()` | `email_imap_host` |

`provider` itself is the one field still read from node parameters, because it
*is* declared on all three Params models.

**Credentials are not readable from node parameters, by design.** There used to
be a documented third tier above these two (`params["imap_host"]`,
`params["password"]`, …). It never worked: the keys were not declared on any
Params model and all three set `extra="ignore"`, so Pydantic stripped them
before `model_dump()` and every `params.get(...)` returned `None`. Declaring
them to revive the tier is not an option either — `ToolNode.as_tool_schema`
dumps `Params.model_json_schema()` wholesale with no field-exclusion hook, so a
declared `password` becomes an argument the LLM can pass to a callable tool.

The `custom` provider's preset is **entirely blank**, which is what makes the
stored keys reachable for a self-hosted server. Do not put default ports or
encryption values back into it: `resolve_credentials` falls through an `or`
chain, so a non-empty preset value shadows the stored key and makes it
unsettable. That is exactly what pinned self-hosted servers to 993/465/tls.
Defaults live in `_himalaya._toml_port` / `_toml_encryption` instead.

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
| `client/src/components/credentials/panels/EmailPanel.tsx` + `panels/schemas/email.ts` | Email credentials panel (provider dropdown, email/password/display-name inputs, conditional custom IMAP/SMTP + encryption section). |

**AI tool schema** is derived automatically from each plugin's `Params` Pydantic model — there is no hand-written `EmailSendSchema` / `EmailReadSchema`. Dual-purpose dispatch goes through the generic plugin fast-path in `server/services/handlers/tools.py` (`instance.execute_as_tool(...)`), not a per-email `_execute_email_tool` branch.

## EmailService API

**File:** `server/nodes/email/_service.py`

### `async resolve_credentials(params: Dict) -> Dict`

Async. Builds the credentials dict consumed by `HimalayaService` (reads stored keys via `AuthService.get_api_key`). Required before every operation. Raises `ValueError` if the stored email address or password is missing.

Returned keys:
- `email` (str, required) — account address, used as IMAP/SMTP login
- `password` (str, required) — raw password or app password
- `display_name` (str) — from the stored `email_display_name` key; written to TOML only when non-empty
- `imap_host`, `imap_port`, `imap_encryption` — resolved via precedence chain
- `smtp_host`, `smtp_port`, `smtp_encryption` — resolved via precedence chain

Stored port values arrive as strings and go through `_coerce_port`, which
returns `None` rather than raising on garbage. **Every port and encryption key
is always present in the returned dict, sometimes valued `None`** — which is
why `_generate_config` must use `_toml_port(creds.get("imap_port"), 993)` and
not `creds.get("imap_port", 993)`: the key *is* present, so `dict.get`'s
default never fires and `backend.port = None` renders as unparseable TOML.

`display_name` resolves from the stored `email_display_name` key (written by the
credentials panel), not from node parameters.

### `send(params: Dict) -> Dict`

Resolves credentials, calls `HimalayaService.send_email()`, and returns the result merged with `{"from": creds["email"]}`.

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

Every field is **coerced, not trusted**: `emailReceive` overrides `execute()`
and passes raw parameters, so the Pydantic `ge=30, le=3600` guard never runs on
that path and `poll_interval` can arrive as `None` or a string.
`dict.get(key, default)` only substitutes when the key is *absent*, so an
explicit `None` used to reach `min()` and raise `TypeError`. Mirrors
`PollingTriggerNode._clamp_interval`.

Note this clamp applies only to the interactive Run path. The deployment path
re-clamps through `PollingTriggerNode._clamp_interval`, so the JSON
`polling.interval` is not consulted there.

### Polling helpers

- `poll_ids(creds, folder) -> Set[str]` — Calls `list_envelopes` with `baseline_page_size` (from JSON config), extracts envelope IDs as strings for baseline/diff.
- `fetch_detail(creds, msg_id, folder) -> Dict` — Calls `read_message` and merges `{message_id, folder}` into the result for downstream consumers.

## HimalayaService API

**File:** `server/nodes/email/_himalaya.py`

### CLI execution model

Every operation follows the same pattern inside `execute()`:

1. `ensure_binary()` — locate `himalaya` on `PATH` via `shutil.which`. Caches the path on the singleton. Raises `RuntimeError` with install instructions if missing. Despite the name it installs nothing; see "Installation Requirement" below.
2. `_generate_config(account_name, credentials)` — build TOML config as a single string (no template file; uses Python f-strings).
3. `tempfile.NamedTemporaryFile(suffix=".toml", delete=False)` — write config to a temp file.
4. `asyncio.create_subprocess_exec(binary, -c <tmp>, -a <account>, --output json, <args>)`
5. Pipe `stdin_data` if provided (used by `send_email` to deliver the MIME message).
6. Delegates to `services.events.cli.run_cli_command`, which enforces the `cli.timeout_seconds` budget from `email_providers.json` AND tree-kills the child on timeout. `asyncio.wait_for` cancels only the wrapper, so the old inline call left an orphaned process holding the temp config open -- and the `finally` unlink then raised PermissionError on Windows, masking the timeout.
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
| `body` | string | no | Plain text or HTML (per `body_type`) |
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
| `page_size` | list | default 20, max 500 |

**AI tool schema:** derived from `EmailReadParams`, exposing every operation and all operation-specific fields. The LLM picks an `operation` and fills the relevant subset.

### emailReceive — Polling trigger

Group: `['email', 'trigger']`. No inputs. Single output `main` with the new email data.

**Parameters:**
| Name | Default | Description |
|---|---|---|
| `provider` | gmail | Provider preset |
| `folder` | INBOX | Mailbox to monitor |
| `poll_interval` | 60 | seconds (clamped 30..3600) |
| `filter_query` | `""` | Case-insensitive substring match over subject / from / to / body, applied in `_filters.build_filter` |
| `mark_as_read` | false | If true, adds `Seen` flag to new messages after fetch |

**Baseline detection:** On first execution the handler calls `poll_ids(creds, folder)` to capture the set of currently-existing envelope IDs. The poll loop then diffs against this baseline -- only newly appearing IDs trigger the workflow. This avoids firing on existing historical mail.

**Standalone event dispatch:** When a new message arrives during an interactive
Run, `EmailReceiveNode.execute()` (a plain override, not an @Operation):
1. Fetches full detail via `fetch_detail`
2. Optionally flags it as read
3. Calls `dispatch_email_received(email_data)` (in `_events.py`), which builds a `WorkflowEvent` (`type="com.opencompany.email.message.received"`) and routes it through `dispatch.emit`
4. Returns the first new email's result

## Deployment Mode (Continuous Polling)

When a workflow containing `emailReceive` is deployed through workflow control,
its generation's `WorkflowControlWorkflow` owns continuous polling. It invokes
the activity generated by `PollingTriggerNode.as_poll_activity()`, maintains the
deduplication baseline in durable workflow state, and starts `MachinaWorkflow`
only for new messages. Pause gates new polls and graph starts; Resume continues
the same controller execution.

`EmailReceiveNode(PollingTriggerNode)` declares `setup_service`, `fetch_ids`,
`fetch_detail` and `post_emit`; `fetch_ids` forwards to `EmailService.poll_ids`.
The plugin system registers the corresponding Temporal poll
activity. The older deployment poll-coroutine registry and
`PollingTriggerWorkflow` remain compatibility paths for uncontrolled deployments
and replay of existing histories, not the authority for new controlled runs.

For each controlled polling Activity, `setup_service` resolves the service and
credentials again. The Activity returns bounded `events` and `seen_ids` to the
controller; the controller owns its next interval, continuation and graph
admission. It is not a single long-lived credential snapshot.

The separate canvas Run / legacy coroutine loop:
1. Resolves credentials once via `EmailService.resolve_credentials`
2. Establishes the baseline via `svc.poll_ids(creds, folder)`
3. On each iteration (clamped interval from `resolve_poll_params`):
   - Polls for current IDs and computes `new_ids = current - seen`
   - For each new ID, oldest UID first: fetches detail, optionally marks read, dispatches the event. The envelope returned to the canvas is the FIRST message; earlier revisions dispatched only one and dropped the rest.
   - Handles `asyncio.CancelledError` cleanly on teardown

All credential resolution and IMAP access still delegate to `EmailService` —
nothing is duplicated in the controller. See
[workflow control](temporal-workflow-control.md) and
[the event framework](event_framework.md) for the current control and trigger
lifecycle. The archived execution RFC describes the earlier design.

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

**File:** `client/src/components/credentials/panels/EmailPanel.tsx`

The Email category appears between Productivity and Android in the sidebar. The panel provides:

- **Provider dropdown** (7 options mirroring `email_providers.json`)
- **Email address** input
- **Password** input (secret, with "leave blank to keep existing" placeholder when already stored)
- **Per-provider auth note** (e.g., "Use an App Password from Google Account > Security > 2-Step Verification")
- **Conditional custom IMAP/SMTP block** shown only when `provider === 'custom'`:
  - IMAP host (text) + IMAP port (number, default 993)
  - SMTP host (text) + SMTP port (number, default 465)
- **Save** button — writes `email_provider`, `email_address`, and conditionally `email_password` (only if user typed a new one) + the four custom IMAP/SMTP keys when applicable.
- **Remove** button — clears all ten `email_*` keys. Switching away from the `custom` provider also clears the six server-specific keys, which otherwise stranded a stale host that then won the `or` chain.

Status is shown via `getSpecialStatus(item)` returning `{ connected: !!emailStored, label: emailAddress || 'Not configured' }`. No WebSocket connection is needed because email credentials are pure API keys (no live session like Telegram or WhatsApp).

## Configuration File

**File:** `server/config/email_providers.json`

```json
{
  "cli": {
    "binary": "himalaya",
    "homepage": "https://github.com/pimalaya/himalaya",
    "install_url": "https://github.com/pimalaya/himalaya/releases",
    "timeout_seconds": 60
  },
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
      "imap_host": "", "imap_port": "", "imap_encryption": "",
      "smtp_host": "", "smtp_port": "", "smtp_encryption": "",
      "auth_note": "Enter your mail server details"
    }
  }
}
```

Loaded lazily by the module-level `_load_config()` in `_service.py` and cached in a module-level `_CONFIG` variable (surfaced through `EmailService.config` / `.defaults` / `.polling` properties). To add a new provider, edit only this JSON — no code changes required.

**Config-driven, with named exceptions.** `resolve_poll_params` reads the polling defaults, `EmailService.read()` reads `defaults.page_size` / `defaults.flag`, and `HimalayaService.execute` reads `cli.timeout_seconds`. Three values deliberately live in code rather than here: the port and encryption fallbacks in `_himalaya._toml_port` / `_toml_encryption` (they must NOT be in the `custom` preset, which would shadow the stored keys), and `EmailReceiveNode.poll_interval_clamp`, which the deployment path uses instead of the JSON clamp. The provider dropdown options and auth notes are duplicated in `panels/schemas/email.ts` with no test asserting they stay in sync with the `providers` keys.

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
- [New Service Integration](./ARCHIVE/new_service_integration.md) — end-to-end integration pattern (use Google Workspace as a richer OAuth example)
- [Credentials Encryption](./credentials_encryption.md) — how `email_*` keys are encrypted on disk

## Security notes

**TOML generation escapes everything.** `_generate_config` routes every
interpolated credential through `_toml_str` / `_toml_port` /
`_toml_encryption`. Do not add a raw f-string interpolation: an unescaped `"`
in a password breaks the config, and `"` plus a newline injects arbitrary
himalaya keys — including a `backend.host` pointing at someone else's server.
App passwords are user-chosen, so that path needs no prior compromise.
`tests/nodes/test_email_service.py` parses the generated config with `tomllib`
and asserts the key set, which is the actual regression guard.

**CLI errors are scrubbed before they leave `execute`.** himalaya echoes the
config path — and on a parse failure, config content — into stderr, and that
string becomes the `RuntimeError` message, the node error envelope, a persisted
node output, and a WebSocket broadcast. `_scrub` runs before both the log call
and the raise.

**The password still touches disk.** Each invocation writes a `0600` +
`O_EXCL` temp TOML and unlinks it in a `finally` (now actually effective on the
timeout path, since the child is killed first). Removing that entirely means
switching to himalaya's `backend.auth.cmd`:

```toml
backend.auth.type = "password"
backend.auth.cmd = "<command that prints the password>"
```

Not adopted yet: `cmd` delegates to pimalaya's process crate, which is `sh -c`
on POSIX and `cmd /C` on Windows, and getting a value to round-trip without
trailing-newline corruption on both shells needs verification against a real
binary. Recorded here so the next person does not re-derive it.
